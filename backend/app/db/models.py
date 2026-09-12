"""SQLAlchemy ORM models for CI/CD pipeline telemetry data."""

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Integer,
    String,
)

from ..data_collection.schemas import PipelineRunRecord
from .session import Base


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
