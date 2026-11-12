"""Unit tests for PostgreSQL database models, sessions, configuration, and repository operations."""

from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

# pyrefly: ignore [missing-import]
from sqlalchemy import create_engine, event
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import sessionmaker

from backend.app.data_collection.schemas import (
    PipelineJobRecord,
    PipelineRunRecord,
    PipelineStepRecord,
)
from backend.app.data_collection.storage import sync_github_runs_to_db
from backend.app.db.config import get_database_url
from backend.app.db.models import PipelineRunModel
from backend.app.db.repository import (
    get_pipeline_run_by_id,
    get_pipeline_runs,
    get_pipeline_stats,
    upsert_pipeline_run,
    upsert_pipeline_runs,
)
from backend.app.db.session import Base


class TestDatabaseConfiguration(unittest.TestCase):
    """Test suite validating database URL resolution and environment variables handling."""

    def test_default_config_construction(self) -> None:
        """Verify URL is built from individual POSTGRES_* environment variables."""
        with patch.dict(os.environ, {
            "POSTGRES_USER": "testuser",
            "POSTGRES_PASSWORD": "secretpassword",
            "POSTGRES_HOST": "dbhost",
            "POSTGRES_PORT": "5433",
            "POSTGRES_DB": "testdb",
        }, clear=True):
            url = get_database_url()
            self.assertEqual(url, "postgresql+psycopg2://testuser:secretpassword@dbhost:5433/testdb")

    def test_database_url_override(self) -> None:
        """Verify explicit DATABASE_URL overrides individual variables."""
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgresql://customuser:pass@host:5432/customdb",
        }):
            url = get_database_url()
            self.assertEqual(url, "postgresql+psycopg2://customuser:pass@host:5432/customdb")

    def test_legacy_postgres_prefix_normalized(self) -> None:
        """Verify postgres:// is properly normalized to postgresql+psycopg2://."""
        with patch.dict(os.environ, {
            "DATABASE_URL": "postgres://user:pass@localhost:5432/dbname",
        }):
            url = get_database_url()
            self.assertEqual(url, "postgresql+psycopg2://user:pass@localhost:5432/dbname")


