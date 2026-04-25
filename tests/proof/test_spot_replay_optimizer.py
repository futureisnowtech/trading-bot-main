from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_sro01_trial_grid_contains_neutral_weight_surface():
    from backtesting.spot_replay_optimizer import _build_trial_grid

    trials = _build_trial_grid()
    labels = {t.label() for t in trials}
    assert any("nw=0.90" in label for label in labels)
    assert any("tp=quick" in label for label in labels)


def test_sro02_dynamic_floor_preserves_live_adjustments():
    from backtesting.spot_replay_optimizer import ReplayTrial, _dynamic_floor

    trial = ReplayTrial(
        neutral_composite_weight=0.90,
        neutral_floor=61.0,
        trend_floor=60.0,
        chop_floor=67.0,
        target_profile="balanced",
    )
    assert _dynamic_floor(trial, "NEUTRAL", 3, "impulse_continuation") == 59.0
    assert _dynamic_floor(trial, "CHOP", 2, "compression_breakout") == 68.0


def test_sro03_simulator_stops_out_cleanly():
    import pandas as pd

    from backtesting.spot_replay_optimizer import _simulate_trade

    idx = pd.date_range("2026-01-01", periods=5, freq="5min", tz="UTC")
    future = pd.DataFrame(
        {
            "open": [100, 99.7, 99.4, 99.1, 98.8],
            "high": [100.1, 99.8, 99.5, 99.2, 99.0],
            "low": [99.2, 98.7, 98.4, 98.0, 97.8],
            "close": [99.5, 99.0, 98.8, 98.4, 98.0],
        },
        index=idx,
    )
    result = _simulate_trade(
        entry_ts=idx[0],
        entry_price=100.0,
        future_5m=future,
        stop_pct=0.01,
        target_r=1.2,
        trail_arm_r=0.8,
        expected_half_life_min=20.0,
    )
    assert result["exit_reason"] == "hard_stop"
    assert result["won"] is False


def test_sro04_setup_mode_maps_to_allowed_families():
    from backtesting.spot_replay_optimizer import _setup_mode_to_allowed_setups

    assert _setup_mode_to_allowed_setups("impulse_only") == ("impulse_continuation",)
    assert _setup_mode_to_allowed_setups("no_compression") == (
        "impulse_continuation",
        "pullback_reclaim",
    )


def test_sro05_fit_symbol_strategies_returns_symbol_policies(monkeypatch):
    from backtesting.spot_replay_optimizer import SpotReplayOptimizer

    optimizer = SpotReplayOptimizer(symbols=["BTC"], days=30)
    monkeypatch.setattr(
        optimizer,
        "load_histories",
        lambda: ({"BTC": {}}, [{"symbol": "BTC", "coverage": 1.0}]),
    )
    monkeypatch.setattr(
        optimizer,
        "build_event_sets_by_symbol",
        lambda histories: {"BTC": [{"symbol": "BTC"}]},
    )
    monkeypatch.setattr(
        optimizer,
        "evaluate_trial",
        lambda trial, events: {
            "trial": trial.label(),
            "n_trades": 14,
            "win_rate": 0.58,
            "profit_factor": 1.20,
            "net_expectancy_per_trade": 0.001,
            "net_pnl_pct": 0.01,
            "avg_hold_bars": 4.0,
            "near_misses": 0,
            "regime_counts": {"TREND": 5},
            "setup_counts": {"impulse_continuation": 5},
        },
    )
    result = optimizer.fit_symbol_strategies(top_n=1)
    assert "BTC" in result["recommendations"]
    assert result["recommendations"]["BTC"]["meets_target"] is True
    assert result["recommendations"]["BTC"]["best_trial"]["recommended_live_policy"]["symbol"] == "BTC"


