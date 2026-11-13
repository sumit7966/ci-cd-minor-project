"""GitHub Actions CI/CD workflow run data collector.

This module provides the collector service to extract, parse, and validate
workflow run metadata from GitHub Actions via the GitHub REST API.
"""

from datetime import datetime, timezone
import http.client
import json
import os
import random
import re
import socket
import time
from typing import Any, Collection, Dict, List, Optional, Set, Tuple
import urllib.error
import urllib.parse
import urllib.request

from .schemas import PipelineJobRecord, PipelineRunRecord, PipelineStepRecord

_BEARER_PATTERN = re.compile(r"(Bearer\s+)[^\s\"']+", re.IGNORECASE)
_TOKEN_PARAM_PATTERN = re.compile(r"([?&](?:access_token|token)=)[^&\s\"']+", re.IGNORECASE)
_TRANSIENT_HTTP_STATUS_CODES = frozenset({500, 502, 503, 504})


def sanitize_error_message(message: Any, token: Optional[str] = None) -> str:
    """Redact credentials, bearer headers, and token query params from error messages."""
    sanitized = str(message if message is not None else "Unknown error")
    if token:
        sanitized = sanitized.replace(token, "[REDACTED]")
    sanitized = _BEARER_PATTERN.sub(r"\1[REDACTED]", sanitized)
    sanitized = _TOKEN_PARAM_PATTERN.sub(r"\1[REDACTED]", sanitized)
    return sanitized


def _is_transient_url_error(err: urllib.error.URLError) -> bool:
    """Return True only for transient socket timeouts or connection-reset errors."""
    reason = getattr(err, "reason", None)
    if isinstance(
        reason,
        (
            TimeoutError,
            socket.timeout,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            EOFError,
            http.client.HTTPException,
        ),
    ):
        return True
    if isinstance(reason, str):
        lowered = reason.lower()
        return any(
            marker in lowered
            for marker in (
                "timed out",
                "timeout",
                "connection reset",
                "remote end closed connection",
                "unexpected eof",
                "temporarily unavailable",
            )
        )
    return False


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

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class RunTelemetryUnavailableError(GitHubAPIError):
    """Raised when historical run job telemetry cannot be retrieved (HTTP 404/410)."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, status_code=status_code, request_id=request_id)


def _safe_parse_header_int(value: Any) -> Optional[int]:
    """Safely parse integer HTTP header values while ignoring mocks or invalid values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 datetime string from GitHub API into a datetime object."""
    if not dt_str or not isinstance(dt_str, str):
        return None
    normalized = dt_str.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _compute_duration_seconds(
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
) -> Optional[float]:
    """Return non-negative elapsed seconds between timestamps, or None when invalid."""
    if start_dt is None or end_dt is None:
        return None
    if (start_dt.tzinfo is None) != (end_dt.tzinfo is None):
        return None
    delta = (end_dt - start_dt).total_seconds()
    if delta < 0:
        return None
    return round(delta, 2)


def _parse_next_link(link_header: Optional[str]) -> Optional[str]:
    """Extract rel='next' URL from a GitHub REST API Link response header."""
    if not link_header or not isinstance(link_header, str):
        return None
    for part in link_header.split(","):
        sections = [segment.strip() for segment in part.split(";")]
        if len(sections) < 2:
            continue
        url_part = sections[0]
        if url_part.startswith("<") and url_part.endswith(">"):
            if any(seg == 'rel="next"' for seg in sections[1:]):
                return url_part[1:-1]
    return None


def _safe_positive_int(value: Any) -> Optional[int]:
    """Convert a JSON value into a positive integer when valid."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


