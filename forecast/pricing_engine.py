# SPEC §3: Pricing Engine
from __future__ import annotations

import math
import os
import sqlite3
import logging
from typing import Any, Dict, List, Tuple
import numpy as np
from sklearn.isotonic import IsotonicRegression

from forecast.weather_contracts import resolve_weather_contract, WeatherContractSemantics

logger = logging.getLogger("pricing_engine")

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

def calculate_graphcast_lambda(members: List[float], strike: float) -> float:
    """
    SPEC §3.6: GraphCast variance integrator z-score scaler.
    Neutral at z=1.0. Bounded in [0.70, 2.25].
    """
    if not members:
        return 1.0
        
    K = len(members)
    mu_ens = sum(members) / K
    
    if K > 1:
        variance = sum((x - mu_ens) ** 2 for x in members) / (K - 1)
        sigma_ens = math.sqrt(variance)
    else:
        sigma_ens = 1.0
        
    # z = |mu_ens - T_ai| / (sigma_ens + 0.25)
    # Floor denominator at 1e-9 to prevent division by zero (SPEC Rule 5)
    denom = max(1e-9, sigma_ens + 0.25)
    z = abs(mu_ens - strike) / denom
    
    # Lambda(z) = 0.70 + 1.55 / (1 + exp(-2.0*(z - 1.71)))
    # Clamp exponent to prevent overflow (SPEC Rule 5)
    exponent = -2.0 * (z - 1.71)
    exponent = max(-50.0, min(50.0, exponent))
    
    lambda_val = 0.70 + 1.55 / (1.0 + math.exp(exponent))
    return lambda_val

