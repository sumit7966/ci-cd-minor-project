"""CI/CD Data Collection module for collecting GitHub Actions workflow-run metadata."""

from .github_collector import (
    GitHubAPIError,
    GitHubCollectorError,
    GitHubWorkflowRunCollector,
    MissingTokenError,
    RateLimitExceededError,
    RepositoryNotFoundError,
    collect_workflow_runs,
)
from .schemas import PipelineRunRecord

__all__ = [
    "PipelineRunRecord",
    "GitHubWorkflowRunCollector",
    "collect_workflow_runs",
    "GitHubCollectorError",
    "MissingTokenError",
    "RepositoryNotFoundError",
    "RateLimitExceededError",
    "GitHubAPIError",
]
