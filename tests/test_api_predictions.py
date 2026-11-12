"""Integration tests for FastAPI CI/CD failure prediction REST endpoints."""

from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.app.api.predictions import get_prediction_service
from backend.app.main import app
from ml.predictor import ModelUnavailableError, PipelineFailurePredictor


class TestPredictionAPI(unittest.TestCase):
    """Test suite validating POST /api/predictions and GET /api/predictions/status endpoints."""

    def setUp(self) -> None:
        """Create TestClient instance."""
        self.client = TestClient(app)

    def tearDown(self) -> None:
        """Clear any FastAPI dependency overrides."""
        app.dependency_overrides.clear()

    def test_post_prediction_success(self) -> None:
        """Requirement: Verify valid pre-run payload returns HTTP 200 with full PredictionResponse schema."""
        payload = {
            "repository": "sumit7966/ci-cd-minor-project",
            "branch": "member1",
            "workflow_name": "CI Pipeline",
            "event": "push",
            "created_at": "2026-09-09T16:00:00Z",
            "run_id": 34322655257,
        }

        response = self.client.post("/api/predictions", json=payload)
        self.assertEqual(response.status_code, 200, response.text)

        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIn(data["predicted_class"], [0, 1])
        self.assertIsInstance(data["is_failure_predicted"], bool)
        self.assertEqual(data["is_failure_predicted"], bool(data["predicted_class"] == 1))

        # Probability boundaries
        self.assertGreaterEqual(data["failure_probability"], 0.0)
        self.assertLessEqual(data["failure_probability"], 1.0)
        self.assertGreaterEqual(data["success_probability"], 0.0)
        self.assertLessEqual(data["success_probability"], 1.0)
        self.assertAlmostEqual(data["failure_probability"] + data["success_probability"], 1.0, places=3)

        # Risk classification
        self.assertIn(data["risk_level"], ["low", "medium", "high"])
        self.assertGreaterEqual(data["confidence_score"], 0.0)
        self.assertLessEqual(data["confidence_score"], 1.0)

        # Feature evaluation and metadata
        self.assertIn("features_evaluated", data)
        self.assertEqual(data["features_evaluated"]["repository"], "sumit7966/ci-cd-minor-project")
        self.assertIn("model_metadata", data)
        self.assertEqual(data["model_metadata"]["model_type"], "LogisticRegression")
        self.assertEqual(data["model_metadata"]["model_version"], "1.0.0")

    def test_post_prediction_missing_required_fields_returns_422(self) -> None:
        """Requirement: Verify missing required fields return HTTP 422 Unprocessable Entity."""
        # Missing branch and workflow_name
        invalid_payload = {
            "repository": "sumit7966/ci-cd-minor-project",
        }

        response = self.client.post("/api/predictions", json=invalid_payload)
        self.assertEqual(response.status_code, 422)
        errors = response.json().get("detail", [])
        missing_fields = [err["loc"][-1] for err in errors]
        self.assertIn("branch", missing_fields)
        self.assertIn("workflow_name", missing_fields)
        self.assertIn("event", missing_fields)

    def test_post_prediction_empty_strings_rejected_with_422(self) -> None:
        """Requirement: Verify empty strings for required fields return HTTP 422."""
        invalid_payload = {
            "repository": "",
            "branch": "main",
            "workflow_name": "CI Pipeline",
            "event": "push",
        }

        response = self.client.post("/api/predictions", json=invalid_payload)
        self.assertEqual(response.status_code, 422)

    def test_post_prediction_unseen_categories_succeeds(self) -> None:
        """Requirement: Verify unseen repository or branch names evaluate cleanly without error."""
        unseen_payload = {
            "repository": "unknown-org/unknown-repo",
            "branch": "feature/unseen-neural-model",
            "workflow_name": "Custom Dynamic CI",
            "event": "workflow_dispatch",
        }

        response = self.client.post("/api/predictions", json=unseen_payload)
        self.assertEqual(response.status_code, 200, response.text)

        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["failure_probability"], 0.0)
        self.assertLessEqual(data["failure_probability"], 1.0)

    def test_post_prediction_target_leakage_safely_ignored(self) -> None:
        """Requirement: Verify post-execution outcome fields in payload are safely ignored."""
        leaky_payload = {
            "repository": "sumit7966/ci-cd-minor-project",
            "branch": "main",
            "workflow_name": "CI Pipeline",
            "event": "push",
            # Outcome fields that must not cause leakage
            "status": "completed",
            "conclusion": "failure",
            "duration_seconds": 120,
            "failed_stage": "test",
            "failure_type": "test_failure",
        }

        response = self.client.post("/api/predictions", json=leaky_payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["status"], "success")

    def test_post_prediction_model_unavailable_returns_503(self) -> None:
        """Requirement: Verify unready predictor triggers HTTP 503 Service Unavailable."""
        mock_unready = MagicMock(spec=PipelineFailurePredictor)
        mock_unready.is_ready.return_value = False

        app.dependency_overrides[get_prediction_service] = lambda: mock_unready

        payload = {
            "repository": "sumit7966/ci-cd-minor-project",
            "branch": "main",
            "workflow_name": "CI Pipeline",
            "event": "push",
        }

        response = self.client.post("/api/predictions", json=payload)
        self.assertEqual(response.status_code, 503)
        self.assertIn("unavailable", response.json()["detail"].lower())

    def test_get_prediction_status_ready(self) -> None:
        """Requirement: Verify GET /api/predictions/status returns ready status and model metadata."""
        response = self.client.get("/api/predictions/status")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["status"], "ready")
        self.assertTrue(data["model_available"])
        self.assertEqual(data["model_type"], "LogisticRegression")
        self.assertEqual(data["model_version"], "1.0.0")

    def test_get_prediction_status_unavailable(self) -> None:
        """Requirement: Verify GET /api/predictions/status reflects unready model state."""
        mock_unready = MagicMock(spec=PipelineFailurePredictor)
        mock_unready.is_ready.return_value = False
        mock_unready.metadata = {}

        app.dependency_overrides[get_prediction_service] = lambda: mock_unready

        response = self.client.get("/api/predictions/status")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["status"], "unavailable")
        self.assertFalse(data["model_available"])
        self.assertIsNone(data["model_version"])

    def test_root_metadata_lists_prediction_endpoints(self) -> None:
        """Requirement: Verify root discovery endpoint lists prediction routes."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        endpoints = data.get("endpoints", {})
        self.assertIn("predictions", endpoints)
        self.assertIn("prediction_status", endpoints)

    def test_preservation_of_member1_pipeline_routes(self) -> None:
        """Requirement: Verify existing Member 1 pipeline routes remain functional without interference."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from backend.app.db.session import Base, get_db

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        def override_db():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        try:
            resp_list = self.client.get("/api/pipelines")
            self.assertEqual(resp_list.status_code, 200)
            self.assertIsInstance(resp_list.json(), list)

            resp_stats = self.client.get("/api/pipelines/stats")
            self.assertEqual(resp_stats.status_code, 200)
            self.assertIn("total_runs", resp_stats.json())
        finally:
            Base.metadata.drop_all(bind=engine)

    def test_preservation_of_member3_analytics_routes(self) -> None:
        """Requirement: Verify existing Member 3 analytics routes remain functional without interference."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from backend.app.db.session import Base, get_db

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        def override_db():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        try:
            resp_overview = self.client.get("/api/pipelines/analytics/overview")
            self.assertEqual(resp_overview.status_code, 200)
            self.assertIn("summary", resp_overview.json())

            resp_summary = self.client.get("/api/pipelines/analytics/summary")
            self.assertEqual(resp_summary.status_code, 200)
            self.assertIn("total_runs", resp_summary.json())
        finally:
            Base.metadata.drop_all(bind=engine)


if __name__ == "__main__":
    unittest.main()
