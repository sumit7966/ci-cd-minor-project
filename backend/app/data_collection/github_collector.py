"""GitHub Actions CI/CD workflow run data collector.

This module provides the collector service to extract, parse, and validate
workflow run metadata from GitHub Actions via the GitHub REST API.
"""

from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request

from .schemas import PipelineRunRecord


class GitHubCollectorError(Exception):
    """Base exception for GitHub Actions data collection errors."""


class MissingTokenError(GitHubCollectorError):
    """Raised when authentication token is required but missing."""


class RepositoryNotFoundError(GitHubCollectorError):
    """Raised when repository cannot be found or accessed."""


class RateLimitExceededError(GitHubCollectorError):
    """Raised when GitHub REST API rate limits are hit."""


class GitHubAPIError(GitHubCollectorError):
    """Raised when GitHub REST API returns an unexpected error."""


def _parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 datetime string from GitHub API into a datetime object."""
    if not dt_str:
        return None
    normalized = dt_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


class GitHubWorkflowRunCollector:
    """Collector service for GitHub Actions workflow execution telemetry.

    Retrieves workflow run records, parses job and step-level failures,
    normalizes execution duration, and validates the output into
    PipelineRunRecord schema instances.
    """

    def __init__(
        self,
        repository: Optional[str] = None,
        github_token: Optional[str] = None,
    ) -> None:
        """Initialize the workflow run collector.

        Args:
            repository: Repository slug in 'owner/repo' format.
                        Defaults to GITHUB_REPOSITORY environment variable.
            github_token: Personal access token for GitHub API authorization.
                          Defaults to GITHUB_TOKEN environment variable.
        """
        repo = repository or os.getenv("GITHUB_REPOSITORY")
        if not repo or "/" not in repo:
            raise ValueError(
                "Invalid repository. Provide repository in 'owner/repo' format "
                "or set the GITHUB_REPOSITORY environment variable."
            )

        self.repository = repo.strip()
        self.github_token = github_token or os.getenv("GITHUB_TOKEN")
        self.api_base_url = "https://api.github.com"

    def _get_headers(self) -> Dict[str, str]:
        """Construct secure HTTP headers for GitHub REST API calls."""
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "CI-CD-Platform-Failure-Predictor",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    def _http_get(self, url: str) -> Dict[str, Any]:
        """Execute an authenticated HTTP GET request against the GitHub REST API.

        Args:
            url: Full target URL.

        Returns:
            Parsed JSON dictionary.
        """
        req = urllib.request.Request(url, headers=self._get_headers())
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                payload = response.read().decode("utf-8")
                return json.loads(payload)
        except urllib.error.HTTPError as err:
            if err.code == 401:
                raise MissingTokenError(
                    "GitHub API authentication failed (401 Unauthorized). "
                    "Check that GITHUB_TOKEN is valid."
                ) from err
            if err.code == 403:
                # Inspect rate limiting headers if available
                rate_limit_remaining = err.headers.get("x-ratelimit-remaining")
                if rate_limit_remaining == "0":
                    reset_time = err.headers.get("x-ratelimit-reset", "unknown")
                    raise RateLimitExceededError(
                        f"GitHub API rate limit exceeded. Reset timestamp: {reset_time}"
                    ) from err
                raise GitHubAPIError(f"GitHub API access forbidden (403): {err.reason}") from err
            if err.code == 404:
                raise RepositoryNotFoundError(
                    f"Repository '{self.repository}' was not found (404) or token lacks access."
                ) from err
            raise GitHubAPIError(f"GitHub API returned HTTP error {err.code}: {err.reason}") from err
        except urllib.error.URLError as err:
            raise GitHubAPIError(f"Failed to reach GitHub API: {err.reason}") from err
        except json.JSONDecodeError as err:
            raise GitHubAPIError("Invalid JSON received from GitHub API.") from err

    def fetch_jobs_for_run(self, run_id: int) -> List[Dict[str, Any]]:
        """Retrieve job and step execution details for a specific workflow run.

        Args:
            run_id: Unique GitHub Actions workflow run identifier.

        Returns:
            List of job detail dictionaries.
        """
        url = f"{self.api_base_url}/repos/{self.repository}/actions/runs/{run_id}/jobs"
        data = self._http_get(url)
        return data.get("jobs", [])

    def parse_workflow_run(
        self,
        raw_run: Dict[str, Any],
        jobs_data: Optional[List[Dict[str, Any]]] = None,
    ) -> PipelineRunRecord:
        """Parse raw GitHub Actions API response payload into a validated PipelineRunRecord.

        Args:
            raw_run: Dictionary containing raw workflow run metadata.
            jobs_data: Optional list of job payloads to identify specific failed steps.

        Returns:
            A validated PipelineRunRecord instance.
        """
        if "id" not in raw_run:
            raise ValueError("Incomplete workflow run data: missing 'id' field.")

        run_id = int(raw_run["id"])
        branch = raw_run.get("head_branch") or "main"
        commit_sha = raw_run.get("head_sha") or "unknown"
        status = raw_run.get("status", "unknown")
        conclusion = raw_run.get("conclusion")
        workflow_name = raw_run.get("name", "CI Pipeline")
        event = raw_run.get("event", "push")

        # Parse timestamps to compute duration in seconds
        created_at_dt = _parse_iso_datetime(raw_run.get("created_at")) or datetime.now(timezone.utc)
        updated_at_dt = _parse_iso_datetime(raw_run.get("updated_at"))

        duration_seconds: Optional[float] = None
        if updated_at_dt and created_at_dt:
            delta = (updated_at_dt - created_at_dt).total_seconds()
            duration_seconds = max(0.0, round(delta, 2))

        # Determine failure details if the run concluded with failure
        failed_stage: Optional[str] = None
        failure_type: Optional[str] = "none"

        # Check workflow_dispatch inputs if recorded
        workflow_inputs = raw_run.get("inputs") or {}
        if "failure_type" in workflow_inputs:
            failure_type = workflow_inputs.get("failure_type")

        # Inspect jobs / steps to pinpoint specific failed stage
        if conclusion == "failure":
            failed_stage = self._detect_failed_stage(jobs_data, failure_type)
            if failure_type in (None, "none") and failed_stage != "unknown":
                failure_type = f"{failed_stage}_failure"
            elif failed_stage == "unknown" and (not failure_type or failure_type == "none"):
                failure_type = "unknown"

        return PipelineRunRecord(
            run_id=run_id,
            repository=self.repository,
            branch=branch,
            commit_sha=commit_sha,
            status=status,
            conclusion=conclusion,
            workflow_name=workflow_name,
            event=event,
            duration_seconds=duration_seconds,
            failed_stage=failed_stage,
            failure_type=failure_type,
            created_at=created_at_dt,
        )

    def _detect_failed_stage(
        self,
        jobs_data: Optional[List[Dict[str, Any]]],
        default_failure_type: Optional[str],
    ) -> str:
        """Inspect jobs and steps data to pinpoint the specific failure stage.

        Maps failed steps to one of: 'dependency', 'build', 'test', or 'unknown'.
        """
        if jobs_data:
            for job in jobs_data:
                steps = job.get("steps", [])
                for step in steps:
                    if step.get("conclusion") == "failure":
                        name = step.get("name", "").lower()
                        if "dependenc" in name:
                            return "dependency"
                        if "build" in name:
                            return "build"
                        if "test" in name:
                            return "test"

        if default_failure_type and default_failure_type != "none":
            clean_type = default_failure_type.replace("_failure", "").lower()
            if clean_type in ("dependency", "build", "test"):
                return clean_type

        return "unknown"

    def fetch_workflow_runs(
        self,
        limit: int = 10,
        fetch_jobs: bool = True,
    ) -> List[PipelineRunRecord]:
        """Retrieve recent workflow runs from GitHub API and parse into records.

        Args:
            limit: Maximum number of workflow run records to return (1-100).
            fetch_jobs: Whether to fetch granular job/step details for failed runs.

        Returns:
            List of validated PipelineRunRecord instances.
        """
        query = urllib.parse.urlencode({"per_page": min(max(1, limit), 100)})
        url = f"{self.api_base_url}/repos/{self.repository}/actions/runs?{query}"

        data = self._http_get(url)
        raw_runs = data.get("workflow_runs", [])

        records: List[PipelineRunRecord] = []
        for raw_run in raw_runs[:limit]:
            jobs_data: Optional[List[Dict[str, Any]]] = None
            if fetch_jobs and raw_run.get("conclusion") == "failure":
                try:
                    jobs_data = self.fetch_jobs_for_run(raw_run["id"])
                except GitHubCollectorError:
                    # Incomplete job data should not break the overall run collection
                    jobs_data = None

            record = self.parse_workflow_run(raw_run, jobs_data=jobs_data)
            records.append(record)

        return records

    def get_sample_run(self, failure_type: str = "none") -> PipelineRunRecord:
        """Generate a single sample PipelineRunRecord for backward compatibility.

        Args:
            failure_type: One of 'none', 'dependency_failure', 'build_failure', 'test_failure'.

        Returns:
            A validated PipelineRunRecord instance.
        """
        all_mock_runs = self.get_mock_workflow_runs()
        for run in all_mock_runs:
            if run.failure_type == failure_type:
                return run
        return all_mock_runs[0]

    def get_mock_workflow_runs(self) -> List[PipelineRunRecord]:
        """Generate realistic mock workflow runs covering all standard pipeline outcomes.

        Provides a safe offline testing path without network calls or token requirements.

        Returns:
            A list of validated PipelineRunRecord instances representing:
            1. Normal successful run ('none')
            2. Controlled dependency failure ('dependency_failure')
            3. Controlled build failure ('build_failure')
            4. Controlled test failure ('test_failure')
        """
        now = datetime.now(timezone.utc)
        iso_now = now.isoformat()

        mock_raw_runs = [
            {
                "id": 1001,
                "name": "CI Pipeline",
                "head_branch": "main",
                "head_sha": "a1b2c3d4e5f6789012345678901234567890abcd",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-09-09T10:00:00Z",
                "updated_at": "2026-09-09T10:00:45Z",
                "inputs": {"failure_type": "none"},
            },
            {
                "id": 1002,
                "name": "CI Pipeline",
                "head_branch": "member1",
                "head_sha": "b2c3d4e5f6a1789012345678901234567890bcde",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-09-09T10:15:00Z",
                "updated_at": "2026-09-09T10:15:15Z",
                "inputs": {"failure_type": "dependency_failure"},
            },
            {
                "id": 1003,
                "name": "CI Pipeline",
                "head_branch": "member1",
                "head_sha": "c3d4e5f6a1b2789012345678901234567890cdef",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-09-09T10:30:00Z",
                "updated_at": "2026-09-09T10:30:28Z",
                "inputs": {"failure_type": "build_failure"},
            },
            {
                "id": 1004,
                "name": "CI Pipeline",
                "head_branch": "member1",
                "head_sha": "d4e5f6a1b2c3789012345678901234567890defa",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-09-09T10:45:00Z",
                "updated_at": "2026-09-09T10:45:34Z",
                "inputs": {"failure_type": "test_failure"},
            },
        ]

        mock_jobs = {
            1002: [
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
            ],
            1003: [
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
            ],
            1004: [
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
            ],
        }

        records: List[PipelineRunRecord] = []
        for raw_run in mock_raw_runs:
            run_id = raw_run["id"]
            jobs = mock_jobs.get(run_id)
            record = self.parse_workflow_run(raw_run, jobs_data=jobs)
            records.append(record)

        return records


def collect_workflow_runs(
    repository: Optional[str] = None,
    github_token: Optional[str] = None,
    limit: int = 10,
    use_mock: bool = False,
) -> List[PipelineRunRecord]:
    """Callable convenience function to collect CI/CD workflow run records.

    Args:
        repository: Repository in 'owner/repo' format. Defaults to GITHUB_REPOSITORY env var.
        github_token: Optional GitHub token. Defaults to GITHUB_TOKEN env var.
        limit: Number of records to fetch.
        use_mock: If True, returns validated mock data without making network requests.

    Returns:
        List of validated PipelineRunRecord instances.
    """
    repo = repository or os.getenv("GITHUB_REPOSITORY", "sumit7966/ci-cd-minor-project")
    collector = GitHubWorkflowRunCollector(repository=repo, github_token=github_token)

    if use_mock:
        return collector.get_mock_workflow_runs()[:limit]

    return collector.fetch_workflow_runs(limit=limit)