_LIFECYCLE_STEPS = frozenset({"set up job", "complete job"})
_DEPENDENCY_PATTERN = re.compile(
    r"(?:dependenc|requirements\.txt|pip3?\s+install|poetry\s+install|uv\s+(?:sync|pip)|"
    r"npm\s+(?:ci|install)|yarn\s+install|pnpm\s+install|bundle\s+install|go\s+mod\s+download|"
    r"restore\s+cache|cache\s+dependencies|\binstall(?:ing)?\s+(?:[\w-]+\s+){0,3}"
    r"(?:deps|dependencies|requirements|packages?|libraries|modules)\b)"
)
_LINT_PATTERN = re.compile(
    r"\b(?:lint\w*|eslint|flake8|pylint|ruff|black|prettier|isort|mypy|pyright|"
    r"type[-_\s]?check\w*|format(?:ting)?[-_\s]?check|style[-_\s]?check)\b"
)
_TEST_PATTERN = re.compile(
    r"\b(?:tests?|testing|pytest|unittest|vitest|jest|mocha|cypress|playwright|"
    r"smoke[-_\s]?tests?|integration[-_\s]?tests?|unit[-_\s]?tests?|e2e)\b"
)
_BUILD_PATTERN = re.compile(
    r"\b(?:build(?:ing)?|compil\w*|bundl\w*|docker\s+build|vite\s+build|webpack|tsc|package)\b"
)
_DEPLOY_PATTERN = re.compile(r"\b(?:deploy\w*|release|publish)\b")
_SETUP_TOOLS_PATTERN = re.compile(
    r"\b(?:python|node|nodejs|java|jdk|go|ruby|rust|docker|buildx|qemu|gcloud|aws|"
    r"terraform|pnpm|uv|poetry)\b"
)


def classify_step_stage(step_name: Optional[str]) -> Optional[str]:
    """Classify a GitHub step name into a normalized CI stage when sufficiently reliable."""
    if not step_name or not isinstance(step_name, str):
        return None
    lowered = step_name.strip().lower()
    if not lowered:
        return None
    if lowered in _LIFECYCLE_STEPS or lowered.startswith("post "):
        return None
    if _DEPENDENCY_PATTERN.search(lowered):
        return "dependency"
    if _LINT_PATTERN.search(lowered):
        return "lint"
    if _TEST_PATTERN.search(lowered):
        return "test"
    if _BUILD_PATTERN.search(lowered):
        return "build"
    if _DEPLOY_PATTERN.search(lowered):
        return "deploy"
    if "actions/checkout" in lowered or re.search(r"\bcheckout\b", lowered):
        return "checkout"
    if "actions/setup-" in lowered or (
        re.search(r"\b(?:set\s*up|setup)\b", lowered)
        and _SETUP_TOOLS_PATTERN.search(lowered)
    ):
        return "setup"
    return None