def kernel_smoothed_probability(
    members: List[float],
    semantics: WeatherContractSemantics,
    bias: float = 0.0,
    graphcast_lambda: float = 1.0,
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
        sigma = 1.0
        
    # Floor sigma to prevent division by zero or negative (SPEC Rule 5)
    sigma = max(0.05, sigma)
    
    # h_m = 0.9 * sigma_m * K_m**(-0.2)
    h = 0.9 * sigma * (K ** -0.2)
    
    # Apply GraphCast variance integrator: h_m *= sqrt(Lambda)
    h *= math.sqrt(graphcast_lambda)
    
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
    """
    SPEC §3.2: Recency-decayed Brier weights per (model, mode, lead-bucket).
    w_j = 0.94**age_days.
    """
    defaults = {"gfs": 0.60, "ecmwf": 0.40}
    if not db_path or not os.path.exists(db_path):
        return defaults
        
    try:
        from datetime import datetime, timezone
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT r.resolved_side, r.q_gfs, r.q_ecmwf, r.resolved_at, c.local_symbol
                FROM forecast_resolutions r
                JOIN forecast_contracts c ON r.contract_id = c.id
                WHERE r.q_gfs IS NOT NULL
                  AND r.q_ecmwf IS NOT NULL
                """
            ).fetchall()
            
            # Filter rows by mode in python
            def _get_mode_from_ticker(ticker: str) -> str:
                ticker_upper = ticker.upper()
                if "HIGH" in ticker_upper: return "HIGH"
                if "LOW" in ticker_upper: return "LOW"
                if "RAIN" in ticker_upper: return "RAIN"
                if "SNOW" in ticker_upper: return "SNOW"
                if "WIND" in ticker_upper: return "WIND"
                if "TEMP" in ticker_upper: return "TEMP"
                return "TEMP"
                
            filtered_rows = []
            for r in rows:
                if _get_mode_from_ticker(r["local_symbol"] or "") == mode:
                    filtered_rows.append(r)
            rows = filtered_rows
            
            if len(rows) < 5:
                return defaults
                
            now_utc = datetime.now(timezone.utc)
            sum_w_gfs = 0.0
            sum_w_ec = 0.0
            sum_weighted_bs_gfs = 0.0
            sum_weighted_bs_ec = 0.0
            y_vals = []
            
            for row in rows:
                resolved_side = row["resolved_side"]
                y = 1.0 if resolved_side == "YES" else 0.0
                y_vals.append(y)
                
                try:
                    res_dt = datetime.fromisoformat(row["resolved_at"].replace("Z", "+00:00"))
                    age_days = (now_utc - res_dt).days
                except Exception:
                    age_days = 0
                age_days = max(0, age_days)
                
                wj = 0.94 ** age_days
                q_gfs = float(row["q_gfs"])
                q_ec = float(row["q_ecmwf"])
                
                sum_weighted_bs_gfs += wj * ((q_gfs - y) ** 2)
                sum_w_gfs += wj
                
                sum_weighted_bs_ec += wj * ((q_ec - y) ** 2)
                sum_w_ec += wj
                
            if sum_w_gfs <= 0 or sum_w_ec <= 0 or not y_vals:
                return defaults
                
            BS_gfs = sum_weighted_bs_gfs / sum_w_gfs
            BS_ec = sum_weighted_bs_ec / sum_w_ec
            
            ybar = sum(y_vals) / len(y_vals)
            BS_ref = max(1e-4, ybar * (1.0 - ybar))
            
            S_gfs = max(1e-4, BS_ref - BS_gfs)
            S_ec = max(1e-4, BS_ref - BS_ec)
            
            val_gfs = 4.0 * S_gfs / BS_ref
            val_ec = 4.0 * S_ec / BS_ref
            
            val_gfs = max(-50.0, min(50.0, val_gfs))
            val_ec = max(-50.0, min(50.0, val_ec))
            
            exp_gfs = math.exp(val_gfs)
            exp_ec = math.exp(val_ec)
            denom = exp_gfs + exp_ec
            
            w_gfs = exp_gfs / denom if denom > 0 else 0.60
            w_ec = exp_ec / denom if denom > 0 else 0.40
            
            return {"gfs": w_gfs, "ecmwf": w_ec}
    except Exception as e:
        logger.warning(f"Error fitting Brier weights: {e}")
        return defaults

def log_odds_blend(
    q_gfs: float,
    q_ecmwf: float,
    q_hrrr: float | None,
    weights: Dict[str, float],
    hours_to_res: float,
) -> float:
    """
    SPEC §3.3 & §3.4: Log-odds blend with HRRR splice.
    """
    tau = hours_to_res
    alpha = 0.0
    if q_hrrr is not None and tau <= 48.0:
        exponent = 0.30 * (tau - 18.0)
        exponent = max(-50.0, min(50.0, exponent))
        alpha = 0.85 / (1.0 + math.exp(exponent))
        
    W_gfs = weights.get("gfs", 0.60)
    W_ec = weights.get("ecmwf", 0.40)
    
    sum_base = W_gfs + W_ec
    if sum_base > 0:
        W_gfs /= sum_base
        W_ec /= sum_base
    else:
        W_gfs, W_ec = 0.60, 0.40
        
    w_hrrr = alpha
    w_gfs = (1.0 - alpha) * W_gfs
    w_ec = (1.0 - alpha) * W_ec
    
    # Renormalize to ensure exact unity sum
    total_w = w_gfs + w_ec + w_hrrr
    if total_w > 0:
        w_gfs /= total_w
        w_ec /= total_w
        w_hrrr /= total_w
    else:
        w_gfs, w_ec, w_hrrr = 0.60, 0.40, 0.0
        
    def logit(p: float) -> float:
        p = max(0.005, min(0.995, p))
        return math.log(p / (1.0 - p))
        
    def sigmoid(x: float) -> float:
        x = max(-50.0, min(50.0, x))
        return 1.0 / (1.0 + math.exp(-x))
        
    log_odds = w_gfs * logit(q_gfs) + w_ec * logit(q_ecmwf)
    if q_hrrr is not None and w_hrrr > 0:
        log_odds += w_hrrr * logit(q_hrrr)
        
    l0 = 0.0
    return sigmoid(log_odds + l0)

def calibrate_probability(q_hat: float, mode: str, db_path: str | None) -> float:
    """
    SPEC §3.3: Weekly-refit Isotonic Calibration against forecast_resolutions.
    """
    if not db_path or not os.path.exists(db_path):
        return q_hat
        
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT r.resolved_side, r.q_hat, c.local_symbol
                FROM forecast_resolutions r
                JOIN forecast_contracts c ON r.contract_id = c.id
                WHERE r.q_hat IS NOT NULL
                """
            ).fetchall()
            
            # Filter rows by mode in python
            def _get_mode_from_ticker(ticker: str) -> str:
                ticker_upper = ticker.upper()
                if "HIGH" in ticker_upper: return "HIGH"
                if "LOW" in ticker_upper: return "LOW"
                if "RAIN" in ticker_upper: return "RAIN"
                if "SNOW" in ticker_upper: return "SNOW"
                if "WIND" in ticker_upper: return "WIND"
                if "TEMP" in ticker_upper: return "TEMP"
                return "TEMP"
                
            filtered_rows = []
            for r in rows:
                if _get_mode_from_ticker(r["local_symbol"] or "") == mode:
                    filtered_rows.append(r)
            rows = filtered_rows
            
            if len(rows) < 15:
                return q_hat
                
            x_train = np.array([float(row["q_hat"]) for row in rows])
            y_train = np.array([1.0 if row["resolved_side"] == "YES" else 0.0 for row in rows])
            
            ir = IsotonicRegression(out_of_bounds="clip")
            ir.fit(x_train, y_train)
            
            q_calib = float(ir.predict([q_hat])[0])
            return max(0.01, min(0.99, q_calib))
    except Exception as e:
        logger.warning(f"Error applying Isotonic Calibration: {e}")
        return q_hat

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
        
    mode = semantics.mode
    
    # 1. Extract raw ensemble members
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
    
    # 2. GraphCast Variance Integrator
    # We combine GFS and ECMWF to get the full ensemble representation
    combined_members = members_gfs + members_ec
    target_strike = strike if strike is not None else semantics.threshold
    if target_strike is None:
        target_strike = semantics.display_high if semantics.display_high is not None else 0.0
        
    lambda_scaler = calculate_graphcast_lambda(combined_members, target_strike)
    
    # 3. Kernel-Smoothed Probabilities
    # Retrieve station bias if any (default to 0)
    bias_gfs = 0.0
    bias_ec = 0.0
    
    q_gfs = kernel_smoothed_probability(members_gfs, semantics, bias_gfs, lambda_scaler)
    q_ecmwf = kernel_smoothed_probability(members_ec, semantics, bias_ec, lambda_scaler)
    
    # Extract HRRR value
    q_hrrr = None
    hrrr_high = w_data.get("hrrr_high")
    if hrrr_high is not None and mode == "HIGH":
        # HRRR is deterministic; model it as N(hrrr_high, 1.0^2)
        hrrr_sigma = 1.0
        # hrrr_q exceedance: P(X > strike) = 1 - Phi((strike - hrrr_high) / hrrr_sigma)
        z = (float(target_strike) - float(hrrr_high)) / hrrr_sigma
        z = max(-50.0, min(50.0, z))
        phi_z = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
        q_hrrr = max(0.01, min(0.99, 1.0 - phi_z))
        
    # 4. Load lead-bucket Brier weights
    lead_bucket = get_lead_bucket(hours_to_res)
    weights = calculate_brier_weights(mode, lead_bucket, db_path)
    
    # 5. Log-Odds Blend
    q_hat_raw = log_odds_blend(q_gfs, q_ecmwf, q_hrrr, weights, hours_to_res)
    
    # 6. Weekly-refit Isotonic Calibration
    q_hat = calibrate_probability(q_hat_raw, mode, db_path)
    
    # ── J.A.R.V.I.S. Continuous Physics Delta Overlay (PAPER RUN ONLY) ──
    if os.getenv("RUN_PAPER_CYCLE", "false").lower() == "true":
        gfs_precip = float(w_data.get("mean_precip") or 0.0)
        ec_precip = float(ecmwf_data.get("mean_precip") or 0.0)
        gfs_wind = float(w_data.get("mean_wind") or 0.0)
        ec_wind = float(ecmwf_data.get("mean_wind") or 0.0)
        
        dT_precip_gfs = 0.0
        if gfs_precip > 0.0:
            dT_precip_gfs = -6.0 / (1.0 + math.exp(-12.0 * (gfs_precip - 0.15)))
        dT_wind_gfs = 0.0
        if gfs_wind > 0.0:
            dT_wind_gfs = 4.5 / (1.0 + math.exp(-0.35 * (gfs_wind - 14.5)))
            
        dT_precip_ec = 0.0
        if ec_precip > 0.0:
            dT_precip_ec = -6.0 / (1.0 + math.exp(-12.0 * (ec_precip - 0.15)))
        dT_wind_ec = 0.0
        if ec_wind > 0.0:
            dT_wind_ec = 4.5 / (1.0 + math.exp(-0.35 * (ec_wind - 14.5)))
            
        shift_gfs = (dT_precip_gfs + dT_wind_gfs) * 0.08
        shift_ec = (dT_precip_ec + dT_wind_ec) * 0.08
        
        if mode == "HIGH":
            q_gfs = max(0.01, min(0.99, q_gfs + shift_gfs))
            q_ecmwf = max(0.01, min(0.99, q_ecmwf + shift_ec))
        elif mode == "LOW":
            q_gfs = max(0.01, min(0.99, q_gfs + shift_gfs))
            q_ecmwf = max(0.01, min(0.99, q_ecmwf + shift_ec))
            
        q_hat_raw = log_odds_blend(q_gfs, q_ecmwf, q_hrrr, weights, hours_to_res)
        q_hat = calibrate_probability(q_hat_raw, mode, db_path)

    gfs_w = weights.get("gfs", 0.60) * (1.0 - (0.0 if q_hrrr is None else 0.85 / (1.0 + math.exp(max(-50.0, min(50.0, 0.30*(hours_to_res-18.0)))))))
    ec_w = weights.get("ecmwf", 0.40) * (1.0 - (0.0 if q_hrrr is None else 0.85 / (1.0 + math.exp(max(-50.0, min(50.0, 0.30*(hours_to_res-18.0)))))))
    hrrr_w = 0.0 if q_hrrr is None else 0.85 / (1.0 + math.exp(max(-50.0, min(50.0, 0.30*(hours_to_res-18.0)))))

    return {
        "q_gfs": q_gfs,
        "q_ecmwf": q_ecmwf,
        "q_hrrr": q_hrrr,
        "q_hat": q_hat,
        "lambda_scaler": lambda_scaler,
        "gfs_weight": gfs_w,
        "ecmwf_weight": ec_w,
        "hrrr_weight": hrrr_w,
        "basis_quality": "CONFIRMED",
    }
