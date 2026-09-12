from fastapi import APIRouter, FastAPI, HTTPException
from typing import List

from .schemas import OptimizationSummarySchema, BottleneckAnalysisSchema, OptimizationRecommendationSchema
from .data_provider import get_pipeline_data
from .bottleneck import BottleneckDetector
from .recommender import OptimizationRecommender

router = APIRouter(prefix="/api", tags=["Optimization"])

@router.get("/optimization", response_model=OptimizationSummarySchema)
def get_optimization_summary():
    """
    Returns a comprehensive optimization summary, combining bottleneck analysis 
    and actionable recommendations based on pipeline execution history.
    """
    try:
        data = get_pipeline_data()
        
        detector = BottleneckDetector(data)
        bottleneck_analysis = detector.analyze()
        
        recommender = OptimizationRecommender(data)
        recommendations = [rec.to_dict() for rec in recommender.generate_recommendations()]
        
        return OptimizationSummarySchema(
            bottlenecks=BottleneckAnalysisSchema(**bottleneck_analysis),
            recommendations=recommendations
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing optimization data: {str(e)}")

@router.get("/bottlenecks", response_model=BottleneckAnalysisSchema)
def get_bottlenecks():
    """
    Returns only the bottleneck analysis (slow stages, failure rates, etc.).
    """
    try:
        data = get_pipeline_data()
        detector = BottleneckDetector(data)
        return BottleneckAnalysisSchema(**detector.analyze())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error analyzing bottlenecks: {str(e)}")

@router.get("/recommendations", response_model=List[OptimizationRecommendationSchema])
def get_recommendations():
    """
    Returns only the specific, explainable recommendations for optimizing the pipeline.
    """
    try:
        data = get_pipeline_data()
        recommender = OptimizationRecommender(data)
        return [rec.to_dict() for rec in recommender.generate_recommendations()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating recommendations: {str(e)}")

# Create a standalone app for local testing purposes
# Member 1 will likely just import `router` into their main FastAPI app later.
app = FastAPI(title="Optimization API (Standalone)")
app.include_router(router)
