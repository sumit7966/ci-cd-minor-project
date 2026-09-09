"""Pydantic schemas for CI/CD pipeline execution run data collection."""

from datetime import datetime
from typing import Any, Optional

# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field, field_validator


class PipelineRunRecord(BaseModel):
    """Schema representing one collected CI/CD workflow execution run record.

    Captures key execution metadata from GitHub Actions for pipeline analytics,
    bottleneck detection, and failure prediction modeling.
    """

    run_id: int = Field(
        ...,
        description="Unique GitHub Actions workflow run ID",
        examples=[1234567890],
    )
    repository: str = Field(
        ...,
        description="Repository path in 'owner/repo' format",
        min_length=1,
        examples=["sumit7966/ci-cd-minor-project"],
    )
    branch: str = Field(
        ...,
        description="Git branch name triggering the pipeline execution",
        min_length=1,
        examples=["main", "member1"],
    )
    commit_sha: str = Field(
        ...,
        description="Git commit hash (SHA) associated with the workflow run",
        min_length=4,
        examples=["1450bb3859663efd1bf379b3ee909ebdf28a7e0d"],
    )
    status: str = Field(
        ...,
        description="Current state of the workflow run (e.g., 'completed', 'in_progress', 'queued')",
        examples=["completed"],
    )
    conclusion: Optional[str] = Field(
        default=None,
        description="Final execution outcome (e.g., 'success', 'failure', 'cancelled', 'skipped')",
        examples=["success", "failure"],
    )
    workflow_name: str = Field(
        ...,
        description="Name of the CI workflow as defined in GitHub Actions",
        min_length=1,
        examples=["CI Pipeline"],
    )
    event: str = Field(
        ...,
        description="Event that triggered the run (e.g., 'push', 'pull_request', 'workflow_dispatch')",
        examples=["push", "workflow_dispatch"],
    )
    duration_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Execution duration in seconds (must be non-negative)",
        examples=[45.2],
    )
    failed_stage: Optional[str] = Field(
        default=None,
        description="Identified failed pipeline step/stage (e.g., 'dependency', 'build', 'test')",
        examples=["test", "build", "dependency"],
    )
    failure_type: Optional[str] = Field(
        default=None,
        description="Controlled failure category (e.g., 'none', 'dependency_failure', 'build_failure', 'test_failure')",
        examples=["none", "test_failure"],
    )
    created_at: datetime = Field(
        ...,
        description="UTC timestamp when the workflow execution was initiated",
    )

    @field_validator(
        "repository",
        "branch",
        "commit_sha",
        "status",
        "workflow_name",
        "event",
        mode="before",
    )
    @classmethod
    def strip_and_validate_non_empty(cls, v: Any) -> Any:
        """Ensure string fields are stripped and not empty."""
        if isinstance(v, str):
            cleaned = v.strip()
            if not cleaned:
                raise ValueError("String field cannot be empty or whitespace only")
            return cleaned
        return v

    @field_validator("duration_seconds")
    @classmethod
    def validate_duration(cls, v: Optional[float]) -> Optional[float]:
        """Ensure duration is non-negative if provided."""
        if v is not None and v < 0:
            raise ValueError("duration_seconds cannot be negative")
        return v
