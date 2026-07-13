# SPEC §4: Covariance Engine
from __future__ import annotations

import os
from datetime import datetime
import math
import sqlite3
import logging
from typing import Any, Dict, List, Tuple
import numpy as np
import pandas as pd
from scipy.stats import norm, multivariate_normal

from forecast.weather_contracts import resolve_weather_contract, WeatherContractSemantics
from data.kalshi_weather_monitor import STATIONS

logger = logging.getLogger("covariance_engine")

def get_station_correlation_matrix(db_path: str | None, stations: List[str]) -> Tuple[Dict[Tuple[str, str], float], bool]:
    """
    SPEC §4.4: Calculate EWMA correlation matrix R with lambda = 0.97
    over at least 90 days of NOAA daily summaries.
    Returns (correlation_dict, is_authoritative).
    """
    defaults = {}
    for s1 in stations:
        for s2 in stations:
            defaults[(s1, s2)] = 1.0 if s1 == s2 else 0.30
            
    if not db_path or not os.path.exists(db_path):
        return defaults, False
        
    try:
        with sqlite3.connect(db_path) as conn:
            df = pd.read_sql_query(
                """
                SELECT station, date, temp_max
                FROM noaa_daily_summaries
                WHERE temp_max IS NOT NULL
                ORDER BY date ASC
                """,
                conn
            )
            
        if df.empty:
            return defaults, False
            
        df_pivot = df.pivot(index="date", columns="station", values="temp_max")
        if len(df_pivot) < 60:
            return defaults, False
            
        df_pivot = df_pivot.ffill().bfill()
        
        # Check if we have at least 60 days of data for each registered station
        is_authoritative = True
        for s in stations:
            if s not in df_pivot.columns or df_pivot[s].count() < 60:
                is_authoritative = False
                
        # EWMA lambda = 0.97 -> alpha = 1 - lambda = 0.03
        df_ewm = df_pivot.ewm(alpha=0.03, adjust=False).mean()
        corr_matrix = df_ewm.corr()
        
        R = {}
        for s1 in stations:
            for s2 in stations:
                if s1 in corr_matrix.columns and s2 in corr_matrix.columns:
                    val = corr_matrix.loc[s1, s2]
                    R[(s1, s2)] = float(val) if not np.isnan(val) else (1.0 if s1 == s2 else 0.30)
                else:
                    R[(s1, s2)] = 1.0 if s1 == s2 else 0.30
                    
        return R, is_authoritative
    except Exception as e:
        logger.warning(f"Error calculating station correlation: {e}")
        return defaults, False

def get_directional_loading(semantics: WeatherContractSemantics, w_data: dict) -> float:
    """
    SPEC §4.2: Directional loadings d_i.
    High/Low >= theta: +1; < theta: -1.
    between-brackets: sgn(mu_post - bracket_mid) with magnitude shrinking to 0 as bracket straddles mode.
    """
    if semantics.mode in ["RAIN", "SNOW", "WIND"]:
        return 1.0
        
    comp = semantics.comparator
    if comp == "between":
        if semantics.lower_bound is None or semantics.upper_bound is None:
            return 1.0
            
        mode = semantics.mode
        if mode == "TEMP":
            key = "members_temp"
        elif mode == "LOW":
            key = "members_low"
        else:
            key = "members_high"
            
        members_gfs = [float(v) for v in (w_data.get(key) or [])]
        ecmwf_data = w_data.get("ecmwf") or {}
        members_ec = [float(v) for v in (ecmwf_data.get(key) or [])]
        combined = members_gfs + members_ec
        
        if not combined:
            return 1.0
            
        mu_post = sum(combined) / len(combined)
        if len(combined) > 1:
            sigma_post = math.sqrt(sum((x - mu_post) ** 2 for x in combined) / (len(combined) - 1))
        else:
            sigma_post = 1.0
            
        sigma_post = max(0.05, sigma_post)
        bracket_mid = (float(semantics.lower_bound) + float(semantics.upper_bound)) / 2.0
        diff = mu_post - bracket_mid
        sgn = 1.0 if diff >= 0 else -1.0
        
        # d_i = sgn * (1 - exp(-|diff|/sigma_post))
        return sgn * (1.0 - math.exp(-abs(diff) / sigma_post))
        
    elif comp == "lt":
        return -1.0
    else:
        return 1.0

