"""Comprehensive unit test suite for Phase 10: Model Persistence, Versioning, and Integrity."""

import os
from pathlib import Path
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

from ml.data import load_pipeline_runs_from_records, prepare_dataset
from ml.persistence import (
    compute_file_sha256,
    get_model_metadata,
    get_model_paths,
    is_model_trained,
    list_model_versions,
    load_model,
    resolve_model_dir,
    save_model,
)
from ml.train import train_failure_model


class TestMLPersistenceAndVersioning(unittest.TestCase):
    """Test suite validating model persistence, versioning, integrity, and safeguards."""

    def setUp(self) -> None:
        """Create an isolated temporary directory for testing."""
        self.test_dir = tempfile.mkdtemp()
        self.model_dir = Path(self.test_dir).resolve()

    def tearDown(self) -> None:
        """Clean up isolated directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _sample_records(self, n: int = 10) -> list:
        """Generate sample records alternating outcomes."""
        records = []
        for i in range(1, n + 1):
            is_failure = (i % 2 == 0)
            records.append({
                "run_id": 2000 + i,
                "repository": "sumit7966/ci-cd-minor-project",
                "branch": "main" if i <= 5 else "feature/test",
                "workflow_name": "CI Pipeline",
                "event": "push" if i % 2 == 1 else "workflow_dispatch",
                "status": "completed",
                "conclusion": "failure" if is_failure else "success",
                "duration_seconds": 20.0 + i,
                "failed_stage": "test" if is_failure else None,
                "failure_type": "test_failure" if is_failure else "none",
                "created_at": f"2026-09-09T{i:02d}:00:00Z",
            })
        return records

    def test_save_and_load_active_roundtrip(self) -> None:
        """Requirement 1, 3, 5: Verify saving and loading active artifact with complete metadata."""
        df = load_pipeline_runs_from_records(self._sample_records(10))
        X, y = prepare_dataset(df)

        pipeline, report = train_failure_model(X, y, random_state=42)
        report["dataset_snapshot_path"] = "data/test_snapshot.csv"

        self.assertFalse(is_model_trained(self.model_dir))
        model_path, meta_path = save_model(
            pipeline,
            report,
            model_dir=self.model_dir,
            version="1.0.0",
        )

        self.assertTrue(model_path.is_file())
        self.assertTrue(meta_path.is_file())
        self.assertTrue(is_model_trained(self.model_dir))

        loaded_pipeline, loaded_metadata = load_model(self.model_dir)

        # Verify truthful metadata attributes
        self.assertEqual(loaded_metadata["model_version"], "1.0.0")
        self.assertEqual(loaded_metadata["model_type"], "LogisticRegression")
        self.assertEqual(loaded_metadata["dataset_size"], 10)
        self.assertEqual(loaded_metadata["train_samples"], 8)
        self.assertEqual(loaded_metadata["test_samples"], 2)
        self.assertIn("features", loaded_metadata)
        self.assertEqual(loaded_metadata["dataset_snapshot_path"], "data/test_snapshot.csv")
        self.assertIn("artifact_sha256", loaded_metadata)
        self.assertEqual(len(loaded_metadata["artifact_sha256"]), 64)

        # Verify evaluation metrics
        self.assertIn("evaluation_metrics", loaded_metadata)
        self.assertIn("accuracy", loaded_metadata["evaluation_metrics"])
        self.assertIn("f1_score", loaded_metadata["evaluation_metrics"])

    def test_prediction_consistency_before_and_after_loading(self) -> None:
        """Requirement 10: Verify predictions and probabilities match before and after persistence."""
        df = load_pipeline_runs_from_records(self._sample_records(12))
        X, y = prepare_dataset(df)

        pipeline, report = train_failure_model(X, y, random_state=42)
        save_model(pipeline, report, model_dir=self.model_dir)

        loaded_pipeline, _ = load_model(self.model_dir)

        # Check prediction probabilities on multiple rows
        orig_preds = pipeline.predict(X)
        loaded_preds = loaded_pipeline.predict(X)
        np.testing.assert_array_equal(orig_preds, loaded_preds)

        orig_probas = pipeline.predict_proba(X)
        loaded_probas = loaded_pipeline.predict_proba(X)
        np.testing.assert_allclose(orig_probas, loaded_probas, rtol=1e-5, atol=1e-5)

    def test_versioned_archive_and_retrieval(self) -> None:
        """Requirement 5, 10: Verify multi-version archiving and loading specific historical versions."""
        df = load_pipeline_runs_from_records(self._sample_records(10))
        X, y = prepare_dataset(df)

        # Save Version 1.0.0
        pipeline_v1, report_v1 = train_failure_model(X, y, random_state=42)
        save_model(pipeline_v1, report_v1, model_dir=self.model_dir, version="1.0.0")

        # Save Version 2.0.0
        pipeline_v2, report_v2 = train_failure_model(X, y, random_state=123)
        save_model(pipeline_v2, report_v2, model_dir=self.model_dir, version="2.0.0")

        versions = list_model_versions(self.model_dir)
        self.assertIn("1.0.0", versions)
        self.assertIn("2.0.0", versions)

        # Load active model (should be latest 2.0.0)
        _, active_meta = load_model(self.model_dir)
        self.assertEqual(active_meta["model_version"], "2.0.0")

        # Load historical v1.0.0
        _, v1_meta = load_model(self.model_dir, version="1.0.0")
        self.assertEqual(v1_meta["model_version"], "1.0.0")

        # Load historical v2.0.0
        _, v2_meta = load_model(self.model_dir, version="2.0.0")
        self.assertEqual(v2_meta["model_version"], "2.0.0")

    def test_missing_artifacts_raise_clear_filenotfound(self) -> None:
        """Requirement 8, 10: Verify missing artifact raises FileNotFoundError with clear message."""
        empty_dir = self.model_dir / "non_existent"
        self.assertFalse(is_model_trained(empty_dir))

        with self.assertRaises(FileNotFoundError) as ctx:
            load_model(empty_dir)
        self.assertIn("Trained model artifact not found", str(ctx.exception))

        # Check missing specific version
        with self.assertRaises(FileNotFoundError) as ctx_v:
            load_model(self.model_dir, version="9.9.9")
        self.assertIn("Model version '9.9.9' not found", str(ctx_v.exception))

    def test_unfitted_or_none_pipeline_rejected(self) -> None:
        """Requirement 4: Verify None or unfitted pipeline cannot be persisted."""
        with self.assertRaises(ValueError) as ctx_none:
            save_model(None, {}, model_dir=self.model_dir)
        self.assertIn("pipeline instance is None", str(ctx_none.exception))

        class DummyUnfitted:
            pass

        with self.assertRaises(ValueError) as ctx_unfitted:
            save_model(DummyUnfitted(), {}, model_dir=self.model_dir)
        self.assertIn("must provide predict()", str(ctx_unfitted.exception))

    def test_error_metadata_status_rejected(self) -> None:
        """Requirement 4: Verify error status in metadata is rejected to prevent overwriting valid models."""
        df = load_pipeline_runs_from_records(self._sample_records(8))
        X, y = prepare_dataset(df)
        pipeline, _ = train_failure_model(X, y)

        error_meta = {
            "status": "error",
            "message": "Cannot train failure model: database contains 0 pipeline runs.",
            "train_samples": 0,
        }
        with self.assertRaises(ValueError) as ctx:
            save_model(pipeline, error_meta, model_dir=self.model_dir)
        self.assertIn("unsuccessful training run", str(ctx.exception))

    def test_sha256_checksum_verification(self) -> None:
        """Requirement 5: Verify SHA-256 digest accurately matches physical artifact file."""
        df = load_pipeline_runs_from_records(self._sample_records(8))
        X, y = prepare_dataset(df)
        pipeline, report = train_failure_model(X, y)

        model_path, meta_path = save_model(pipeline, report, model_dir=self.model_dir)
        calculated_sha = compute_file_sha256(model_path)

        meta = get_model_metadata(self.model_dir)
        self.assertEqual(meta["artifact_sha256"], calculated_sha)

    def test_robust_path_resolution(self) -> None:
        """Requirement 7: Verify path resolution is independent of working directory."""
        # Custom string path
        custom_p = resolve_model_dir(str(self.model_dir))
        self.assertTrue(custom_p.is_absolute())
        self.assertEqual(custom_p, self.model_dir)

        # Via environment variable
        os.environ["ML_MODEL_DIR"] = str(self.model_dir)
        try:
            env_resolved = resolve_model_dir(None)
            self.assertEqual(env_resolved, self.model_dir)
        finally:
            del os.environ["ML_MODEL_DIR"]


if __name__ == "__main__":
    unittest.main()
