"""
ml/walk_forward_trainer.py — Walk-forward training for XGBoost + LightGBM ensemble.

Walk-forward protocol:
  - 60-day train window / 10-day validate / 7-day step (always forward-moving)
  - Minimum 30 days of live data before ML activates
  - Separate models per pair direction: BTC_LONG, BTC_SHORT, ETH_LONG, ETH_SHORT,
    SOL_LONG, SOL_SHORT, GENERIC_LONG, GENERIC_SHORT
  - XGBoost (60%) + LightGBM (40%) ensemble
  - Optuna hyperparameter optimization targeting Sharpe ratio

Pass criteria per fold:
  WR >= 54%, PF >= 1.35, Sharpe >= 0.8, DD <= 18%, trades >= 30
  75% of folds must pass

Model storage: ml/models/{pair}_{direction}.xgb.pkl, {pair}_{direction}.lgbm.pkl
"""

import logging
import os
import pickle
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODELS_DIR = os.path.join(os.path.dirname(__file__), 'models')
os.makedirs(MODELS_DIR, exist_ok=True)

# Pair routing
_PAIR_MAP = {
    'BTCUSDT': 'BTC',
    'ETHUSDT': 'ETH',
    'SOLUSDT': 'SOL',
}

_TRAIN_DAYS  = 60
_VALID_DAYS  = 10
_STEP_DAYS   = 7
_MIN_TRADES  = 30
_MIN_LIVE_DAYS = 30

# Pass criteria
_MIN_WR      = 0.54
_MIN_PF      = 1.35
_MIN_SHARPE  = 0.8
_MAX_DD      = 0.18
_PASS_PCT    = 0.75   # 75% of folds must pass

try:
    import xgboost as xgb
    _XGB_OK = True
except Exception:
    _XGB_OK = False
    logger.warning('[wft] xgboost not available (install libomp via brew)')

try:
    import lightgbm as lgb
    _LGB_OK = True
except ImportError:
    _LGB_OK = False
    logger.warning('[wft] lightgbm not available')

try:
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    _OPTUNA_OK = True
except ImportError:
    _OPTUNA_OK = False


def _get_pair_key(symbol: str) -> str:
    return _PAIR_MAP.get(symbol.upper(), 'GENERIC')


def _model_path(pair_key: str, direction: str, model_type: str) -> str:
    return os.path.join(MODELS_DIR, f'{pair_key}_{direction}_{model_type}.pkl')


def _load_training_data(pair_key: str, direction: str,
                         paper: bool = True) -> Optional[pd.DataFrame]:
    """
    Load feature + label data from trade_attribution + features stored in DB.
    Returns DataFrame with FEATURE_NAMES columns + 'won' label column.
    Filters to live trades only (not backtest seeds).
    """
    try:
        from logging_db.trade_logger import get_logger
        from ml.feature_builder import FEATURE_NAMES
        db = get_logger()

        # Load trades with stored features.
        # Include both paper=1 (paper_v10) and paper=0 (live_v10) trades tagged with
        # clean v10 sources. Exclude backtest seeds and pre-v10 contaminated rows.
        # NOTE: paper=0 filter was wrong — v10 paper trades use paper=1 and source='paper_v10'.
        #       Using paper=0 alone would return zero rows until live trading begins.
        rows = db.conn.execute("""
            SELECT t.ts, t.won, t.pnl_usd, t.symbol,
                   ta.technical_score, ta.ml_score, ta.composite_score, ta.regime
            FROM trades t
            LEFT JOIN trade_attribution ta ON t.id = ta.trade_id
            WHERE t.action='SELL'
              AND t.source NOT IN ('backtest', 'pre_v10_contaminated', 'bybit_paper')
              AND t.won IS NOT NULL
            ORDER BY t.ts ASC
        """).fetchall()

        if not rows:
            return None

        df = pd.DataFrame(rows, columns=['ts', 'won', 'pnl_usd', 'symbol',
                                           'technical_score', 'ml_score',
                                           'composite_score', 'regime'])

        # Filter by pair direction
        if pair_key != 'GENERIC':
            pair_syms = [s for s, k in _PAIR_MAP.items() if k == pair_key]
            df = df[df['symbol'].isin(pair_syms)]

        if len(df) < _MIN_TRADES:
            return None

        return df

    except Exception as e:
        logger.debug(f'[wft] data load error: {e}')
        return None


