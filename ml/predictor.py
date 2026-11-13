"""Inference service for CI/CD pipeline failure risk prediction.

Loads trained scikit-learn pipeline artifacts once, enforces strict input validation,
prevents target leakage, safely handles missing fields and unseen categories,
and computes failure probability with risk level classification.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union
import numpy as np
import pandas as pd

from .features import LEAKAGE_COLUMNS, PRE_RUN_CATEGORICAL_COLUMNS, PRE_RUN_FEATURE_COLUMNS
from .persistence import get_model_paths, is_model_trained, load_model

logger = logging.getLogger("ml.predictor")


class ModelUnavailableError(RuntimeError):
    """Raised when inference is requested but no valid trained model artifact is available."""
    pass


# Backward compatibility alias
ModelNotTrainedError = ModelUnavailableError


class InvalidPredictionInputError(ValueError):
    """Raised when the input telemetry sample is malformed or cannot be validated."""
    pass


class PipelineFailurePredictor:
    """Production predictor evaluating CI/CD failure probability before pipeline execution starts.

    Guarantees:
    - Loads the trained artifact once into memory and reuses it across requests.
    - Validates input types and values.
    - Strips any post-execution outcome fields to prevent target leakage.
    - Applies the exact same feature engineering and preprocessing pipeline fitted during training.
    - Returns valid probabilities bounded in [0.0, 1.0].
    - Handles missing fields, empty strings, and unseen categories gracefully.
    - Never hardcodes predictions or retrains per request.
    """

    def __init__(
        self,
        model_dir: Optional[Union[Path, str]] = None,
        auto_load: bool = True,
        version: Optional[str] = None,
    ) -> None:
        """Initialize the predictor.

        Args:
            model_dir: Optional custom model directory path.
            auto_load: If True, automatically loads model artifact on instantiation if available.
            version: Optional specific archived model version to load.
        """
        self.model_dir = model_dir
        self.version = version
        self.pipeline: Optional[Any] = None
        self.metadata: Dict[str, Any] = {}
        self.load_count: int = 0

        if auto_load and is_model_trained(self.model_dir, version=self.version):
            self.load()

    def is_ready(self) -> bool:
        """Return True if a trained model is loaded and ready for inference."""
        return self.pipeline is not None

    def load(self) -> None:
        """Load trained model pipeline and metadata from disk once into memory."""
        try:
            self.pipeline, self.metadata = load_model(self.model_dir, version=self.version)
            self.load_count += 1
            logger.info(
                "Loaded ML model pipeline (version: %s, type: %s, load_count: %d)",
                self.metadata.get("model_version", "unknown"),
                self.metadata.get("model_type", "unknown"),
                self.load_count,
            )
        except FileNotFoundError as err:
            raise ModelUnavailableError(
                f"Model artifact is unavailable: {err}. "
                "Train and persist a model first using 'python -m ml.train'."
            ) from err
        except Exception as err:
            raise ModelUnavailableError(
                f"Failed to load model artifact: {err}."
            ) from err

    def validate_and_format_input(
        self,
        sample: Union[Dict[str, Any], pd.Series, pd.DataFrame],
    ) -> pd.DataFrame:
        """Validate input telemetry, prevent target leakage, and construct model-ready DataFrame.

        Args:
            sample: Input telemetry dictionary, Series, or single-row DataFrame.

        Returns:
            Normalized single-row pandas DataFrame matching approved pre-run feature schema.

        Raises:
            InvalidPredictionInputError: If input is None, empty, or wrong type.
        """
        if sample is None:
            raise InvalidPredictionInputError("Prediction input sample cannot be None.")

        if isinstance(sample, dict):
            if not sample:
                raise InvalidPredictionInputError("Prediction input dictionary cannot be empty.")
            raw_data = dict(sample)
        elif isinstance(sample, pd.Series):
            if sample.empty:
                raise InvalidPredictionInputError("Prediction input Series cannot be empty.")
            raw_data = sample.to_dict()
        elif isinstance(sample, pd.DataFrame):
            if sample.empty:
                raise InvalidPredictionInputError("Prediction input DataFrame cannot be empty.")
            raw_data = sample.iloc[0].to_dict()
        else:
            raise InvalidPredictionInputError(
                f"Prediction input must be a dict, pd.Series, or pd.DataFrame; received {type(sample).__name__}."
            )

        # Safeguard: Strip any leakage fields that might have been accidentally passed
        for leak_col in LEAKAGE_COLUMNS:
            if leak_col in raw_data:
                logger.debug("Stripping target leakage column '%s' from prediction input.", leak_col)
                del raw_data[leak_col]

        # Normalize categorical pre-run fields (handle missing, None, non-string)
        formatted: Dict[str, Any] = {}
        for col in PRE_RUN_CATEGORICAL_COLUMNS:
            val = raw_data.get(col)
            if val is None or (isinstance(val, str) and not val.strip()):
                formatted[col] = "unknown"
            else:
                formatted[col] = str(val).strip()

        # Normalize created_at timestamp
        created_val = raw_data.get("created_at")
        if created_val is None or (isinstance(created_val, str) and not created_val.strip()):
            formatted["created_at"] = datetime.now(timezone.utc)
        elif isinstance(created_val, (datetime, pd.Timestamp)):
            if created_val.tzinfo is None:
                formatted["created_at"] = created_val.replace(tzinfo=timezone.utc)
            else:
                formatted["created_at"] = created_val.astimezone(timezone.utc)
        else:
            # Parse date string safely
            parsed_dt = pd.to_datetime(created_val, utc=True, errors="coerce")
            if pd.isna(parsed_dt):
                logger.warning("Invalid created_at timestamp '%s'; defaulting to current UTC.", created_val)
                formatted["created_at"] = datetime.now(timezone.utc)
            else:
                formatted["created_at"] = parsed_dt.to_pydatetime()

        # Preserve run_id if provided
        if "run_id" in raw_data:
            formatted["run_id"] = raw_data["run_id"]

        return pd.DataFrame([formatted])

    def predict_proba(
        self,
        sample: Union[Dict[str, Any], pd.Series, pd.DataFrame],
    ) -> float:
        """Estimate the failure probability for the given pipeline run.

        Args:
            sample: Input telemetry sample before execution starts.

        Returns:
            Failure probability as a float strictly in the range [0.0, 1.0].

        Raises:
            ModelUnavailableError: If no model has been loaded yet.
            InvalidPredictionInputError: If input is invalid.
        """
        if not self.is_ready():
            raise ModelUnavailableError(
                "Prediction cannot be performed: no trained model is currently loaded in memory. "
                "Train a model first or call predictor.load()."
            )

        df = self.validate_and_format_input(sample)

        try:
            probabilities = self.pipeline.predict_proba(df)
            prob_failure = float(probabilities[0][1])
        except Exception as err:
            raise RuntimeError(f"Model inference execution failed: {err}") from err

        # Strictly enforce valid probability bounds [0.0, 1.0]
        prob_bounded = max(0.0, min(1.0, prob_failure))
        return round(prob_bounded, 4)

    def predict(
        self,
        sample: Union[Dict[str, Any], pd.Series, pd.DataFrame],
    ) -> Dict[str, Any]:
        """Perform comprehensive CI/CD pipeline failure risk prediction.

        Args:
            sample: Input pre-run telemetry (repository, branch, workflow_name, event, created_at).

        Returns:
            Structured prediction dictionary containing:
            - status: "success"
            - predicted_class: 0 (success) or 1 (failure)
            - is_failure_predicted: bool
            - failure_probability: float in [0.0, 1.0]
            - success_probability: float in [0.0, 1.0]
            - risk_level: "low", "medium", or "high"
            - confidence_score: float in [0.0, 1.0]
            - features_evaluated: dictionary of pre-run inputs
            - model_metadata: version, type, and training timestamp

        Raises:
            ModelUnavailableError: If model is not loaded.
            InvalidPredictionInputError: If input fails validation.
        """
        df = self.validate_and_format_input(sample)
        prob = self.predict_proba(df)

        predicted_class = int(prob >= 0.5)
        is_failure = bool(predicted_class == 1)

        # Categorize risk level
        if prob >= 0.65:
            risk_level = "high"
        elif prob >= 0.35:
            risk_level = "medium"
        else:
            risk_level = "low"

        # Confidence: distance from decision threshold 0.5 scaled to [0.0, 1.0]
        confidence = round(float(abs(prob - 0.5) * 2.0), 4)
        success_prob = round(float(1.0 - prob), 4)

        features_used = {
            col: str(df.iloc[0][col])
            for col in PRE_RUN_FEATURE_COLUMNS
            if col in df.columns
        }

        return {
            "status": "success",
            "predicted_class": predicted_class,
            "is_failure_predicted": is_failure,
            "failure_probability": prob,
            "success_probability": success_prob,
            "risk_level": risk_level,
            "confidence_score": confidence,
            "features_evaluated": features_used,
            "model_metadata": {
                "model_version": self.metadata.get("model_version", "1.0.0"),
                "model_type": self.metadata.get("model_type", "LogisticRegression"),
                "trained_at": self.metadata.get("trained_at"),
                "artifact_sha256": self.metadata.get("artifact_sha256"),
            },
        }

    def predict_safe(
        self,
        sample: Union[Dict[str, Any], pd.Series, pd.DataFrame],
    ) -> Dict[str, Any]:
        """Safe wrapper returning error dictionary instead of raising exceptions.

        Useful for API endpoints and web hooks needing clean JSON error states.
        """
        if not self.is_ready():
            return {
                "status": "model_unavailable",
                "message": (
                    "Failure prediction model is currently unavailable. "
                    "Ensure a trained model artifact is saved in ml/models/."
                ),
                "is_failure_predicted": None,
                "failure_probability": None,
            }

        try:
            return self.predict(sample)
        except InvalidPredictionInputError as err:
            return {
                "status": "invalid_input",
                "message": str(err),
                "is_failure_predicted": None,
                "failure_probability": None,
            }
        except Exception as err:
            logger.error("Unexpected prediction error: %s", err, exc_info=True)
            return {
                "status": "error",
                "message": f"Inference failed: {err}",
                "is_failure_predicted": None,
                "failure_probability": None,
            }


# Module-level singleton instance for shared in-memory reuse
_GLOBAL_PREDICTOR: Optional[PipelineFailurePredictor] = None


def get_predictor(
    model_dir: Optional[Union[Path, str]] = None,
    reload: bool = False,
) -> PipelineFailurePredictor:
    """Retrieve the shared in-memory predictor singleton, loading the model once.

    Args:
        model_dir: Optional model directory.
        reload: If True, forces reloading the model from disk.

    Returns:
        Loaded PipelineFailurePredictor instance.
    """
    global _GLOBAL_PREDICTOR
    if (
        _GLOBAL_PREDICTOR is None
        or reload
        or (model_dir is not None and _GLOBAL_PREDICTOR.model_dir != model_dir)
    ):
        _GLOBAL_PREDICTOR = PipelineFailurePredictor(model_dir=model_dir, auto_load=True)
    return _GLOBAL_PREDICTOR
