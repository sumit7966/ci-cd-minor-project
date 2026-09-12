"""Unit and integration tests for CI/CD pipeline analytics service and REST endpoints."""

from datetime import datetime, timezone
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.analytics.service import (
    get_analytics_summary,
    get_complete_analytics,
    get_duration_analytics,
    get_execution_trends,
    get_failure_analytics,
)
from backend.app.data_collection.schemas import PipelineRunRecord
from backend.app.db.models import PipelineRunModel
from backend.app.db.repository import upsert_pipeline_runs
from backend.app.db.session import Base, get_db
from backend.app.main import app


class TestAnalyticsService(unittest.TestCase):
    """Test suite validating core analytics calculation algorithms and empty DB safety."""

    def setUp(self) -> None:
        """Create isolated in-memory SQLite database."""
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        """Clean up test database."""
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def _seed_runs(self) -> None:
        """Seed a representative set of multi-branch, multi-workflow test records."""
        runs = [
            PipelineRunRecord(
                run_id=101,
                repository="org/repo-a",
                branch="main",
                commit_sha="c1111111",
                status="completed",
                conclusion="success",
                workflow_name="CI Pipeline",
                event="push",
                duration_seconds=30.0,
                failed_stage=None,
                failure_type="none",
                created_at=datetime(2026, 9, 10, 10, 0, 0, tzinfo=timezone.utc),
            ),
            PipelineRunRecord(
                run_id=102,
                repository="org/repo-a",
                branch="main",
                commit_sha="c2222222",
                status="completed",
                conclusion="success",
                workflow_name="CI Pipeline",
                event="push",
                duration_seconds=50.0,
                failed_stage=None,
                failure_type="none",
                created_at=datetime(2026, 9, 10, 14, 0, 0, tzinfo=timezone.utc),
            ),
            PipelineRunRecord(
                run_id=103,
                repository="org/repo-a",
                branch="feature-x",
                commit_sha="c3333333",
                status="completed",
                conclusion="failure",
                workflow_name="CI Pipeline",
                event="pull_request",
                duration_seconds=20.0,
                failed_stage="test",
                failure_type="test_failure",
                created_at=datetime(2026, 9, 11, 9, 0, 0, tzinfo=timezone.utc),
            ),
            PipelineRunRecord(
                run_id=104,
                repository="org/repo-a",
                branch="feature-x",
                commit_sha="c4444444",
                status="completed",
                conclusion="failure",
                workflow_name="Release Pipeline",
                event="workflow_dispatch",
                duration_seconds=10.0,
                failed_stage="dependency",
                failure_type="dependency_failure",
                created_at=datetime(2026, 9, 11, 11, 0, 0, tzinfo=timezone.utc),
            ),
            PipelineRunRecord(
                run_id=105,
                repository="org/repo-b",
                branch="main",
                commit_sha="c5555555",
                status="completed",
                conclusion="success",
                workflow_name="CI Pipeline",
                event="push",
                duration_seconds=60.0,
                failed_stage=None,
                failure_type="none",
                created_at=datetime(2026, 9, 11, 15, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        upsert_pipeline_runs(self.db, runs)

    def test_empty_database_safety(self) -> None:
        """Verify all analytical methods return safe defaults on an empty database."""
        summary = get_analytics_summary(self.db)
        self.assertEqual(summary["total_runs"], 0)
        self.assertEqual(summary["success_rate_percent"], 0.0)
        self.assertEqual(summary["failure_rate_percent"], 0.0)
        self.assertIsNone(summary["average_duration_seconds"])

        trends = get_execution_trends(self.db)
        self.assertEqual(trends, [])

        failures = get_failure_analytics(self.db)
        self.assertEqual(failures["total_failures"], 0)
        self.assertEqual(failures["by_failure_type"], [])
        self.assertEqual(failures["by_failed_stage"], [])
        self.assertIsNone(failures["most_frequent_failure_type"])

        duration = get_duration_analytics(self.db)
        self.assertEqual(duration["overall"]["total_runs_with_duration"], 0)
        self.assertIsNone(duration["overall"]["average_duration_seconds"])
        self.assertEqual(duration["by_workflow"], [])
        self.assertEqual(duration["by_branch"], [])

        complete = get_complete_analytics(self.db)
        self.assertEqual(complete["summary"]["total_runs"], 0)

    def test_populated_summary(self) -> None:
        """Verify summary metrics and success/failure rates with populated data."""
        self._seed_runs()
        summary = get_analytics_summary(self.db, repository="org/repo-a")
        self.assertEqual(summary["total_runs"], 4)
        self.assertEqual(summary["success_count"], 2)
        self.assertEqual(summary["failure_count"], 2)
        self.assertEqual(summary["success_rate_percent"], 50.0)
        self.assertEqual(summary["failure_rate_percent"], 50.0)
        # durations: 30, 50, 20, 10 -> sum=110 / 4 = 27.5
        self.assertEqual(summary["average_duration_seconds"], 27.5)
        self.assertEqual(summary["min_duration_seconds"], 10.0)
        self.assertEqual(summary["max_duration_seconds"], 50.0)
        self.assertEqual(summary["median_duration_seconds"], 25.0)

    def test_execution_trends(self) -> None:
        """Verify daily trend aggregation."""
        self._seed_runs()
        trends = get_execution_trends(self.db, repository="org/repo-a")
        self.assertEqual(len(trends), 2)  # 2026-09-10 and 2026-09-11

        day1 = trends[0]
        self.assertEqual(day1["date"], "2026-09-10")
        self.assertEqual(day1["total_runs"], 2)
        self.assertEqual(day1["success_count"], 2)
        self.assertEqual(day1["failure_count"], 0)
        self.assertEqual(day1["success_rate_percent"], 100.0)
        self.assertEqual(day1["average_duration_seconds"], 40.0)

        day2 = trends[1]
        self.assertEqual(day2["date"], "2026-09-11")
        self.assertEqual(day2["total_runs"], 2)
        self.assertEqual(day2["success_count"], 0)
        self.assertEqual(day2["failure_count"], 2)
        self.assertEqual(day2["failure_rate_percent"], 100.0)
        self.assertEqual(day2["average_duration_seconds"], 15.0)

    def test_failure_breakdowns(self) -> None:
        """Verify failure breakdown by failure_type and failed_stage."""
        self._seed_runs()
        failures = get_failure_analytics(self.db, repository="org/repo-a")
        self.assertEqual(failures["total_failures"], 2)
        self.assertIn(failures["most_frequent_failure_type"], ["test_failure", "dependency_failure"])

        f_types = {item["failure_type"]: item["count"] for item in failures["by_failure_type"]}
        self.assertEqual(f_types["test_failure"], 1)
        self.assertEqual(f_types["dependency_failure"], 1)

        f_stages = {item["stage"]: item["count"] for item in failures["by_failed_stage"]}
        self.assertEqual(f_stages["test"], 1)
        self.assertEqual(f_stages["dependency"], 1)

    def test_duration_analytics_by_workflow_and_branch(self) -> None:
        """Verify duration metrics broken down by workflow and branch."""
        self._seed_runs()
        dur = get_duration_analytics(self.db, repository="org/repo-a")
        wf_stats = {w["workflow_name"]: w for w in dur["by_workflow"]}
        self.assertEqual(wf_stats["CI Pipeline"]["total_runs"], 3)
        # CI Pipeline runs: 30, 50, 20 -> avg = 33.33
        self.assertAlmostEqual(wf_stats["CI Pipeline"]["average_duration_seconds"], 33.33, places=1)

        br_stats = {b["branch"]: b for b in dur["by_branch"]}
        self.assertEqual(br_stats["main"]["total_runs"], 2)
        self.assertEqual(br_stats["main"]["average_duration_seconds"], 40.0)
        self.assertEqual(br_stats["feature-x"]["total_runs"], 2)
        self.assertEqual(br_stats["feature-x"]["average_duration_seconds"], 15.0)


class TestAnalyticsAPI(unittest.TestCase):
    """Test suite validating FastAPI analytics endpoints."""

    def setUp(self) -> None:
        """Create test DB session and set up TestClient with get_db override."""
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        """Clean up."""
        app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def _seed_api_data(self) -> None:
        """Seed sample data for API queries."""
        runs = [
            PipelineRunRecord(
                run_id=201,
                repository="test/repo",
                branch="main",
                commit_sha="sha111",
                status="completed",
                conclusion="success",
                workflow_name="CI",
                event="push",
                duration_seconds=25.0,
                failed_stage=None,
                failure_type="none",
                created_at=datetime(2026, 9, 12, 8, 0, 0, tzinfo=timezone.utc),
            ),
            PipelineRunRecord(
                run_id=202,
                repository="test/repo",
                branch="main",
                commit_sha="sha222",
                status="completed",
                conclusion="failure",
                workflow_name="CI",
                event="push",
                duration_seconds=15.0,
                failed_stage="test",
                failure_type="test_failure",
                created_at=datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        upsert_pipeline_runs(self.db, runs)

    def test_api_analytics_overview_empty(self) -> None:
        """Verify GET /api/pipelines/analytics/ returns valid structure when database is empty."""
        response = self.client.get("/api/pipelines/analytics/overview")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("summary", data)
        self.assertIn("trends", data)
        self.assertIn("failures", data)
        self.assertIn("duration", data)
        self.assertEqual(data["summary"]["total_runs"], 0)

    def test_api_analytics_summary_populated(self) -> None:
        """Verify GET /api/pipelines/analytics/summary returns accurate metric rates."""
        self._seed_api_data()
        response = self.client.get("/api/pipelines/analytics/summary")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total_runs"], 2)
        self.assertEqual(data["success_rate_percent"], 50.0)
        self.assertEqual(data["failure_rate_percent"], 50.0)
        self.assertEqual(data["average_duration_seconds"], 20.0)

    def test_api_analytics_trends_populated(self) -> None:
        """Verify GET /api/pipelines/analytics/trends returns array of daily entries."""
        self._seed_api_data()
        response = self.client.get("/api/pipelines/analytics/trends")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["date"], "2026-09-12")
        self.assertEqual(data[0]["total_runs"], 2)

    def test_api_analytics_failures_populated(self) -> None:
        """Verify GET /api/pipelines/analytics/failures returns failure type and stage rankings."""
        self._seed_api_data()
        response = self.client.get("/api/pipelines/analytics/failures")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["total_failures"], 1)
        self.assertEqual(data["by_failure_type"][0]["failure_type"], "test_failure")
        self.assertEqual(data["by_failed_stage"][0]["stage"], "test")

    def test_api_analytics_duration_populated(self) -> None:
        """Verify GET /api/pipelines/analytics/duration returns workflow and branch statistics."""
        self._seed_api_data()
        response = self.client.get("/api/pipelines/analytics/duration")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["overall"]["total_runs_with_duration"], 2)
        self.assertEqual(data["overall"]["average_duration_seconds"], 20.0)
        self.assertEqual(data["overall"]["min_duration_seconds"], 15.0)
        self.assertEqual(data["overall"]["max_duration_seconds"], 25.0)
        self.assertEqual(len(data["by_workflow"]), 1)
        self.assertEqual(len(data["by_branch"]), 1)
