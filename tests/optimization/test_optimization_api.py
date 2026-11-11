"""Integration tests for optimization API routes (standalone and mounted in backend.app.main)."""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.data_collection.schemas import PipelineRunRecord
from backend.app.db.repository import upsert_pipeline_runs
from backend.app.db.session import Base, get_db
from backend.app.main import app as main_app
from optimization.api import app as standalone_app


class TestOptimizationAPI(unittest.TestCase):
    """Validates standalone optimization endpoints and unified backend.app.main mounting."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        def override_get_db():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        standalone_app.dependency_overrides[get_db] = override_get_db
        main_app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(standalone_app)
        self.main_client = TestClient(main_app)

    def tearDown(self) -> None:
        standalone_app.dependency_overrides.clear()
        main_app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _seed_db_runs(self) -> None:
        base = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)
        records = [
            PipelineRunRecord(
                run_id=301,
                repository="owner/repo-a",
                branch="main",
                commit_sha="aaaa1111",
                status="completed",
                conclusion="success",
                workflow_name="CI Pipeline",
                event="push",
                duration_seconds=20.0,
                failed_stage=None,
                failure_type="none",
                created_at=base,
            ),
            PipelineRunRecord(
                run_id=302,
                repository="owner/repo-a",
                branch="main",
                commit_sha="bbbb2222",
                status="completed",
                conclusion="success",
                workflow_name="CI Pipeline",
                event="push",
                duration_seconds=30.0,
                failed_stage=None,
                failure_type="none",
                created_at=base + timedelta(minutes=10),
            ),
            PipelineRunRecord(
                run_id=303,
                repository="owner/repo-a",
                branch="main",
                commit_sha="cccc3333",
                status="completed",
                conclusion="success",
                workflow_name="CI Pipeline",
                event="push",
                duration_seconds=40.0,
                failed_stage=None,
                failure_type="none",
                created_at=base + timedelta(minutes=20),
            ),
            PipelineRunRecord(
                run_id=304,
                repository="owner/repo-a",
                branch="feature",
                commit_sha="dddd4444",
                status="completed",
                conclusion="failure",
                workflow_name="CI Pipeline",
                event="pull_request",
                duration_seconds=10.0,
                failed_stage="test",
                failure_type="test_failure",
                created_at=base + timedelta(minutes=30),
            ),
            PipelineRunRecord(
                run_id=305,
                repository="owner/repo-b",
                branch="main",
                commit_sha="eeee5555",
                status="completed",
                conclusion="success",
                workflow_name="CI Pipeline",
                event="push",
                duration_seconds=100.0,
                failed_stage=None,
                failure_type="none",
                created_at=base + timedelta(minutes=40),
            ),
        ]
        upsert_pipeline_runs(self.db, records)

    def test_get_bottlenecks_empty_database(self) -> None:
        response = self.client.get("/api/bottlenecks")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["average_pipeline_duration"], 0.0)
        self.assertEqual(data["stage_metrics"], {})
        self.assertIsNone(data["slowest_stage"])
        self.assertEqual(data["potential_bottlenecks"], [])
        self.assertEqual(data["message"], "Empty dataset")

    def test_get_recommendations_empty_database(self) -> None:
        response = self.client.get("/api/recommendations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_get_optimization_summary_from_real_db_and_affected_stage_null(self) -> None:
        self._seed_db_runs()
        response = self.client.get("/api/optimization")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        bottlenecks = data["bottlenecks"]
        # (20 + 30 + 40 + 10 + 100) / 5 = 40.0
        self.assertEqual(bottlenecks["average_pipeline_duration"], 40.0)
        self.assertEqual(bottlenecks["stage_metrics"], {})
        self.assertIsNone(bottlenecks["slowest_stage"])
        self.assertEqual(
            bottlenecks["message"],
            "Stage-level duration breakdown is unavailable; only run-level pipeline duration and failed_stage telemetry are stored.",
        )

        recommendations = data["recommendations"]
        trend_rec = next(
            (r for r in recommendations if r["title"] == "Review Recent Pipeline Changes"),
            None,
        )
        self.assertIsNotNone(trend_rec)
        # Rule 5 affected_stage must serialize to JSON null (None), not "pipeline"
        self.assertIsNone(trend_rec["affected_stage"])

    def test_repository_query_filtering(self) -> None:
        self._seed_db_runs()

        res_a = self.client.get("/api/optimization?repository=owner/repo-a")
        self.assertEqual(res_a.status_code, 200)
        data_a = res_a.json()
        # (20 + 30 + 40 + 10) / 4 = 25.0
        self.assertEqual(data_a["bottlenecks"]["average_pipeline_duration"], 25.0)
        titles_a = [r["title"] for r in data_a["recommendations"]]
        # 1/4 = 25% failed_stage="test" across 4 eligible runs triggers Rule 4 fallback
        self.assertIn("Investigate Stage Reliability", titles_a)
        self.assertIn("Review Recent Pipeline Changes", titles_a)

        res_b = self.client.get("/api/bottlenecks?repository=owner/repo-b")
        self.assertEqual(res_b.status_code, 200)
        self.assertEqual(res_b.json()["average_pipeline_duration"], 100.0)

        res_recs_b = self.client.get("/api/recommendations?repository=owner/repo-b")
        self.assertEqual(res_recs_b.status_code, 200)
        self.assertEqual(res_recs_b.json(), [])

    def test_unified_main_app_mounts_optimization_and_preserves_member1_routes(self) -> None:
        self._seed_db_runs()

        root_res = self.main_client.get("/")
        self.assertEqual(root_res.status_code, 200)
        root_data = root_res.json()
        self.assertEqual(root_data["status"], "online")
        self.assertIn("optimization", root_data["endpoints"])
        self.assertIn("bottlenecks", root_data["endpoints"])
        self.assertIn("recommendations", root_data["endpoints"])

        # Member 3 endpoints mounted in backend.app.main
        opt_res = self.main_client.get("/api/optimization?repository=owner/repo-a")
        self.assertEqual(opt_res.status_code, 200)
        self.assertEqual(opt_res.json()["bottlenecks"]["average_pipeline_duration"], 25.0)

        bot_res = self.main_client.get("/api/bottlenecks")
        self.assertEqual(bot_res.status_code, 200)
        self.assertEqual(bot_res.json()["average_pipeline_duration"], 40.0)

        rec_res = self.main_client.get("/api/recommendations?repository=owner/repo-a")
        self.assertEqual(rec_res.status_code, 200)
        self.assertGreaterEqual(len(rec_res.json()), 1)

        # Member 1 routes remain functional
        self.assertEqual(self.main_client.get("/health").status_code, 200)
        pipelines_res = self.main_client.get("/api/pipelines")
        self.assertEqual(pipelines_res.status_code, 200)
        self.assertEqual(len(pipelines_res.json()), 5)
        self.assertEqual(self.main_client.get("/api/pipelines/stats").status_code, 200)
        self.assertEqual(self.main_client.get("/api/pipelines/analytics/summary").status_code, 200)

    @patch("optimization.api.get_pipeline_data")
    def test_api_empty_data_handling(self, mock_get_data) -> None:
        mock_get_data.return_value = []

        res_bottlenecks = self.client.get("/api/bottlenecks")
        self.assertEqual(res_bottlenecks.status_code, 200)
        self.assertEqual(res_bottlenecks.json()["average_pipeline_duration"], 0.0)

        res_recs = self.client.get("/api/recommendations")
        self.assertEqual(res_recs.status_code, 200)
        self.assertEqual(res_recs.json(), [])

    @patch("optimization.api.get_pipeline_data")
    def test_api_error_handling(self, mock_get_data) -> None:
        mock_get_data.side_effect = Exception("Database connection failed")

        response = self.client.get("/api/optimization")
        self.assertEqual(response.status_code, 500)
        self.assertIn("Database connection failed", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
