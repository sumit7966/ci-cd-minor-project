"""FastAPI API package."""

from .analytics import router as analytics_router
from .pipelines import router as pipelines_router
from .predictions import router as predictions_router

__all__ = ["pipelines_router", "analytics_router", "predictions_router"]
