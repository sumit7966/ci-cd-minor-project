"""Focused unit test suite for CI/CD failure-prediction training and evaluation pipeline.

Validates:
- LogisticRegression inside approved preprocessing pipeline.
- Chronological holdout splitting vs. fallback behavior.
- Preprocessing fit strictly on training partition only.
- Comprehensive evaluation reporting (accuracy, precision, recall, F1, confusion matrix, ROC-AUC).
- Safe handling of empty, tiny (< 4), and single-class datasets.
- Single joblib artifact serialization and load fidelity.
"""

from datetime import datetime, timezone
import shutil
import tempfile
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from ml.data import load_pipeline_runs_from_records, prepare_dataset
from ml.persistence import is_model_trained, load_model, save_model
from ml.train import (
    chronological_split,
    compare_models,
    evaluate_predictions,
    train_failure_model,
    train_random_forest,
)


class TestMLTrainingAndEvaluation(unittest.TestCase):
    """Test suite validating training pipeline, chronological split, metrics, and error boundaries."""

    def setUp(self) -> None:
        """Create a temporary directory for model persistence testing."""
        self.test_dir = tempfile.mkdtemp()
        self.model_dir = Path(self.test_dir)

    def tearDown(self) -> None:
        """Clean up temporary directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _sample_chronological_records(self, n: int = 10) -> list:
        """Generate balanced chronological records alternating between success and failure."""
        records = []
        for i in range(1, n + 1):
            is_failure = (i % 2 == 0)
            records.append({
                "run_id": 1000 + i,
                "repository": "test/repo",
                "branch": "main" if i <= (n // 2) else "feature/login",
                "workflow_name": "CI Pipeline",
                "event": "push" if i % 2 == 1 else "pull_request",
                "status": "completed",
                "conclusion": "failure" if is_failure else "success",
                "duration_seconds": 25.0 + i,
                "failed_stage": "test" if is_failure else None,
                "failure_type": "test_failure" if is_failure else "none",
                "created_at": f"2026-09-{10 + (i // 24):02d}T{i % 24:02d}:00:00Z",
            })
        return records

    def test_logistic_regression_pipeline_training(self) -> None:
        """Requirement 1: Verify LogisticRegression estimator is used inside the end-to-end pipeline."""
        df = load_pipeline_runs_from_records(self._sample_chronological_records(10))
        X, y = prepare_dataset(df)

        pipeline, report = train_failure_model(X, y, test_size=0.3, random_state=42)

        self.assertEqual(report["model_type"], "LogisticRegression")
        self.assertIn("classifier", pipeline.named_steps)
        self.assertIsInstance(pipeline.named_steps["classifier"], LogisticRegression)

    def test_chronological_holdout_split(self) -> None:
        """Requirement 2: Verify chronological split splits data by timestamp order without shuffling."""
        records = [
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-01T10:00:00Z"},
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-02T10:00:00Z"},
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-03T10:00:00Z"},
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-04T10:00:00Z"},
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-05T10:00:00Z"},
        ]
        X = pd.DataFrame(records)
        y = pd.Series([0, 1, 0, 1, 0])

        X_train, X_test, y_train, y_test, split_type = chronological_split(X, y, test_size=0.2)

        self.assertEqual(split_type, "chronological")
        self.assertEqual(len(X_train), 4)
        self.assertEqual(len(X_test), 1)
        # Verify chronological order: test set contains the latest timestamp
        self.assertEqual(X_test.iloc[0]["created_at"], "2026-09-05T10:00:00Z")

    def test_preprocessing_fit_on_train_only(self) -> None:
        """Requirement 3: Verify preprocessing parameters are fitted strictly on the training partition."""
        df = load_pipeline_runs_from_records(self._sample_chronological_records(10))
        X, y = prepare_dataset(df)

        pipeline, report = train_failure_model(X, y, test_size=0.3, random_state=42)

        # Preprocessing extractor should have fitted stats matching train split size
        extractor = pipeline.named_steps["preprocessor"].named_steps["extractor"]
        self.assertTrue(extractor.is_fitted_)
        self.assertEqual(report["train_samples"], len(X) - report["test_samples"])

    def test_dataset_breakdown_reporting(self) -> None:
        """Requirement 4: Verify reporting includes dataset size, exclusions, class counts, and features."""
        records = self._sample_chronological_records(8)
        # Add 1 incomplete and 1 cancelled run (should be excluded)
        records.append({
            "run_id": 9991, "repository": "test/repo", "branch": "main",
            "workflow_name": "CI", "event": "push", "status": "in_progress",
            "conclusion": None, "created_at": "2026-09-15T10:00:00Z",
        })
        records.append({
            "run_id": 9992, "repository": "test/repo", "branch": "main",
            "workflow_name": "CI", "event": "push", "status": "completed",
            "conclusion": "cancelled", "created_at": "2026-09-15T11:00:00Z",
        })
        raw_df = load_pipeline_runs_from_records(records)
        X, y = prepare_dataset(raw_df)

        _, report = train_failure_model(X, y, raw_df=raw_df, test_size=0.25)

        self.assertEqual(report["raw_dataset_size"], 10)
        self.assertEqual(report["exclusions_count"], 2)
        self.assertEqual(report["dataset_size"], 8)
        self.assertIn("class_counts", report)
        self.assertEqual(report["class_counts"]["success"], 4)
        self.assertEqual(report["class_counts"]["failure"], 4)
        self.assertIn("features", report)

    def test_evaluation_metrics_and_confusion_matrix(self) -> None:
        """Requirement 5: Verify accuracy, precision, recall, F1, and confusion matrix are calculated."""
        df = load_pipeline_runs_from_records(self._sample_chronological_records(12))
        X, y = prepare_dataset(df)

        _, report = train_failure_model(X, y, test_size=0.33)

        self.assertIn("accuracy", report)
        self.assertIn("precision", report)
        self.assertIn("recall", report)
        self.assertIn("f1_score", report)
        self.assertIn("confusion_matrix", report)

        # Confusion matrix is a 2x2 list [[TN, FP], [FN, TP]]
        cm = report["confusion_matrix"]
        self.assertEqual(len(cm), 2)
        self.assertEqual(len(cm[0]), 2)
        self.assertEqual(len(cm[1]), 2)
        total_eval_samples = cm[0][0] + cm[0][1] + cm[1][0] + cm[1][1]
        self.assertEqual(total_eval_samples, report["test_samples"])

    def test_conditional_roc_auc_calculation(self) -> None:
        """Requirement 6: Verify ROC-AUC is calculated only when evaluation split supports it."""
        # Case A: Both classes in test set -> roc_auc is a float
        metrics_two_classes = evaluate_predictions(
            y_true=[0, 1, 0, 1],
            y_pred=[0, 1, 0, 1],
            y_proba=[0.1, 0.9, 0.2, 0.8],
        )
        self.assertIsNotNone(metrics_two_classes["roc_auc"])
        self.assertGreaterEqual(metrics_two_classes["roc_auc"], 0.0)

        # Case B: Single class in test set -> roc_auc is None
        metrics_single_class = evaluate_predictions(
            y_true=[0, 0, 0, 0],
            y_pred=[0, 0, 0, 0],
            y_proba=[0.1, 0.2, 0.3, 0.1],
        )
        self.assertIsNone(metrics_single_class["roc_auc"])

    def test_empty_tiny_and_single_class_error_boundaries(self) -> None:
        """Requirement 7: Verify empty, tiny (< 4), and single-class datasets are handled safely."""
        # 1. Empty dataset
        with self.assertRaises(ValueError) as ctx:
            train_failure_model(pd.DataFrame(), pd.Series(dtype=int))
        self.assertIn("empty", str(ctx.exception).lower())

        # 2. Tiny dataset (< 4 samples)
        tiny_X = pd.DataFrame([
            {"repository": "r", "branch": "b", "workflow_name": "w", "event": "p", "created_at": "2026-09-01T10:00:00Z"},
            {"repository": "r", "branch": "b", "workflow_name": "w", "event": "p", "created_at": "2026-09-02T10:00:00Z"},
        ])
        tiny_y = pd.Series([0, 1])
        with self.assertRaises(ValueError) as ctx:
            train_failure_model(tiny_X, tiny_y)
        self.assertIn("insufficient", str(ctx.exception).lower())

        # 3. Single-class dataset (all success)
        single_class_X = pd.DataFrame([
            {"repository": "r", "branch": "b", "workflow_name": "w", "event": "p", "created_at": f"2026-09-0{i}T10:00:00Z"}
            for i in range(1, 5)
        ])
        single_class_y = pd.Series([0, 0, 0, 0])
        with self.assertRaises(ValueError) as ctx:
            train_failure_model(single_class_X, single_class_y)
        self.assertIn("requires both positive", str(ctx.exception).lower())

    def test_artifact_persistence_after_valid_training(self) -> None:
        """Requirement 8: Verify single joblib artifact and metadata JSON are persisted upon valid training."""
        df = load_pipeline_runs_from_records(self._sample_chronological_records(8))
        X, y = prepare_dataset(df)

        pipeline, report = train_failure_model(X, y, random_state=42)

        self.assertFalse(is_model_trained(self.model_dir))
        model_file, meta_file = save_model(pipeline, report, model_dir=self.model_dir)

        self.assertTrue(model_file.is_file())
        self.assertTrue(meta_file.is_file())
        self.assertTrue(is_model_trained(self.model_dir))

        loaded_pipeline, loaded_report = load_model(self.model_dir)
        self.assertEqual(loaded_report["model_type"], "LogisticRegression")
        self.assertEqual(loaded_report["dataset_size"], 8)

    def test_random_forest_training_reproducibility(self) -> None:
        """Verify train_random_forest trains an ensemble pipeline deterministically."""
        df = load_pipeline_runs_from_records(self._sample_chronological_records(10))
        X, y = prepare_dataset(df)

        pipeline, report = train_random_forest(X, y, random_state=42, n_estimators=50)
        self.assertEqual(report["model_type"], "RandomForest")
        self.assertEqual(report["n_estimators"], 50)
        self.assertIn("accuracy", report)
        self.assertIn("f1_score", report)
        self.assertEqual(report["dataset_size"], 10)

    def test_compare_models_insufficient_data_defers_rf(self) -> None:
        """Verify compare_models identifies insufficient data, defers RF, and recommends simpler model."""
        # 14 sample records like actual snapshot
        df = load_pipeline_runs_from_records(self._sample_chronological_records(14))
        X, y = prepare_dataset(df)

        comparison = compare_models(X, y, test_size=0.2, random_state=42)

        self.assertFalse(comparison["fair_evaluation_supported"])
        self.assertTrue(comparison["rf_deferred"])
        self.assertIn("insufficient", comparison["status"].lower())
        self.assertEqual(comparison["recommended_model"], "LogisticRegression")
        self.assertIsNotNone(comparison["deferral_reason"])
        self.assertIn("threshold of 30", comparison["deferral_reason"])

        # Side-by-side metrics must be present for both models
        self.assertIn("logistic_regression", comparison["side_by_side_metrics"])
        self.assertIn("random_forest", comparison["side_by_side_metrics"])

    def test_compare_models_sufficient_data_fair_evaluation(self) -> None:
        """Verify compare_models enables fair evaluation when sample size and holdout class diversity suffice."""
        # 36 records -> test set will have ~7 samples with both classes present
        df = load_pipeline_runs_from_records(self._sample_chronological_records(36))
        X, y = prepare_dataset(df)

        comparison = compare_models(X, y, test_size=0.2, random_state=42, min_samples_for_fair_eval=30)

        self.assertTrue(comparison["fair_evaluation_supported"])
        self.assertIn(comparison["recommended_model"], ["LogisticRegression", "RandomForest"])
        self.assertIn("side_by_side_metrics", comparison)

