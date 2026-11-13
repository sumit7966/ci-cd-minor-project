"""FastAPI application entry point for CI/CD Pipeline Failure Prediction & Optimization Platform."""

from contextlib import asynccontextmanager
import logging
from typing import Any, AsyncGenerator, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from optimization.api import router as optimization_router

from .api.analytics import router as analytics_router
from .api.pipelines import router as pipelines_router
from .api.predictions import router as predictions_router
from .db.session import get_engine, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cicd_platform")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager initializing database tables on startup."""
    try:
        init_db()
        logger.info("Database tables initialized successfully.")
    except Exception as exc:
        logger.warning(
            "Could not automatically initialize database tables on startup. "
            "Ensure PostgreSQL is running. Error: %s",
            exc,
        )
    yield


app = FastAPI(
    title="CI/CD Pipeline Failure Prediction & Optimization API",
    description="Backend REST API providing telemetry collection, PostgreSQL storage, failure analytics, and pipeline metrics.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for React frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers (analytics before pipelines so /analytics is matched prior to /{run_id})
app.include_router(analytics_router)
app.include_router(pipelines_router)
app.include_router(optimization_router)
app.include_router(predictions_router)


@app.get("/", summary="API Root", tags=["health"])
def root() -> Dict[str, Any]:
    """Root metadata endpoint with links to interactive API documentation."""
    return {
        "project": "CI/CD Pipeline Failure Prediction & Optimization Platform",
        "version": "1.0.0",
        "status": "online",
        "documentation": "/docs",
        "openapi": "/openapi.json",
        "endpoints": {
            "sync": "POST /api/pipelines/sync",
            "list": "GET /api/pipelines",
            "stats": "GET /api/pipelines/stats",
            "detail": "GET /api/pipelines/{run_id}",
            "analytics_overview": "GET /api/pipelines/analytics/overview",
            "analytics_summary": "GET /api/pipelines/analytics/summary",
            "analytics_trends": "GET /api/pipelines/analytics/trends",
            "analytics_failures": "GET /api/pipelines/analytics/failures",
            "analytics_duration": "GET /api/pipelines/analytics/duration",
            "optimization": "GET /api/optimization",
            "bottlenecks": "GET /api/bottlenecks",
            "recommendations": "GET /api/recommendations",
            "predictions": "POST /api/predictions",
            "prediction_status": "GET /api/predictions/status",
            "health": "GET /health",
        },
    }


@app.get("/health", summary="Health check endpoint", tags=["health"])
def health_check() -> Dict[str, Any]:
    """Check application and database connectivity health."""
    db_status = "unknown"
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:
        db_status = f"unavailable: {str(exc)}"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, reload=True)
