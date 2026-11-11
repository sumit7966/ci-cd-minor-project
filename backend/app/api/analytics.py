"""FastAPI router for CI/CD pipeline analytics, trends, failure breakdowns, and duration benchmarks."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..analytics.service import (
    get_analytics_summary,
    get_complete_analytics,
    get_duration_analytics,
    get_execution_trends,
    get_failure_analytics,
)
from ..db.session import get_db

router = APIRouter(prefix="/api/pipelines/analytics", tags=["analytics"])


@router.get("", summary="Get complete pipeline analytics dashboard payload")
@router.get("/", include_in_schema=False)
@router.get("/overview", summary="Overview of all pipeline analytical dimensions")
def get_dashboard_analytics(
    repository: Optional[str] = Query(None, description="Filter analytics by repository"),
    days: Optional[int] = Query(30, ge=1, le=365, description="Number of recent days for trends"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve full analytics bundle including summary, trends, failures, and duration."""
    return get_complete_analytics(db=db, repository=repository, days=days)


@router.get("/summary", summary="Get execution counts, success/failure rates, and duration summary")
def get_summary(
    repository: Optional[str] = Query(None, description="Filter summary by repository"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve overall pipeline execution summary and success/failure rates."""
    return get_analytics_summary(db=db, repository=repository)


@router.get("/trends", summary="Get pipeline execution volume and success trends over time")
def get_trends(
    repository: Optional[str] = Query(None, description="Filter trends by repository"),
    days: Optional[int] = Query(30, ge=1, le=365, description="Days of history to analyze"),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Retrieve chronologically bucketed daily pipeline execution metrics."""
    return get_execution_trends(db=db, repository=repository, days=days)


@router.get("/failures", summary="Get failure breakdowns by failure type and failed stage")
def get_failures(
    repository: Optional[str] = Query(None, description="Filter failures by repository"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve categorical failure distributions by failure_type and failed_stage."""
    return get_failure_analytics(db=db, repository=repository)


@router.get("/duration", summary="Get duration statistics overall and by workflow and branch")
def get_durations(
    repository: Optional[str] = Query(None, description="Filter duration stats by repository"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve average, min, max, and median durations grouped by workflow and branch."""
    return get_duration_analytics(db=db, repository=repository)
