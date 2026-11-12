"""Tests for Phase 2D Step 1 job and step telemetry models and schemas."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pydantic import ValidationError
from sqlalchemy import create_engine, event, inspect as sa_inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import configure_mappers, sessionmaker

from backend.app.data_collection import (
    PipelineJobRecord,
    PipelineRunRecord,
    PipelineStepRecord,
)
from backend.app.db import (
    PipelineJobModel,
    PipelineRunModel,
    PipelineStepModel,
    init_db,
)
from backend.app.db.models import build_job_key


def _make_run(run_id: int = 1001, **overrides) -> PipelineRunModel:
    base_time = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
    payload = {
        "run_id": run_id,
        "repository": "owner/repo",
        "workflow_name": "CI",
        "branch": "main",
        "commit_sha": "abc1234",
        "status": "completed",
        "conclusion": "success",
        "event": "push",
        "duration_seconds": 120.0,
        "failure_type": "none",
        "created_at": base_time,
    }
    payload.update(overrides)
    return PipelineRunModel(**payload)


class TestTelemetrySchemas(unittest.TestCase):
    """Verify Pydantic validation and backward compatibility for telemetry schemas."""

    def test_pipeline_run_record_backward_compatible_without_jobs(self):
        created_at = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        record = PipelineRunRecord(
            run_id=501,
            repository="owner/repo",
            workflow_name="CI",
            branch="main",
            commit_sha="deadbeef",
            status="completed",
            conclusion="success",
            event="push",
            duration_seconds=45.0,
            failure_type="none",
            created_at=created_at,
        )
        self.assertEqual(record.jobs, [])

    def test_job_and_step_schemas_allow_missing_timestamps_and_durations(self):
        step = PipelineStepRecord(
            step_number=1,
            step_name="  Checkout code  ",
            status="queued",
        )
        job = PipelineJobRecord(
            job_name="  build-and-test  ",
            status="queued",
            steps=[step],
        )
        self.assertEqual(step.step_name, "Checkout code")
        self.assertIsNone(step.started_at)
        self.assertIsNone(step.completed_at)
        self.assertIsNone(step.duration_seconds)
        self.assertEqual(job.job_name, "build-and-test")
        self.assertIsNone(job.github_job_id)
        self.assertIsNone(job.queue_duration_seconds)
        self.assertIsNone(job.execution_duration_seconds)
        self.assertEqual(len(job.steps), 1)

    def test_schema_rejects_negative_durations_and_invalid_step_numbers(self):
        with self.assertRaises(ValidationError):
            PipelineStepRecord(
                step_number=0,
                step_name="Build",
                status="completed",
            )

        with self.assertRaises(ValidationError):
            PipelineStepRecord(
                step_number=1,
                step_name="Build",
                status="completed",
                duration_seconds=-0.5,
            )

        with self.assertRaises(ValidationError):
            PipelineJobRecord(
                job_name="test",
                status="completed",
                queue_duration_seconds=-1.0,
            )

        with self.assertRaises(ValidationError):
            PipelineJobRecord(
                job_name="test",
                status="completed",
                execution_duration_seconds=-0.01,
            )

        with self.assertRaises(ValidationError):
            PipelineJobRecord(
                job_name="   ",
                status="completed",
            )

    def test_nested_pipeline_run_record_accepts_jobs_and_steps(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        run_record = PipelineRunRecord(
            run_id=777,
            repository="owner/repo",
            workflow_name="CI",
            branch="main",
            commit_sha="123abcd",
            status="completed",
            conclusion="failure",
            event="push",
            duration_seconds=90.0,
            failure_type="test_failure",
            created_at=t0,
            jobs=[
                PipelineJobRecord(
                    github_job_id=9001,
                    job_name="unit-tests",
                    run_attempt=1,
                    status="completed",
                    conclusion="failure",
                    created_at=t0,
                    started_at=t0 + timedelta(seconds=5),
                    completed_at=t0 + timedelta(seconds=90),
                    queue_duration_seconds=5.0,
                    execution_duration_seconds=85.0,
                    runner_name="ubuntu-latest",
                    steps=[
                        PipelineStepRecord(
                            step_number=1,
                            step_name="Set up Python",
                            normalized_stage="setup",
                            status="completed",
                            conclusion="success",
                            duration_seconds=12.5,
                        ),
                        PipelineStepRecord(
                            step_number=2,
                            step_name="Run pytest",
                            normalized_stage="test",
                            status="completed",
                            conclusion="failure",
                            duration_seconds=72.5,
                        ),
                    ],
                )
            ],
        )
        self.assertEqual(len(run_record.jobs), 1)
        self.assertEqual(len(run_record.jobs[0].steps), 2)
        self.assertEqual(run_record.jobs[0].steps[1].normalized_stage, "test")


class TestTelemetryORMModels(unittest.TestCase):
    """Verify SQLAlchemy mappers, init_db, constraints, ordering, and cascades."""

    def setUp(self):
        configure_mappers()
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(self.engine, "connect")
        def _enable_sqlite_fks(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        init_db(engine=self.engine)
        self.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )

    def tearDown(self):
        self.engine.dispose()

    def test_init_db_adds_new_tables_without_modifying_existing_runs(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "existing_runs.db"
            preexisting_engine = create_engine(
                f"sqlite:///{db_path}",
                connect_args={"check_same_thread": False},
            )
            PipelineRunModel.__table__.create(bind=preexisting_engine)

            PreSession = sessionmaker(bind=preexisting_engine)
            with PreSession() as session:
                session.add(_make_run(run_id=4242, branch="release"))
                session.commit()

            inspector_before = sa_inspect(preexisting_engine)
            self.assertEqual(inspector_before.get_table_names(), ["pipeline_runs"])

            init_db(engine=preexisting_engine)

            inspector_after = sa_inspect(preexisting_engine)
            tables = set(inspector_after.get_table_names())
            self.assertTrue({"pipeline_runs", "pipeline_jobs", "pipeline_steps"}.issubset(tables))

            with PreSession() as session:
                preserved = session.query(PipelineRunModel).filter_by(run_id=4242).one()
                self.assertEqual(preserved.branch, "release")

            preexisting_engine.dispose()

    def test_job_and_step_round_trip_and_detached_safety(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        job_record = PipelineJobRecord(
            github_job_id=88001,
            job_name="build",
            run_attempt=1,
            status="completed",
            conclusion="success",
            created_at=t0,
            started_at=t0 + timedelta(seconds=3),
            completed_at=t0 + timedelta(seconds=63),
            queue_duration_seconds=3.0,
            execution_duration_seconds=60.0,
            runner_name="ubuntu-22.04",
            steps=[
                PipelineStepRecord(
                    step_number=1,
                    step_name="Install dependencies",
                    normalized_stage="dependencies",
                    status="completed",
                    conclusion="success",
                    started_at=t0 + timedelta(seconds=4),
                    completed_at=t0 + timedelta(seconds=34),
                    duration_seconds=30.0,
                )
            ],
        )

        with self.SessionLocal() as session:
            session.add(_make_run(run_id=1001))
            session.flush()

            job_model = PipelineJobModel.from_pydantic(job_record, run_id=1001)
            session.add(job_model)
            session.commit()

            fetched_job = session.query(PipelineJobModel).filter_by(run_id=1001).one()
            round_trip = fetched_job.to_pydantic(include_steps=True)
            self.assertEqual(round_trip.github_job_id, 88001)
            self.assertEqual(round_trip.queue_duration_seconds, 3.0)
            self.assertEqual(len(round_trip.steps), 1)
            self.assertEqual(round_trip.steps[0].normalized_stage, "dependencies")
            detached_run = session.query(PipelineRunModel).filter_by(run_id=1001).one()
            detached_loaded_job = fetched_job

        with self.SessionLocal() as fresh_session:
            detached_unloaded_job = (
                fresh_session.query(PipelineJobModel).filter_by(run_id=1001).one()
            )

        # Unloaded relationships must not raise DetachedInstanceError after session closes,
        # while already-loaded steps remain accessible on detached instances.
        self.assertEqual(detached_run.to_pydantic().jobs, [])
        self.assertEqual(len(detached_loaded_job.to_pydantic().steps), 1)
        self.assertEqual(detached_unloaded_job.to_pydantic().steps, [])
        self.assertEqual(detached_unloaded_job.to_dict()["steps"], [])

    def test_unique_constraints_with_github_job_id_and_nullable_fallback(self):
        with self.SessionLocal() as session:
            session.add(_make_run(run_id=2001))
            session.add(_make_run(run_id=2002))
            session.flush()

            job_a = PipelineJobModel(
                run_id=2001,
                github_job_id=555,
                job_name="test",
                status="completed",
            )
            session.add(job_a)
            session.commit()

            # Duplicate (run_id, github_job_id) must fail.
            dup_github_job = PipelineJobModel(
                run_id=2001,
                github_job_id=555,
                job_name="test-renamed",
                status="completed",
            )
            session.add(dup_github_job)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            # Same github_job_id under a different run_id is allowed by composite scope.
            other_run_job = PipelineJobModel(
                run_id=2002,
                github_job_id=555,
                job_name="test",
                status="completed",
            )
            session.add(other_run_job)
            session.commit()

            # Nullable github_job_id: duplicate (run_id, job_name, run_attempt) is rejected.
            legacy_1 = PipelineJobModel(
                run_id=2001,
                github_job_id=None,
                job_name="build",
                run_attempt=None,
                status="completed",
            )
            session.add(legacy_1)
            session.commit()
            self.assertEqual(legacy_1.job_key, build_job_key("build", None, None))

            dup_legacy = PipelineJobModel(
                run_id=2001,
                github_job_id=None,
                job_name=" build ",
                run_attempt=1,
                status="completed",
            )
            session.add(dup_legacy)
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            # Different run_attempt is allowed for nullable github_job_id.
            legacy_attempt_2 = PipelineJobModel(
                run_id=2001,
                github_job_id=None,
                job_name="build",
                run_attempt=2,
                status="completed",
            )
            session.add(legacy_attempt_2)
            session.commit()

    def test_step_uniqueness_and_deterministic_ordering(self):
        with self.SessionLocal() as session:
            session.add(_make_run(run_id=3001))
            session.flush()

            job1 = PipelineJobModel(
                run_id=3001,
                github_job_id=101,
                job_name="lint",
                status="completed",
            )
            job2 = PipelineJobModel(
                run_id=3001,
                github_job_id=102,
                job_name="test",
                status="completed",
            )
            session.add_all([job1, job2])
            session.flush()

            # Insert steps out of order for both jobs.
            session.add_all(
                [
                    PipelineStepModel(
                        job_id=job2.id,
                        run_id=3001,
                        step_number=2,
                        step_name="Run pytest",
                        status="completed",
                    ),
                    PipelineStepModel(
                        job_id=job1.id,
                        run_id=3001,
                        step_number=3,
                        step_name="Report",
                        status="completed",
                    ),
                    PipelineStepModel(
                        job_id=job1.id,
                        run_id=3001,
                        step_number=1,
                        step_name="Checkout",
                        status="completed",
                    ),
                    PipelineStepModel(
                        job_id=job2.id,
                        run_id=3001,
                        step_number=1,
                        step_name="Checkout",
                        status="completed",
                    ),
                ]
            )
            session.commit()

            # Duplicate step_number on the same job must fail.
            session.add(
                PipelineStepModel(
                    job_id=job1.id,
                    run_id=3001,
                    step_number=1,
                    step_name="Duplicate step 1",
                    status="completed",
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            run = session.query(PipelineRunModel).filter_by(run_id=3001).one()
            self.assertEqual([j.job_name for j in run.jobs], ["lint", "test"])
            self.assertEqual([s.step_number for s in run.jobs[0].steps], [1, 3])
            self.assertEqual(
                [(s.job_id, s.step_number) for s in run.steps],
                [(job1.id, 1), (job1.id, 3), (job2.id, 1), (job2.id, 2)],
            )

    def test_cascade_deletes_and_foreign_key_enforcement(self):
        with self.SessionLocal() as session:
            run = _make_run(run_id=4001)
            session.add(run)
            session.flush()

            job1 = PipelineJobModel(
                run_id=4001,
                github_job_id=201,
                job_name="build",
                status="completed",
                steps=[
                    PipelineStepModel(
                        run_id=4001,
                        step_number=1,
                        step_name="Compile",
                        status="completed",
                    )
                ],
            )
            job2 = PipelineJobModel(
                run_id=4001,
                github_job_id=202,
                job_name="package",
                status="completed",
                steps=[
                    PipelineStepModel(
                        run_id=4001,
                        step_number=1,
                        step_name="Archive",
                        status="completed",
                    )
                ],
            )
            session.add_all([job1, job2])
            session.commit()

            # Deleting one job cascades to its own steps only.
            session.delete(job1)
            session.commit()
            self.assertEqual(session.query(PipelineJobModel).count(), 1)
            self.assertEqual(session.query(PipelineStepModel).count(), 1)

            # Deleting the parent run cascades to remaining jobs and steps cleanly.
            session.delete(run)
            session.commit()
            self.assertEqual(session.query(PipelineRunModel).count(), 0)
            self.assertEqual(session.query(PipelineJobModel).count(), 0)
            self.assertEqual(session.query(PipelineStepModel).count(), 0)

            # FK violation: inserting a job for a non-existent run_id must fail.
            session.add(
                PipelineJobModel(
                    run_id=999999,
                    github_job_id=301,
                    job_name="orphan",
                    status="completed",
                )
            )
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()

            # DB check constraint: negative execution_duration_seconds must fail.
            session.add(_make_run(run_id=4002))
            session.commit()
            with self.assertRaises(IntegrityError):
                session.execute(
                    text(
                        "INSERT INTO pipeline_jobs "
                        "(run_id, github_job_id, job_key, job_name, status, execution_duration_seconds, synced_at) "
                        "VALUES (:run_id, :gjid, :jkey, :jname, :status, :dur, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "run_id": 4002,
                        "gjid": 302,
                        "jkey": "github:302",
                        "jname": "bad-dur",
                        "status": "completed",
                        "dur": -5.0,
                    },
                )
                session.commit()
            session.rollback()


if __name__ == "__main__":
    unittest.main()
