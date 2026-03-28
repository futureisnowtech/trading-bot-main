"""
scripts/inspect_ml_model.py — Inspect the saved ML model from learning/ml_signal.py.

Prints:
  - Model type (LightGBM or sklearn GBT)
  - Top-10 feature importances (sorted descending)
  - Model metadata: training date, n_samples, train accuracy
  - Feature correlation matrix for top 5 features

Usage:
    python3 scripts/inspect_ml_model.py

Exit codes:
    0  — model found and inspected successfully
    1  — no model found (not yet trained, or insufficient data)
"""
import os
import sys
import time

# Allow imports from project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def _separator(title: str = '') -> None:
    width = 60
    if title:
        pad = (width - len(title) - 2) // 2
        print('-' * pad + f' {title} ' + '-' * pad)
    else:
        print('-' * width)


def main() -> int:
    # ── 1. Load model state from ml_signal module ──────────────────────────────
    print()
    _separator('ML MODEL INSPECTOR')
    print()

    try:
        from learning.ml_signal import (
            train,
            _model,          # module-level singleton (may be None)
            _feature_cols,
            _last_retrain_ts,
            SIGNAL_FEATURES,
            REGIME_MAP,
            _load_training_data,
            get_feature_importance,
        )
    except ImportError as e:
        print(f"[ERROR] Could not import learning.ml_signal: {e}")
        return 1

    # Access module globals directly so we see the in-memory state after train()
    import learning.ml_signal as _ml_mod

    # Attempt a fresh train so we have the model in memory for inspection
    print("Loading training data and fitting model (this is read-only — same as runtime)...")
    success = train()

    # Re-read module globals after train()
    model = _ml_mod._model
    feature_cols = _ml_mod._feature_cols
    last_retrain_ts = _ml_mod._last_retrain_ts

    if not success or model is None:
        print()
        print("[RESULT] No model available.")
        print("  Possible reasons:")
        print("  - Fewer than 30 labeled trades in trade_attribution table")
        print("  - Only one class present in training data (all wins or all losses)")
        print("  - Database does not exist yet")
        print()
        print("Run the bot in paper mode to accumulate trades, then re-run this script.")
        return 1

    # ── 2. Model type ──────────────────────────────────────────────────────────
    _separator('Model Type')
    model_type = type(model).__name__
    model_module = type(model).__module__
    print(f"  Class   : {model_type}")
    print(f"  Module  : {model_module}")
    if 'lightgbm' in model_module.lower():
        print(f"  Backend : LightGBM")
    elif 'gradient' in model_type.lower():
        print(f"  Backend : sklearn GradientBoostingClassifier")
    elif 'logistic' in model_type.lower():
        print(f"  Backend : sklearn LogisticRegression (fallback)")
    else:
        print(f"  Backend : Unknown")

    # ── 3. Metadata ────────────────────────────────────────────────────────────
    _separator('Model Metadata')
    if last_retrain_ts > 0:
        retrain_str = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(last_retrain_ts))
    else:
        retrain_str = 'unknown'
    print(f"  Last trained : {retrain_str}")

    # n_estimators / n_iter depending on model type
    n_est = (getattr(model, 'n_estimators', None)
             or getattr(model, 'num_iterations', None)
             or 'N/A')
    print(f"  n_estimators : {n_est}")

    # Training accuracy via train data
    X_train, y_train = _load_training_data()
    if X_train is not None:
        n_samples = len(X_train)
        try:
            train_acc = model.score(X_train, y_train)
            print(f"  n_samples    : {n_samples}")
            print(f"  Train acc    : {train_acc:.4f}  ({train_acc:.1%})")
            print(f"  Positive rate: {y_train.mean():.1%}  ({int(y_train.sum())}/{n_samples} wins)")
        except Exception as e:
            print(f"  n_samples    : {n_samples}")
            print(f"  Train acc    : could not compute — {e}")
    else:
        print(f"  n_samples    : N/A (data not reloadable)")

    # ── 4. Feature importances ─────────────────────────────────────────────────
    _separator('Feature Importances (top 10)')
    importances = getattr(model, 'feature_importances_', None)
    all_cols = SIGNAL_FEATURES + ['regime_encoded']

    if importances is None:
        # LogisticRegression: use abs(coef_) as proxy
        coef = getattr(model, 'coef_', None)
        if coef is not None:
            import numpy as np
            importances = abs(coef[0])
            print("  (LogisticRegression: using |coef| as importance proxy)")
        else:
            print("  [WARNING] Model has no feature_importances_ and no coef_ — cannot show importances.")

    if importances is not None:
        import numpy as np
        ranked = sorted(zip(all_cols, importances), key=lambda x: x[1], reverse=True)
        total = sum(v for _, v in ranked) or 1.0
        print(f"  {'Rank':<5} {'Feature':<30} {'Score':>10}  {'Share':>7}")
        print(f"  {'----':<5} {'-------':<30} {'-----':>10}  {'-----':>7}")
        for rank, (name, score) in enumerate(ranked[:10], 1):
            share = score / total
            print(f"  {rank:<5} {name:<30} {score:>10.4f}  {share:>6.1%}")
        top5_features = [name for name, _ in ranked[:5]]
    else:
        top5_features = all_cols[:5]

    # ── 5. Feature correlation matrix (top 5) ─────────────────────────────────
    _separator('Correlation Matrix — Top 5 Features')
    if X_train is not None and len(X_train) > 0:
        try:
            import numpy as np
            top5_indices = [all_cols.index(f) for f in top5_features if f in all_cols]
            X_top5 = X_train[:, top5_indices]
            top5_names = [all_cols[i] for i in top5_indices]

            # Compute correlation; handle zero-variance columns
            with np.errstate(invalid='ignore', divide='ignore'):
                corr = np.corrcoef(X_top5.T)
            corr = np.nan_to_num(corr)  # replace NaN (zero-variance) with 0

            col_w = 14
            header = f"  {'':22}" + ''.join(f'{n[:col_w]:>{col_w}}' for n in top5_names)
            print(header)
            for i, row_name in enumerate(top5_names):
                row_str = f"  {row_name:<22}" + ''.join(f'{corr[i, j]:>{col_w}.3f}' for j in range(len(top5_names)))
                print(row_str)
        except Exception as e:
            print(f"  [WARNING] Could not compute correlation matrix: {e}")
    else:
        print("  [SKIP] Training data not available for correlation matrix.")

    _separator()
    print()
    print("Inspection complete.")
    print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
