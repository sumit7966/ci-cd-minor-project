from dataclasses import dataclass, field
from typing import List, Optional, Dict
from datetime import datetime

@dataclass
class StageRun:
    """Represents a single stage within a pipeline run."""
    name: str
    duration: float  # Duration in seconds
    status: str  # e.g., 'success', 'failure', 'skipped'
    failure_type: Optional[str] = None  # e.g., 'test_failure', 'timeout', 'build_error'

@dataclass
class PipelineRun:
    """Represents an entire CI/CD pipeline execution."""
    run_id: str
    status: str
    pipeline_duration: float
    timestamp: datetime
    stages: List[StageRun] = field(default_factory=list)
    failed_stage: Optional[str] = None

@dataclass
class OptimizationRecommendation:
    """Represents a suggested optimization based on analysis."""
    title: str
    description: str
    reason: str
    affected_stage: Optional[str]
    priority: str
    recommendation_type: str
    
    def to_dict(self) -> Dict[str, Optional[str]]:
        """Convert recommendation instance to JSON-serializable dictionary."""
        return {
            "title": self.title,
            "description": self.description,
            "reason": self.reason,
            "affected_stage": self.affected_stage,
            "priority": self.priority,
            "recommendation_type": self.recommendation_type
        }