def are_disjoint_brackets(sem1: WeatherContractSemantics, sem2: WeatherContractSemantics) -> bool:
    """Check if two contracts represent disjoint brackets on the same station and mode."""
    if sem1.mode != sem2.mode:
        return False
        
    # Same comparator lt vs gt is disjoint if they are at the same strike
    limit1 = sem1.threshold if sem1.threshold is not None else sem1.display_high
    limit2 = sem2.threshold if sem2.threshold is not None else sem2.display_high
    
    if sem1.comparator == "between" and sem2.comparator == "between":
        if sem1.lower_bound is not None and sem1.upper_bound is not None:
            if sem2.lower_bound is not None and sem2.upper_bound is not None:
                return (float(sem1.upper_bound) <= float(sem2.lower_bound) or
                        float(sem2.upper_bound) <= float(sem1.lower_bound))
                        
    if sem1.comparator == "lt" and sem2.comparator == "gt" and limit1 is not None and limit2 is not None:
        if float(limit1) <= float(limit2):
            return True
            
    if sem1.comparator == "gt" and sem2.comparator == "lt" and limit1 is not None and limit2 is not None:
        if float(limit2) <= float(limit1):
            return True
            
    return False

def _parse_time_hours(dt_str: str) -> float:
    """Parse ISO datetime string to float hours since epoch."""
    if not dt_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.timestamp() / 3600.0
    except Exception:
        return 0.0

def get_station_code_for_ticker(ticker: str, contract_name: str = "") -> str:
    from data.kalshi_weather_monitor import STATIONS, resolve_weather_city_key
    city_key = resolve_weather_city_key(ticker, contract_name=contract_name)
    if city_key and city_key in STATIONS:
        return str(STATIONS[city_key].get("icao") or "")
    # Robust fallback parsing of prefix
    t = str(ticker).upper()
    for city_key, info in STATIONS.items():
        for ser in info.get("series", []):
            if t.startswith(ser.upper()):
                return str(info.get("icao") or "")
    return "KNYC"


