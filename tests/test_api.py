"""Integration tests for FastAPI REST API endpoints using FastAPI TestClient."""

from datetime import datetime, timezone
import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.data_collection.github_collector import (
    GitHubAPIError,
    MissingTokenError,
    RateLimitExceededError,
    RepositoryNotFoundError,
)
from backend.app.data_collection.schemas import PipelineRunRecord
from backend.app.db.models import PipelineRunModel
from backend.app.db.repository import upsert_pipeline_runs
from backend.app.db.session import Base, get_db
from backend.app.main import app


class TestPipelineAPIs(unittest.TestCase):
    """Test suite validating all FastAPI API endpoints."""

    def setUp(self) -> None:
        """Create isolated in-memory test database and override get_db dependency."""
        self.env_patcher = patch.dict(os.environ, {"GITHUB_REPOSITORY": "test-owner/test-repo"})
        self.env_patcher.start()

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
        """Tear down database and clear dependency overrides."""
        app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.env_patcher.stop()

    def _seed_sample_runs(self) -> None:
        """Seed test database with known sample pipeline execution records."""
        records = [
            PipelineRunRecord(
                run_id=9001,
                repository="sumit7966/ci-cd-minor-project",
                branch="main",
                commit_sha="a1b2c3d4e5f6789012345678901234567890abcd",
                status="completed",
                conclusion="success",
                workflow_name="CI Pipeline",
                event="push",
                duration_seconds=45.0,
                failed_stage=None,
                failure_type="none",
                created_at=datetime.now(timezone.utc),
            ),
            PipelineRunRecord(
                run_id=9002,
                repository="sumit7966/ci-cd-minor-project",
                branch="member1",
                commit_sha="b2c3d4e5f6a1789012345678901234567890bcde",
                status="completed",
                conclusion="failure",
                workflow_name="CI Pipeline",
                event="workflow_dispatch",
                duration_seconds=15.0,
                failed_stage="dependency",
                failure_type="dependency_failure",
                created_at=datetime.now(timezone.utc),
            ),
        ]
        upsert_pipeline_runs(self.db, records)

    def test_root_endpoint(self) -> None:
        """Verify root endpoint returns project metadata and endpoint map."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "online")
        self.assertIn("endpoints", data)

    def test_health_endpoint(self) -> None:
        """Verify health check endpoint reports status."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertIn("status", response.json())

    def test_sync_pipeline_runs_mock(self) -> None:
        """Verify POST /api/pipelines/sync persists runs without duplicate collisions."""
        # Initial sync
        response = self.client.post("/api/pipelines/sync?use_mock=true&limit=4")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["data"]["inserted"], 4)
        self.assertEqual(data["data"]["updated"], 0)

        # Re-syncing same mock runs updates rather than duplicates
        response2 = self.client.post("/api/pipelines/sync?use_mock=true&limit=4")
        self.assertEqual(response2.status_code, 200)
        data2 = response2.json()
        self.assertEqual(data2["data"]["inserted"], 0)
        self.assertEqual(data2["data"]["updated"], 4)

    def test_get_pipelines_list(self) -> None:
        """Verify GET /api/pipelines retrieves stored runs."""
        self._seed_sample_runs()
        response = self.client.get("/api/pipelines")
        self.assertEqual(response.status_code, 200)
        runs = response.json()
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["run_id"], 9002)  # ordered desc by created_at
        self.assertEqual(runs[1]["run_id"], 9001)

    def test_get_pipelines_filtering(self) -> None:
        """Verify filtering by conclusion on GET /api/pipelines."""
        self._seed_sample_runs()
        response = self.client.get("/api/pipelines?conclusion=failure")
        self.assertEqual(response.status_code, 200)
        runs = response.json()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], 9002)
        self.assertEqual(runs[0]["failure_type"], "dependency_failure")

    def test_get_pipeline_by_id_success(self) -> None:
        """Verify GET /api/pipelines/{run_id} returns the correct run."""
        self._seed_sample_runs()
        response = self.client.get("/api/pipelines/9001")
        self.assertEqual(response.status_code, 200)
        run = response.json()
        self.assertEqual(run["run_id"], 9001)
        self.assertEqual(run["conclusion"], "success")

    def test_get_pipeline_by_id_not_found(self) -> None:
        """Verify GET /api/pipelines/{run_id} returns 404 for unknown run."""
        response = self.client.get("/api/pipelines/99999999")
        self.assertEqual(response.status_code, 404)
        self.assertIn("not found", response.json()["detail"].lower())

    def test_get_pipeline_stats(self) -> None:
        """Verify GET /api/pipelines/stats returns aggregated metrics."""
        self._seed_sample_runs()
        response = self.client.get("/api/pipelines/stats")
        self.assertEqual(response.status_code, 200)
        stats = response.json()
        self.assertEqual(stats["total_runs"], 2)
        self.assertEqual(stats["success_count"], 1)
        self.assertEqual(stats["failure_count"], 1)
        self.assertEqual(stats["success_rate_percent"], 50.0)
        self.assertEqual(stats["failure_rate_percent"], 50.0)
        self.assertEqual(stats["average_duration_seconds"], 30.0)
        self.assertEqual(stats["failure_types"]["dependency_failure"], 1)
        self.assertEqual(stats["failed_stages"]["dependency"], 1)

    @patch("backend.app.api.pipelines.sync_github_runs_to_db")
    def test_sync_missing_token_error_handling(self, mock_sync) -> None:
        """Verify 401 response on missing or invalid GitHub token."""
        mock_sync.side_effect = MissingTokenError("Invalid token")
        response = self.client.post("/api/pipelines/sync")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid token", response.json()["detail"])

    @patch("backend.app.api.pipelines.sync_github_runs_to_db")
    def test_sync_repository_not_found_error_handling(self, mock_sync) -> None:
        """Verify 404 response when repository is not found."""
        mock_sync.side_effect = RepositoryNotFoundError("Repository not found")
        response = self.client.post("/api/pipelines/sync")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Repository not found", response.json()["detail"])

    @patch("backend.app.api.pipelines.sync_github_runs_to_db")
    def test_sync_rate_limit_error_handling(self, mock_sync) -> None:
        """Verify 429 response when GitHub API rate limit is exceeded."""
        mock_sync.side_effect = RateLimitExceededError("Rate limit exceeded")
        response = self.client.post("/api/pipelines/sync")
        self.assertEqual(response.status_code, 429)
        self.assertIn("Rate limit exceeded", response.json()["detail"])

    @patch("backend.app.api.pipelines.sync_github_runs_to_db")
    def test_sync_github_api_error_handling(self, mock_sync) -> None:
        """Verify 502 Bad Gateway response on unexpected GitHub API errors."""
        mock_sync.side_effect = GitHubAPIError("GitHub 500 error")
        response = self.client.post("/api/pipelines/sync")
        self.assertEqual(response.status_code, 502)
        self.assertIn("GitHub 500 error", response.json()["detail"])

    def test_sync_real_github_flow_success_and_deduplication(self) -> None:
        """Verify end-to-end real GitHub data sync flow with mocked HTTP responses and deduplication."""
        raw_runs_payload = json.dumps({
            "total_count": 2,
            "workflow_runs": [
                {
                    "id": 8801,
                    "name": "CI Pipeline",
                    "head_branch": "main",
                    "head_sha": "a1b2c3d4e5f6789012345678901234567890abcd",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                    "created_at": "2026-09-12T10:00:00Z",
                    "updated_at": "2026-09-12T10:01:15Z",
                },
                {
                    "id": 8802,
                    "name": "CI Pipeline",
                    "head_branch": "feature-sync",
                    "head_sha": "b2c3d4e5f6a1789012345678901234567890bcde",
                    "event": "pull_request",
                    "status": "completed",
                    "conclusion": "failure",
                    "created_at": "2026-09-12T10:30:00Z",
                    "updated_at": "2026-09-12T10:30:45Z",
                },
            ],
        }).encode("utf-8")

        raw_jobs_payload = json.dumps({
            "jobs": [
                {
                    "id": 9901,
                    "name": "build-and-test",
                    "steps": [
                        {"name": "Checkout", "conclusion": "success"},
                        {"name": "Setup Python", "conclusion": "success"},
                        {"name": "Install Dependencies", "conclusion": "success"},
                        {"name": "Test Suite", "conclusion": "failure"},
                    ],
                }
            ]
        }).encode("utf-8")

        def fake_urlopen(req, timeout=15):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            mock_resp = MagicMock()
            if "jobs" in url:
                mock_resp.read.return_value = raw_jobs_payload
            else:
                mock_resp.read.return_value = raw_runs_payload
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        # 1. First sync with use_mock=false
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response = self.client.post("/api/pipelines/sync?use_mock=false&repository=test-owner/test-repo&limit=2")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["data"]["total_fetched"], 2)
            self.assertEqual(data["data"]["inserted"], 2)
            self.assertEqual(data["data"]["updated"], 0)

        # Verify records stored in PostgreSQL/database
        run1 = self.client.get("/api/pipelines/8801")
        self.assertEqual(run1.status_code, 200)
        self.assertEqual(run1.json()["conclusion"], "success")
        self.assertEqual(run1.json()["duration_seconds"], 75.0)

        run2 = self.client.get("/api/pipelines/8802")
        self.assertEqual(run2.status_code, 200)
        self.assertEqual(run2.json()["conclusion"], "failure")
        self.assertEqual(run2.json()["failed_stage"], "test")
        self.assertEqual(run2.json()["failure_type"], "test_failure")

        # 2. Re-syncing identical runs triggers safe update (deduplication)
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            response2 = self.client.post("/api/pipelines/sync?use_mock=false&repository=test-owner/test-repo&limit=2")
            self.assertEqual(response2.status_code, 200)
            data2 = response2.json()
            self.assertEqual(data2["data"]["inserted"], 0)
            self.assertEqual(data2["data"]["updated"], 2)

    def test_sync_real_github_flow_empty_runs(self) -> None:
        """Verify syncing when repository has no workflow runs returns 0 fetched."""
        empty_payload = json.dumps({"total_count": 0, "workflow_runs": []}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = empty_payload
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            response = self.client.post("/api/pipelines/sync?use_mock=false&repository=test-owner/empty-repo")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["data"]["total_fetched"], 0)
            self.assertEqual(data["data"]["inserted"], 0)

    def test_sync_missing_repository_error(self) -> None:
        """Verify 400 Bad Request when repository is missing from both environment and query."""
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.post("/api/pipelines/sync?use_mock=false")
            self.assertEqual(response.status_code, 400)
            self.assertIn("Invalid repository", response.json()["detail"])

    def test_sync_real_github_http_401(self) -> None:
        """Verify real GitHub sync surfaces HTTP 401 Unauthorized for invalid/missing token."""
        http_err = urllib.error.HTTPError(
            url="https://api.github.com/test",
            code=401,
            msg="Unauthorized",
            hdrs=MagicMock(),
            fp=io.BytesIO(b'{"message": "Bad credentials"}'),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            response = self.client.post("/api/pipelines/sync?use_mock=false&repository=test-owner/test-repo")
            self.assertEqual(response.status_code, 401)
            self.assertIn("401 Unauthorized", response.json()["detail"])

    def test_sync_real_github_http_404(self) -> None:
        """Verify real GitHub sync surfaces HTTP 404 Not Found for missing repo."""
        http_err = urllib.error.HTTPError(
            url="https://api.github.com/test",
            code=404,
            msg="Not Found",
            hdrs=MagicMock(),
            fp=io.BytesIO(b'{"message": "Not Found"}'),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            response = self.client.post("/api/pipelines/sync?use_mock=false&repository=test-owner/missing-repo")
            self.assertEqual(response.status_code, 404)
            self.assertIn("not found", response.json()["detail"].lower())

    def test_sync_real_github_http_429_rate_limit(self) -> None:
        """Verify real GitHub sync surfaces HTTP 429 Too Many Requests when rate limited."""
        hdrs = MagicMock()
        hdrs.get.side_effect = lambda k, default=None: "0" if k == "x-ratelimit-remaining" else "1700000000"
        http_err = urllib.error.HTTPError(
            url="https://api.github.com/test",
            code=403,
            msg="Forbidden",
            hdrs=hdrs,
            fp=io.BytesIO(b'{"message": "API rate limit exceeded"}'),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            response = self.client.post("/api/pipelines/sync?use_mock=false&repository=test-owner/test-repo")
            self.assertEqual(response.status_code, 429)
            self.assertIn("rate limit", response.json()["detail"].lower())

    def test_sync_real_github_network_error(self) -> None:
        """Verify real GitHub sync surfaces HTTP 502 Bad Gateway on connection/URL error."""
        url_err = urllib.error.URLError("Connection refused or unreachable DNS")
        with patch("urllib.request.urlopen", side_effect=url_err):
            response = self.client.post("/api/pipelines/sync?use_mock=false&repository=test-owner/test-repo")
            self.assertEqual(response.status_code, 502)
            self.assertIn("Failed to reach GitHub API", response.json()["detail"])