def test_sro06_multi_symbol_fit_writes_progress_incrementally(monkeypatch, tmp_path):
    import backtesting.spot_replay_optimizer as sro

    monkeypatch.setattr(sro, "BACKTEST_DIR", str(tmp_path), raising=False)

    def _fake_load_histories(self):
        symbol = self.symbols[0]
        return ({symbol: {}}, [{"symbol": symbol, "coverage": 1.0}])

    def _fake_build_event_sets(self, histories):
        symbol = next(iter(histories))
        return {symbol: [{"symbol": symbol}]}

    monkeypatch.setattr(sro.SpotReplayOptimizer, "load_histories", _fake_load_histories)
    monkeypatch.setattr(
        sro.SpotReplayOptimizer,
        "build_event_sets_by_symbol",
        _fake_build_event_sets,
    )
    monkeypatch.setattr(
        sro.SpotReplayOptimizer,
        "evaluate_trial",
        lambda self, trial, events: {
            "trial": trial.label(),
            "n_trades": 14,
            "win_rate": 0.58,
            "profit_factor": 1.20,
            "net_expectancy_per_trade": 0.001,
            "net_pnl_pct": 0.01,
            "avg_hold_bars": 4.0,
            "near_misses": 0,
            "regime_counts": {"TREND": 5},
            "setup_counts": {"impulse_continuation": 5},
        },
    )

    optimizer = sro.SpotReplayOptimizer(symbols=["BTC", "ETH"], days=30)
    result = optimizer.fit_symbol_strategies(top_n=1)
    assert set(result["recommendations"]) == {"BTC", "ETH"}
    progress_path = tmp_path / "spot_symbol_fit_30d_progress.json"
    assert progress_path.exists()
    fit_btc = tmp_path / "fit_BTC_30d.json"
    fit_eth = tmp_path / "fit_ETH_30d.json"
    assert fit_btc.exists()
    assert fit_eth.exists()


