from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class OptimizationRecommendationSchema(BaseModel):
    title: str
    description: str
    reason: str
    affected_stage: Optional[str] = None
    priority: str
    recommendation_type: str

class BottleneckAnalysisSchema(BaseModel):
    average_pipeline_duration: float
    stage_metrics: Dict[str, Any]
    slowest_stage: Optional[str] = None
    potential_bottlenecks: List[Dict[str, Any]]
    message: Optional[str] = None
    telemetry_coverage: Optional[Dict[str, Any]] = None

class OptimizationSummarySchema(BaseModel):
    bottlenecks: BottleneckAnalysisSchema
    recommendations: List[OptimizationRecommendationSchema]
