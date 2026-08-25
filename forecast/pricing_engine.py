# SPEC §3: Pricing Engine
from __future__ import annotations

import math
import logging
from typing import Any, Dict, List

from config import DB_PATH, PHYSICS_DELTA_ENABLED
from forecast.weather_contracts import resolve_weather_contract, WeatherContractSemantics

logger = logging.getLogger("pricing_engine")

# Conservative physical corrections are applied in degrees Fahrenheit to the
# deterministic forecast variable before the contract CDF. They are never
# added directly to a probability.
_HIGH_MAX_COOLING_F = -2.5
_LOW_MAX_LIFT_F = 2.5
_PRECIP_MIDPOINT = 0.15  # inches
_PRECIP_STEEPNESS = 12.0
_WIND_MIDPOINT = 14.5    # mph
_WIND_STEEPNESS = 0.35
PHYSICS_METHOD = "bounded_heuristic_v1"
PRODUCTION_MODEL_PATH = "deterministic_gfs_ecmwf_aigfs_hrrr_physics"


def _sigmoid_signal(value: float, midpoint: float, steepness: float) -> float:
    exponent = max(-50.0, min(50.0, -steepness * (value - midpoint)))
    return 1.0 / (1.0 + math.exp(exponent))


def calculate_temperature_physics_adjustment(
    model_data: dict,
    mode: str,
    hours_to_res: float,
) -> dict[str, float]:
    """Return a bounded, mode-aware correction in degrees Fahrenheit.

    Daily highs receive only a radiative/precipitation cooling correction.
    Daily lows receive a cloud/moisture and stable-boundary-layer wind-mixing
    lift. Hourly temperature, precipitation, snow, and wind contracts receive
    no cross-variable correction because direction cannot be inferred safely
    without advection, phase, and time-of-day state.
    """
    precip = float(model_data.get("mean_precip") or 0.0)
    wind = float(model_data.get("mean_wind") or 0.0)
    cloud = max(0.0, min(1.0, float(model_data.get("peak_tcdc") or 0.0) / 100.0))
    ssrd = model_data.get("peak_ssrd")
    solar_deficit = (
        max(0.0, min(1.0, (550.0 - float(ssrd)) / 550.0))
        if ssrd is not None
        else 0.0
    )
    precip_signal = _sigmoid_signal(precip, _PRECIP_MIDPOINT, _PRECIP_STEEPNESS) if precip > 0.0 else 0.0
    wind_signal = _sigmoid_signal(wind, _WIND_MIDPOINT, _WIND_STEEPNESS) if wind > 0.0 else 0.0
    lead_scale = max(0.35, min(1.0, 1.0 - (max(0.0, hours_to_res) / 240.0)))

    high_cooling = 0.0
    low_lift = 0.0
    mode_upper = str(mode or "").upper()
    if mode_upper == "HIGH":
        radiative_signal = max(precip_signal, 0.65 * cloud + 0.35 * solar_deficit)
        high_cooling = _HIGH_MAX_COOLING_F * radiative_signal * lead_scale
    elif mode_upper == "LOW":
        moisture_signal = max(precip_signal, cloud)
        inversion_signal = max(0.20, 1.0 - cloud)
        low_lift = min(
            _LOW_MAX_LIFT_F,
            (1.25 * moisture_signal + 1.25 * wind_signal * inversion_signal) * lead_scale,
        )

    return {
        "adjustment_f": high_cooling + low_lift,
        "high_cooling_f": high_cooling,
        "low_lift_f": low_lift,
        "precip_signal": precip_signal,
        "wind_signal": wind_signal,
        "cloud_fraction": cloud,
        "solar_deficit": solar_deficit,
        "lead_scale": lead_scale,
    }


def apply_temperature_physics(
    members: List[float],
    model_data: dict,
    mode: str,
    hours_to_res: float,
) -> tuple[List[float], dict[str, float]]:
    details = calculate_temperature_physics_adjustment(model_data, mode, hours_to_res)
    if not PHYSICS_DELTA_ENABLED or not members:
        details["adjustment_f"] = 0.0
        details["high_cooling_f"] = 0.0
        details["low_lift_f"] = 0.0
        return list(members), details
    adjustment = float(details["adjustment_f"])
    return [float(value) + adjustment for value in members], details