def _compute_metrics(y_true: np.ndarray, y_pred: np.ndarray,
                      pnls: np.ndarray) -> Dict:
    """Compute WR, PF, Sharpe, max DD from predictions."""
    # Binary predictions at 0.5 threshold
    y_bin = (y_pred >= 0.5).astype(int)

    # Only evaluate trades the model would have taken
    traded_mask = y_pred >= 0.5

    if traded_mask.sum() < 5:
        return {'wr': 0, 'pf': 0, 'sharpe': 0, 'max_dd': 1.0, 'n_trades': 0}

    traded_pnls = pnls[traded_mask]
    traded_won  = y_true[traded_mask]

    wr = float(traded_won.mean())

    wins  = traded_pnls[traded_pnls > 0]
    losses = traded_pnls[traded_pnls <= 0]
    pf = (wins.sum() / (abs(losses.sum()) + 1e-9)) if len(losses) > 0 else float('inf')

    # Daily Sharpe proxy (use trade-level returns)
    if len(traded_pnls) > 1:
        sharpe = float(np.mean(traded_pnls) / (np.std(traded_pnls) + 1e-9) * np.sqrt(252))
    else:
        sharpe = 0.0

    # Max drawdown
    cum_pnl = np.cumsum(traded_pnls)
    running_max = np.maximum.accumulate(cum_pnl)
    dd_series = (running_max - cum_pnl) / (running_max + 1e-9)
    max_dd = float(dd_series.max()) if len(dd_series) > 0 else 0.0

    return {
        'wr': round(wr, 4),
        'pf': round(float(pf), 4),
        'sharpe': round(sharpe, 4),
        'max_dd': round(max_dd, 4),
        'n_trades': int(traded_mask.sum()),
    }


def _fold_passes(metrics: Dict) -> bool:
    return (
        metrics['wr'] >= _MIN_WR and
        metrics['pf'] >= _MIN_PF and
        metrics['sharpe'] >= _MIN_SHARPE and
        metrics['max_dd'] <= _MAX_DD and
        metrics['n_trades'] >= _MIN_TRADES
    )


def _train_xgb(X_train: np.ndarray, y_train: np.ndarray,
                params: Optional[Dict] = None):
    """Train XGBoost binary classifier."""
    if not _XGB_OK:
        return None

    default_params = {
        'n_estimators': 200,
        'max_depth': 4,
        'learning_rate': 0.05,
        'subsample': 0.8,
        'colsample_bytree': 0.8,
        'use_label_encoder': False,
        'eval_metric': 'logloss',
        'random_state': 42,
        'n_jobs': -1,
    }
    if params:
        default_params.update(params)

    model = xgb.XGBClassifier(**default_params)
    model.fit(X_train, y_train, verbose=False)
    return model


def _train_lgbm(X_train: np.ndarray, y_train: np.ndarray,
                 params: Optional[Dict] = None):
    """Train LightGBM binary classifier."""
    if not _LGB_OK:
        return None

    default_params = {
        'n_estimators': 200,
        'max_depth': 4,
        'learning_rate': 0.05,
        'subsample': 0.8,
        'colsample_bytree': 0.8,
        'random_state': 42,
        'n_jobs': -1,
        'verbose': -1,
    }
    if params:
        default_params.update(params)

    model = lgb.LGBMClassifier(**default_params)
    model.fit(X_train, y_train)
    return model


def _optuna_optimize(X: np.ndarray, y: np.ndarray,
                      model_type: str = 'xgb',
                      n_trials: int = 20) -> Optional[Dict]:
    """
    Run Optuna HPO targeting Sharpe ratio (not AUC).
    Returns best params dict.
    """
    if not _OPTUNA_OK or len(X) < 50:
        return None

    from sklearn.model_selection import TimeSeriesSplit

    def objective(trial):
        if model_type == 'xgb':
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 100, 400),
                'max_depth': trial.suggest_int('max_depth', 3, 6),
                'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.15, log=True),
                'subsample': trial.suggest_float('subsample', 0.6, 1.0),
                'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
            }
        else:
            params = {
                'n_estimators': trial.suggest_int('n_estimators', 100, 400),
                'max_depth': trial.suggest_int('max_depth', 3, 6),
                'learning_rate': trial.suggest_float('learning_rate', 0.01, 0.15, log=True),
                'subsample': trial.suggest_float('subsample', 0.6, 1.0),
                'colsample_bytree': trial.suggest_float('colsample_bytree', 0.6, 1.0),
                'num_leaves': trial.suggest_int('num_leaves', 15, 63),
            }

        tscv = TimeSeriesSplit(n_splits=3)
        sharpes = []
        for train_idx, val_idx in tscv.split(X):
            if len(train_idx) < 20 or len(val_idx) < 5:
                continue
            try:
                if model_type == 'xgb':
                    m = _train_xgb(X[train_idx], y[train_idx], params)
                else:
                    m = _train_lgbm(X[train_idx], y[train_idx], params)
                if m is None:
                    continue
                preds = m.predict_proba(X[val_idx])[:, 1]
                # Sharpe proxy
                pnl_proxy = np.where(preds >= 0.5,
                                     np.where(y[val_idx] == 1, 1.0, -1.0), 0.0)
                if pnl_proxy.std() > 1e-9:
                    sharpes.append(pnl_proxy.mean() / pnl_proxy.std())
            except Exception:
                continue

        return float(np.mean(sharpes)) if sharpes else -1.0

    try:
        study = optuna.create_study(direction='maximize')
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        return study.best_params
    except Exception as e:
        logger.debug(f'[wft] optuna error: {e}')
        return None


