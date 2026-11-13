"""Unit tests for Member 2 ML failure prediction module."""

from datetime import datetime, timezone
import shutil
import tempfile
import unittest
from pathlib import Path
import pandas as pd

from ml import (
    LEAKAGE_COLUMNS,
    PRE_RUN_FEATURE_COLUMNS,
    PipelineFailurePredictor,
    build_preprocessor,
    is_model_trained,
    load_model,
    load_pipeline_runs_from_records,
    prepare_dataset,
    save_model,
    train_failure_model,
)
from ml.predictor import ModelNotTrainedError


class TestMLModuleStructure(unittest.TestCase):
    """Test suite validating ML module architecture, leakage prevention, training, and inference."""

    def setUp(self) -> None:
        """Create a temporary directory for isolated model persistence testing."""
        self.test_dir = tempfile.mkdtemp()
        self.model_dir = Path(self.test_dir)

    def tearDown(self) -> None:
        """Clean up temporary directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_sample_records(self):
        """Generate a balanced set of sample telemetry records for testing."""
        return [
            {
                "run_id": 1,
                "repository": "test/repo",
                "branch": "main",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "duration_seconds": 45.0,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-10T10:00:00Z",
            },
            {
                "run_id": 2,
                "repository": "test/repo",
                "branch": "main",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "duration_seconds": 50.0,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-10T12:00:00Z",
            },
            {
                "run_id": 3,
                "repository": "test/repo",
                "branch": "feature/login",
                "workflow_name": "CI Pipeline",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "failure",
                "duration_seconds": 15.0,
                "failed_stage": "test",
                "failure_type": "test_failure",
                "created_at": "2026-09-11T14:00:00Z",
            },
            {
                "run_id": 4,
                "repository": "test/repo",
                "branch": "feature/login",
                "workflow_name": "CI Pipeline",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "failure",
                "duration_seconds": 12.0,
                "failed_stage": "build",
                "failure_type": "build_failure",
                "created_at": "2026-09-11T16:00:00Z",
            },
            {
                "run_id": 5,
                "repository": "test/repo",
                "branch": "main",
                "workflow_name": "Release",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "duration_seconds": 80.0,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-12T09:00:00Z",
            },
            {
                "run_id": 6,
                "repository": "test/repo",
                "branch": "feature/api",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "status": "completed",
                "conclusion": "failure",
                "duration_seconds": 22.0,
                "failed_stage": "dependency",
                "failure_type": "dependency_failure",
                "created_at": "2026-09-12T11:00:00Z",
            },
        ]

    def test_package_exports(self) -> None:
        """Verify all core functions and classes are importable from ml root."""
        self.assertTrue(callable(prepare_dataset))
        self.assertTrue(callable(train_failure_model))
        self.assertTrue(callable(save_model))
        self.assertTrue(callable(load_model))
        self.assertTrue(callable(build_preprocessor))
        self.assertTrue(hasattr(PipelineFailurePredictor, "predict"))

    def test_target_leakage_safeguards(self) -> None:
        """Verify target leakage fields are strictly excluded from the feature matrix X."""
        records = self._create_sample_records()
        df = load_pipeline_runs_from_records(records)
        X, y = prepare_dataset(df)

        # X must contain only pre-run features
        for leakage_col in LEAKAGE_COLUMNS:
            self.assertNotIn(
                leakage_col,
                X.columns,
                f"Target leakage violation! Found '{leakage_col}' in feature matrix X.",
            )

        # y must be a binary indicator
        self.assertEqual(len(y), len(X))
        self.assertEqual(set(y.unique()), {0, 1})
        self.assertEqual(y.sum(), 3)  # 3 failures in 6 records

    def test_preprocessor_transformation(self) -> None:
        """Verify ColumnTransformer handles categoricals and temporal feature extraction."""
        records = self._create_sample_records()
        df = load_pipeline_runs_from_records(records)
        X, _ = prepare_dataset(df)

        preprocessor = build_preprocessor()
        transformed = preprocessor.fit_transform(X)

        self.assertGreater(transformed.shape[0], 0)
        self.assertGreater(transformed.shape[1], 0)
        # Ensure no NaN or infinite values
        self.assertFalse(pd.isna(transformed).any())

    def test_training_and_evaluation(self) -> None:
        """Verify training produces a scikit-learn Pipeline and valid evaluation metrics."""
        records = self._create_sample_records()
        df = load_pipeline_runs_from_records(records)
        X, y = prepare_dataset(df)

        pipeline, metrics = train_failure_model(X, y, test_size=0.33, random_state=42)

        self.assertIsNotNone(pipeline)
        self.assertIn("accuracy", metrics)
        self.assertIn("precision", metrics)
        self.assertIn("recall", metrics)
        self.assertIn("f1_score", metrics)
        self.assertEqual(metrics["model_type"], "LogisticRegression")
        self.assertGreaterEqual(metrics["train_samples"], 1)

    def test_persistence_roundtrip(self) -> None:
        """Verify model artifacts can be saved and reloaded with identical predictions."""
        records = self._create_sample_records()
        df = load_pipeline_runs_from_records(records)
        X, y = prepare_dataset(df)

        pipeline, metrics = train_failure_model(X, y, random_state=42)

        self.assertFalse(is_model_trained(self.model_dir))
        model_file, meta_file = save_model(pipeline, metrics, model_dir=self.model_dir)

        self.assertTrue(model_file.is_file())
        self.assertTrue(meta_file.is_file())
        self.assertTrue(is_model_trained(self.model_dir))

        loaded_pipeline, loaded_metrics = load_model(self.model_dir)
        self.assertEqual(loaded_metrics["model_type"], "LogisticRegression")

        # Compare inference consistency
        sample = X.iloc[[0]]
        orig_prob = pipeline.predict_proba(sample)[0][1]
        loaded_prob = loaded_pipeline.predict_proba(sample)[0][1]
        self.assertAlmostEqual(orig_prob, loaded_prob, places=5)

    def test_predictor_uninitialized_behavior(self) -> None:
        """Verify predictor raises ModelNotTrainedError when no model has been trained."""
        empty_dir = self.model_dir / "empty"
        empty_dir.mkdir()
        predictor = PipelineFailurePredictor(model_dir=empty_dir, auto_load=True)

        self.assertFalse(predictor.is_ready())
        with self.assertRaises(ModelNotTrainedError):
            predictor.predict({
                "repository": "test/repo",
                "branch": "main",
                "workflow_name": "CI Pipeline",
                "event": "push",
            })

    def test_predictor_inference(self) -> None:
        """Verify predictor output schema, probability range, and risk categorization."""
        records = self._create_sample_records()
        df = load_pipeline_runs_from_records(records)
        X, y = prepare_dataset(df)

        pipeline, metrics = train_failure_model(X, y, random_state=42)
        save_model(pipeline, metrics, model_dir=self.model_dir)

        predictor = PipelineFailurePredictor(model_dir=self.model_dir)
        self.assertTrue(predictor.is_ready())

        result = predictor.predict({
            "repository": "test/repo",
            "branch": "feature/login",
            "workflow_name": "CI Pipeline",
            "event": "pull_request",
        })

        self.assertIn("is_failure_predicted", result)
        self.assertIn("failure_probability", result)
        self.assertIn("risk_level", result)
        self.assertIn("confidence_score", result)
        self.assertIn(result["risk_level"], ["low", "medium", "high"])
        self.assertGreaterEqual(result["failure_probability"], 0.0)
        self.assertLessEqual(result["failure_probability"], 1.0)