def get_lead_bucket(hours_to_res: float) -> int:
    """Classify lead time into one of the 4 lead-buckets (SPEC §3.2)."""
    if hours_to_res < 6.0:
        return 0
    elif hours_to_res < 24.0:
        return 1
    elif hours_to_res < 72.0:
        return 2
    else:
        return 3

def calculate_aigfs_lambda(
    physical_members: List[float],
    aigfs_value: float | None,
) -> float:
    """
    Machine-learning forecast variance integrator z-score scaler.
    Neutral at z=1.0. Bounded in [0.70, 2.25].

    The original implementation passed the contract strike as ``T_ai`` and
    requested a retired GraphCast identifier that now yields null values. That
    measured distance-to-strike, not model disagreement. ``aigfs_value`` is the
    actual contract-aligned NCEP AIGFS forecast. Missing AIGFS is neutral.
    """
    if not physical_members or aigfs_value is None:
        return 1.0

    K = len(physical_members)
    mu_ens = sum(physical_members) / K

    if K > 1:
        variance = sum((x - mu_ens) ** 2 for x in physical_members) / (K - 1)
        sigma_ens = math.sqrt(variance)
    else:
        sigma_ens = 1.0

    # z = |mu_ens - T_ai| / (sigma_ens + 0.25)
    # Floor denominator at 1e-9 to prevent division by zero (SPEC Rule 5)
    denom = max(1e-9, sigma_ens + 0.25)
    z = abs(mu_ens - float(aigfs_value)) / denom

    # Lambda(z) = 0.70 + 1.55 / (1 + exp(-2.0*(z - 1.71)))
    # Clamp exponent to prevent overflow (SPEC Rule 5)
    exponent = -2.0 * (z - 1.71)
    exponent = max(-50.0, min(50.0, exponent))

    lambda_val = 0.70 + 1.55 / (1.0 + math.exp(exponent))
    return lambda_val


# Compatibility name for old imports. New code and operator truth use AIGFS.
calculate_graphcast_lambda = calculate_aigfs_lambda

def kernel_smoothed_probability(
    members: List[float],
    semantics: WeatherContractSemantics,
    bias: float = 0.0,
    aigfs_lambda: float = 1.0,
    predictive_sigma: float | None = None,
) -> float:
    """
    SPEC §3.1: Kernel-smoothed exceedance probability.
    Uses standard normal CDF with bandwidth h_m.
    """
    if not members:
        return 0.5

    K = len(members)
    mean = sum(members) / K
    if K > 1:
        variance = sum((x - mean) ** 2 for x in members) / (K - 1)
        sigma = math.sqrt(variance)
    else:
        sigma = float(predictive_sigma) if predictive_sigma is not None else 1.0

    if predictive_sigma is not None:
        sigma = max(sigma, float(predictive_sigma))

    # Floor sigma to prevent division by zero or negative (SPEC Rule 5)
    sigma = max(0.05, sigma)

    # h_m = 0.9 * sigma_m * K_m**(-0.2)
    h = 0.9 * sigma * (K ** -0.2)

    # Apply the AIGFS variance integrator: h_m *= sqrt(Lambda)
    h *= math.sqrt(aigfs_lambda)

    # Floor denominator to prevent division by zero (SPEC Rule 5)
    h = max(1e-9, h)

    def phi(z: float) -> float:
        # Standard normal CDF
        # Clamp input to prevent overflow/underflow (SPEC Rule 5)
        z = max(-50.0, min(50.0, z))
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    comp = semantics.comparator

    if comp == "between":
        if semantics.lower_bound is None or semantics.upper_bound is None:
            return 0.5
        lower = float(semantics.lower_bound)
        upper = float(semantics.upper_bound)

        probs = []
        for x in members:
            # P(lower <= X <= upper) = Phi((upper - (x - bias)) / h) - Phi((lower - (x - bias)) / h)
            prob_k = phi((upper - (x - bias)) / h) - phi((lower - (x - bias)) / h)
            probs.append(prob_k)
        # Clamp probability input to [0.01, 0.99] (SPEC Rule 5)
        return max(0.01, min(0.99, sum(probs) / K))

    # Default to threshold/strike
    limit = semantics.threshold if semantics.threshold is not None else semantics.display_high
    if limit is None:
        limit = semantics.display_low
    if limit is None:
        return 0.5

    limit = float(limit)
    probs = []

    for x in members:
        if comp == "lt":
            # P(X < limit) = Phi((limit - (x - bias)) / h)
            prob_k = phi((limit - (x - bias)) / h)
        else: # gt or fallback
            # P(X > limit) = 1.0 - Phi((limit - (x - bias)) / h)
            prob_k = 1.0 - phi((limit - (x - bias)) / h)
        probs.append(prob_k)

    # Clamp probability input to [0.01, 0.99] (SPEC Rule 5)
    return max(0.01, min(0.99, sum(probs) / K))