def train_walk_forward(pair_key: str = 'GENERIC', direction: str = 'LONG',
                        paper: bool = True, optimize: bool = False) -> Dict:
    """
    Run walk-forward training and save models if passes criteria.

    Returns:
        {
          'success': bool,
          'folds_total': int,
          'folds_passed': int,
          'avg_wr': float,
          'avg_pf': float,
          'avg_sharpe': float,
          'model_saved': bool,
          'reason': str,
        }
    """
    result = {
        'success': False,
        'folds_total': 0,
        'folds_passed': 0,
        'avg_wr': 0.0,
        'avg_pf': 0.0,
        'avg_sharpe': 0.0,
        'model_saved': False,
        'reason': 'not_run',
    }

    if not _XGB_OK and not _LGB_OK:
        result['reason'] = 'no_ml_library'
        return result

    df = _load_training_data(pair_key, direction, paper)
    if df is None or len(df) < _MIN_TRADES:
        result['reason'] = f'insufficient_data: {0 if df is None else len(df)} trades'
        return result

    # Check minimum live days
    min_ts = df['ts'].min()
    live_days = (time.time() - min_ts) / 86400
    if live_days < _MIN_LIVE_DAYS:
        result['reason'] = f'need {_MIN_LIVE_DAYS}d live data, only {live_days:.0f}d'
        return result

    from ml.feature_builder import FEATURE_NAMES

    # ── KNOWN LIMITATION — 3-proxy feature training ────────────────────────────
    # The walk-forward trainer currently trains on only 3 aggregate score columns
    # (technical_score, ml_score, composite_score) because per-trade 57-feature
    # snapshots are not yet persisted to the DB.
    #
    # CONSEQUENCE: Any model saved by this trainer will have input shape (N, 3).
    # ModelStore.predict_proba() builds a (1, 57) array via to_array(features) and
    # calls model.predict_proba(arr), which will fail with a shape mismatch and
    # return None — causing signal_engine to fall back to the neutral score of 50.0.
    # The ML tower is therefore effectively inactive (returns 50 for all candidates)
    # until full 57-feature snapshots are stored per trade by learning_loop.py.
    #
    # FIX PATH: learning_loop.record_closed_trade() must store the full features dict
    # to a trade_features table (keyed by trade_id), then _load_training_data() here
    # joins that table and returns X with 57 columns.  Until that join is implemented,
    # models saved here are decorative — they will always produce a prediction error
    # on the live 57-feature input.
    #
    # The 3-proxy model is harmless (fails gracefully → 50.0) but wastes compute.
    # Do not promote a model trained on proxies to production sizing decisions.
    df['won'] = df['won'].fillna(0).astype(int)
    df['pnl_usd'] = df['pnl_usd'].fillna(0).astype(float)

    # Build feature matrix from stored scores (proxy until full feature logging active)
    X_proxy = np.column_stack([
        df['technical_score'].fillna(50).values / 100,
        df['ml_score'].fillna(50).values / 100,
        df['composite_score'].fillna(50).values / 100,
    ])
    logger.warning(
        f'[wft] {pair_key}_{direction}: training on 3-proxy features '
        f'(not 57 — model will fail shape-check at inference, ML tower stays at 50.0 neutral). '
        f'Wire 57-feature snapshot storage in learning_loop to activate real ML.'
    )
    y = df['won'].values
    pnls = df['pnl_usd'].values

    # Optimize hyperparams
    xgb_params = None
    lgbm_params = None
    if optimize and _OPTUNA_OK:
        logger.info(f'[wft] Optuna HPO for {pair_key}_{direction}...')
        xgb_params = _optuna_optimize(X_proxy, y, 'xgb', n_trials=15)
        lgbm_params = _optuna_optimize(X_proxy, y, 'lgbm', n_trials=15)

    # Walk-forward folds
    fold_metrics = []
    step = _STEP_DAYS
    train_d = _TRAIN_DAYS
    val_d = _VALID_DAYS

    # Convert to time-indexed walks
    df['date'] = pd.to_datetime(df['ts'], unit='s')
    df = df.sort_values('ts').reset_index(drop=True)
    min_date = df['date'].min()
    max_date = df['date'].max()

    current_train_start = min_date
    while True:
        train_end = current_train_start + pd.Timedelta(days=train_d)
        val_end   = train_end + pd.Timedelta(days=val_d)

        if val_end > max_date:
            break

        train_mask = (df['date'] >= current_train_start) & (df['date'] < train_end)
        val_mask   = (df['date'] >= train_end) & (df['date'] < val_end)

        X_tr = X_proxy[train_mask]
        y_tr = y[train_mask]
        X_vl = X_proxy[val_mask]
        y_vl = y[val_mask]
        pnl_vl = pnls[val_mask]

        if len(X_tr) < 15 or len(X_vl) < 5:
            current_train_start += pd.Timedelta(days=step)
            continue

        xgb_model = _train_xgb(X_tr, y_tr, xgb_params)
        lgb_model  = _train_lgbm(X_tr, y_tr, lgbm_params)

        if xgb_model is not None and lgb_model is not None:
            xgb_prob = xgb_model.predict_proba(X_vl)[:, 1]
            lgb_prob  = lgb_model.predict_proba(X_vl)[:, 1]
            ensemble_prob = 0.6 * xgb_prob + 0.4 * lgb_prob
        elif xgb_model is not None:
            ensemble_prob = xgb_model.predict_proba(X_vl)[:, 1]
        elif lgb_model is not None:
            ensemble_prob = lgb_model.predict_proba(X_vl)[:, 1]
        else:
            current_train_start += pd.Timedelta(days=step)
            continue

        metrics = _compute_metrics(y_vl, ensemble_prob, pnl_vl)
        fold_metrics.append(metrics)
        current_train_start += pd.Timedelta(days=step)

    if not fold_metrics:
        result['reason'] = 'no_valid_folds'
        return result

    n_folds = len(fold_metrics)
    n_passed = sum(1 for m in fold_metrics if _fold_passes(m))
    pass_rate = n_passed / n_folds

    avg_wr     = float(np.mean([m['wr'] for m in fold_metrics]))
    avg_pf     = float(np.mean([m['pf'] for m in fold_metrics if m['pf'] < 10]))
    avg_sharpe = float(np.mean([m['sharpe'] for m in fold_metrics]))

    result.update({
        'folds_total': n_folds,
        'folds_passed': n_passed,
        'avg_wr': round(avg_wr, 4),
        'avg_pf': round(avg_pf, 4),
        'avg_sharpe': round(avg_sharpe, 4),
    })

    if pass_rate < _PASS_PCT:
        result['reason'] = (f'walk-forward failed: {n_passed}/{n_folds} folds passed '
                           f'(need {_PASS_PCT:.0%}), avg WR={avg_wr:.1%}')
        return result

    # Train final model on all data
    xgb_final = _train_xgb(X_proxy, y, xgb_params)
    lgb_final  = _train_lgbm(X_proxy, y, lgbm_params)

    saved = False
    if xgb_final is not None:
        path = _model_path(pair_key, direction, 'xgb')
        with open(path, 'wb') as f:
            pickle.dump(xgb_final, f)
        saved = True

    if lgb_final is not None:
        path = _model_path(pair_key, direction, 'lgbm')
        with open(path, 'wb') as f:
            pickle.dump(lgb_final, f)
        saved = True

    result.update({
        'success': True,
        'model_saved': saved,
        'reason': f'passed {n_passed}/{n_folds} folds, avg WR={avg_wr:.1%}',
    })

    logger.info(f'[wft] {pair_key}_{direction}: {result["reason"]} → models saved')
    return result


def retrain_all(paper: bool = True):
    """
    Retrain all 8 models: BTC/ETH/SOL/GENERIC × LONG/SHORT.
    Called by learning_loop.py on schedule.
    """
    pairs = ['BTC', 'ETH', 'SOL', 'GENERIC']
    directions = ['LONG', 'SHORT']
    results = {}

    for pair in pairs:
        for direction in directions:
            key = f'{pair}_{direction}'
            try:
                r = train_walk_forward(pair, direction, paper)
                results[key] = r
                logger.info(f'[wft] {key}: success={r["success"]} {r["reason"]}')
            except Exception as e:
                results[key] = {'success': False, 'reason': str(e)}
                logger.error(f'[wft] {key} error: {e}')

    return results
