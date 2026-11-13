"""CI/CD Data Collection module for collecting and storing GitHub Actions workflow-run metadata."""

from .github_collector import (
    GitHubAPIError,
    GitHubCollectorError,
    GitHubWorkflowRunCollector,
    MissingTokenError,
    RateLimitExceededError,
    RepositoryNotFoundError,
    RunTelemetryUnavailableError,
    collect_workflow_runs,
)
from .schemas import (
    PipelineJobRecord,
    PipelineRunRecord,
    PipelineStepRecord,
)
from .storage import (
    save_pipeline_runs_to_db,
    sync_github_runs_to_db,
)

__all__ = [
    "PipelineRunRecord",
    "PipelineJobRecord",
    "PipelineStepRecord",
    "GitHubWorkflowRunCollector",
    "collect_workflow_runs",
    "GitHubCollectorError",
    "MissingTokenError",
    "RepositoryNotFoundError",
    "RateLimitExceededError",
    "RunTelemetryUnavailableError",
    "GitHubAPIError",
    "save_pipeline_runs_to_db",
    "sync_github_runs_to_db",
    "backfill_job_telemetry",
]


def __getattr__(name: str):
    """Lazily expose backfill_job_telemetry without triggering db circular imports or runpy warnings."""
    if name == "backfill_job_telemetry":
        from .backfill import backfill_job_telemetry

        return backfill_job_telemetry
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