def calculate_brier_weights(mode: str, lead_bucket: int, db_path: str | None) -> Dict[str, float]:
    """Return only the explicitly promoted RBI 2.0 champion weights."""
    defaults = {"gfs": 0.60, "ecmwf": 0.40}
    try:
        from intelligence.rbi2 import get_active_model_weights
        weights = get_active_model_weights(mode, db_path=db_path or DB_PATH)
        return {"gfs": weights["gfs"], "ecmwf": weights["ecmwf"]}
    except Exception as e:
        logger.warning(f"Error loading RBI 2.0 champion: {e}")
        return defaults

def _hrrr_alpha(q_hrrr: float | None, hours_to_res: float) -> float:
    if q_hrrr is None or hours_to_res > 48.0:
        return 0.0
    exponent = max(-50.0, min(50.0, 0.30 * (hours_to_res - 18.0)))
    return 0.85 / (1.0 + math.exp(exponent))


def calculate_blend_weights(
    q_gfs: float | None,
    q_ecmwf: float | None,
    q_hrrr: float | None,
    weights: Dict[str, float],
    hours_to_res: float,
) -> Dict[str, float]:
    """Normalize only models that supplied real contract-aligned probabilities."""
    physical_available = {
        "gfs": q_gfs is not None,
        "ecmwf": q_ecmwf is not None,
    }
    raw_gfs = max(0.0, float(weights.get("gfs", 0.60)))
    raw_ecmwf = max(0.0, float(weights.get("ecmwf", 0.40)))
    learned_pair_total = raw_gfs + raw_ecmwf
    if learned_pair_total <= 0.0:
        raw_gfs, raw_ecmwf, learned_pair_total = 0.60, 0.40, 1.0

    raw = {"gfs": raw_gfs, "ecmwf": raw_ecmwf}

    for model, available in physical_available.items():
        if not available:
            raw[model] = 0.0
    physical_total = sum(raw.values())
    if physical_total <= 0.0:
        return {"gfs": 0.0, "ecmwf": 0.0, "hrrr": 0.0}

    alpha = _hrrr_alpha(q_hrrr, hours_to_res)
    normalized = {
        model: (1.0 - alpha) * value / physical_total
        for model, value in raw.items()
    }
    normalized["hrrr"] = alpha
    return normalized


def log_odds_blend(
    q_gfs: float | None,
    q_ecmwf: float | None,
    q_hrrr: float | None,
    weights: Dict[str, float],
    hours_to_res: float,
) -> float:
    """
    SPEC §3.3 & §3.4: Log-odds blend with HRRR splice.
    """
    blend_weights = calculate_blend_weights(q_gfs, q_ecmwf, q_hrrr, weights, hours_to_res)

    def logit(p: float) -> float:
        p = max(0.005, min(0.995, p))
        return math.log(p / (1.0 - p))

    def sigmoid(x: float) -> float:
        x = max(-50.0, min(50.0, x))
        return 1.0 / (1.0 + math.exp(-x))

    probabilities = {
        "gfs": q_gfs,
        "ecmwf": q_ecmwf,
        "hrrr": q_hrrr,
    }
    log_odds = sum(
        blend_weights[model] * logit(probability)
        for model, probability in probabilities.items()
        if probability is not None and blend_weights[model] > 0.0
    )

    l0 = 0.0
    return sigmoid(log_odds + l0)

