"""SQLAlchemy ORM models for CI/CD pipeline telemetry data."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.orm import relationship

from ..data_collection.schemas import (
    PipelineJobRecord,
    PipelineRunRecord,
    PipelineStepRecord,
)
from .session import Base


def build_job_key(
    job_name: str,
    github_job_id: Optional[int] = None,
    run_attempt: Optional[int] = None,
) -> str:
    """Build a deterministic per-run job key even when github_job_id is NULL."""
    if github_job_id is not None:
        return f"github:{int(github_job_id)}"

    normalized_name = (job_name or "").strip()
    attempt = 1 if run_attempt is None else int(run_attempt)
    return f"legacy:{normalized_name}:{attempt}"


def _default_job_key(context: Any) -> str:
    """Compute a fallback job_key during Core inserts that omit job_key."""
    params = context.get_current_parameters() if context is not None else {}
    return build_job_key(
        job_name=params.get("job_name") or "",
        github_job_id=params.get("github_job_id"),
        run_attempt=params.get("run_attempt"),
    )


class PipelineJobModel(Base):
    """SQLAlchemy model representing a GitHub Actions job within a pipeline run."""

    __tablename__ = "pipeline_jobs"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "github_job_id",
            name="uq_pipeline_jobs_run_github_job",
        ),
        UniqueConstraint(
            "run_id",
            "job_key",
            name="uq_pipeline_jobs_run_job_key",
        ),
        CheckConstraint(
            "queue_duration_seconds IS NULL OR queue_duration_seconds >= 0",
            name="ck_pipeline_jobs_queue_duration_non_negative",
        ),
        CheckConstraint(
            "execution_duration_seconds IS NULL OR execution_duration_seconds >= 0",
            name="ck_pipeline_jobs_execution_duration_non_negative",
        ),
    )

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
        index=True,
        doc="Surrogate primary key for the pipeline job record",
    )
    run_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("pipeline_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    github_job_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        nullable=True,
        index=True,
    )
    job_key = Column(
        String(320),
        nullable=False,
        default=_default_job_key,
        doc="Deterministic composite key enforcing per-run uniqueness when github_job_id is NULL",
    )
    job_name = Column(String(255), nullable=False, index=True)
    run_attempt = Column(Integer, nullable=True)
    status = Column(String(64), nullable=False, index=True)
    conclusion = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    queue_duration_seconds = Column(Float, nullable=True)
    execution_duration_seconds = Column(Float, nullable=True)
    runner_name = Column(String(255), nullable=True)
    synced_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    pipeline_run = relationship("PipelineRunModel", back_populates="jobs")
    steps = relationship(
        "PipelineStepModel",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by=lambda: PipelineStepModel.step_number,
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if not self.job_key:
            self.job_key = build_job_key(
                job_name=self.job_name or "",
                github_job_id=self.github_job_id,
                run_attempt=self.run_attempt,
            )

    def to_pydantic(self, include_steps: bool = False) -> PipelineJobRecord:
        """Convert ORM job instance to validated PipelineJobRecord."""
        steps: List[PipelineStepRecord] = []
        if include_steps or "steps" not in inspect(self).unloaded:
            steps = [step.to_pydantic() for step in self.steps]

        return PipelineJobRecord(
            github_job_id=self.github_job_id,
            job_name=self.job_name,
            run_attempt=self.run_attempt,
            status=self.status,
            conclusion=self.conclusion,
            created_at=self.created_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            queue_duration_seconds=self.queue_duration_seconds,
            execution_duration_seconds=self.execution_duration_seconds,
            runner_name=self.runner_name,
            steps=steps,
        )

    def to_dict(self, include_steps: bool = False) -> Dict[str, Any]:
        """Convert ORM job instance to JSON-serializable dictionary."""
        steps: List[Dict[str, Any]] = []
        if include_steps or "steps" not in inspect(self).unloaded:
            steps = [step.to_dict() for step in self.steps]

        return {
            "id": self.id,
            "run_id": self.run_id,
            "github_job_id": self.github_job_id,
            "job_key": self.job_key,
            "job_name": self.job_name,
            "run_attempt": self.run_attempt,
            "status": self.status,
            "conclusion": self.conclusion,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "queue_duration_seconds": self.queue_duration_seconds,
            "execution_duration_seconds": self.execution_duration_seconds,
            "runner_name": self.runner_name,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
            "steps": steps,
        }

    @classmethod
    def from_pydantic(
        cls,
        record: PipelineJobRecord,
        *,
        run_id: int,
    ) -> "PipelineJobModel":
        """Instantiate ORM job model from a PipelineJobRecord schema instance."""
        return cls(
            run_id=run_id,
            github_job_id=record.github_job_id,
            job_key=build_job_key(
                job_name=record.job_name,
                github_job_id=record.github_job_id,
                run_attempt=record.run_attempt,
            ),
            job_name=record.job_name,
            run_attempt=record.run_attempt,
            status=record.status,
            conclusion=record.conclusion,
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            queue_duration_seconds=record.queue_duration_seconds,
            execution_duration_seconds=record.execution_duration_seconds,
            runner_name=record.runner_name,
            steps=[
                PipelineStepModel.from_pydantic(step, run_id=run_id)
                for step in record.steps
            ],
        )


class PipelineStepModel(Base):
    """SQLAlchemy model representing a single step inside a pipeline job."""

    __tablename__ = "pipeline_steps"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "step_number",
            name="uq_pipeline_steps_job_step_number",
        ),
        CheckConstraint(
            "step_number >= 1",
            name="ck_pipeline_steps_step_number_positive",
        ),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_pipeline_steps_duration_non_negative",
        ),
    )

    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
        index=True,
        doc="Surrogate primary key for the pipeline step record",
    )
    job_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("pipeline_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    run_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("pipeline_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_number = Column(Integer, nullable=False)
    step_name = Column(String(255), nullable=False, index=True)
    normalized_stage = Column(String(64), nullable=True, index=True)
    status = Column(String(64), nullable=False, index=True)
    conclusion = Column(String(64), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)
    synced_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    job = relationship("PipelineJobModel", back_populates="steps")
    pipeline_run = relationship(
        "PipelineRunModel",
        back_populates="steps",
        viewonly=True,
    )

    def to_pydantic(self) -> PipelineStepRecord:
        """Convert ORM step instance to validated PipelineStepRecord."""
        return PipelineStepRecord(
            step_number=self.step_number,
            step_name=self.step_name,
            normalized_stage=self.normalized_stage,
            status=self.status,
            conclusion=self.conclusion,
            started_at=self.started_at,
            completed_at=self.completed_at,
            duration_seconds=self.duration_seconds,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert ORM step instance to JSON-serializable dictionary."""
        return {
            "id": self.id,
            "job_id": self.job_id,
            "run_id": self.run_id,
            "step_number": self.step_number,
            "step_name": self.step_name,
            "normalized_stage": self.normalized_stage,
            "status": self.status,
            "conclusion": self.conclusion,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
        }

    @classmethod
    def from_pydantic(
        cls,
        record: PipelineStepRecord,
        *,
        run_id: int,
        job_id: Optional[int] = None,
    ) -> "PipelineStepModel":
        """Instantiate ORM step model from a PipelineStepRecord schema instance."""
        kwargs: Dict[str, Any] = {
            "run_id": run_id,
            "step_number": record.step_number,
            "step_name": record.step_name,
            "normalized_stage": record.normalized_stage,
            "status": record.status,
            "conclusion": record.conclusion,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "duration_seconds": record.duration_seconds,
        }
        if job_id is not None:
            kwargs["job_id"] = job_id
        return cls(**kwargs)


