"""Pydantic schemas for CI/CD pipeline execution run data collection."""

from datetime import datetime
from typing import Any, List, Optional

# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field, PrivateAttr, field_validator


class PipelineStepRecord(BaseModel):
    """Schema representing one GitHub Actions step within a workflow job."""

    step_number: int = Field(
        ...,
        ge=1,
        description="1-based step index within the job",
        examples=[1],
    )
    step_name: str = Field(
        ...,
        min_length=1,
        description="Original step name from GitHub Actions",
        examples=["Install Dependencies"],
    )
    normalized_stage: Optional[str] = Field(
        default=None,
        description="Optional normalized CI/CD stage name (e.g., 'dependency', 'build', 'test')",
        examples=["dependency"],
    )
    status: str = Field(
        ...,
        min_length=1,
        description="Execution status of the step (e.g., 'completed', 'in_progress', 'queued')",
        examples=["completed"],
    )
    conclusion: Optional[str] = Field(
        default=None,
        description="Execution outcome of the step (e.g., 'success', 'failure', 'skipped')",
        examples=["success"],
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when step execution started",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when step execution completed",
    )
    duration_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Step execution duration in seconds (non-negative when available)",
        examples=[4.25],
    )

    @field_validator("step_name", "status", mode="before")
    @classmethod
    def strip_and_validate_required_text(cls, v: Any) -> Any:
        """Ensure required string fields are stripped and not empty."""
        if isinstance(v, str):
            cleaned = v.strip()
            if not cleaned:
                raise ValueError("String field cannot be empty or whitespace only")
            return cleaned
        return v

    @field_validator("normalized_stage", "conclusion", mode="before")
    @classmethod
    def strip_optional_text(cls, v: Any) -> Any:
        """Strip optional string fields and normalize whitespace-only strings to None."""
        if isinstance(v, str):
            cleaned = v.strip()
            return cleaned or None
        return v

    @field_validator("duration_seconds")
    @classmethod
    def validate_step_duration(cls, v: Optional[float]) -> Optional[float]:
        """Ensure step duration is non-negative when provided."""
        if v is not None and v < 0:
            raise ValueError("duration_seconds cannot be negative")
        return v


class PipelineJobRecord(BaseModel):
    """Schema representing one GitHub Actions job associated with a workflow run."""

    github_job_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Unique GitHub Actions job ID when present",
        examples=[9901],
    )
    job_name: str = Field(
        ...,
        min_length=1,
        description="Name of the GitHub Actions job",
        examples=["build-and-test"],
    )
    run_attempt: Optional[int] = Field(
        default=None,
        ge=1,
        description="Workflow run attempt number",
        examples=[1],
    )
    status: str = Field(
        ...,
        min_length=1,
        description="Current job status (e.g., 'completed', 'in_progress', 'queued')",
        examples=["completed"],
    )
    conclusion: Optional[str] = Field(
        default=None,
        description="Final job outcome (e.g., 'success', 'failure', 'cancelled')",
        examples=["success"],
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when the job was queued/created",
    )
    started_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when job execution started",
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp when job execution completed",
    )
    queue_duration_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Job queue wait duration in seconds before runner execution",
        examples=[1.5],
    )
    execution_duration_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Active job execution duration in seconds",
        examples=[11.0],
    )
    runner_name: Optional[str] = Field(
        default=None,
        description="Name of the GitHub Actions runner executing the job",
        examples=["GitHub Actions 2"],
    )
    steps: List[PipelineStepRecord] = Field(
        default_factory=list,
        description="Ordered step telemetry records belonging to this job",
    )

    @field_validator("job_name", "status", mode="before")
    @classmethod
    def strip_and_validate_job_text(cls, v: Any) -> Any:
        """Ensure required job string fields are stripped and not empty."""
        if isinstance(v, str):
            cleaned = v.strip()
            if not cleaned:
                raise ValueError("String field cannot be empty or whitespace only")
            return cleaned
        return v

    @field_validator("conclusion", "runner_name", mode="before")
    @classmethod
    def strip_optional_job_text(cls, v: Any) -> Any:
        """Strip optional string fields and normalize whitespace-only strings to None."""
        if isinstance(v, str):
            cleaned = v.strip()
            return cleaned or None
        return v

    @field_validator("queue_duration_seconds", "execution_duration_seconds")
    @classmethod
    def validate_job_durations(cls, v: Optional[float]) -> Optional[float]:
        """Ensure job durations are non-negative when provided."""
        if v is not None and v < 0:
            raise ValueError("Job duration cannot be negative")
        return v


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
    jobs: List[PipelineJobRecord] = Field(
        default_factory=list,
        description="Optional job-level telemetry records belonging to this workflow run",
    )
    _jobs_complete: Optional[bool] = PrivateAttr(default=None)

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