def calibrate_probability(q_hat: float, mode: str, db_path: str | None) -> float:
    """Return the governed raw blend until a versioned calibrator exists.

    The retired implementation refit isotonic regression on every pricing call
    using unversioned, mixed-era history. That could transform live probability
    with stale semantics. RBI 2.0 remains the only adaptive production layer;
    calibration can return only after it has its own epoch, walk-forward proof,
    immutable artifact, and explicit promotion gate.
    """
    return max(0.01, min(0.99, float(q_hat)))

def calculate_pricing(
    ticker: str,
    w_data: dict,
    hours_to_res: float,
    *,
    contract_name: str = "",
    strike: float | None = None,
    db_path: str | None = None,
) -> Dict[str, Any]:
    """
    SPEC §3: Canonical entrypoint to run the entire pricing engine pipeline.
    """
    semantics = resolve_weather_contract(
        ticker=ticker,
        contract_name=contract_name,
        strike=strike,
    )
    if semantics is None or semantics.ambiguous:
        raise ValueError(f"Pricing engine cannot resolve semantics for {ticker}")

    provider_mode = str(w_data.get("provider_mode") or "deterministic_multi_model")
    if provider_mode == "ensemble_members":
        raise ValueError("Commercial ensemble payloads are retired from the production probability path")

    mode = semantics.mode

    # 1. Extract contract-aligned deterministic model values. The list-shaped
    # representation is retained because the CDF also supports replay fixtures.
    if mode in ["RAIN", "SNOW"]:
        key = "members_precip"
    elif mode == "WIND":
        key = "members_wind"
    elif mode == "LOW":
        key = "members_low"
    elif mode == "TEMP":
        key = "members_temp"
    else:
        key = "members_high"

    members_gfs = [float(v) for v in (w_data.get(key) or [])]
    ecmwf_data = w_data.get("ecmwf") or {}
    members_ec = [float(v) for v in (ecmwf_data.get(key) or [])]
    aigfs_data = w_data.get("aigefs") or {}
    members_aigfs = [float(v) for v in (aigfs_data.get(key) or [])]
    target_strike = strike if strike is not None else semantics.threshold
    if target_strike is None:
        target_strike = semantics.display_high if semantics.display_high is not None else 0.0

    # 2. Apply bounded meteorological corrections in forecast-variable space.
    members_gfs, physics_gfs = apply_temperature_physics(
        members_gfs, w_data, mode, hours_to_res
    )
    members_ec, physics_ecmwf = apply_temperature_physics(
        members_ec, ecmwf_data, mode, hours_to_res
    )
    members_aigfs, physics_aigfs = apply_temperature_physics(
        members_aigfs, aigfs_data, mode, hours_to_res
    )

    # 3. AIGFS uncertainty integrator. Compare the actual deterministic AI
    # forecast with all available physical members.  Agreement narrows the
    # kernels; disagreement widens them and later reduces Kelly size.
    combined_members = members_gfs + members_ec
    aigfs_value = members_aigfs[0] if members_aigfs else None
    lambda_scaler = calculate_aigfs_lambda(combined_members, aigfs_value)

    # 4. Kernel-smoothed probabilities with explicit predictive-error sigma.
    # Retrieve station bias if any (default to 0)
    bias_gfs = 0.0
    bias_ec = 0.0

    sigma_key = {
        "HIGH": "sigma_high",
        "LOW": "sigma_low",
        "TEMP": "sigma_temp",
        "RAIN": "sigma_precip",
        "SNOW": "sigma_precip",
        "WIND": "sigma_wind",
    }.get(mode, "sigma_high")

    def _predictive_sigma(model_data: dict) -> float:
        raw = model_data.get(sigma_key)
        if raw is None:
            return 1.5 if mode in {"HIGH", "LOW", "TEMP"} else 0.10
        return max(0.05, float(raw))

    q_gfs = kernel_smoothed_probability(
        members_gfs,
        semantics,
        bias_gfs,
        lambda_scaler,
        predictive_sigma=_predictive_sigma(w_data),
    ) if members_gfs else None
    q_ecmwf = kernel_smoothed_probability(
        members_ec,
        semantics,
        bias_ec,
        lambda_scaler,
        predictive_sigma=_predictive_sigma(ecmwf_data),
    ) if members_ec else None
    q_aigfs = kernel_smoothed_probability(
        members_aigfs,
        semantics,
        0.0,
        1.0,
        predictive_sigma=_predictive_sigma(aigfs_data),
    ) if members_aigfs else None

    # Extract HRRR value
    q_hrrr = None
    intraday = w_data.get("intraday") or {}
    hrrr_high = intraday.get("hrrr_high", w_data.get("hrrr_high"))
    if hrrr_high is not None and mode == "HIGH":
        # HRRR is deterministic; model it as N(hrrr_high, 1.0^2)
        hrrr_sigma = 1.0
        # hrrr_q exceedance: P(X > strike) = 1 - Phi((strike - hrrr_high) / hrrr_sigma)
        z = (float(target_strike) - float(hrrr_high)) / hrrr_sigma
        z = max(-50.0, min(50.0, z))
        phi_z = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        q_hrrr = max(0.01, min(0.99, 1.0 - phi_z))

    # 5. Load governed RBI weights. Lead bucket is retained in evidence even
    # though the current champion is mode-level.
    lead_bucket = get_lead_bucket(hours_to_res)
    weights = calculate_brier_weights(mode, lead_bucket, db_path)

    # 6. Log-odds blend.
    if q_gfs is None and q_ecmwf is None and q_hrrr is None:
        raise ValueError(f"No contract-aligned physical model probability for {ticker}")
    q_hat_raw = log_odds_blend(q_gfs, q_ecmwf, q_hrrr, weights, hours_to_res)

    # 7. Safe calibration boundary (currently identity until governed).
    q_hat = calibrate_probability(q_hat_raw, mode, db_path)

    final_weights = calculate_blend_weights(q_gfs, q_ecmwf, q_hrrr, weights, hours_to_res)
    projection_values = {
        "gfs": (sum(members_gfs) / len(members_gfs)) if members_gfs else None,
        "ecmwf": (sum(members_ec) / len(members_ec)) if members_ec else None,
        "hrrr": float(hrrr_high) if hrrr_high is not None and mode == "HIGH" else None,
    }
    projection_weight = sum(
        float(final_weights[name])
        for name, value in projection_values.items()
        if value is not None
    )
    consensus_projection = (
        sum(
            float(final_weights[name]) * float(value)
            for name, value in projection_values.items()
            if value is not None
        )
        / projection_weight
        if projection_weight > 0.0
        else None
    )

    return {
        "q_gfs": q_gfs,
        "q_ecmwf": q_ecmwf,
        "q_aigfs": q_aigfs,
        "q_graphcast": q_aigfs,  # compatibility field for older evidence readers
        "q_hrrr": q_hrrr,
        "q_hat": q_hat,
        "q_hat_raw": q_hat_raw,
        "consensus_projection": consensus_projection,
        "projection_gfs": projection_values["gfs"],
        "projection_ecmwf": projection_values["ecmwf"],
        "projection_hrrr": projection_values["hrrr"],
        "lambda_scaler": lambda_scaler,
        "gfs_weight": final_weights["gfs"],
        "ecmwf_weight": final_weights["ecmwf"],
        "hrrr_weight": final_weights["hrrr"],
        "model_path": PRODUCTION_MODEL_PATH,
        "physics_method": PHYSICS_METHOD,
        "physics_validation_status": "learning_epoch_pending_outcomes",
        "provider_mode": provider_mode,
        "calibration_status": "identity_until_versioned_artifact",
        "physics_gfs": physics_gfs,
        "physics_ecmwf": physics_ecmwf,
        "physics_aigfs": physics_aigfs,
        "basis_quality": "CONFIRMED",
    }
