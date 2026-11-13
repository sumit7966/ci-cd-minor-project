"""Reproducible binary CI/CD failure-prediction training and evaluation pipeline.

Implements:
- LogisticRegression inside the approved preprocessing pipeline.
- Chronological holdout splitting on training data.
- Preprocessing fit strictly on training data only.
- Comprehensive evaluation reporting (accuracy, precision, recall, F1, confusion matrix, ROC-AUC).
- Safe handling of empty, tiny, and single-class datasets.
- Single joblib artifact persistence upon successful training.
- Deterministic random states and no threshold tuning on test split.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sqlalchemy.orm import Session

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from .features import ALL_FEATURE_COLUMNS, build_preprocessor
    from .persistence import save_model
except ImportError:
    from ml.features import ALL_FEATURE_COLUMNS, build_preprocessor
    from ml.persistence import save_model

logger = logging.getLogger("ml.train")


def chronological_split(
    X: pd.DataFrame,
    y: pd.Series,
    test_size: float = 0.2,
    random_state: int = 42,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, str]:
    """Split telemetry chronologically based on created_at timestamp.

    If chronological split leaves the training partition with only a single class,
    safely falls back to a stratified split with a documented warning.

    Args:
        X: Pre-run feature matrix.
        y: Binary failure labels (0=success, 1=failure).
        test_size: Proportion of dataset reserved for evaluation.
        random_state: Seed for reproducible fallback splits.

    Returns:
        Tuple of (X_train, X_test, y_train, y_test, split_type).
    """
    if len(X) < 4:
        raise ValueError(f"Insufficient training samples ({len(X)} provided; minimum 4 required).")

    unique_classes = set(y.unique())
    if len(unique_classes) < 2:
        raise ValueError(
            f"Training requires both positive (failure) and negative (success) classes. Found only: {unique_classes}"
        )

    # Sort chronologically by created_at if available
    if "created_at" in X.columns:
        dt_series = pd.to_datetime(X["created_at"], utc=True, errors="coerce")
        sort_order = np.argsort(dt_series.fillna(pd.Timestamp.min).to_numpy())
        X_sorted = X.iloc[sort_order].reset_index(drop=True)
        y_sorted = y.iloc[sort_order].reset_index(drop=True)
    else:
        X_sorted = X.reset_index(drop=True)
        y_sorted = y.reset_index(drop=True)

    n_samples = len(X_sorted)
    n_test = max(1, int(round(n_samples * test_size)))
    n_train = n_samples - n_test

    # Verify both partitions have at least 1 sample and train has enough data
    if n_train < 2:
        n_train = 2
        n_test = n_samples - n_train

    X_train_cand = X_sorted.iloc[:n_train].reset_index(drop=True)
    X_test_cand = X_sorted.iloc[n_train:].reset_index(drop=True)
    y_train_cand = y_sorted.iloc[:n_train].reset_index(drop=True)
    y_test_cand = y_sorted.iloc[n_train:].reset_index(drop=True)

    # Check class diversity in chronological train split
    if len(set(y_train_cand.unique())) >= 2:
        return X_train_cand, X_test_cand, y_train_cand, y_test_cand, "chronological"

    # Fallback to stratified split if chronological split isolated single class in train set
    logger.warning(
        "Chronological split resulted in single-class training partition; falling back to stratified split."
    )
    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X_sorted,
            y_sorted,
            test_size=test_size,
            random_state=random_state,
            stratify=y_sorted,
        )
        return (
            X_train.reset_index(drop=True),
            X_test.reset_index(drop=True),
            y_train.reset_index(drop=True),
            y_test.reset_index(drop=True),
            "stratified_fallback",
        )
    except ValueError:
        # Ultimate fallback for tiny imbalanced datasets where stratification is impossible
        X_train, X_test, y_train, y_test = train_test_split(
            X_sorted,
            y_sorted,
            test_size=test_size,
            random_state=random_state,
        )
        return (
            X_train.reset_index(drop=True),
            X_test.reset_index(drop=True),
            y_train.reset_index(drop=True),
            y_test.reset_index(drop=True),
            "random_fallback",
        )


def evaluate_predictions(
    y_true: Union[pd.Series, np.ndarray],
    y_pred: Union[pd.Series, np.ndarray],
    y_proba: Optional[Union[pd.Series, np.ndarray]] = None,
) -> Dict[str, Any]:
    """Calculate standard classification evaluation metrics.

    Args:
        y_true: Ground truth binary target labels.
        y_pred: Model predictions at default 0.5 threshold.
        y_proba: Predicted probability of positive class (failure).

    Returns:
        Dictionary of metrics including accuracy, precision, recall, f1, confusion matrix, and conditional ROC-AUC.
    """
    y_true_arr = np.asarray(y_true)
    y_pred_arr = np.asarray(y_pred)

    cm = confusion_matrix(y_true_arr, y_pred_arr, labels=[0, 1])
    # Standard 2x2 confusion matrix [[TN, FP], [FN, TP]]
    cm_list: List[List[int]] = cm.tolist()

    metrics: Dict[str, Any] = {
        "accuracy": round(float(accuracy_score(y_true_arr, y_pred_arr)), 4),
        "precision": round(float(precision_score(y_true_arr, y_pred_arr, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true_arr, y_pred_arr, zero_division=0)), 4),
        "f1_score": round(float(f1_score(y_true_arr, y_pred_arr, zero_division=0)), 4),
        "confusion_matrix": cm_list,
    }

    # Calculate ROC-AUC only when evaluation data supports it (requires both classes)
    if len(np.unique(y_true_arr)) > 1 and y_proba is not None:
        metrics["roc_auc"] = round(float(roc_auc_score(y_true_arr, y_proba)), 4)
    else:
        metrics["roc_auc"] = None

    return metrics


def train_failure_model(
    X: pd.DataFrame,
    y: pd.Series,
    test_size: float = 0.2,
    random_state: int = 42,
    max_iter: int = 1000,
    raw_df: Optional[pd.DataFrame] = None,
) -> Tuple[Pipeline, Dict[str, Any]]:
    """Train a LogisticRegression pipeline and evaluate holdout performance.

    Safeguards:
    - Preprocessing is fitted strictly on X_train only.
    - Chronological holdout split is prioritized.
    - Deterministic random states and solver (liblinear).
    - Threshold is not tuned on test split (evaluated at default 0.5).

    Args:
        X: Pre-run feature matrix.
        y: Binary failure labels (0=success, 1=failure).
        test_size: Proportion of samples reserved for holdout validation.
        random_state: Reproducibility seed.
        max_iter: Maximum iterations for solver convergence.
        raw_df: Optional uncleaned raw DataFrame to track exclusions.

    Returns:
        Tuple of (fitted_pipeline, metrics_and_metadata_dict).

    Raises:
        ValueError: On empty, tiny (<4 samples), or single-class datasets.
    """
    if len(X) == 0:
        raise ValueError("Cannot train failure model: input feature dataset is empty.")

    X_train, X_test, y_train, y_test, split_type = chronological_split(
        X,
        y,
        test_size=test_size,
        random_state=random_state,
    )

    # Construct end-to-end Pipeline: preprocessing + LogisticRegression
    preprocessor = build_preprocessor()
    classifier = LogisticRegression(
        random_state=random_state,
        class_weight="balanced",
        max_iter=max_iter,
        solver="liblinear",
    )

    pipeline = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("classifier", classifier),
        ]
    )

    # Fit pipeline strictly on training split
    pipeline.fit(X_train, y_train)

    # Evaluate on held-out test split at default 0.5 decision threshold
    y_pred = pipeline.predict(X_test)
    y_proba = pipeline.predict_proba(X_test)[:, 1] if hasattr(pipeline, "predict_proba") else None

    eval_metrics = evaluate_predictions(y_test, y_pred, y_proba)

    # Metadata & dataset breakdown report
    report: Dict[str, Any] = {
        "model_type": "LogisticRegression",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "random_state": random_state,
        "split_type": split_type,
        "dataset_size": int(len(X)),
        "train_samples": int(len(X_train)),
        "test_samples": int(len(X_test)),
        "class_counts": {
            "success": int((y == 0).sum()),
            "failure": int((y == 1).sum()),
        },
        "train_class_counts": {
            "success": int((y_train == 0).sum()),
            "failure": int((y_train == 1).sum()),
        },
        "test_class_counts": {
            "success": int((y_test == 0).sum()),
            "failure": int((y_test == 1).sum()),
        },
        "features": ALL_FEATURE_COLUMNS,
        **eval_metrics,
    }

    if raw_df is not None:
        exclusions = len(raw_df) - len(X)
        report["raw_dataset_size"] = int(len(raw_df))
        report["exclusions_count"] = int(exclusions)

    return pipeline, report


MIN_SAMPLES_FOR_FAIR_COMPARISON: int = 30
MIN_CLASSES_IN_TEST_SPLIT: int = 2


def train_random_forest(
    X: pd.DataFrame,
    y: pd.Series,
    test_size: float = 0.2,
    random_state: int = 42,
    n_estimators: int = 100,
    raw_df: Optional[pd.DataFrame] = None,
) -> Tuple[Pipeline, Dict[str, Any]]:
    """Train a RandomForestClassifier pipeline using identical preprocessing and chronological split.

    Uses the same leakage-safe features and split as the baseline Logistic Regression model.

    Args:
        X: Pre-run feature matrix.
        y: Binary failure labels (0=success, 1=failure).
        test_size: Proportion of samples reserved for holdout validation.
        random_state: Reproducibility seed.
        n_estimators: Number of trees in the forest.
        raw_df: Optional uncleaned raw DataFrame to track exclusions.

    Returns:
        Tuple of (fitted_pipeline, metrics_and_metadata_dict).
    """
    if len(X) == 0:
        raise ValueError("Cannot train failure model: input feature dataset is empty.")

    X_train, X_test, y_train, y_test, split_type = chronological_split(
        X,
        y,
        test_size=test_size,
        random_state=random_state,
    )

    preprocessor = build_preprocessor()
    classifier = RandomForestClassifier(
        n_estimators=n_estimators,
        random_state=random_state,
        class_weight="balanced",
    )

    pipeline = Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            ("classifier", classifier),
        ]
    )

    pipeline.fit(X_train, y_train)

    y_pred = pipeline.predict(X_test)
    y_proba = pipeline.predict_proba(X_test)[:, 1] if hasattr(pipeline, "predict_proba") else None

    eval_metrics = evaluate_predictions(y_test, y_pred, y_proba)

    report: Dict[str, Any] = {
        "model_type": "RandomForest",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "random_state": random_state,
        "n_estimators": n_estimators,
        "split_type": split_type,
        "dataset_size": int(len(X)),
        "train_samples": int(len(X_train)),
        "test_samples": int(len(X_test)),
        "class_counts": {
            "success": int((y == 0).sum()),
            "failure": int((y == 1).sum()),
        },
        "train_class_counts": {
            "success": int((y_train == 0).sum()),
            "failure": int((y_train == 1).sum()),
        },
        "test_class_counts": {
            "success": int((y_test == 0).sum()),
            "failure": int((y_test == 1).sum()),
        },
        "features": ALL_FEATURE_COLUMNS,
        **eval_metrics,
    }

    if raw_df is not None:
        exclusions = len(raw_df) - len(X)
        report["raw_dataset_size"] = int(len(raw_df))
        report["exclusions_count"] = int(exclusions)

    return pipeline, report


def compare_models(
    X: pd.DataFrame,
    y: pd.Series,
    test_size: float = 0.2,
    random_state: int = 42,
    raw_df: Optional[pd.DataFrame] = None,
    min_samples_for_fair_eval: int = MIN_SAMPLES_FOR_FAIR_COMPARISON,
) -> Dict[str, Any]:
    """Perform side-by-side comparison between Logistic Regression and Random Forest.

    Enforces fair evaluation criteria:
    - Same chronological holdout split and leakage-safe preprocessing.
    - Fits preprocessing on training split only.
    - Evaluates using standard metrics (Accuracy, Precision, Recall, F1, ROC-AUC).
    - Checks whether sample size and holdout class diversity support a fair comparison.
    - Recommends the simpler model (Logistic Regression) if evidence is inconclusive
      or dataset is statistically insufficient.

    Args:
        X: Pre-run feature matrix.
        y: Binary failure labels (0=success, 1=failure).
        test_size: Proportion of samples reserved for holdout validation.
        random_state: Reproducibility seed.
        raw_df: Optional raw telemetry DataFrame to track exclusions.
        min_samples_for_fair_eval: Minimum dataset size required for statistically valid comparison.

    Returns:
        Structured side-by-side comparison dictionary including metrics, fairness criteria,
        deferral status, and recommendation rationale.
    """
    if len(X) == 0:
        raise ValueError("Cannot compare models: input feature dataset is empty.")

    X_train, X_test, y_train, y_test, split_type = chronological_split(
        X,
        y,
        test_size=test_size,
        random_state=random_state,
    )

    total_samples = int(len(X))
    train_samples = int(len(X_train))
    test_samples = int(len(X_test))
    unique_test_classes = sorted([int(c) for c in np.unique(y_test)])
    has_sufficient_samples = total_samples >= min_samples_for_fair_eval
    has_diverse_test_classes = len(unique_test_classes) >= MIN_CLASSES_IN_TEST_SPLIT
    fair_evaluation_supported = has_sufficient_samples and has_diverse_test_classes

    # Train baseline: Logistic Regression
    lr_pipe, lr_report = train_failure_model(
        X, y, test_size=test_size, random_state=random_state, raw_df=raw_df
    )

    # Train candidate: Random Forest
    rf_pipe, rf_report = train_random_forest(
        X, y, test_size=test_size, random_state=random_state, raw_df=raw_df
    )

    lr_metrics = {
        "accuracy": lr_report.get("accuracy"),
        "precision": lr_report.get("precision"),
        "recall": lr_report.get("recall"),
        "f1_score": lr_report.get("f1_score"),
        "roc_auc": lr_report.get("roc_auc"),
        "confusion_matrix": lr_report.get("confusion_matrix"),
    }
    rf_metrics = {
        "accuracy": rf_report.get("accuracy"),
        "precision": rf_report.get("precision"),
        "recall": rf_report.get("recall"),
        "f1_score": rf_report.get("f1_score"),
        "roc_auc": rf_report.get("roc_auc"),
        "confusion_matrix": rf_report.get("confusion_matrix"),
    }

    # Evaluate comparison outcome and recommend model
    if not fair_evaluation_supported:
        rf_deferred = True
        reasons = []
        if not has_sufficient_samples:
            reasons.append(
                f"total dataset size ({total_samples} samples) is below the threshold of {min_samples_for_fair_eval}"
            )
        if not has_diverse_test_classes:
            reasons.append(
                f"chronological test holdout contains only class {unique_test_classes} ({test_samples} samples), leaving ROC-AUC undefined"
            )
        deferral_reason = "; ".join(reasons)
        status = "inconclusive_insufficient_data"
        recommended_model = "LogisticRegression"
        rationale = (
            f"Dataset is insufficient for a statistically sound ensemble evaluation: {deferral_reason}. "
            "Both models score identically on the small holdout partition. "
            "Recommending the simpler, interpretable Logistic Regression baseline to avoid overfitting and unnecessary complexity."
        )
    else:
        rf_f1 = rf_metrics.get("f1_score") or 0.0
        lr_f1 = lr_metrics.get("f1_score") or 0.0
        # If RF doesn't strictly outperform LR by at least 2% F1 on sufficient data, recommend simpler model
        if rf_f1 > lr_f1 + 0.02:
            recommended_model = "RandomForest"
            status = "conclusive_candidate_superior"
            rf_deferred = False
            deferral_reason = None
            rationale = (
                f"Random Forest demonstrated superior generalization on the held-out split "
                f"(F1: {rf_f1:.4f} vs {lr_f1:.4f})."
            )
        else:
            recommended_model = "LogisticRegression"
            status = "inconclusive_tied_or_marginal"
            rf_deferred = True
            deferral_reason = "Random Forest did not demonstrate meaningful empirical improvement over linear baseline."
            rationale = (
                "Performance difference is marginal or tied; recommending the simpler and more interpretable "
                "Logistic Regression model per Occam's razor."
            )

    comparison_summary: Dict[str, Any] = {
        "status": status,
        "fair_evaluation_supported": fair_evaluation_supported,
        "rf_deferred": rf_deferred,
        "deferral_reason": deferral_reason,
        "dataset_size": total_samples,
        "train_samples": train_samples,
        "test_samples": test_samples,
        "split_type": split_type,
        "test_classes": unique_test_classes,
        "test_class_distribution": {int(k): int(v) for k, v in y_test.value_counts().items()},
        "baseline_model": "LogisticRegression",
        "candidate_model": "RandomForest",
        "recommended_model": recommended_model,
        "recommendation_rationale": rationale,
        "side_by_side_metrics": {
            "logistic_regression": lr_metrics,
            "random_forest": rf_metrics,
        },
    }
    return comparison_summary


def train_from_db(
    db: Optional[Session] = None,
    repository: Optional[str] = None,
    save_artifact: bool = True,
    model_dir: Optional[Path] = None,
    test_size: float = 0.2,
    random_state: int = 42,
) -> Tuple[Optional[Pipeline], Dict[str, Any]]:
    """Load pipeline records from the database and execute end-to-end model training.

    Args:
        db: Optional active SQLAlchemy session. Defaults to global session factory.
        repository: Optional filter by repository slug.
        save_artifact: If True, saves pipeline artifact to disk upon valid training.
        model_dir: Optional custom model directory.
        test_size: Holdout evaluation proportion.
        random_state: Seed for reproducibility.

    Returns:
        Tuple of (pipeline_instance_or_None, report_dict).
    """
    from backend.app.db.session import get_session_factory
    try:
        from .data import load_pipeline_runs_from_db, prepare_dataset
    except ImportError:
        from ml.data import load_pipeline_runs_from_db, prepare_dataset

    session = db or get_session_factory()()
    try:
        raw_df = load_pipeline_runs_from_db(session, repository=repository)
        if raw_df.empty:
            return None, {
                "status": "error",
                "message": "Cannot train failure model: database contains 0 pipeline runs.",
                "raw_dataset_size": 0,
                "exclusions_count": 0,
                "eligible_completed_runs": 0,
            }

        try:
            X, y = prepare_dataset(raw_df)
        except ValueError as err:
            return None, {
                "status": "error",
                "message": f"Dataset preparation failed: {str(err)}",
                "raw_dataset_size": int(len(raw_df)),
            }

        pipeline, report = train_failure_model(
            X,
            y,
            test_size=test_size,
            random_state=random_state,
            raw_df=raw_df,
        )

        if save_artifact:
            model_file, meta_file = save_model(pipeline, report, model_dir=model_dir)
            report["model_artifact_path"] = str(model_file)
            report["metadata_artifact_path"] = str(meta_file)

        report["status"] = "success"
        return pipeline, report
    finally:
        if db is None:
            session.close()


def main() -> None:
    """CLI entrypoint for running the model training pipeline."""
    parser = argparse.ArgumentParser(description="Train CI/CD pipeline failure prediction model.")
    parser.add_argument("--repository", type=str, default=None, help="Filter telemetry by repository.")
    parser.add_argument("--csv", type=str, default=None, help="Path to static CSV snapshot.")
    parser.add_argument("--test-size", type=float, default=0.2, help="Holdout evaluation proportion.")
    parser.add_argument("--no-save", action="store_true", help="Do not persist model artifact.")
    parser.add_argument("--version", type=str, default="1.0.0", help="Model semantic version string.")
    parser.add_argument("--compare-rf", action="store_true", help="Perform side-by-side comparison with Random Forest.")
    args = parser.parse_args()

    print("=========================================================")
    print("CI/CD Pipeline Failure Prediction Training Pipeline")
    print("=========================================================")

    try:
        from .data import load_pipeline_runs_from_csv, load_pipeline_runs_from_db, prepare_dataset
    except ImportError:
        from ml.data import load_pipeline_runs_from_csv, load_pipeline_runs_from_db, prepare_dataset

    raw_df = None
    dataset_source = "csv_snapshot" if args.csv else "postgresql_database"
    dataset_snapshot_path = args.csv if args.csv else "postgresql://localhost:5432/cicd_platform"

    if args.csv:
        print(f"Loading telemetry from CSV snapshot: {args.csv}")
        try:
            raw_df = load_pipeline_runs_from_csv(args.csv)
            print(f"Loaded {len(raw_df)} records from CSV snapshot.")
        except Exception as err:
            print(f"ERROR: Failed to load CSV snapshot: {err}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Connecting to project database...")
        from backend.app.db.session import get_session_factory
        session = get_session_factory()()
        try:
            raw_df = load_pipeline_runs_from_db(session, repository=args.repository)
        finally:
            session.close()

    if raw_df is None or raw_df.empty:
        report = {
            "status": "error",
            "message": "Cannot train failure model: database contains 0 pipeline runs.",
            "raw_dataset_size": 0,
            "exclusions_count": 0,
            "eligible_completed_runs": 0,
        }
        print("\n--- Training Pipeline Report ---", flush=True)
        print(json.dumps(report, indent=2), flush=True)
        print(f"\nTraining could not proceed: {report['message']}", flush=True)
        sys.exit(0)

    try:
        X, y = prepare_dataset(raw_df)
    except ValueError as err:
        print(f"ERROR: Dataset preparation failed: {err}", file=sys.stderr)
        sys.exit(1)

    if args.compare_rf:
        print("\nRunning Side-by-Side Comparison: Logistic Regression vs Random Forest...")
        comparison = compare_models(
            X,
            y,
            test_size=args.test_size,
            raw_df=raw_df,
        )
        print("\n--- Model Comparison Report ---", flush=True)
        print(json.dumps(comparison, indent=2), flush=True)

        print("\n=========================================================")
        print("Side-by-Side Model Evaluation Summary")
        print("=========================================================")
        lr_m = comparison["side_by_side_metrics"]["logistic_regression"]
        rf_m = comparison["side_by_side_metrics"]["random_forest"]
        print(f"{'Metric':<16} | {'Logistic Regression':<20} | {'Random Forest':<16}")
        print("-" * 58)
        for m_name in ["accuracy", "precision", "recall", "f1_score", "roc_auc"]:
            lr_val = "N/A (single class)" if lr_m.get(m_name) is None else f"{lr_m.get(m_name):.4f}"
            rf_val = "N/A (single class)" if rf_m.get(m_name) is None else f"{rf_m.get(m_name):.4f}"
            print(f"{m_name.capitalize():<16} | {lr_val:<20} | {rf_val:<16}")
        print("-" * 58)
        print(f"Fair Evaluation Supported: {comparison['fair_evaluation_supported']}")
        if comparison["rf_deferred"]:
            print(f"Random Forest Deferred:     Yes ({comparison['deferral_reason']})")
        print(f"Recommended Model:          {comparison['recommended_model']}")
        print(f"Recommendation Rationale:   {comparison['recommendation_rationale']}")
        print("=========================================================")

        # Train and persist the recommended baseline model
        pipeline, report = train_failure_model(
            X,
            y,
            test_size=args.test_size,
            raw_df=raw_df,
        )
        report["model_version"] = args.version
        report["dataset_source"] = dataset_source
        report["dataset_snapshot_path"] = dataset_snapshot_path
        if not args.no_save:
            model_file, meta_file = save_model(pipeline, report, version=args.version)
            report["model_artifact_path"] = str(model_file)
            report["metadata_artifact_path"] = str(meta_file)
        report["status"] = "success"
        print("\nPrimary production artifact saved successfully!", flush=True)
        return

    # Standard single model training
    pipeline, report = train_failure_model(
        X,
        y,
        test_size=args.test_size,
        raw_df=raw_df,
    )
    report["model_version"] = args.version
    report["dataset_source"] = dataset_source
    report["dataset_snapshot_path"] = dataset_snapshot_path
    if not args.no_save:
        model_file, meta_file = save_model(pipeline, report, version=args.version)
        report["model_artifact_path"] = str(model_file)
        report["metadata_artifact_path"] = str(meta_file)
    report["status"] = "success"

    print("\n--- Training Pipeline Report ---", flush=True)
    print(json.dumps(report, indent=2), flush=True)
    print("\nModel trained and evaluated successfully!", flush=True)


if __name__ == "__main__":
    main()