def assemble_covariance_matrix(
    contracts: List[Dict[str, Any]],
    pricing_dict: Dict[str, Dict[str, Any]],
    w_data_dict: Dict[str, dict],
    R: Dict[Tuple[str, str], float],
    is_authoritative: bool,
) -> np.ndarray:
    """
    SPEC §4.3: Assemble regularized, PSD covariance matrix Sigma using Gaussian copula.
    """
    N = len(contracts)
    Sigma = np.zeros((N, N))
    
    if N == 0:
        return Sigma
        
    resolved_semantics = []
    for c in contracts:
        ticker = c.get("local_symbol", "")
        sem = resolve_weather_contract(
            ticker=ticker,
            contract_name=c.get("contract_name", ""),
            strike=c.get("strike")
        )
        resolved_semantics.append((ticker, sem))
        
    for i in range(N):
        t_i, sem_i = resolved_semantics[i]
        q_i = pricing_dict.get(t_i, {}).get("q_hat", 0.5)
        # Floor denominator & clamp probability input (SPEC Rule 5)
        qi_clamped = max(0.001, min(0.999, q_i))
        
        for j in range(i, N):
            t_j, sem_j = resolved_semantics[j]
            q_j = pricing_dict.get(t_j, {}).get("q_hat", 0.5)
            qj_clamped = max(0.001, min(0.999, q_j))
            
            if i == j:
                Sigma[i, i] = qi_clamped * (1.0 - qi_clamped)
            else:
                if sem_i is None or sem_j is None:
                    # Fallback for unresolvable contract
                    Sigma[i, j] = 0.0
                    Sigma[j, i] = 0.0
                    continue
                    
                # Disjoint same-station check
                s_i = get_station_code_for_ticker(t_i, sem_i.contract_name)
                s_j = get_station_code_for_ticker(t_j, sem_j.contract_name)
                if s_i == s_j and sem_i.mode == sem_j.mode and are_disjoint_brackets(sem_i, sem_j):
                    cov_val = -qi_clamped * qj_clamped
                else:
                    # Gaussian Copula
                    d_i = get_directional_loading(sem_i, w_data_dict.get(t_i, {}))
                    d_j = get_directional_loading(sem_j, w_data_dict.get(t_j, {}))
                    
                    r_val = R.get((s_i, s_j), 1.0 if s_i == s_j else 0.30)
                    
                    # chi factor
                    mode_i = sem_i.mode
                    mode_j = sem_j.mode
                    
                    if mode_i == mode_j:
                        chi = 1.0
                    elif s_i == s_j and {mode_i, mode_j} == {"TEMP", "RAIN"}:
                        # check month
                        res_at = contracts[i].get("resolution_at") or contracts[i].get("last_trade_at", "")
                        month = 4
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(res_at.replace("Z", "+00:00"))
                            month = dt.month
                        except Exception:
                            pass
                        chi = -0.35 if (4 <= month <= 9) else -0.10
                    else:
                        chi = 0.0
                        
                    # Correlation rho_ij
                    rho = d_i * d_j * r_val * chi
                    
                    # Hourly decorrelation
                    h_i = _parse_time_hours(contracts[i].get("resolution_at") or contracts[i].get("last_trade_at", ""))
                    h_j = _parse_time_hours(contracts[j].get("resolution_at") or contracts[j].get("last_trade_at", ""))
                    
                    from forecast.weather_contracts import is_hourly_weather_contract
                    is_h_i = is_hourly_weather_contract(t_i, contract_name=contracts[i].get("contract_name", ""))
                    is_h_j = is_hourly_weather_contract(t_j, contract_name=contracts[j].get("contract_name", ""))
                    
                    if is_h_i != is_h_j:
                        # one hourly, one daily
                        decorr = math.exp(-abs(h_i - h_j) / 12.0)
                        rho *= decorr
                        
                    # Clamp correlation to prevent singular matrix (SRE Pillar 1)
                    rho = max(-0.999, min(0.999, rho))
                    
                    # Bivariate normal CDF
                    try:
                        x = norm.ppf(qi_clamped)
                        y = norm.ppf(qj_clamped)
                        phi2 = multivariate_normal.cdf(
                            [x, y], 
                            mean=[0.0, 0.0], 
                            cov=[[1.0, rho], [rho, 1.0]]
                        )
                        cov_val = phi2 - qi_clamped * qj_clamped
                    except Exception:
                        cov_val = 0.0
                        
                Sigma[i, j] = cov_val
                Sigma[j, i] = cov_val
                
    # Regularize: Sigma <- 0.9 * Sigma + 0.1 * diag(Sigma)
    if N > 0:
        diag_part = np.diag(np.diag(Sigma))
        Sigma = 0.9 * Sigma + 0.1 * diag_part
        
        # Eigenvalue floor 1e-6 to ensure PSD (SPEC §4.5)
        try:
            eigvals, eigvecs = np.linalg.eigh(Sigma)
            eigvals_floored = np.clip(eigvals, 1e-6, None)
            Sigma = eigvecs @ np.diag(eigvals_floored) @ eigvecs.T
        except Exception:
            pass
            
    return Sigma

def calculate_portfolio_variance(w: np.ndarray, Sigma: np.ndarray) -> float:
    """Calculate portfolio variance w^T Sigma w."""
    if len(w) == 0:
        return 0.0
    return float(w.T @ Sigma @ w)