class TestDatabaseRepository(unittest.TestCase):
    """Test suite validating ORM persistence, deduplication, queries, and statistics."""

    def setUp(self) -> None:
        """Set up an isolated in-memory SQLite database for testing."""
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        """Clean up test database resources."""
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def _make_record(
        self,
        run_id: int = 5001,
        status: str = "completed",
        conclusion: str = "success",
        duration: float = 30.5,
        failure_type: str = "none",
        failed_stage: str = None,
    ) -> PipelineRunRecord:
        """Helper to create PipelineRunRecord instances."""
        return PipelineRunRecord(
            run_id=run_id,
            repository="test-owner/test-repo",
            branch="main",
            commit_sha="abcd1234efgh5678",
            status=status,
            conclusion=conclusion,
            workflow_name="CI Pipeline",
            event="push",
            duration_seconds=duration,
            failed_stage=failed_stage,
            failure_type=failure_type,
            created_at=datetime.now(timezone.utc),
        )

    def test_model_conversion(self) -> None:
        """Verify round-trip between PipelineRunRecord and PipelineRunModel."""
        record = self._make_record()
        model = PipelineRunModel.from_pydantic(record)
        self.assertEqual(model.run_id, record.run_id)
        self.assertEqual(model.repository, record.repository)

        converted_record = model.to_pydantic()
        self.assertEqual(converted_record.run_id, record.run_id)
        self.assertEqual(converted_record.conclusion, record.conclusion)

        dict_repr = model.to_dict()
        self.assertEqual(dict_repr["run_id"], record.run_id)
        self.assertIn("synced_at", dict_repr)

    def test_upsert_prevents_duplicate_primary_keys(self) -> None:
        """Verify that persisting the same run_id twice updates the record instead of raising duplicates."""
        record1 = self._make_record(run_id=5001, status="in_progress", conclusion=None)
        model1, created1 = upsert_pipeline_run(self.db, record1)
        self.db.commit()
        self.assertTrue(created1)

        # Count records in table
        self.assertEqual(self.db.query(PipelineRunModel).count(), 1)
        self.assertEqual(model1.status, "in_progress")

        # Update the same run_id after pipeline completes
        record2 = self._make_record(run_id=5001, status="completed", conclusion="success")
        model2, created2 = upsert_pipeline_run(self.db, record2)
        self.db.commit()
        self.assertFalse(created2)

        # Still only 1 record in database, but updated
        self.assertEqual(self.db.query(PipelineRunModel).count(), 1)
        refreshed = get_pipeline_run_by_id(self.db, 5001)
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed.status, "completed")
        self.assertEqual(refreshed.conclusion, "success")

    def test_batch_upsert_runs(self) -> None:
        """Verify batch upsert correctly counts inserted and updated records."""
        runs = [
            self._make_record(run_id=6001, conclusion="success"),
            self._make_record(run_id=6002, conclusion="failure", failure_type="test_failure"),
        ]
        result1 = upsert_pipeline_runs(self.db, runs)
        self.assertEqual(result1["total"], 2)
        self.assertEqual(result1["inserted"], 2)
        self.assertEqual(result1["updated"], 0)

        # Re-syncing same runs
        result2 = upsert_pipeline_runs(self.db, runs)
        self.assertEqual(result2["total"], 2)
        self.assertEqual(result2["inserted"], 0)
        self.assertEqual(result2["updated"], 2)

    def test_get_pipeline_runs_filtering(self) -> None:
        """Verify filtering by conclusion, branch, and failure type."""
        runs = [
            self._make_record(run_id=7001, conclusion="success", failure_type="none"),
            self._make_record(run_id=7002, conclusion="failure", failure_type="test_failure"),
            self._make_record(run_id=7003, conclusion="failure", failure_type="build_failure"),
        ]
        upsert_pipeline_runs(self.db, runs)

        all_runs = get_pipeline_runs(self.db)
        self.assertEqual(len(all_runs), 3)

        success_runs = get_pipeline_runs(self.db, conclusion="success")
        self.assertEqual(len(success_runs), 1)
        self.assertEqual(success_runs[0].run_id, 7001)

        test_fail_runs = get_pipeline_runs(self.db, failure_type="test_failure")
        self.assertEqual(len(test_fail_runs), 1)
        self.assertEqual(test_fail_runs[0].run_id, 7002)

    def test_get_pipeline_stats(self) -> None:
        """Verify calculation of success rate, failure rate, and category breakdowns."""
        runs = [
            self._make_record(run_id=8001, conclusion="success", duration=40.0),
            self._make_record(run_id=8002, conclusion="success", duration=20.0),
            self._make_record(run_id=8003, conclusion="failure", duration=15.0, failure_type="test_failure", failed_stage="test"),
            self._make_record(run_id=8004, conclusion="failure", duration=25.0, failure_type="build_failure", failed_stage="build"),
        ]
        upsert_pipeline_runs(self.db, runs)

        stats = get_pipeline_stats(self.db)
        self.assertEqual(stats["total_runs"], 4)
        self.assertEqual(stats["success_count"], 2)
        self.assertEqual(stats["failure_count"], 2)
        self.assertEqual(stats["success_rate_percent"], 50.0)
        self.assertEqual(stats["failure_rate_percent"], 50.0)
        self.assertEqual(stats["average_duration_seconds"], 25.0)
        self.assertEqual(stats["failure_types"]["test_failure"], 1)
        self.assertEqual(stats["failure_types"]["build_failure"], 1)
        self.assertEqual(stats["failed_stages"]["test"], 1)
        self.assertEqual(stats["failed_stages"]["build"], 1)

    def test_sync_github_runs_to_db_mock(self) -> None:
        """Verify storage bridge sync function stores mock runs without network calls."""
        res = sync_github_runs_to_db(self.db, repository="test-owner/test-repo", use_mock=True, limit=4)
        self.assertEqual(res["total_fetched"], 4)
        self.assertEqual(res["inserted"], 4)
        self.assertEqual(self.db.query(PipelineRunModel).count(), 4)

    def _make_run_with_jobs(
        self,
        *,
        run_id: int,
        repository: str = "owner/repo-a",
        status: str = "completed",
        conclusion: str = "success",
        created_at: datetime = None,
        job_count: int = 2,
        steps_per_job: int = 3,
    ) -> PipelineRunRecord:
        """Build a PipelineRunRecord with deterministic nested jobs and steps."""
        base_dt = created_at or datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        jobs = []
        for j_idx in range(1, job_count + 1):
            steps = []
            for s_idx in range(1, steps_per_job + 1):
                step_start = base_dt + timedelta(seconds=(j_idx - 1) * 10 + s_idx)
                step_end = step_start + timedelta(seconds=2)
                steps.append(
                    PipelineStepRecord(
                        step_number=s_idx,
                        step_name=f"Step {s_idx}",
                        normalized_stage="test" if s_idx == steps_per_job else "build",
                        status="completed",
                        conclusion=conclusion if conclusion in ("success", "failure") else "success",
                        started_at=step_start,
                        completed_at=step_end,
                        duration_seconds=2.0,
                    )
                )
            jobs.append(
                PipelineJobRecord(
                    github_job_id=run_id * 100 + j_idx,
                    job_name=f"job-{j_idx}",
                    run_attempt=1,
                    status=status,
                    conclusion=conclusion,
                    created_at=base_dt,
                    started_at=base_dt + timedelta(seconds=1),
                    completed_at=base_dt + timedelta(seconds=10),
                    queue_duration_seconds=1.0,
                    execution_duration_seconds=9.0,
                    runner_name="ubuntu-latest",
                    steps=steps,
                )
            )

        return PipelineRunRecord(
            run_id=run_id,
            repository=repository,
            branch="main",
            commit_sha=f"sha{run_id}",
            status=status,
            conclusion=conclusion,
            workflow_name="CI Pipeline",
            event="push",
            duration_seconds=20.0,
            failed_stage="test" if conclusion == "failure" else None,
            failure_type="test_failure" if conclusion == "failure" else "none",
            created_at=base_dt,
            jobs=jobs,
        )

    def test_get_pipeline_runs_include_telemetry_eager_loading_and_bounded_queries(self) -> None:
        """Verify include_telemetry=True loads nested jobs/steps in bounded queries and stays detached-safe."""
        base_dt = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        records = [
            self._make_run_with_jobs(
                run_id=9000 + idx,
                conclusion="failure" if idx % 2 == 0 else "success",
                created_at=base_dt + timedelta(minutes=idx),
                job_count=2,
                steps_per_job=3,
            )
            for idx in range(10)
        ]
        # Add one run with no jobs/steps to confirm empty collections work cleanly
        records.append(self._make_record(run_id=9099, status="completed", conclusion="success"))
        upsert_pipeline_runs(self.db, records, replace_jobs=True)
        self.db.expire_all()

        sql_statements = []

        def _count_queries(conn, cursor, statement, parameters, context, executemany):
            sql_statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", _count_queries)
        try:
            loaded_runs = get_pipeline_runs(
                self.db,
                limit=50,
                status="completed",
                include_telemetry=True,
            )
            # Accessing nested jobs and steps while open must trigger zero lazy queries
            query_count_after_load = len(sql_statements)
            observed = [
                (
                    run.run_id,
                    [job.job_name for job in run.jobs],
                    [[step.step_name for step in job.steps] for job in run.jobs],
                )
                for run in loaded_runs
            ]
            query_count_after_traversal = len(sql_statements)
        finally:
            event.remove(self.engine, "before_cursor_execute", _count_queries)

        self.assertEqual(len(loaded_runs), 11)
        self.assertEqual(query_count_after_load, 3)
        self.assertEqual(query_count_after_traversal, 3)

        by_id = {rid: (job_names, step_lists) for rid, job_names, step_lists in observed}
        self.assertEqual(by_id[9099], ([], []))
        self.assertEqual(by_id[9000][0], ["job-1", "job-2"])
        self.assertEqual(by_id[9000][1], [["Step 1", "Step 2", "Step 3"], ["Step 1", "Step 2", "Step 3"]])

        # Relationships loaded via selectinload remain accessible even after closing the session
        self.db.close()
        run_9000 = next(r for r in loaded_runs if r.run_id == 9000)
        self.assertEqual(len(run_9000.jobs), 2)
        self.assertEqual(len(run_9000.jobs[0].steps), 3)

    def test_get_pipeline_runs_conclusions_and_filters_with_telemetry(self) -> None:
        """Verify conclusions filter, repository/status/limit/offset pagination, and default compatibility."""
        base_dt = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
        records = [
            self._make_run_with_jobs(
                run_id=9101,
                repository="owner/repo-a",
                status="completed",
                conclusion="success",
                created_at=base_dt,
            ),
            self._make_run_with_jobs(
                run_id=9102,
                repository="owner/repo-a",
                status="completed",
                conclusion="failure",
                created_at=base_dt + timedelta(minutes=1),
            ),
            self._make_run_with_jobs(
                run_id=9103,
                repository="owner/repo-a",
                status="completed",
                conclusion="cancelled",
                created_at=base_dt + timedelta(minutes=2),
            ),
            self._make_run_with_jobs(
                run_id=9104,
                repository="owner/repo-a",
                status="in_progress",
                conclusion="success",
                created_at=base_dt + timedelta(minutes=3),
            ),
            self._make_run_with_jobs(
                run_id=9105,
                repository="owner/repo-b",
                status="completed",
                conclusion="success",
                created_at=base_dt + timedelta(minutes=4),
            ),
        ]
        upsert_pipeline_runs(self.db, records, replace_jobs=True)
        self.db.expire_all()

        # Default signature remains unchanged for existing callers
        default_runs = get_pipeline_runs(self.db, repository="owner/repo-a")
        self.assertEqual([r.run_id for r in default_runs], [9104, 9103, 9102, 9101])

        # Combined filters + conclusions tuple + telemetry
        filtered = get_pipeline_runs(
            self.db,
            repository="owner/repo-a",
            status="completed",
            conclusions=("success", "failure"),
            include_telemetry=True,
        )
        self.assertEqual([r.run_id for r in filtered], [9102, 9101])
        self.assertEqual(len(filtered[0].jobs), 2)
        self.assertEqual(len(filtered[0].jobs[0].steps), 3)

        # Pagination (limit & offset) applies strictly to runs without row inflation from joins
        page = get_pipeline_runs(
            self.db,
            repository="owner/repo-a",
            status="completed",
            conclusions=("success", "failure"),
            limit=1,
            offset=1,
            include_telemetry=True,
        )
        self.assertEqual([r.run_id for r in page], [9101])
        self.assertEqual(len(page[0].jobs), 2)

        # Empty conclusions tuple returns no matches cleanly
        self.assertEqual(
            get_pipeline_runs(self.db, conclusions=(), include_telemetry=True),
            [],
        )
