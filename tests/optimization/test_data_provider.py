"""Tests for the PostgreSQL optimization data adapter using isolated SQLite sessions."""

from datetime import datetime, timedelta, timezone
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.data_collection.schemas import PipelineRunRecord
from backend.app.db.repository import upsert_pipeline_runs
from backend.app.db.session import Base
from optimization.data_provider import MAX_ANALYSIS_WINDOW, get_pipeline_data


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


if __name__ == "__main__":
    unittest.main()

