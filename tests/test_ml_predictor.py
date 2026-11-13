"""Comprehensive unit test suite for Member 2 Pipeline Failure Predictor Service."""

from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

from ml.data import load_pipeline_runs_from_records, prepare_dataset
from ml.persistence import save_model
from ml.predictor import (
    InvalidPredictionInputError,
    ModelNotTrainedError,
    ModelUnavailableError,
    PipelineFailurePredictor,
    get_predictor,
)
from ml.train import train_failure_model


class TestMLPredictorService(unittest.TestCase):
    """Test suite validating inference, schema enforcement, input validation, and error boundaries."""

    @classmethod
    def setUpClass(cls) -> None:
        """Create a shared trained model artifact in a temporary directory."""
        cls.test_dir = tempfile.mkdtemp()
        cls.model_dir = Path(cls.test_dir).resolve()

        # Build balanced sample dataset for testing
        records = []
        for i in range(1, 15):
            is_fail = (i % 2 == 0)
            records.append({
                "run_id": 5000 + i,
                "repository": "sumit7966/ci-cd-minor-project",
                "branch": "main" if i <= 7 else "member1",
                "workflow_name": "CI Pipeline",
                "event": "push" if i % 2 == 1 else "workflow_dispatch",
                "status": "completed",
                "conclusion": "failure" if is_fail else "success",
                "duration_seconds": 20.0 + i,
                "failed_stage": "test" if is_fail else None,
                "failure_type": "test_failure" if is_fail else "none",
                "created_at": f"2026-09-09T{i:02d}:00:00Z",
            })

        df = load_pipeline_runs_from_records(records)
        X, y = prepare_dataset(df)
        pipeline, report = train_failure_model(X, y, random_state=42)
        save_model(pipeline, report, model_dir=cls.model_dir, version="1.0.0")

    @classmethod
    def tearDownClass(cls) -> None:
        """Clean up shared temporary directory."""
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def setUp(self) -> None:
        """Instantiate predictor for each test."""
        self.predictor = PipelineFailurePredictor(model_dir=self.model_dir)

    def test_predictor_loaded_and_ready(self) -> None:
        """Requirement 1: Verify predictor initializes, loads model, and reports ready state."""
        self.assertTrue(self.predictor.is_ready())
        self.assertEqual(self.predictor.load_count, 1)

    def test_successful_prediction_output_schema(self) -> None:
        """Requirement 5, 6: Verify prediction produces full valid schema with bounded probability."""
        sample = {
            "repository": "sumit7966/ci-cd-minor-project",
            "branch": "member1",
            "workflow_name": "CI Pipeline",
            "event": "push",
            "created_at": "2026-09-09T15:00:00Z",
        }

        result = self.predictor.predict(sample)

        self.assertEqual(result["status"], "success")
        self.assertIn(result["predicted_class"], [0, 1])
        self.assertIsInstance(result["is_failure_predicted"], bool)
        self.assertEqual(result["is_failure_predicted"], bool(result["predicted_class"] == 1))

        # Check numeric probability
        prob = result["failure_probability"]
        self.assertIsInstance(prob, float)
        self.assertGreaterEqual(prob, 0.0)
        self.assertLessEqual(prob, 1.0)

        # Check success probability sum
        succ_prob = result["success_probability"]
        self.assertAlmostEqual(prob + succ_prob, 1.0, places=3)

        # Check risk level
        self.assertIn(result["risk_level"], ["low", "medium", "high"])
        self.assertGreaterEqual(result["confidence_score"], 0.0)
        self.assertLessEqual(result["confidence_score"], 1.0)

        # Check metadata inclusion
        self.assertEqual(result["model_metadata"]["model_type"], "LogisticRegression")
        self.assertEqual(result["model_metadata"]["model_version"], "1.0.0")

    def test_invalid_input_types_rejected(self) -> None:
        """Requirement 2: Verify non-dict, non-DataFrame, empty, and None inputs raise InvalidPredictionInputError."""
        with self.assertRaises(InvalidPredictionInputError):
            self.predictor.predict(None)

        with self.assertRaises(InvalidPredictionInputError):
            self.predictor.predict({})

        with self.assertRaises(InvalidPredictionInputError):
            self.predictor.predict("not-a-dictionary")

        with self.assertRaises(InvalidPredictionInputError):
            self.predictor.predict(pd.DataFrame())

    def test_missing_fields_and_type_coercion(self) -> None:
        """Requirement 7: Verify missing fields and missing/invalid timestamps are handled safely."""
        # Telemetry with only repository provided
        sparse_sample = {
            "repository": "sumit7966/ci-cd-minor-project",
        }

        result = self.predictor.predict(sparse_sample)
        self.assertEqual(result["status"], "success")
        self.assertGreaterEqual(result["failure_probability"], 0.0)
        self.assertLessEqual(result["failure_probability"], 1.0)

        # Formatted inputs must have coerced missing categoricals to 'unknown'
        df = self.predictor.validate_and_format_input(sparse_sample)
        self.assertEqual(df.iloc[0]["branch"], "unknown")
        self.assertEqual(df.iloc[0]["workflow_name"], "unknown")
        self.assertEqual(df.iloc[0]["event"], "unknown")
        self.assertIsInstance(df.iloc[0]["created_at"], datetime)

        # Malformed date string should default gracefully to current UTC
        bad_date_sample = {
            "repository": "sumit7966/ci-cd-minor-project",
            "created_at": "invalid-date-string-123",
        }
        df_bad = self.predictor.validate_and_format_input(bad_date_sample)
        self.assertIsInstance(df_bad.iloc[0]["created_at"], datetime)

    def test_unseen_categories_handled_gracefully(self) -> None:
        """Requirement 7: Verify new/unseen branches, repositories, or workflows do not cause errors."""
        unseen_sample = {
            "repository": "unknown-org/brand-new-repo",
            "branch": "feature/unseen-neural-model",
            "workflow_name": "Never Before Seen Workflow",
            "event": "cron_schedule",
            "created_at": "2026-10-10T12:00:00Z",
        }

        result = self.predictor.predict(unseen_sample)
        self.assertEqual(result["status"], "success")
        self.assertGreaterEqual(result["failure_probability"], 0.0)
        self.assertLessEqual(result["failure_probability"], 1.0)

    def test_target_leakage_columns_are_stripped(self) -> None:
        """Requirement 3: Verify post-execution target columns are stripped to prevent data leakage."""
        leaky_sample = {
            "repository": "sumit7966/ci-cd-minor-project",
            "branch": "main",
            "workflow_name": "CI Pipeline",
            "event": "push",
            # Post-execution outcome columns that must be stripped
            "status": "completed",
            "conclusion": "failure",
            "failed_stage": "test",
            "failure_type": "test_failure",
            "duration_seconds": 150,
        }

        df = self.predictor.validate_and_format_input(leaky_sample)
        self.assertNotIn("status", df.columns)
        self.assertNotIn("conclusion", df.columns)
        self.assertNotIn("failed_stage", df.columns)
        self.assertNotIn("failure_type", df.columns)
        self.assertNotIn("duration_seconds", df.columns)

    def test_missing_model_raises_model_unavailable_error(self) -> None:
        """Requirement 8: Verify uninitialized predictor raises clear ModelUnavailableError."""
        empty_temp = tempfile.mkdtemp()
        try:
            predictor_empty = PipelineFailurePredictor(model_dir=empty_temp, auto_load=False)
            self.assertFalse(predictor_empty.is_ready())

            with self.assertRaises(ModelUnavailableError) as ctx:
                predictor_empty.predict({"repository": "test/repo"})
            self.assertIn("no trained model is currently loaded", str(ctx.exception))

            # Also verify ModelNotTrainedError alias works identically
            with self.assertRaises(ModelNotTrainedError):
                predictor_empty.predict_proba({"repository": "test/repo"})
        finally:
            shutil.rmtree(empty_temp, ignore_errors=True)

    def test_model_loaded_once_and_reused_across_requests(self) -> None:
        """Requirement 1, 9: Verify artifact is loaded once in memory and reused across repeated calls."""
        initial_load_count = self.predictor.load_count
        self.assertEqual(initial_load_count, 1)

        sample = {
            "repository": "sumit7966/ci-cd-minor-project",
            "branch": "main",
            "workflow_name": "CI Pipeline",
            "event": "push",
        }

        # Execute 5 repeated predictions
        for _ in range(5):
            res = self.predictor.predict(sample)
            self.assertEqual(res["status"], "success")

        # Load count must strictly remain 1 (no re-reading from disk per request)
        self.assertEqual(self.predictor.load_count, 1)

    def test_predict_safe_graceful_error_handling(self) -> None:
        """Requirement 8: Verify predict_safe returns clean status dictionaries instead of raising."""
        # 1. Invalid input
        res_invalid = self.predictor.predict_safe(None)
        self.assertEqual(res_invalid["status"], "invalid_input")
        self.assertIsNone(res_invalid["failure_probability"])

        # 2. Model unavailable
        empty_temp = tempfile.mkdtemp()
        try:
            predictor_empty = PipelineFailurePredictor(model_dir=empty_temp, auto_load=False)
            res_unavail = predictor_empty.predict_safe({"repository": "test/repo"})
            self.assertEqual(res_unavail["status"], "model_unavailable")
            self.assertIsNone(res_unavail["failure_probability"])
        finally:
            shutil.rmtree(empty_temp, ignore_errors=True)

    def test_singleton_get_predictor(self) -> None:
        """Requirement 1: Verify get_predictor returns the shared singleton instance."""
        p1 = get_predictor(model_dir=self.model_dir)
        p2 = get_predictor(model_dir=self.model_dir)
        self.assertIs(p1, p2)


if __name__ == "__main__":
    unittest.main()
