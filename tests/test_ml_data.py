"""Focused unit tests for Member 2 ML Data-Access Milestone.

Validates data loading, deduplication, handling of malformed records and invalid timestamps,
completed run extraction, exclusion of unfinished/ambiguous runs, and CSV snapshot export/load.
"""

from datetime import datetime, timezone
import os
import shutil
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.data_collection.schemas import PipelineRunRecord
from backend.app.db.models import PipelineRunModel
from backend.app.db.repository import upsert_pipeline_runs
from backend.app.db.session import Base
from ml.data import (
    CANONICAL_COLUMNS,
    clean_pipeline_runs_dataframe,
    export_pipeline_runs_to_csv,
    extract_completed_runs,
    load_pipeline_runs_from_csv,
    load_pipeline_runs_from_db,
    load_pipeline_runs_from_records,
    prepare_dataset,
)
from ml.features import LEAKAGE_COLUMNS, PRE_RUN_FEATURE_COLUMNS


class TestMLDataAccessMilestone(unittest.TestCase):
    """Test suite validating data-access layer, validation rules, and leakage safeguards."""

    def setUp(self) -> None:
        """Create temporary directory for CSV snapshots and isolated SQLite in-memory DB."""
        self.test_dir = tempfile.mkdtemp()
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        """Clean up database session and temporary directory."""
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _sample_records(self):
        """Generate representative pipeline run records with diverse statuses and conclusions."""
        return [
            # 1. Completed success run
            {
                "run_id": 101,
                "repository": "sumit7966/ci-cd-minor-project",
                "branch": "main",
                "commit_sha": "a1b2c3d4e5f60123456789012345678901234567",
                "status": "completed",
                "conclusion": "success",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "duration_seconds": 45.2,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-09T10:00:00Z",
                "synced_at": "2026-09-09T10:05:00Z",
            },
            # 2. Completed failure run
            {
                "run_id": 102,
                "repository": "sumit7966/ci-cd-minor-project",
                "branch": "feature/login",
                "commit_sha": "b2c3d4e5f6a10123456789012345678901234567",
                "status": "completed",
                "conclusion": "failure",
                "workflow_name": "CI Pipeline",
                "event": "pull_request",
                "duration_seconds": 18.5,
                "failed_stage": "test",
                "failure_type": "test_failure",
                "created_at": "2026-09-09T11:00:00Z",
                "synced_at": "2026-09-09T11:05:00Z",
            },
            # 3. Unfinished run (in_progress)
            {
                "run_id": 103,
                "repository": "sumit7966/ci-cd-minor-project",
                "branch": "main",
                "commit_sha": "c3d4e5f6a1b20123456789012345678901234567",
                "status": "in_progress",
                "conclusion": None,
                "workflow_name": "CI Pipeline",
                "event": "push",
                "duration_seconds": None,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-09T12:00:00Z",
                "synced_at": "2026-09-09T12:01:00Z",
            },
            # 4. Unfinished run (queued)
            {
                "run_id": 104,
                "repository": "sumit7966/ci-cd-minor-project",
                "branch": "feature/api",
                "commit_sha": "d4e5f6a1b2c30123456789012345678901234567",
                "status": "queued",
                "conclusion": None,
                "workflow_name": "CI Pipeline",
                "event": "push",
                "duration_seconds": None,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-09T13:00:00Z",
                "synced_at": "2026-09-09T13:01:00Z",
            },
            # 5. Completed cancelled run (ambiguous outcome)
            {
                "run_id": 105,
                "repository": "sumit7966/ci-cd-minor-project",
                "branch": "feature/ui",
                "commit_sha": "e5f6a1b2c3d40123456789012345678901234567",
                "status": "completed",
                "conclusion": "cancelled",
                "workflow_name": "CI Pipeline",
                "event": "pull_request",
                "duration_seconds": 10.0,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-09T14:00:00Z",
                "synced_at": "2026-09-09T14:05:00Z",
            },
            # 6. Completed skipped run (ambiguous outcome)
            {
                "run_id": 106,
                "repository": "sumit7966/ci-cd-minor-project",
                "branch": "docs/update",
                "commit_sha": "f6a1b2c3d4e50123456789012345678901234567",
                "status": "completed",
                "conclusion": "skipped",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "duration_seconds": 2.0,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-09T15:00:00Z",
                "synced_at": "2026-09-09T15:05:00Z",
            },
        ]

    def test_load_from_db(self) -> None:
        """Verify load_pipeline_runs_from_db returns a clean DataFrame using active DB session."""
        records = [
            PipelineRunRecord(
                run_id=r["run_id"],
                repository=r["repository"],
                branch=r["branch"],
                commit_sha=r["commit_sha"],
                status=r["status"],
                conclusion=r["conclusion"],
                workflow_name=r["workflow_name"],
                event=r["event"],
                duration_seconds=r["duration_seconds"],
                failed_stage=r["failed_stage"],
                failure_type=r["failure_type"],
                created_at=datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")),
            )
            for r in self._sample_records()
        ]
        upsert_pipeline_runs(self.db, records)

        df = load_pipeline_runs_from_db(self.db)
        self.assertEqual(len(df), 6)
        self.assertIn("run_id", df.columns)
        self.assertIn("repository", df.columns)
        self.assertIn("status", df.columns)
        self.assertIn("conclusion", df.columns)

    def test_deduplication_of_run_ids(self) -> None:
        """Verify duplicate run_ids are deduplicated, keeping the latest synced/created record."""
        duplicate_records = [
            {
                "run_id": 200,
                "repository": "test/repo",
                "branch": "main",
                "status": "in_progress",
                "conclusion": None,
                "workflow_name": "CI Pipeline",
                "event": "push",
                "created_at": "2026-09-10T10:00:00Z",
                "synced_at": "2026-09-10T10:01:00Z",
            },
            # Updated version of same run
            {
                "run_id": 200,
                "repository": "test/repo",
                "branch": "main",
                "status": "completed",
                "conclusion": "success",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "created_at": "2026-09-10T10:00:00Z",
                "synced_at": "2026-09-10T10:05:00Z",
            },
        ]
        df = load_pipeline_runs_from_records(duplicate_records)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["run_id"], 200)
        self.assertEqual(df.iloc[0]["status"], "completed")
        self.assertEqual(df.iloc[0]["conclusion"], "success")

    def test_exclude_unfinished_and_ambiguous_runs(self) -> None:
        """Verify completed runs extraction retains only success/failure runs and excludes unfinished/cancelled/skipped."""
        raw_df = load_pipeline_runs_from_records(self._sample_records())
        self.assertEqual(len(raw_df), 6)

        completed_df = extract_completed_runs(raw_df)
        # Should only contain run_id 101 ('success') and 102 ('failure')
        self.assertEqual(len(completed_df), 2)
        self.assertEqual(set(completed_df["run_id"]), {101, 102})
        self.assertEqual(set(completed_df["conclusion"]), {"success", "failure"})
        self.assertTrue((completed_df["status"] == "completed").all())

    def test_prepare_dataset_binary_labels_and_leakage(self) -> None:
        """Verify prepare_dataset produces binary 0/1 target and strictly excludes leakage columns from X."""
        raw_df = load_pipeline_runs_from_records(self._sample_records())
        X, y = prepare_dataset(raw_df)

        # Only 2 completed runs (101=success -> 0, 102=failure -> 1)
        self.assertEqual(len(X), 2)
        self.assertEqual(len(y), 2)
        self.assertEqual(list(y), [0, 1])

        # Verify no leakage column is in X
        for col in LEAKAGE_COLUMNS:
            self.assertNotIn(col, X.columns)

        # Verify only pre-run features in X
        for col in X.columns:
            self.assertIn(col, PRE_RUN_FEATURE_COLUMNS)

    def test_handling_malformed_records_and_missing_fields(self) -> None:
        """Verify malformed records lacking required fields are dropped gracefully."""
        records = [
            # Valid record
            {
                "run_id": 301,
                "repository": "test/repo",
                "branch": "main",
                "status": "completed",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "created_at": "2026-09-10T10:00:00Z",
            },
            # Missing run_id
            {
                "repository": "test/repo",
                "branch": "main",
                "status": "completed",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "created_at": "2026-09-10T10:00:00Z",
            },
            # Empty repository
            {
                "run_id": 302,
                "repository": "   ",
                "branch": "main",
                "status": "completed",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "created_at": "2026-09-10T10:00:00Z",
            },
            # Missing branch
            {
                "run_id": 303,
                "repository": "test/repo",
                "branch": None,
                "status": "completed",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "created_at": "2026-09-10T10:00:00Z",
            },
        ]
        df = clean_pipeline_runs_dataframe(pd.DataFrame(records))
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["run_id"], 301)

    def test_handling_invalid_timestamps(self) -> None:
        """Verify records with unparseable/corrupted timestamps are dropped safely."""
        records = [
            # Valid timestamp
            {
                "run_id": 401,
                "repository": "test/repo",
                "branch": "main",
                "status": "completed",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "created_at": "2026-09-10T10:00:00Z",
            },
            # Corrupted invalid string timestamp
            {
                "run_id": 402,
                "repository": "test/repo",
                "branch": "main",
                "status": "completed",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "created_at": "INVALID_TIMESTAMP_CORRUPTED",
            },
            # None timestamp
            {
                "run_id": 403,
                "repository": "test/repo",
                "branch": "main",
                "status": "completed",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "created_at": None,
            },
        ]
        df = clean_pipeline_runs_dataframe(pd.DataFrame(records))
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["run_id"], 401)
        self.assertFalse(pd.isna(df.iloc[0]["created_at"]))

    def test_csv_snapshot_roundtrip(self) -> None:
        """Verify exporting to a CSV snapshot with metadata header and loading back cleanly."""
        records = self._sample_records()
        original_df = load_pipeline_runs_from_records(records)

        snapshot_path = Path(self.test_dir) / "pipeline_runs_snapshot.csv"
        saved_path = export_pipeline_runs_to_csv(original_df, snapshot_path, is_snapshot=True)

        self.assertTrue(saved_path.is_file())

        # Verify snapshot metadata header exists in the file
        with open(saved_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("# CI/CD Pipeline Telemetry Snapshot", content)
            self.assertIn("# Exported At (UTC):", content)
            self.assertIn("# Note: Static snapshot.", content)

        # Load back from CSV snapshot
        loaded_df = load_pipeline_runs_from_csv(saved_path)
        self.assertEqual(len(loaded_df), len(original_df))
        self.assertEqual(list(loaded_df["run_id"]), list(original_df["run_id"]))
        self.assertEqual(list(loaded_df["status"]), list(original_df["status"]))
        self.assertEqual(
            loaded_df["conclusion"].fillna("none").tolist(),
            original_df["conclusion"].fillna("none").tolist(),
        )

    def test_load_csv_missing_file_raises_error(self) -> None:
        """Verify load_pipeline_runs_from_csv raises FileNotFoundError for non-existent path."""
        non_existent = Path(self.test_dir) / "does_not_exist.csv"
        with self.assertRaises(FileNotFoundError):
            load_pipeline_runs_from_csv(non_existent)