def _should_skip_job_fetch_for_run(raw_run: Dict[str, Any]) -> bool:
    """Skip job collection only for queued runs that have not yet started."""
    status = (raw_run.get("status") or "").strip().lower()
    return (
        status == "queued"
        and not raw_run.get("conclusion")
        and not raw_run.get("run_started_at")
    )


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
        *,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        backoff_jitter_seconds: float = 0.25,
    ) -> None:
        """Initialize the workflow run collector.

        Args:
            repository: Repository slug in 'owner/repo' format.
                        Defaults to GITHUB_REPOSITORY environment variable.
            github_token: Personal access token for GitHub API authorization.
                          Defaults to GITHUB_TOKEN environment variable.
            max_retries: Maximum retry attempts after an initial transient failure.
            backoff_base_seconds: Base delay in seconds for exponential backoff (~1s, 2s, 4s).
            backoff_jitter_seconds: Maximum random jitter added to each retry delay.
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
        self.max_retries = max(0, int(max_retries))
        self.backoff_base_seconds = max(0.0, float(backoff_base_seconds))
        self.backoff_jitter_seconds = max(0.0, float(backoff_jitter_seconds))
        self._last_link_header: Optional[str] = None
        self.last_rate_limit_remaining: Optional[int] = None
        self.last_rate_limit_reset: Optional[int] = None
        self.last_request_id: Optional[str] = None

    def _update_rate_limit_headers(self, headers: Any) -> None:
        """Capture GitHub REST API rate-limit metadata from response headers when numeric."""
        get_hdr = getattr(headers, "get", None)
        if not callable(get_hdr):
            return

        remaining = _safe_parse_header_int(
            get_hdr("x-ratelimit-remaining") or get_hdr("X-RateLimit-Remaining")
        )
        reset_at = _safe_parse_header_int(
            get_hdr("x-ratelimit-reset") or get_hdr("X-RateLimit-Reset")
        )
        if remaining is not None:
            self.last_rate_limit_remaining = remaining
        if reset_at is not None:
            self.last_rate_limit_reset = reset_at

    def _update_request_id_header(self, headers: Any) -> None:
        """Capture safe X-GitHub-Request-Id diagnostic metadata from response headers."""
        get_hdr = getattr(headers, "get", None)
        if not callable(get_hdr):
            return

        raw_id = get_hdr("x-github-request-id") or get_hdr("X-GitHub-Request-Id")
        if isinstance(raw_id, str):
            cleaned = raw_id.strip()
            if cleaned:
                self.last_request_id = sanitize_error_message(
                    cleaned[:128],
                    self.github_token,
                )

    def _sleep_before_retry(self, retry_idx: int) -> float:
        """Sleep with exponential backoff plus bounded jitter before retrying."""
        base_delay = self.backoff_base_seconds * (2 ** retry_idx)
        jitter = random.uniform(0.0, self.backoff_jitter_seconds)
        delay = base_delay + jitter
        if delay > 0:
            time.sleep(delay)
        return delay

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
        """Execute an authenticated HTTP GET request with bounded transient retries.

        Args:
            url: Full target URL.

        Returns:
            Parsed JSON dictionary.
        """
        self._last_link_header = None
        self.last_request_id = None
        max_attempts = 1 + self.max_retries

        for attempt_idx in range(max_attempts):
            is_last_attempt = attempt_idx >= (max_attempts - 1)
            self._last_link_header = None
            self.last_request_id = None
            req = urllib.request.Request(url, headers=self._get_headers())
            try:
                with urllib.request.urlopen(req, timeout=15) as response:
                    headers = getattr(response, "headers", None)
                    if headers is not None and callable(getattr(headers, "get", None)):
                        link_val = headers.get("Link") or headers.get("link")
                        if isinstance(link_val, str):
                            self._last_link_header = link_val
                        self._update_rate_limit_headers(headers)
                        self._update_request_id_header(headers)
                    payload = response.read().decode("utf-8")
                    parsed = json.loads(payload)
                    if not isinstance(parsed, dict):
                        raise GitHubAPIError(
                            "Unexpected JSON payload format from GitHub API.",
                            request_id=self.last_request_id,
                        )
                    return parsed
            except urllib.error.HTTPError as err:
                err_headers = err.headers if err.headers is not None else {}
                get_hdr = getattr(err_headers, "get", None)
                self._update_rate_limit_headers(err_headers)
                self._update_request_id_header(err_headers)
                req_id = self.last_request_id
                req_id_suffix = f" (request_id={req_id})" if req_id else ""

                if err.code == 401:
                    raise MissingTokenError(
                        "GitHub API authentication failed (401 Unauthorized). "
                        f"Check that GITHUB_TOKEN is valid.{req_id_suffix}"
                    ) from err
                if err.code == 429:
                    retry_after = (
                        get_hdr("retry-after") or get_hdr("Retry-After")
                        if callable(get_hdr)
                        else None
                    )
                    reset_hint = retry_after if isinstance(retry_after, (str, int)) else "unknown"
                    raise RateLimitExceededError(
                        f"GitHub API rate limit exceeded (429). Retry-After: {reset_hint}{req_id_suffix}"
                    ) from err
                if err.code == 403:
                    rate_limit_remaining = (
                        get_hdr("x-ratelimit-remaining") or get_hdr("X-RateLimit-Remaining")
                        if callable(get_hdr)
                        else None
                    )
                    retry_after = (
                        get_hdr("retry-after") or get_hdr("Retry-After")
                        if callable(get_hdr)
                        else None
                    )
                    reason_text = str(getattr(err, "reason", "") or "").lower()
                    if (
                        rate_limit_remaining == "0"
                        or isinstance(retry_after, (str, int))
                        or "rate limit" in reason_text
                    ):
                        reset_time = (
                            get_hdr("x-ratelimit-reset") or get_hdr("X-RateLimit-Reset") or "unknown"
                            if callable(get_hdr)
                            else "unknown"
                        )
                        raise RateLimitExceededError(
                            f"GitHub API rate limit exceeded. Reset timestamp: {reset_time}{req_id_suffix}"
                        ) from err
                    safe_reason = sanitize_error_message(getattr(err, "reason", ""), self.github_token)
                    raise GitHubAPIError(
                        f"GitHub API access forbidden (403): {safe_reason}{req_id_suffix}",
                        status_code=403,
                        request_id=req_id,
                    ) from err
                if err.code == 404:
                    raise RepositoryNotFoundError(
                        f"Repository '{self.repository}' was not found (404) or token lacks access."
                    ) from err
                if err.code == 410:
                    raise RunTelemetryUnavailableError(
                        f"Resource in repository '{self.repository}' is permanently unavailable (410 Gone).",
                        status_code=410,
                        request_id=req_id,
                    ) from err

                if err.code in _TRANSIENT_HTTP_STATUS_CODES and not is_last_attempt:
                    self._sleep_before_retry(attempt_idx)
                    continue

                safe_reason = sanitize_error_message(getattr(err, "reason", ""), self.github_token)
                attempts_note = (
                    f" after {attempt_idx + 1} attempts" if attempt_idx > 0 else ""
                )
                raise GitHubAPIError(
                    f"GitHub API returned HTTP error {err.code}: {safe_reason}{attempts_note}{req_id_suffix}",
                    status_code=err.code,
                    request_id=req_id,
                ) from err
            except (
                TimeoutError,
                socket.timeout,
                http.client.HTTPException,
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
            ) as err:
                if not is_last_attempt:
                    self._sleep_before_retry(attempt_idx)
                    continue
                attempts_note = (
                    f" after {attempt_idx + 1} attempts" if attempt_idx > 0 else ""
                )
                timeout_note = " (request timed out)" if isinstance(err, (TimeoutError, socket.timeout)) else ""
                safe_err = sanitize_error_message(err, self.github_token)
                raise GitHubAPIError(
                    f"Failed to reach GitHub API{timeout_note}{attempts_note}: {safe_err}",
                    request_id=self.last_request_id,
                ) from err
            except urllib.error.URLError as err:
                if _is_transient_url_error(err) and not is_last_attempt:
                    self._sleep_before_retry(attempt_idx)
                    continue
                attempts_note = (
                    f" after {attempt_idx + 1} attempts" if attempt_idx > 0 else ""
                )
                safe_reason = sanitize_error_message(err.reason, self.github_token)
                raise GitHubAPIError(
                    f"Failed to reach GitHub API{attempts_note}: {safe_reason}",
                    request_id=self.last_request_id,
                ) from err
            except json.JSONDecodeError as err:
                raise GitHubAPIError(
                    "Invalid JSON received from GitHub API.",
                    request_id=self.last_request_id,
                ) from err

        raise GitHubAPIError(
            "Failed to complete GitHub API request.",
            request_id=self.last_request_id,
        )

    def fetch_jobs_for_run(
        self,
        run_id: int,
        *,
        raise_run_unavailable: bool = False,
    ) -> List[Dict[str, Any]]:
        """Retrieve all job and step execution details for a workflow run using pagination.

        Args:
            run_id: Unique GitHub Actions workflow run identifier.
            raise_run_unavailable: If True, map HTTP 404/410 errors to
                RunTelemetryUnavailableError with run-scoped diagnostics instead
                of treating HTTP 404 solely as a missing repository.

        Returns:
            Complete list of raw GitHub job detail dictionaries.
        """
        per_page = 100
        page = 1
        query = urllib.parse.urlencode({"per_page": per_page, "page": page})
        next_url: Optional[str] = (
            f"{self.api_base_url}/repos/{self.repository}/actions/runs/{run_id}/jobs?{query}"
        )

        all_jobs: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()
        seen_job_ids: Set[int] = set()
        expected_total: Optional[int] = None

        while next_url:
            if next_url in seen_urls:
                raise GitHubAPIError(
                    f"Detected pagination loop while fetching jobs for run {run_id}."
                )
            seen_urls.add(next_url)

            try:
                data = self._http_get(next_url)
            except RepositoryNotFoundError as err:
                if raise_run_unavailable:
                    raise RunTelemetryUnavailableError(
                        f"Job telemetry for run {run_id} in '{self.repository}' returned HTTP 404 "
                        "(run deleted, logs expired, or insufficient repository access permission).",
                        status_code=404,
                    ) from err
                raise
            except RunTelemetryUnavailableError as err:
                if raise_run_unavailable and err.status_code == 410:
                    req_id = getattr(err, "request_id", None)
                    req_id_suffix = f" (request_id={req_id})" if req_id else ""
                    raise RunTelemetryUnavailableError(
                        f"Job telemetry for run {run_id} in '{self.repository}' returned HTTP 410 Gone "
                        f"(run telemetry or logs permanently expired or removed).{req_id_suffix}",
                        status_code=410,
                        request_id=req_id,
                    ) from err
                raise
            if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
                raise GitHubAPIError(
                    f"Incomplete or malformed jobs payload received for run {run_id}."
                )

            page_jobs: List[Dict[str, Any]] = data["jobs"]
            raw_total = data.get("total_count")
            if isinstance(raw_total, int) and not isinstance(raw_total, bool):
                expected_total = raw_total

            if page > 1 and not page_jobs:
                break

            page_ids = {
                parsed_id
                for job in page_jobs
                if isinstance(job, dict)
                and (parsed_id := _safe_positive_int(job.get("id"))) is not None
            }
            if page > 1 and page_ids and page_ids.issubset(seen_job_ids):
                break

            all_jobs.extend(page_jobs)
            seen_job_ids.update(page_ids)

            link_next = _parse_next_link(self._last_link_header)
            if link_next:
                next_url = link_next
                page += 1
            elif (
                expected_total is not None
                and len(all_jobs) < expected_total
                and len(page_jobs) > 0
            ):
                page += 1
                next_query = urllib.parse.urlencode({"per_page": per_page, "page": page})
                next_url = (
                    f"{self.api_base_url}/repos/{self.repository}/actions/runs/{run_id}/jobs"
                    f"?{next_query}"
                )
            else:
                next_url = None

        if expected_total is not None and len(all_jobs) < expected_total:
            raise GitHubAPIError(
                f"Incomplete job telemetry for run {run_id}: "
                f"expected {expected_total} jobs, received {len(all_jobs)}."
            )

        return all_jobs

    def parse_step_record(
        self,
        raw_step: Dict[str, Any],
        default_step_number: int = 1,
    ) -> PipelineStepRecord:
        """Parse a single raw GitHub Actions step payload into a PipelineStepRecord."""
        step_number = _safe_positive_int(raw_step.get("number")) or max(1, default_step_number)
        raw_name = raw_step.get("name")
        step_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else f"Step {step_number}"

        raw_conclusion = raw_step.get("conclusion")
        conclusion = (
            raw_conclusion.strip()
            if isinstance(raw_conclusion, str) and raw_conclusion.strip()
            else None
        )

        raw_status = raw_step.get("status")
        status = (
            raw_status.strip()
            if isinstance(raw_status, str) and raw_status.strip()
            else ("completed" if conclusion else "unknown")
        )

        started_at = _parse_iso_datetime(raw_step.get("started_at"))
        completed_at = _parse_iso_datetime(raw_step.get("completed_at"))

        if (conclusion or "").lower() == "skipped" or status.lower() != "completed":
            duration_seconds = None
        else:
            duration_seconds = _compute_duration_seconds(started_at, completed_at)

        return PipelineStepRecord(
            step_number=step_number,
            step_name=step_name,
            normalized_stage=classify_step_stage(step_name),
            status=status,
            conclusion=conclusion,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=duration_seconds,
        )

    def parse_job_record(
        self,
        raw_job: Dict[str, Any],
        default_run_attempt: Optional[int] = None,
    ) -> PipelineJobRecord:
        """Parse a raw GitHub Actions job payload and its steps into a PipelineJobRecord."""
        github_job_id = _safe_positive_int(raw_job.get("id"))
        raw_name = raw_job.get("name")
        job_name = (
            raw_name.strip()
            if isinstance(raw_name, str) and raw_name.strip()
            else (f"job-{github_job_id}" if github_job_id is not None else "unnamed-job")
        )
        run_attempt = _safe_positive_int(raw_job.get("run_attempt")) or _safe_positive_int(
            default_run_attempt
        )

        raw_conclusion = raw_job.get("conclusion")
        conclusion = (
            raw_conclusion.strip()
            if isinstance(raw_conclusion, str) and raw_conclusion.strip()
            else None
        )

        raw_status = raw_job.get("status")
        status = (
            raw_status.strip()
            if isinstance(raw_status, str) and raw_status.strip()
            else ("completed" if conclusion else "unknown")
        )

        created_at = _parse_iso_datetime(raw_job.get("created_at"))
        started_at = _parse_iso_datetime(raw_job.get("started_at"))
        completed_at = _parse_iso_datetime(raw_job.get("completed_at"))

        raw_runner = raw_job.get("runner_name")
        runner_name = (
            raw_runner.strip()
            if isinstance(raw_runner, str) and raw_runner.strip()
            else None
        )

        if (conclusion or "").lower() == "skipped":
            queue_duration_seconds = None
            execution_duration_seconds = None
        else:
            queue_duration_seconds = _compute_duration_seconds(created_at, started_at)
            execution_duration_seconds = (
                _compute_duration_seconds(started_at, completed_at)
                if status.lower() == "completed"
                else None
            )

        raw_steps = raw_job.get("steps") or []
        parsed_steps: List[PipelineStepRecord] = []
        used_step_numbers: Set[int] = set()
        if isinstance(raw_steps, list):
            next_fallback_number = 1
            for idx, raw_step in enumerate(raw_steps, start=1):
                if not isinstance(raw_step, dict):
                    continue
                step_record = self.parse_step_record(raw_step, default_step_number=idx)
                if step_record.step_number in used_step_numbers:
                    while next_fallback_number in used_step_numbers:
                        next_fallback_number += 1
                    step_record = step_record.model_copy(
                        update={"step_number": next_fallback_number}
                    )
                used_step_numbers.add(step_record.step_number)
                next_fallback_number = max(next_fallback_number, step_record.step_number + 1)
                parsed_steps.append(step_record)

        return PipelineJobRecord(
            github_job_id=github_job_id,
            job_name=job_name,
            run_attempt=run_attempt,
            status=status,
            conclusion=conclusion,
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
            queue_duration_seconds=queue_duration_seconds,
            execution_duration_seconds=execution_duration_seconds,
            runner_name=runner_name,
            steps=parsed_steps,
        )

    def parse_workflow_run(
        self,
        raw_run: Dict[str, Any],
        jobs_data: Optional[List[Dict[str, Any]]] = None,
        jobs_complete: Optional[bool] = None,
    ) -> PipelineRunRecord:
        """Parse raw GitHub Actions API response payload into a validated PipelineRunRecord.

        Args:
            raw_run: Dictionary containing raw workflow run metadata.
            jobs_data: Optional list of job payloads to parse and diagnose failed steps.
            jobs_complete: Internal marker distinguishing authoritative complete job fetches
                           from skipped/failed/partial or legacy omitted payloads.

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
            duration_seconds = _compute_duration_seconds(created_at_dt, updated_at_dt)

        parsed_jobs: List[PipelineJobRecord] = []
        if isinstance(jobs_data, list):
            run_attempt = _safe_positive_int(raw_run.get("run_attempt"))
            parsed_jobs = [
                self.parse_job_record(raw_job, default_run_attempt=run_attempt)
                for raw_job in jobs_data
                if isinstance(raw_job, dict)
            ]

        # Determine failure details based on workflow conclusion and evidence from steps
        failed_stage: Optional[str] = None
        failure_type: Optional[str] = "none"

        workflow_inputs = raw_run.get("inputs") or {}
        if isinstance(workflow_inputs, dict) and "failure_type" in workflow_inputs:
            failure_type = workflow_inputs.get("failure_type")

        normalized_conclusion = (conclusion or "").strip().lower() or None
        if normalized_conclusion == "failure":
            failed_stage = self._detect_failed_stage(
                jobs_data,
                failure_type,
                parsed_jobs=parsed_jobs,
            )
            if failure_type in (None, "none"):
                if failed_stage == "unknown":
                    failure_type = "unknown"
                elif failed_stage in ("checkout", "setup"):
                    failure_type = "setup_failure"
                else:
                    failure_type = f"{failed_stage}_failure"
        elif normalized_conclusion == "timed_out":
            failed_stage = self._detect_failed_stage(
                jobs_data,
                None,
                parsed_jobs=parsed_jobs,
                target_conclusions=("timed_out", "failure"),
            )
            failure_type = "timed_out"
        elif normalized_conclusion == "startup_failure":
            detected_stage = self._detect_failed_stage(
                jobs_data,
                None,
                parsed_jobs=parsed_jobs,
                target_conclusions=("startup_failure", "failure"),
            )
            failed_stage = detected_stage if detected_stage != "unknown" else "startup"
            failure_type = "startup_failure"

        record = PipelineRunRecord(
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
            jobs=parsed_jobs,
        )
        record._jobs_complete = jobs_complete
        return record

    def _detect_failed_stage(
        self,
        jobs_data: Optional[List[Dict[str, Any]]],
        default_failure_type: Optional[str],
        parsed_jobs: Optional[List[PipelineJobRecord]] = None,
        target_conclusions: Tuple[str, ...] = ("failure",),
    ) -> str:
        """Inspect jobs and steps data to pinpoint the specific failure stage.

        Uses reliable step stage normalization before optional workflow_dispatch input
        hints, and falls back to 'unknown' when evidence is insufficient.
        """
        candidates = parsed_jobs
        if candidates is None and isinstance(jobs_data, list):
            candidates = [
                self.parse_job_record(job)
                for job in jobs_data
                if isinstance(job, dict)
            ]

        target_set = {c.lower() for c in target_conclusions}
        if candidates:
            for job in candidates:
                for step in job.steps:
                    step_conc = (step.conclusion or "").strip().lower()
                    if step_conc in target_set and step.normalized_stage:
                        return step.normalized_stage

        if default_failure_type and default_failure_type != "none":
            clean_type = default_failure_type.replace("_failure", "").lower()
            if clean_type in ("dependency", "build", "test", "lint", "deploy", "setup", "checkout"):
                return clean_type

        return "unknown"

    def fetch_workflow_runs(
        self,
        limit: int = 10,
        fetch_jobs: bool = True,
        skip_job_fetch_run_ids: Optional[Collection[int]] = None,
    ) -> List[PipelineRunRecord]:
        """Retrieve recent workflow runs from GitHub API and parse into records.

        Args:
            limit: Maximum number of workflow run records to return (1-100).
            fetch_jobs: Whether to fetch granular job/step telemetry for non-queued runs.
            skip_job_fetch_run_ids: Completed run IDs whose job telemetry is already stored.

        Returns:
            List of validated PipelineRunRecord instances.
        """
        query = urllib.parse.urlencode({"per_page": min(max(1, limit), 100)})
        url = f"{self.api_base_url}/repos/{self.repository}/actions/runs?{query}"

        data = self._http_get(url)
        raw_runs = data.get("workflow_runs", [])
        skip_ids = set(skip_job_fetch_run_ids or ())

        records: List[PipelineRunRecord] = []
        for raw_run in raw_runs[:limit]:
            if "id" not in raw_run:
                raise ValueError("Incomplete workflow run data: missing 'id' field.")

            run_id = int(raw_run["id"])
            run_status = (raw_run.get("status") or "").strip().lower()
            jobs_data: Optional[List[Dict[str, Any]]] = None
            jobs_complete: Optional[bool] = None

            if not fetch_jobs:
                jobs_complete = False
            elif _should_skip_job_fetch_for_run(raw_run):
                jobs_complete = False
            elif run_status == "completed" and run_id in skip_ids:
                jobs_complete = False
            else:
                try:
                    fetched_jobs = self.fetch_jobs_for_run(run_id)
                    if isinstance(fetched_jobs, list):
                        jobs_data = fetched_jobs
                        jobs_complete = True
                    else:
                        jobs_data = None
                        jobs_complete = False
                except (RateLimitExceededError, MissingTokenError):
                    raise
                except GitHubCollectorError:
                    # Incomplete or failed job collection preserves stored child telemetry
                    # and must not invent fake failure stages from API errors.
                    jobs_data = None
                    jobs_complete = False

            record = self.parse_workflow_run(
                raw_run,
                jobs_data=jobs_data,
                jobs_complete=jobs_complete,
            )
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
    fetch_jobs: bool = True,
) -> List[PipelineRunRecord]:
    """Callable convenience function to collect CI/CD workflow run records.

    Args:
        repository: Repository in 'owner/repo' format. Defaults to GITHUB_REPOSITORY env var.
        github_token: Optional GitHub token. Defaults to GITHUB_TOKEN env var.
        limit: Number of records to fetch.
        use_mock: If True, returns validated mock data without making network requests.
        fetch_jobs: Whether to collect job and step telemetry on non-mock runs.

    Returns:
        List of validated PipelineRunRecord instances.
    """
    repo = repository or os.getenv("GITHUB_REPOSITORY", "sumit7966/ci-cd-minor-project")
    collector = GitHubWorkflowRunCollector(repository=repo, github_token=github_token)

    if use_mock:
        return collector.get_mock_workflow_runs()[:limit]

    return collector.fetch_workflow_runs(limit=limit, fetch_jobs=fetch_jobs)