def test_sro07_load_histories_reuses_enriched_cache(monkeypatch, tmp_path):
    import pandas as pd
    import backtesting.spot_replay_optimizer as sro

    monkeypatch.setattr(sro, "BACKTEST_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(sro, "REPLAY_CACHE_DIR", str(tmp_path / "cache"), raising=False)

    idx = pd.date_range("2026-01-01", periods=20, freq="5min", tz="UTC")
    frame = pd.DataFrame(
        {
            "open": [1.0] * 20,
            "high": [1.0] * 20,
            "low": [1.0] * 20,
            "close": [1.0] * 20,
            "volume": [1.0] * 20,
        },
        index=idx,
    )
    monkeypatch.setattr(
        sro,
        "ensure_history",
        lambda symbol, timeframe, days: (frame.copy(), {"symbol": symbol, "timeframe": timeframe, "coverage": 1.0}),
    )
    monkeypatch.setattr(sro, "_resample_ohlcv", lambda df, timeframe: frame.copy())

    calls = {"n": 0}

    def _fake_add(df):
        calls["n"] += 1
        out = df.copy()
        out["marker"] = calls["n"]
        return out

    monkeypatch.setattr(sro, "add_all_indicators", _fake_add)

    opt = sro.SpotReplayOptimizer(symbols=["BTC"], days=30)
    histories1, _ = opt.load_histories()
    histories2, _ = opt.load_histories()

    assert calls["n"] == 4
    assert float(histories1["BTC"]["5m"]["marker"].iloc[-1]) == float(histories2["BTC"]["5m"]["marker"].iloc[-1])


def test_sro08_utility_prefers_positive_expectancy_over_prettier_win_rate():
    from backtesting.spot_replay_optimizer import _utility_score

    negative = {
        "n_trades": 40,
        "win_rate": 0.62,
        "profit_factor": 0.91,
        "net_expectancy_per_trade": -0.0003,
    }
    positive = {
        "n_trades": 20,
        "win_rate": 0.41,
        "profit_factor": 1.22,
        "net_expectancy_per_trade": 0.0009,
    }
    assert _utility_score(positive, min_trades=12) > _utility_score(negative, min_trades=12)


def test_sro08b_utility_penalizes_zero_trade_candidates():
    from backtesting.spot_replay_optimizer import _utility_score

    inactive = {
        "n_trades": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "net_expectancy_per_trade": 0.0,
    }
    weak_but_real = {
        "n_trades": 8,
        "win_rate": 0.38,
        "profit_factor": 0.88,
        "net_expectancy_per_trade": -0.0002,
    }
    assert _utility_score(inactive, min_trades=12) < _utility_score(weak_but_real, min_trades=12)


def test_sro09_extract_optimal_strategies_writes_research_artifact(monkeypatch, tmp_path):
    import backtesting.spot_replay_optimizer as sro

    monkeypatch.setattr(sro, "BACKTEST_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(sro, "REPLAY_CACHE_DIR", str(tmp_path / "cache"), raising=False)

    def _fake_events(self, symbol, **kwargs):
        return ([{"symbol": symbol, "regime": "TREND", "setup_family": "pullback_reclaim"}], [{"symbol": symbol, "coverage": 1.0}])

    monkeypatch.setattr(sro.SpotReplayOptimizer, "_load_or_build_events", _fake_events)

    def _fake_eval(self, trial, events):
        if trial.target_profile == "precision":
            return {
                "trial": trial.label(),
                "n_trades": 18,
                "win_rate": 0.44,
                "profit_factor": 1.24,
                "net_expectancy_per_trade": 0.0012,
                "net_pnl_pct": 0.0216,
                "avg_hold_bars": 4.0,
                "near_misses": 9,
                "regime_counts": {"TREND": 10},
                "setup_counts": {"pullback_reclaim": 10},
            }
        return {
            "trial": trial.label(),
            "n_trades": 30,
            "win_rate": 0.58,
            "profit_factor": 0.92,
            "net_expectancy_per_trade": -0.0002,
            "net_pnl_pct": -0.006,
            "avg_hold_bars": 3.0,
            "near_misses": 15,
            "regime_counts": {"TREND": 10},
            "setup_counts": {"pullback_reclaim": 10},
        }

    monkeypatch.setattr(sro.SpotReplayOptimizer, "evaluate_trial", _fake_eval)

    result = sro.SpotReplayOptimizer(symbols=["BTC", "ETH"], days=30).extract_optimal_strategies(top_n=2)
    assert os.path.exists(result["output_path"])
    assert result["recommendations"]["BTC"]["recommended_strategy"]["recommended_live_policy"]["target_profile"] == "precision"
    assert result["recommendations"]["BTC"]["recommendation_status"] == "active_candidate"
    assert result["recommendations"]["BTC"]["viable_frontier_count"] >= 1


def test_sro10_trial_roundtrip_and_coin_optimizer(monkeypatch, tmp_path):
    import backtesting.spot_replay_optimizer as sro

    monkeypatch.setattr(sro, "BACKTEST_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(sro, "REPLAY_CACHE_DIR", str(tmp_path / "cache"), raising=False)

    events = [
        {
            "symbol": "BTC",
            "regime": "TREND",
            "setup_family": "pullback_reclaim",
            "setup_score": 0.8,
            "states": {
                "5m": {
                    "frame_score": 58.0,
                    "momentum_impulse": 0.03,
                    "structure_component": 0.08,
                    "path_efficiency": 0.24,
                    "participation_component": 0.02,
                },
                "30m": {
                    "frame_score": 56.0,
                    "volatility_quality": -0.02,
                },
            },
        }
    ]

    def _fake_events(self, symbol, **kwargs):
        return (events, [{"symbol": symbol, "coverage": 1.0}])

    monkeypatch.setattr(sro.SpotReplayOptimizer, "_load_or_build_events", _fake_events)

    def _fake_eval(self, trial, events):
        win_rate = 0.20
        profit_factor = 0.80
        expectancy = -0.0004
        trades = 20
        if trial.target_profile == "precision":
            win_rate += 0.08
            profit_factor += 0.20
            expectancy += 0.0005
        if trial.setup_mode == "pullback_only":
            win_rate += 0.10
            profit_factor += 0.35
            expectancy += 0.0006
            trades -= 4
        if trial.regime_mode == "trend_only":
            win_rate += 0.04
            profit_factor += 0.10
            expectancy += 0.0002
            trades -= 2
        if trial.min_confirm_count == 3:
            win_rate += 0.02
            profit_factor += 0.05
            expectancy += 0.0001
            trades -= 1
        if trial.min_path_efficiency >= 0.2:
            win_rate += 0.03
            profit_factor += 0.08
            expectancy += 0.0002
            trades -= 1
        return {
            "trial": trial.label(),
            "n_trades": max(trades, 1),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
            "net_expectancy_per_trade": round(expectancy, 6),
            "net_pnl_pct": round(expectancy * max(trades, 1), 6),
            "avg_hold_bars": 4.0,
            "near_misses": 5,
            "regime_counts": {"TREND": 10},
            "setup_counts": {"pullback_reclaim": 10},
        }

    monkeypatch.setattr(sro.SpotReplayOptimizer, "evaluate_trial", _fake_eval)

    trial = sro.ReplayTrial(0.84, 60.0, 60.0, 68.0, "precision", "pullback_only", "trend_only", 3, 56.0, 55.0, 0.0, 0.05, 0.2, 0.0, -0.05)
    parsed = sro._trial_from_label(trial.label())
    assert parsed == trial

    result = sro.SpotReplayOptimizer(symbols=["BTC"], days=30).optimize_coin_strategies(top_n=2)
    rec = result["recommendations"]["BTC"]
    assert os.path.exists(result["output_path"])
    assert rec["recommendation_status"] in {"improved_candidate", "promotable_research_candidate"}
    assert rec["scorecard"]["delta"]["profit_factor"] > 0
    assert rec["scorecard"]["delta"]["net_expectancy_per_trade"] > 0
    assert rec["tweak_deltas"]
