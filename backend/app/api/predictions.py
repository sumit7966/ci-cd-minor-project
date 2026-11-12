"""FastAPI router for CI/CD pipeline failure risk prediction and model status."""

from datetime import datetime
import logging
from typing import Any, Dict, Optional

# pyrefly: ignore [missing-import]
from fastapi import APIRouter, Depends, HTTPException, status
# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field

from ml.predictor import (
    InvalidPredictionInputError,
    ModelUnavailableError,
    PipelineFailurePredictor,
    get_predictor,
)

logger = logging.getLogger("cicd_platform.api.predictions")

router = APIRouter(prefix="/api/predictions", tags=["predictions"])


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class PredictionRequest(BaseModel):
    """Input payload representing pre-run pipeline telemetry before execution starts.

    Safeguard: Post-execution outcome fields (e.g., status, conclusion, failed_stage,
    duration_seconds) are strictly excluded from the schema to prevent target leakage.
    Any extra unexpected fields are safely ignored.
    """

    repository: str = Field(
        ...,
        min_length=1,
        description="Repository path in 'owner/repo' format",
        examples=["sumit7966/ci-cd-minor-project"],
    )
    branch: str = Field(
        ...,
        min_length=1,
        description="Git branch triggering the pipeline execution",
        examples=["main", "member1"],
    )
    workflow_name: str = Field(
        ...,
        min_length=1,
        description="Name of the CI workflow as defined in GitHub Actions",
        examples=["CI Pipeline"],
    )
    event: str = Field(
        ...,
        min_length=1,
        description="Event that triggered the run (e.g., 'push', 'pull_request', 'workflow_dispatch')",
        examples=["push", "workflow_dispatch"],
    )
    created_at: Optional[datetime] = Field(
        default=None,
        description="Optional trigger timestamp in ISO-8601 UTC format. Defaults to current server time if omitted.",
    )
    run_id: Optional[int] = Field(
        default=None,
        description="Optional pipeline run ID for tracking and correlation",
        examples=[34322655257],
    )

    model_config = {
        "extra": "ignore",
    }


class ModelMetadataResponse(BaseModel):
    """Metadata describing the active trained model artifact."""

    model_version: str = Field(..., description="Semantic version of the model artifact", examples=["1.0.0"])
    model_type: str = Field(..., description="Classification algorithm type", examples=["LogisticRegression"])
    trained_at: Optional[str] = Field(None, description="ISO-8601 UTC timestamp when model training completed")
    artifact_sha256: Optional[str] = Field(None, description="SHA-256 cryptographic digest of the .joblib artifact")


class PredictionResponse(BaseModel):
    """Structured response containing failure probability and risk assessment."""

    status: str = Field("success", description="Prediction status indicator", examples=["success"])
    predicted_class: int = Field(..., description="Binary decision: 0 for success, 1 for failure", examples=[1])
    is_failure_predicted: bool = Field(..., description="True if failure is predicted, False otherwise", examples=[True])
    failure_probability: float = Field(..., ge=0.0, le=1.0, description="Estimated failure probability in [0.0, 1.0]", examples=[0.8392])
    success_probability: float = Field(..., ge=0.0, le=1.0, description="Estimated success probability in [0.0, 1.0]", examples=[0.1608])
    risk_level: str = Field(..., description="Categorical risk tier: 'low', 'medium', or 'high'", examples=["high"])
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Model confidence score in [0.0, 1.0]", examples=[0.6784])
    features_evaluated: Dict[str, Any] = Field(..., description="Dictionary of pre-run features processed by the model")
    model_metadata: ModelMetadataResponse


class PredictionStatusResponse(BaseModel):
    """Status report indicating whether the ML model is loaded and ready for inference."""

    status: str = Field(..., description="Operational status: 'ready' or 'unavailable'", examples=["ready"])
    model_available: bool = Field(..., description="True if model artifact is loaded in memory", examples=[True])
    model_version: Optional[str] = Field(None, description="Active model version", examples=["1.0.0"])
    model_type: Optional[str] = Field(None, description="Active model type", examples=["LogisticRegression"])
    trained_at: Optional[str] = Field(None, description="Training completion timestamp")
    artifact_sha256: Optional[str] = Field(None, description="SHA-256 digest of active artifact")


# ---------------------------------------------------------------------------
# Dependency Provider
# ---------------------------------------------------------------------------


def get_prediction_service() -> PipelineFailurePredictor:
    """Dependency provider returning the cached PipelineFailurePredictor singleton.

    Loads the joblib pipeline artifact once into memory and reuses it across requests.
    Can be easily overridden in tests via app.dependency_overrides.
    """
    return get_predictor()


# ---------------------------------------------------------------------------
# Router Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=PredictionResponse,
    summary="Predict CI/CD pipeline failure risk before execution",
    status_code=status.HTTP_200_OK,
)
@router.post("/", response_model=PredictionResponse, include_in_schema=False)
def predict_pipeline_failure(
    request: PredictionRequest,
    predictor: PipelineFailurePredictor = Depends(get_prediction_service),
) -> PredictionResponse:
    """Evaluate pipeline failure risk using the trained ML model pipeline.

    - Loads model artifact once and reuses in-memory pipeline.
    - Strips target leakage fields to ensure pre-execution integrity.
    - Applies identical feature engineering and scaling pipeline from training.
    - Returns failure probability, risk tier ('low' / 'medium' / 'high'), and confidence.

    Raises:
        HTTPException 422: If request body violates validation rules.
        HTTPException 400: If input data is malformed or invalid.
        HTTPException 503: If trained model artifact is unavailable.
    """
    if not predictor.is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Failure prediction model is currently unavailable. "
                "Ensure a trained model artifact is saved in ml/models/."
            ),
        )

    try:
        # Convert request model to dictionary for predictor ingestion
        payload = request.model_dump(exclude_unset=False)
        result = predictor.predict(payload)
        return PredictionResponse(**result)
    except InvalidPredictionInputError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        ) from err
    except ModelUnavailableError as err:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(err),
        ) from err
    except Exception as err:
        logger.error("Unexpected prediction inference failure: %s", err, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Inference execution failed: {err}",
        ) from err


@router.get(
    "/status",
    response_model=PredictionStatusResponse,
    summary="Get ML model availability and version metadata",
)
def get_prediction_model_status(
    predictor: PipelineFailurePredictor = Depends(get_prediction_service),
) -> PredictionStatusResponse:
    """Check whether the ML failure prediction model is loaded and ready for inference."""
    is_ready = predictor.is_ready()
    metadata = predictor.metadata if is_ready else {}

    return PredictionStatusResponse(
        status="ready" if is_ready else "unavailable",
        model_available=is_ready,
        model_version=metadata.get("model_version"),
        model_type=metadata.get("model_type"),
        trained_at=metadata.get("trained_at"),
        artifact_sha256=metadata.get("artifact_sha256"),
    )
