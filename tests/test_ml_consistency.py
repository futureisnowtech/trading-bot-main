"""
tests/test_ml_consistency.py

Regression tests for ML signal gate.
Catches: feature count mismatches, stale/broken model returning 0.5 fallback,
         feature list drift between trainer and inference.
"""
import sys
import os
import pytest
import numpy as np

pytestmark = pytest.mark.skip(reason="ml_signal.py replaced by ModelStore in v18 refactor; tests are stale")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_all_features_count():
    """ALL_FEATURES must have exactly 22 entries (19 signals + regime + hour + session)."""
    from learning.ml_signal import ALL_FEATURES, SIGNAL_FEATURES
    assert len(SIGNAL_FEATURES) == 19, f"Expected 19 signal features, got {len(SIGNAL_FEATURES)}"
    assert len(ALL_FEATURES) == 22, f"Expected 22 total features, got {len(ALL_FEATURES)}"
    assert 'hour_et_norm' in ALL_FEATURES, "Time feature hour_et_norm missing from ALL_FEATURES"
    assert 'session_norm' in ALL_FEATURES, "Time feature session_norm missing from ALL_FEATURES"
    assert 'regime_encoded' in ALL_FEATURES, "regime_encoded missing from ALL_FEATURES"


def test_trainer_uses_all_features():
    """ml_trainer.py must use ALL_FEATURES, not the old 20-feature list."""
    trainer_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'learning', 'ml_trainer.py'
    )
    with open(trainer_path) as f:
        src = f.read()
    assert 'hour_et_norm' in src, "ml_trainer.py missing hour_et_norm time feature"
    assert 'session_norm' in src, "ml_trainer.py missing session_norm time feature"
    # Must NOT hardcode the old 20-feature list as the final feature set
    assert "SIGNAL_FEATURES + ['regime_encoded']" not in src or 'ALL_FEATURES' in src, \
        "ml_trainer.py uses old 20-feature list instead of ALL_FEATURES"


def test_inline_train_sets_correct_feature_cols():
    """After inline train(), _feature_cols must equal ALL_FEATURES (22 items)."""
    import learning.ml_signal as ms
    # Manually call train with synthetic data to exercise the slow path
    orig_load = ms._load_from_pkl
    orig_load_data = ms._load_training_data

    # Stub _load_from_pkl to force slow path
    ms._load_from_pkl = lambda: False

    # Build minimal synthetic training data (22 features, 2 classes)
    X = np.random.rand(40, 22)
    y = np.array([1] * 20 + [0] * 20)
    ms._load_training_data = lambda: (X, y)

    ms._model = None
    ms._feature_cols = []
    ms.train()

    # Restore
    ms._load_from_pkl = orig_load
    ms._load_training_data = orig_load_data

    assert ms._model is not None, "train() did not set _model"
    assert len(ms._feature_cols) == 22, (
        f"After train(), _feature_cols has {len(ms._feature_cols)} features, expected 22. "
        f"This causes inference mismatches."
    )


def test_inference_does_not_return_error_fallback():
    """get_ml_signal() must not return label='error' when a valid model is loaded."""
    import learning.ml_signal as ms
    from sklearn.linear_model import LogisticRegression

    # Plant a known-good 22-feature model
    clf = LogisticRegression(max_iter=100)
    clf.fit(np.random.rand(30, 22), [1]*15 + [0]*15)
    ms._model = clf
    ms._feature_cols = ms.ALL_FEATURES

    market_data = {
        'regime': 'trending',
        'macd_consensus': True,
        'supertrend_bullish': True,
    }
    p, lbl = ms.get_ml_signal(market_data)
    assert lbl != 'error', f"get_ml_signal() returned error label: p={p}, lbl={lbl}"
    assert 0.0 <= p <= 1.0, f"p_win={p} is out of [0,1] range"


def test_feature_vector_length_matches_model():
    """Inference feature vector must match model's expected input shape."""
    import learning.ml_signal as ms
    from sklearn.linear_model import LogisticRegression

    clf = LogisticRegression(max_iter=100)
    clf.fit(np.random.rand(30, 22), [1]*15 + [0]*15)
    ms._model = clf
    ms._feature_cols = ms.ALL_FEATURES

    # If feature vector is wrong length, predict_proba will throw
    # which would make label='error' — this test catches that
    market_data = {'regime': 'ranging'}
    p, lbl = ms.get_ml_signal(market_data)
    assert lbl != 'error', (
        f"Feature vector length mismatch: model expects 22, inference built wrong count. "
        f"Got p={p}, lbl={lbl}"
    )
