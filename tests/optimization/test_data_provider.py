"""Tests for the PostgreSQL optimization data adapter using isolated SQLite sessions."""

from datetime import datetime, timedelta, timezone
import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.data_collection.schemas import (
    PipelineJobRecord,
    PipelineRunRecord,
    PipelineStepRecord,
)
from backend.app.db.models import PipelineJobModel, PipelineRunModel, PipelineStepModel
from backend.app.db.repository import upsert_pipeline_runs
from backend.app.db.session import Base
from optimization.data_provider import MAX_ANALYSIS_WINDOW, _to_optimization_run, get_pipeline_data


class TestOptimizationDataProvider(unittest.TestCase):
    """Validates mapping, repository filtering, run eligibility, and analysis window limits."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _make_record(
        self,
        run_id: int,
        repository: str = "owner/repo-a",
        status: str = "completed",
        conclusion: str = "success",
        duration: float = 42.0,
        failed_stage: str = None,
        failure_type: str = "none",
        created_at: datetime = None,
    ) -> PipelineRunRecord:
        return PipelineRunRecord(
            run_id=run_id,
            repository=repository,
            branch="main",
            commit_sha=f"sha{run_id:06d}",
            status=status,
            conclusion=conclusion,
            workflow_name="CI Pipeline",
            event="push",
            duration_seconds=duration,
            failed_stage=failed_stage,
            failure_type=failure_type,
            created_at=created_at or datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
        )

    def test_empty_database_returns_empty_list(self) -> None:
        self.assertEqual(get_pipeline_data(self.db), [])

    def test_maps_eligible_runs_and_excludes_ineligible(self) -> None:
        base = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        records = [
            self._make_record(101, conclusion="success", duration=40.5, created_at=base),
            self._make_record(
                102,
                conclusion="failure",
                duration=22.0,
                failed_stage="test",
                failure_type="test_failure",
                created_at=base + timedelta(minutes=5),
            ),
            # Excluded cases:
            self._make_record(103, status="in_progress", conclusion=None, duration=15.0, created_at=base + timedelta(minutes=6)),
            self._make_record(104, status="completed", conclusion="cancelled", duration=10.0, created_at=base + timedelta(minutes=7)),
            self._make_record(105, status="completed", conclusion="skipped", duration=0.0, created_at=base + timedelta(minutes=8)),
            self._make_record(106, status="completed", conclusion="success", duration=None, created_at=base + timedelta(minutes=9)),
        ]
        upsert_pipeline_runs(self.db, records)

        runs = get_pipeline_data(self.db)
        self.assertEqual(len(runs), 2)

        by_id = {run.run_id: run for run in runs}
        self.assertEqual(set(by_id.keys()), {"101", "102"})
        self.assertEqual(by_id["101"].status, "success")
        self.assertEqual(by_id["101"].pipeline_duration, 40.5)
        self.assertEqual(by_id["101"].stages, [])
        self.assertIsNone(by_id["101"].failed_stage)

        self.assertEqual(by_id["102"].status, "failure")
        self.assertEqual(by_id["102"].pipeline_duration, 22.0)
        self.assertEqual(by_id["102"].failed_stage, "test")
        self.assertEqual(by_id["102"].stages, [])

    def test_filters_by_repository(self) -> None:
        base = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        records = [
            self._make_record(201, repository="owner/repo-a", duration=30.0, created_at=base),
            self._make_record(202, repository="owner/repo-b", duration=60.0, created_at=base + timedelta(minutes=1)),
        ]
        upsert_pipeline_runs(self.db, records)

        repo_a = get_pipeline_data(self.db, repository="owner/repo-a")
        self.assertEqual([r.run_id for r in repo_a], ["201"])

        repo_b = get_pipeline_data(self.db, repository="owner/repo-b")
        self.assertEqual([r.run_id for r in repo_b], ["202"])

    def test_caps_window_at_100_records(self) -> None:
        base = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        records = [
            self._make_record(1000 + i, created_at=base + timedelta(minutes=i))
            for i in range(110)
        ]
        upsert_pipeline_runs(self.db, records)

        runs = get_pipeline_data(self.db, limit=250)
        self.assertEqual(len(runs), MAX_ANALYSIS_WINDOW)

    def test_single_job_sequential_steps_lifecycle_exclusion_and_metadata(self) -> None:
        """Verify sequential step summation, lifecycle hook filtering, 0.0s execution, and metadata."""
        base = datetime(2026, 10, 11, 8, 50, 0, tzinfo=timezone.utc)
        record = PipelineRunRecord(
            run_id=2001,
            repository="owner/repo-a",
            branch="main",
            commit_sha="sha2001",
            status="completed",
            conclusion="failure",
            workflow_name="CI Pipeline",
            event="push",
            duration_seconds=12.0,
            failed_stage="build",
            failure_type="build_failure",
            created_at=base,
            jobs=[
                PipelineJobRecord(
                    github_job_id=5001,
                    job_name="build-and-test",
                    run_attempt=1,
                    status="completed",
                    conclusion="failure",
                    created_at=base,
                    started_at=base + timedelta(seconds=2),
                    completed_at=base + timedelta(seconds=12),
                    queue_duration_seconds=2.0,
                    execution_duration_seconds=10.0,
                    steps=[
                        PipelineStepRecord(
                            step_number=1,
                            step_name="Set up job",
                            normalized_stage=None,
                            status="completed",
                            conclusion="success",
                            started_at=base + timedelta(seconds=2),
                            completed_at=base + timedelta(seconds=3),
                            duration_seconds=1.0,
                        ),
                        PipelineStepRecord(
                            step_number=2,
                            step_name="Setup Python",
                            normalized_stage="setup",
                            status="completed",
                            conclusion="success",
                            started_at=base + timedelta(seconds=3),
                            completed_at=base + timedelta(seconds=3),
                            duration_seconds=0.0,
                        ),
                        PipelineStepRecord(
                            step_number=3,
                            step_name="Install pip",
                            normalized_stage="dependency",
                            status="completed",
                            conclusion="success",
                            started_at=base + timedelta(seconds=3),
                            completed_at=base + timedelta(seconds=6),
                            duration_seconds=3.0,
                        ),
                        PipelineStepRecord(
                            step_number=4,
                            step_name="Install node deps",
                            normalized_stage="dependency",
                            status="completed",
                            conclusion="success",
                            started_at=base + timedelta(seconds=6),
                            completed_at=base + timedelta(seconds=8),
                            duration_seconds=2.0,
                        ),
                        PipelineStepRecord(
                            step_number=5,
                            step_name="Build",
                            normalized_stage="build",
                            status="completed",
                            conclusion="failure",
                            started_at=base + timedelta(seconds=8),
                            completed_at=base + timedelta(seconds=8),
                            duration_seconds=0.0,
                        ),
                        PipelineStepRecord(
                            step_number=6,
                            step_name="Test",
                            normalized_stage="test",
                            status="completed",
                            conclusion="skipped",
                            started_at=base + timedelta(seconds=8),
                            completed_at=base + timedelta(seconds=8),
                            duration_seconds=None,
                        ),
                        PipelineStepRecord(
                            step_number=13,
                            step_name="Complete job",
                            normalized_stage=None,
                            status="completed",
                            conclusion="success",
                            started_at=base + timedelta(seconds=8),
                            completed_at=base + timedelta(seconds=9),
                            duration_seconds=1.0,
                        ),
                    ],
                )
            ],
        )
        upsert_pipeline_runs(self.db, [record], replace_jobs=True)
        self.db.expire_all()

        sql_statements = []

        def _count_queries(conn, cursor, statement, parameters, context, executemany):
            sql_statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", _count_queries)
        try:
            runs = get_pipeline_data(self.db)
        finally:
            event.remove(self.engine, "before_cursor_execute", _count_queries)

        self.assertEqual(len(sql_statements), 3)
        self.assertEqual(len(runs), 1)
        run = runs[0]

        self.assertTrue(run.has_stage_telemetry)
        self.assertEqual(run.job_count, 1)
        self.assertEqual(run.step_count, 7)
        self.assertEqual(run.queue_duration_seconds, 2.0)
        self.assertEqual(run.failed_stage, "build")

        # Lifecycle hooks (None) excluded; stage order follows execution
        self.assertEqual([s.name for s in run.stages], ["setup", "dependency", "build", "test"])

        stages_by_name = {s.name: s for s in run.stages}
        # Valid 0.0s success remains executed
        self.assertEqual(stages_by_name["setup"].duration, 0.0)
        self.assertTrue(stages_by_name["setup"].duration_available)
        self.assertTrue(stages_by_name["setup"].is_executed)

        # Sequential steps summed within one job (3.0 + 2.0 = 5.0)
        self.assertEqual(stages_by_name["dependency"].duration, 5.0)
        self.assertEqual(stages_by_name["dependency"].status, "success")
        self.assertTrue(stages_by_name["dependency"].is_executed)

        # Valid 0.0s failure remains executed and captures parent failure_type
        self.assertEqual(stages_by_name["build"].duration, 0.0)
        self.assertEqual(stages_by_name["build"].status, "failure")
        self.assertEqual(stages_by_name["build"].failure_type, "build_failure")
        self.assertTrue(stages_by_name["build"].duration_available)
        self.assertTrue(stages_by_name["build"].is_executed)

        # Skipped step has duration_available=False and is not treated as executed
        self.assertEqual(stages_by_name["test"].status, "skipped")
        self.assertEqual(stages_by_name["test"].duration, 0.0)
        self.assertFalse(stages_by_name["test"].duration_available)
        self.assertEqual(stages_by_name["test"].skipped_count, 1)
        self.assertFalse(stages_by_name["test"].is_executed)

    def test_parallel_jobs_overlapping_timestamps_compute_interval_union(self) -> None:
        """Overlapping steps across parallel jobs merge intervals instead of double-counting."""
        base = datetime(2026, 10, 12, 9, 0, 0, tzinfo=timezone.utc)
        record = PipelineRunRecord(
            run_id=2002,
            repository="owner/repo-a",
            branch="main",
            commit_sha="sha2002",
            status="completed",
            conclusion="success",
            workflow_name="CI Pipeline",
            event="push",
            duration_seconds=25.0,
            failure_type="none",
            created_at=base,
            jobs=[
                PipelineJobRecord(
                    github_job_id=6001,
                    job_name="test-shard-1",
                    status="completed",
                    conclusion="success",
                    created_at=base,
                    started_at=base + timedelta(seconds=3),
                    completed_at=base + timedelta(seconds=15),
                    steps=[
                        PipelineStepRecord(
                            step_number=1,
                            step_name="Run unit tests",
                            normalized_stage="test",
                            status="completed",
                            conclusion="success",
                            started_at=base + timedelta(seconds=3),
                            completed_at=base + timedelta(seconds=13),
                            duration_seconds=10.0,
                        )
                    ],
                ),
                PipelineJobRecord(
                    github_job_id=6002,
                    job_name="test-shard-2",
                    status="completed",
                    conclusion="success",
                    created_at=base + timedelta(seconds=1),
                    started_at=base + timedelta(seconds=8),
                    completed_at=base + timedelta(seconds=23),
                    steps=[
                        PipelineStepRecord(
                            step_number=1,
                            step_name="Run integration tests",
                            normalized_stage="test",
                            status="completed",
                            conclusion="success",
                            started_at=base + timedelta(seconds=8),
                            completed_at=base + timedelta(seconds=23),
                            duration_seconds=15.0,
                        )
                    ],
                ),
            ],
        )
        upsert_pipeline_runs(self.db, [record], replace_jobs=True)
        self.db.expire_all()

        runs = get_pipeline_data(self.db)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        # Earliest valid queue duration computed from created_at -> started_at (3.0 vs 7.0)
        self.assertEqual(run.queue_duration_seconds, 3.0)
        self.assertEqual(run.job_count, 2)
        self.assertEqual(run.step_count, 2)
        # Union of [3s, 13s] and [8s, 23s] = [3s, 23s] = 20.0s (not 10 + 15 = 25.0s)
        self.assertEqual(len(run.stages), 1)
        self.assertEqual(run.stages[0].name, "test")
        self.assertEqual(run.stages[0].duration, 20.0)
        self.assertTrue(run.stages[0].duration_available)

    def test_parallel_jobs_without_timestamps_use_max_fallback(self) -> None:
        """Missing or invalid timestamps across parallel jobs fall back to max(job_durations)."""
        base = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)
        record = PipelineRunRecord(
            run_id=2003,
            repository="owner/repo-a",
            branch="main",
            commit_sha="sha2003",
            status="completed",
            conclusion="success",
            workflow_name="CI Pipeline",
            event="push",
            duration_seconds=18.0,
            failure_type="none",
            created_at=base,
            jobs=[
                PipelineJobRecord(
                    github_job_id=6101,
                    job_name="job-a",
                    status="completed",
                    conclusion="success",
                    steps=[
                        PipelineStepRecord(
                            step_number=1,
                            step_name="Compile A",
                            normalized_stage="build",
                            status="completed",
                            conclusion="success",
                            duration_seconds=7.0,
                        ),
                        PipelineStepRecord(
                            step_number=2,
                            step_name="Package A",
                            normalized_stage="build",
                            status="completed",
                            conclusion="success",
                            duration_seconds=5.0,
                        ),
                    ],
                ),
                PipelineJobRecord(
                    github_job_id=6102,
                    job_name="job-b",
                    status="completed",
                    conclusion="success",
                    steps=[
                        PipelineStepRecord(
                            step_number=1,
                            step_name="Compile B",
                            normalized_stage="build",
                            status="completed",
                            conclusion="success",
                            # Invalid reversed timestamps should safely trigger max fallback
                            started_at=base + timedelta(seconds=20),
                            completed_at=base + timedelta(seconds=5),
                            duration_seconds=15.0,
                        )
                    ],
                ),
            ],
        )
        upsert_pipeline_runs(self.db, [record], replace_jobs=True)
        self.db.expire_all()

        runs = get_pipeline_data(self.db)
        self.assertEqual(len(runs), 1)
        run = runs[0]
        # Job timestamps missing -> do not fabricate queue_duration_seconds
        self.assertIsNone(run.queue_duration_seconds)
        # Job A sequential sum = 12.0s, Job B = 15.0s -> parallel max fallback = 15.0s
        self.assertEqual(run.stages[0].duration, 15.0)
        self.assertTrue(run.stages[0].duration_available)

    def test_incomplete_skipped_missing_duration_and_empty_telemetry_handling(self) -> None:
        """Verify queued/in-progress steps, missing/negative durations, lifecycle-only jobs, and empty jobs."""
        base = datetime(2026, 10, 12, 11, 0, 0, tzinfo=timezone.utc)

        # In-memory ORM models allow testing negative durations directly without DB CheckConstraint
        run_edge = PipelineRunModel(
            run_id=2004,
            repository="owner/repo-a",
            branch="main",
            commit_sha="sha2004",
            status="completed",
            conclusion="failure",
            workflow_name="CI Pipeline",
            event="push",
            duration_seconds=30.0,
            failed_stage="test",
            failure_type="test_failure",
            created_at=base,
            jobs=[
                PipelineJobModel(
                    github_job_id=7001,
                    job_name="mixed-job",
                    status="completed",
                    conclusion="failure",
                    queue_duration_seconds=-2.0,
                    steps=[
                        # Executed + skipped mixed stage -> skipped excluded, executed summed
                        PipelineStepModel(
                            step_number=1,
                            step_name="Install core",
                            normalized_stage="dependency",
                            status="completed",
                            conclusion="success",
                            duration_seconds=4.5,
                        ),
                        PipelineStepModel(
                            step_number=2,
                            step_name="Install optional",
                            normalized_stage="dependency",
                            status="completed",
                            conclusion="skipped",
                            duration_seconds=None,
                        ),
                        # Incomplete/queued/in-progress steps only
                        PipelineStepModel(
                            step_number=3,
                            step_name="Run lint",
                            normalized_stage="lint",
                            status="in_progress",
                            conclusion=None,
                            duration_seconds=None,
                        ),
                        PipelineStepModel(
                            step_number=4,
                            step_name="Run queued test",
                            normalized_stage="test",
                            status="queued",
                            conclusion=None,
                            duration_seconds=None,
                        ),
                        # Completed step with missing duration (must not become 0.0 valid timing)
                        PipelineStepModel(
                            step_number=5,
                            step_name="Build artifact",
                            normalized_stage="build",
                            status="completed",
                            conclusion="success",
                            duration_seconds=None,
                        ),
                        # Negative duration treated as invalid / unavailable
                        PipelineStepModel(
                            step_number=6,
                            step_name="Deploy artifact",
                            normalized_stage="deploy",
                            status="completed",
                            conclusion="success",
                            duration_seconds=-1.5,
                        ),
                    ],
                )
            ],
        )

        mapped_edge = _to_optimization_run(run_edge)
        self.assertIsNotNone(mapped_edge)
        self.assertTrue(mapped_edge.has_stage_telemetry)
        self.assertEqual(mapped_edge.job_count, 1)
        self.assertEqual(mapped_edge.step_count, 6)
        self.assertIsNone(mapped_edge.queue_duration_seconds)

        by_stage = {s.name: s for s in mapped_edge.stages}
        self.assertEqual(by_stage["dependency"].duration, 4.5)
        self.assertEqual(by_stage["dependency"].skipped_count, 1)
        self.assertTrue(by_stage["dependency"].duration_available)
        self.assertTrue(by_stage["dependency"].is_executed)

        self.assertEqual(by_stage["lint"].status, "incomplete")
        self.assertEqual(by_stage["lint"].incomplete_count, 1)
        self.assertFalse(by_stage["lint"].duration_available)
        self.assertFalse(by_stage["lint"].is_executed)

        self.assertEqual(by_stage["test"].status, "incomplete")
        self.assertEqual(by_stage["test"].incomplete_count, 1)
        self.assertFalse(by_stage["test"].duration_available)
        self.assertFalse(by_stage["test"].is_executed)

        self.assertEqual(by_stage["build"].duration, 0.0)
        self.assertFalse(by_stage["build"].duration_available)
        self.assertFalse(by_stage["build"].is_executed)

        self.assertEqual(by_stage["deploy"].duration, 0.0)
        self.assertFalse(by_stage["deploy"].duration_available)
        self.assertFalse(by_stage["deploy"].is_executed)

        # Run with jobs containing only empty steps or lifecycle hooks -> no stage telemetry
        run_lifecycle_only = PipelineRunModel(
            run_id=2005,
            repository="owner/repo-a",
            branch="main",
            commit_sha="sha2005",
            status="completed",
            conclusion="success",
            workflow_name="CI Pipeline",
            event="push",
            duration_seconds=5.0,
            created_at=base,
            jobs=[
                PipelineJobModel(
                    github_job_id=7002,
                    job_name="hook-only-job",
                    status="completed",
                    conclusion="success",
                    queue_duration_seconds=1.2,
                    steps=[
                        PipelineStepModel(
                            step_number=1,
                            step_name="Set up job",
                            normalized_stage=None,
                            status="completed",
                            conclusion="success",
                            duration_seconds=1.0,
                        )
                    ],
                ),
                PipelineJobModel(
                    github_job_id=7003,
                    job_name="empty-steps-job",
                    status="completed",
                    conclusion="skipped",
                    steps=[],
                ),
            ],
        )
        mapped_lifecycle = _to_optimization_run(run_lifecycle_only)
        self.assertIsNotNone(mapped_lifecycle)
        self.assertFalse(mapped_lifecycle.has_stage_telemetry)
        self.assertEqual(mapped_lifecycle.stages, [])
        self.assertEqual(mapped_lifecycle.job_count, 2)
        self.assertEqual(mapped_lifecycle.step_count, 1)
        self.assertEqual(mapped_lifecycle.queue_duration_seconds, 1.2)


if __name__ == "__main__":
    unittest.main()