def calculate_shrinkage_limit(
    w: np.ndarray,
    Sigma: np.ndarray,
    candidate_idx: int,
    candidate_side_sign: float, # +1.0 for YES, -1.0 for NO
    bankroll: float,
) -> float:
    """
    SPEC §4.6: Return maximum allowed quantity K >= 0 for the candidate contract
    to satisfy variance budget: sigma_p^2(w + s * K * e_c) <= (0.08 * B)^2.
    """
    N = len(w)
    if N == 0:
        return 0.0
        
    limit_var = (0.08 * bankroll) ** 2
    
    # Current portfolio variance
    var_current = calculate_portfolio_variance(w, Sigma)
    if var_current > limit_var:
        return 0.0 # Already over budget
        
    s = candidate_side_sign
    cov_c = 0.0
    for i in range(N):
        cov_c += w[i] * Sigma[i, candidate_idx]
        
    var_c = Sigma[candidate_idx, candidate_idx]
    
    # Quadratic equation coefficients: A * K^2 + B * K + C = 0
    # A = var_c
    # B = 2 * s * cov_c
    # C = var_current - limit_var
    A = max(1e-9, var_c) # floor denominator (SPEC Rule 5)
    B = 2.0 * s * cov_c
    C = var_current - limit_var
    
    discriminant = B ** 2 - 4.0 * A * C
    if discriminant < 0:
        return 0.0
        
    # Since C <= 0, sqrt(B^2 - 4AC) >= |B|
    # So the positive root is (-B + sqrt(B^2 - 4AC)) / (2A)
    K = (-B + math.sqrt(discriminant)) / (2.0 * A)
    return max(0.0, K)

def calculate_marginal_risk_charge(
    w: np.ndarray,
    Sigma: np.ndarray,
    candidate_idx: int,
    candidate_side_sign: float,
    bankroll: float,
) -> float:
    """
    SPEC §4.6: Calculate Marginal Risk Charge MRC for the candidate contract.
    Returns scaling factor: 1 / (1 + MRC_c * w_c / (0.08 * B)^2).
    """
    N = len(w)
    if N == 0:
        return 1.0
        
    cov_c = 0.0
    for i in range(N):
        cov_c += w[i] * Sigma[i, candidate_idx]
        
    # MRC_c = 2 * Cov(w, c)
    mrc = 2.0 * cov_c
    
    # Candidate signed weight w_c = side_sign
    w_c = candidate_side_sign
    
    limit_var = (0.08 * bankroll) ** 2
    limit_var = max(1e-9, limit_var) # floor denominator
    
    denom = 1.0 + (mrc * w_c) / limit_var
    # Floor denominator to prevent divide-by-zero or negative multiplier (SRE Pillar 1)
    denom = max(0.1, denom)
    
    return 1.0 / denom


