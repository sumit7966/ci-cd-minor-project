"""Unit tests for GitHub Actions workflow run data collector."""

from datetime import datetime, timezone
import io
import os
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from backend.app.data_collection import (
    GitHubAPIError,
    GitHubCollectorError,
    GitHubWorkflowRunCollector,
    MissingTokenError,
    PipelineRunRecord,
    RateLimitExceededError,
    RepositoryNotFoundError,
    collect_workflow_runs,
)


class TestGitHubWorkflowRunCollector(unittest.TestCase):
    """Test suite validating GitHub Actions workflow run data collection and parsing."""

    def setUp(self) -> None:
        """Set up test fixtures with test repository."""
        self.repo = "sumit7966/ci-cd-minor-project"
        self.collector = GitHubWorkflowRunCollector(repository=self.repo)

    def test_import_and_schema_usage(self) -> None:
        """Verify collector imports and produces instances of PipelineRunRecord."""
        sample_run = self.collector.get_sample_run(failure_type="none")
        self.assertIsInstance(sample_run, PipelineRunRecord)
        self.assertEqual(sample_run.repository, self.repo)

    def test_environment_variables_configuration(self) -> None:
        """Verify repository and token are loaded from environment variables."""
        with patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "test-owner/test-repo",
            "GITHUB_TOKEN": "mock-token-abc123xyz",
        }):
            collector = GitHubWorkflowRunCollector()
            self.assertEqual(collector.repository, "test-owner/test-repo")
            self.assertEqual(collector.github_token, "mock-token-abc123xyz")
            headers = collector._get_headers()
            self.assertEqual(headers["Authorization"], "Bearer mock-token-abc123xyz")

    def test_no_hardcoded_secrets(self) -> None:
        """Verify collector initializes without token and headers do not contain dummy secrets."""
        collector = GitHubWorkflowRunCollector(repository=self.repo, github_token=None)
        headers = collector._get_headers()
        self.assertNotIn("Authorization", headers)

    def test_successful_workflow_run(self) -> None:
        """Verify parsing of a successful workflow execution."""
        raw_run = {
            "id": 2001,
            "name": "CI Pipeline",
            "head_branch": "main",
            "head_sha": "f1a2b3c4d5e6789012345678901234567890ffff",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-09T12:00:00Z",
            "updated_at": "2026-09-09T12:00:50Z",
        }
        record = self.collector.parse_workflow_run(raw_run)
        self.assertEqual(record.run_id, 2001)
        self.assertEqual(record.status, "completed")
        self.assertEqual(record.conclusion, "success")
        self.assertIsNone(record.failed_stage)
        self.assertEqual(record.failure_type, "none")
        self.assertEqual(record.duration_seconds, 50.0)

    def test_test_failure_mapping(self) -> None:
        """Verify that step-level test failures map to test_failure."""
        raw_run = {
            "id": 2002,
            "name": "CI Pipeline",
            "head_branch": "member1",
            "head_sha": "a1a2a3a4a5a6789012345678901234567890aaaa",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-09-09T12:10:00Z",
            "updated_at": "2026-09-09T12:10:35Z",
        }
        jobs_data = [
            {
                "name": "build-and-test",
                "steps": [
                    {"name": "Checkout", "conclusion": "success"},
                    {"name": "Setup Python", "conclusion": "success"},
                    {"name": "Install Dependencies", "conclusion": "success"},
                    {"name": "Build", "conclusion": "success"},
                    {"name": "Test", "conclusion": "failure"},
                ],
            }
        ]
        record = self.collector.parse_workflow_run(raw_run, jobs_data=jobs_data)
        self.assertEqual(record.failed_stage, "test")
        self.assertEqual(record.failure_type, "test_failure")
        self.assertEqual(record.duration_seconds, 35.0)

    def test_build_failure_mapping(self) -> None:
        """Verify that step-level build failures map to build_failure."""
        raw_run = {
            "id": 2003,
            "name": "CI Pipeline",
            "head_branch": "member1",
            "head_sha": "b1b2b3b4b5b6789012345678901234567890bbbb",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-09-09T12:20:00Z",
            "updated_at": "2026-09-09T12:20:25Z",
        }
        jobs_data = [
            {
                "name": "build-and-test",
                "steps": [
                    {"name": "Checkout", "conclusion": "success"},
                    {"name": "Setup Python", "conclusion": "success"},
                    {"name": "Install Dependencies", "conclusion": "success"},
                    {"name": "Build", "conclusion": "failure"},
                    {"name": "Test", "conclusion": "skipped"},
                ],
            }
        ]
        record = self.collector.parse_workflow_run(raw_run, jobs_data=jobs_data)
        self.assertEqual(record.failed_stage, "build")
        self.assertEqual(record.failure_type, "build_failure")
        self.assertEqual(record.duration_seconds, 25.0)

    def test_dependency_failure_mapping(self) -> None:
        """Verify that step-level dependency failures map to dependency_failure."""
        raw_run = {
            "id": 2004,
            "name": "CI Pipeline",
            "head_branch": "member1",
            "head_sha": "c1c2c3c4c5c6789012345678901234567890cccc",
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-09-09T12:30:00Z",
            "updated_at": "2026-09-09T12:30:12Z",
        }
        jobs_data = [
            {
                "name": "build-and-test",
                "steps": [
                    {"name": "Checkout", "conclusion": "success"},
                    {"name": "Setup Python", "conclusion": "success"},
                    {"name": "Install Dependencies", "conclusion": "failure"},
                    {"name": "Build", "conclusion": "skipped"},
                    {"name": "Test", "conclusion": "skipped"},
                ],
            }
        ]
        record = self.collector.parse_workflow_run(raw_run, jobs_data=jobs_data)
        self.assertEqual(record.failed_stage, "dependency")
        self.assertEqual(record.failure_type, "dependency_failure")
        self.assertEqual(record.duration_seconds, 12.0)

    def test_unknown_and_incomplete_failure(self) -> None:
        """Verify that unknown failure sources are mapped safely and missing IDs raise errors."""
        raw_run = {
            "id": 2005,
            "name": "CI Pipeline",
            "head_branch": "main",
            "head_sha": "d1d2d3d4d5d6789012345678901234567890dddd",
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-09-09T12:40:00Z",
            "updated_at": "2026-09-09T12:40:15Z",
        }
        # No jobs data and no inputs -> should map to unknown
        record = self.collector.parse_workflow_run(raw_run, jobs_data=[])
        self.assertEqual(record.failed_stage, "unknown")
        self.assertEqual(record.failure_type, "unknown")

        # Incomplete run missing id
        with self.assertRaises(ValueError):
            self.collector.parse_workflow_run({"status": "completed"})

    def test_duration_seconds_calculation(self) -> None:
        """Verify accurate duration calculation and non-negative constraints."""
        raw_run = {
            "id": 2006,
            "created_at": "2026-09-09T13:00:00Z",
            "updated_at": "2026-09-09T13:02:15.500Z",
            "status": "completed",
            "head_sha": "1234567890abcdef",
        }
        record = self.collector.parse_workflow_run(raw_run)
        self.assertEqual(record.duration_seconds, 135.5)

    def test_mock_collection_path(self) -> None:
        """Verify collect_workflow_runs(use_mock=True) returns validated records."""
        runs = collect_workflow_runs(use_mock=True)
        self.assertEqual(len(runs), 4)
        for run in runs:
            self.assertIsInstance(run, PipelineRunRecord)
        # Check that all failure types are represented
        failure_types = {r.failure_type for r in runs}
        self.assertEqual(failure_types, {"none", "dependency_failure", "build_failure", "test_failure"})

    def test_github_api_401_unauthorized(self) -> None:
        """Verify that HTTP 401 raises MissingTokenError."""
        mock_err = urllib.error.HTTPError(
            url="https://api.github.com/test",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore
            fp=io.BytesIO(b'{"message": "Bad credentials"}'),
        )
        with patch("urllib.request.urlopen", side_effect=mock_err):
            with self.assertRaises(MissingTokenError):
                self.collector._http_get("https://api.github.com/test")

    def test_github_api_404_not_found(self) -> None:
        """Verify that HTTP 404 raises RepositoryNotFoundError."""
        mock_err = urllib.error.HTTPError(
            url="https://api.github.com/test",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore
            fp=io.BytesIO(b'{"message": "Not Found"}'),
        )
        with patch("urllib.request.urlopen", side_effect=mock_err):
            with self.assertRaises(RepositoryNotFoundError):
                self.collector._http_get("https://api.github.com/test")

    def test_github_api_403_rate_limit(self) -> None:
        """Verify that HTTP 403 with zero remaining rate limit raises RateLimitExceededError."""
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"}
        mock_err = urllib.error.HTTPError(
            url="https://api.github.com/test",
            code=403,
            msg="rate limit exceeded",
            hdrs=headers,  # type: ignore
            fp=io.BytesIO(b'{"message": "API rate limit exceeded"}'),
        )
        with patch("urllib.request.urlopen", side_effect=mock_err):
            with self.assertRaises(RateLimitExceededError):
                self.collector._http_get("https://api.github.com/test")

    def test_github_api_network_url_error(self) -> None:
        """Verify that network unreachable raises GitHubAPIError."""
        mock_err = urllib.error.URLError(reason="Connection refused")
        with patch("urllib.request.urlopen", side_effect=mock_err):
            with self.assertRaises(GitHubAPIError):
                self.collector._http_get("https://api.github.com/test")


if __name__ == "__main__":
    unittest.main()