def _sync_pipeline_job_key(
    mapper: Any,
    connection: Any,
    target: PipelineJobModel,
) -> None:
    """Keep the deterministic job_key synchronized across inserts and updates."""
    target.job_key = build_job_key(
        job_name=target.job_name or "",
        github_job_id=target.github_job_id,
        run_attempt=target.run_attempt,
    )


event.listen(PipelineJobModel, "before_insert", _sync_pipeline_job_key)
event.listen(PipelineJobModel, "before_update", _sync_pipeline_job_key)


class PipelineRunModel(Base):
    """SQLAlchemy model representing a CI/CD workflow run in PostgreSQL."""

    __tablename__ = "pipeline_runs"

    run_id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        index=True,
        doc="Unique GitHub Actions workflow run identifier",
    )
    repository = Column(String(255), nullable=False, index=True)
    branch = Column(String(255), nullable=False, index=True)
    commit_sha = Column(String(64), nullable=False)
    status = Column(String(64), nullable=False, index=True)
    conclusion = Column(String(64), nullable=True, index=True)
    workflow_name = Column(String(255), nullable=False, index=True)
    event = Column(String(64), nullable=False)
    duration_seconds = Column(Float, nullable=True)
    failed_stage = Column(String(64), nullable=True, index=True)
    failure_type = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    synced_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    jobs = relationship(
        "PipelineJobModel",
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
        order_by=lambda: PipelineJobModel.id,
    )
    steps = relationship(
        "PipelineStepModel",
        back_populates="pipeline_run",
        viewonly=True,
        order_by=lambda: (PipelineStepModel.job_id, PipelineStepModel.step_number),
    )

    def to_pydantic(self) -> PipelineRunRecord:
        """Convert ORM model instance to validated PipelineRunRecord."""
        return PipelineRunRecord(
            run_id=self.run_id,
            repository=self.repository,
            branch=self.branch,
            commit_sha=self.commit_sha,
            status=self.status,
            conclusion=self.conclusion,
            workflow_name=self.workflow_name,
            event=self.event,
            duration_seconds=self.duration_seconds,
            failed_stage=self.failed_stage,
            failure_type=self.failure_type,
            created_at=self.created_at,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert ORM model instance to JSON-serializable dictionary."""
        return {
            "run_id": self.run_id,
            "repository": self.repository,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "status": self.status,
            "conclusion": self.conclusion,
            "workflow_name": self.workflow_name,
            "event": self.event,
            "duration_seconds": self.duration_seconds,
            "failed_stage": self.failed_stage,
            "failure_type": self.failure_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
        }

    @classmethod
    def from_pydantic(cls, record: PipelineRunRecord) -> "PipelineRunModel":
        """Instantiate ORM model from a PipelineRunRecord schema instance."""
        return cls(
            run_id=record.run_id,
            repository=record.repository,
            branch=record.branch,
            commit_sha=record.commit_sha,
            status=record.status,
            conclusion=record.conclusion,
            workflow_name=record.workflow_name,
            event=record.event,
            duration_seconds=record.duration_seconds,
            failed_stage=record.failed_stage,
            failure_type=record.failure_type,
            created_at=record.created_at,
            synced_at=datetime.now(timezone.utc),
        )