def check_and_shrink_candidate(
    candidate_contract: Dict[str, Any],
    candidate_side: str,
    candidate_price: float,
    candidate_qty: int,
    open_positions: List[Dict[str, Any]],
    bankroll: float,
    db_path: str | None = None,
) -> Tuple[int, float, Dict[str, Any]]:
    """
    SPEC §4.6: Run variance budget check, candidate admission, shrinkage,
    absolute backstop, and marginal risk charge.
    Returns (shrunk_qty, marginal_risk_charge_factor, debug_info).
    """
    # 0. Quick checks
    if candidate_qty <= 0:
        return 0, 1.0, {"reason": "zero_qty", "qty": 0}

    # Import locally to prevent circular imports
    from forecast.db import get_contract_metadata
    from data.kalshi_weather_monitor import get_weather_data
    from forecast.pricing_engine import calculate_pricing
    from datetime import timezone

    # 1. Enrich open positions with metadata
    open_contracts = []
    for pos in open_positions:
        ticker = pos.get("local_symbol", "")
        meta = get_contract_metadata(ticker, db_path)
        enriched = {
            "local_symbol": ticker,
            "contract_name": pos.get("contract_name") or (meta.get("contract_name") if meta else ""),
            "strike": pos.get("strike") if pos.get("strike") is not None else (meta.get("strike") if meta else None),
            "resolution_at": pos.get("resolution_at") or (meta.get("resolution_at") if meta else ""),
            "last_trade_at": pos.get("last_trade_at") or (meta.get("last_trade_at") if meta else ""),
            "entry_price": float(pos.get("entry_price") or pos.get("entry") or 0.50),
            "qty": float(pos.get("qty") or 0),
            "side": pos.get("side", "YES").upper()
        }
        open_contracts.append(enriched)

    # 2. Get correlation matrix R
    station_codes = [loc["icao"] for loc in STATIONS.values()]
    R, is_authoritative = get_station_correlation_matrix(db_path, station_codes)

    # 3. Pricing and weather dict
    contracts_all = open_contracts + [candidate_contract]
    pricing_dict = {}
    w_data_dict = {}
    now_utc_ts = datetime.now(timezone.utc).timestamp()

    for c in contracts_all:
        ticker = c.get("local_symbol", "")
        w_data = get_weather_data(ticker)
        if w_data:
            w_data_dict[ticker] = w_data
            try:
                hours_to_res = _hours_to_resolution(c.get("last_trade_at", ""))
                pricing = calculate_pricing(
                    ticker,
                    w_data,
                    hours_to_res=hours_to_res,
                    contract_name=c.get("contract_name", ""),
                    strike=c.get("strike"),
                    db_path=db_path,
                )
            except Exception:
                pricing = {"q_hat": 0.5, "lambda_scaler": 1.0, "gfs_weight": 0.6, "ecmwf_weight": 0.4, "hrrr_weight": 0.0}
        else:
            pricing = {"q_hat": 0.5, "lambda_scaler": 1.0, "gfs_weight": 0.6, "ecmwf_weight": 0.4, "hrrr_weight": 0.0}
        pricing_dict[ticker] = pricing

    # 4. Assemble full covariance matrix
    Sigma_full = assemble_covariance_matrix(
        contracts_all,
        pricing_dict,
        w_data_dict,
        R,
        is_authoritative
    )

    # 5. Extract components
    N_open = len(open_contracts)
    w_open = np.array([float(pos["qty"]) * (1.0 if pos["side"] == "YES" else -1.0) for pos in open_contracts])
    candidate_idx = N_open
    candidate_side_sign = 1.0 if candidate_side == "YES" else -1.0

    # 6. Current portfolio check
    var_limit = (0.08 * bankroll) ** 2
    if N_open > 0:
        Sigma_open = Sigma_full[:N_open, :N_open]
        var_current = calculate_portfolio_variance(w_open, Sigma_open)
        if var_current > var_limit:
            return 0, 1.0, {
                "reason": "portfolio_already_over_budget",
                "var_current": var_current,
                "limit": var_limit,
                "qty": 0
            }
    else:
        var_current = 0.0

    # 7. Sizing & Shrinkage via positive quadratic root
    max_qty_by_variance = candidate_qty
    if N_open > 0:
        limit_K = calculate_shrinkage_limit(
            w_open,
            Sigma_full,
            candidate_idx,
            candidate_side_sign,
            bankroll
        )
        max_qty_by_variance = min(candidate_qty, int(math.floor(limit_K)))

    # 8. Absolute backstop limit
    # sum(|w_i| * fill_price_i) <= 0.90 * B
    current_cost = sum(abs(w_open[i]) * open_contracts[i]["entry_price"] for i in range(N_open))
    max_cost_allowed = max(0.0, 0.90 * bankroll - current_cost)
    max_qty_by_backstop = int(math.floor(max_cost_allowed / max(1e-9, candidate_price)))

    allowed_qty = min(max_qty_by_variance, max_qty_by_backstop)
    if allowed_qty <= 0:
        return 0, 1.0, {
            "reason": "shrunk_to_zero",
            "max_by_variance": max_qty_by_variance,
            "max_by_backstop": max_qty_by_backstop,
            "qty": 0
        }

    # 9. Marginal Risk Charge
    charge_factor = calculate_marginal_risk_charge(
        w_open,
        Sigma_full,
        candidate_idx,
        candidate_side_sign,
        bankroll
    )
    final_qty = int(math.floor(allowed_qty * charge_factor))

    return final_qty, charge_factor, {
        "reason": "approved",
        "qty": final_qty,
        "charge_factor": charge_factor,
        "max_by_variance": max_qty_by_variance,
        "max_by_backstop": max_qty_by_backstop,
        "var_current": var_current,
        "limit": var_limit,
    }

