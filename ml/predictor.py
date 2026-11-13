"""Inference service for CI/CD pipeline failure risk prediction.

Loads trained lightweight model artifacts once, enforces strict input validation,
prevents target leakage, safely handles missing fields and unseen categories,
and computes failure probability with risk level classification using standard library math.
"""

from datetime import datetime, timezone
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .features import LEAKAGE_COLUMNS, PRE_RUN_CATEGORICAL_COLUMNS, PRE_RUN_FEATURE_COLUMNS
from .persistence import (
    get_model_paths,
    get_weights_path,
    has_weights,
    is_model_trained,
    load_model,
    load_weights,
)

logger = logging.getLogger("ml.predictor")


class ModelUnavailableError(RuntimeError):
    """Raised when inference is requested but no valid trained model artifact is available."""
    pass


# Backward compatibility alias
ModelNotTrainedError = ModelUnavailableError


class InvalidPredictionInputError(ValueError):
    """Raised when the input telemetry sample is malformed or cannot be validated."""
    pass


class _Row(dict):
    """Lightweight dictionary subclass providing .to_dict() compatibility."""

    def to_dict(self) -> Dict[str, Any]:
        return dict(self)


class NormalizedSample:
    """Lightweight single-row tabular container simulating DataFrame behavior for inference."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = _Row(data)
        self.columns: List[str] = list(data.keys())
        self.iloc: List[_Row] = [self._data]
        self.index: List[int] = [0]

    def __len__(self) -> int:
        return 1

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)


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
    - Operates with zero heavy ML dependencies (no pandas, NumPy, SciPy, or scikit-learn needed).
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
        self.weights: Optional[Dict[str, Any]] = None
        self.pipeline: Optional[Any] = None
        self.metadata: Dict[str, Any] = {}
        self.load_count: int = 0

        if auto_load and is_model_trained(self.model_dir, version=self.version):
            self.load()

    def is_ready(self) -> bool:
        """Return True if a trained model is loaded and ready for inference."""
        return self.weights is not None or self.pipeline is not None

    def load(self) -> None:
        """Load trained model pipeline and metadata from disk once into memory."""
        # Priority 1: Lightweight weights JSON
        if has_weights(self.model_dir, version=self.version):
            try:
                self.weights, self.metadata = load_weights(self.model_dir, version=self.version)
                self.pipeline = None
                self.load_count += 1
                logger.info(
                    "Loaded lightweight ML weights (version: %s, type: %s, load_count: %d)",
                    self.metadata.get("model_version", self.weights.get("model_version", "unknown")),
                    self.metadata.get("model_type", self.weights.get("model_type", "unknown")),
                    self.load_count,
                )
                return
            except Exception as err:
                logger.warning("Could not load lightweight weights artifact, falling back: %s", err)

        # Priority 2: Binary joblib pipeline (if joblib is installed locally)
        try:
            self.pipeline, self.metadata = load_model(self.model_dir, version=self.version)
            self.weights = None
            self.load_count += 1
            logger.info(
                "Loaded binary ML model pipeline (version: %s, type: %s, load_count: %d)",
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
        sample: Any,
    ) -> NormalizedSample:
        """Validate input telemetry, prevent target leakage, and construct normalized sample container.

        Args:
            sample: Input telemetry dictionary, Series, or single-row DataFrame.

        Returns:
            Normalized sample matching approved pre-run feature schema.

        Raises:
            InvalidPredictionInputError: If input is None, empty, or wrong type.
        """
        if sample is None:
            raise InvalidPredictionInputError("Prediction input sample cannot be None.")

        if isinstance(sample, dict):
            if not sample:
                raise InvalidPredictionInputError("Prediction input dictionary cannot be empty.")
            raw_data = dict(sample)
        elif hasattr(sample, "to_dict"):
            if hasattr(sample, "empty") and sample.empty:
                raise InvalidPredictionInputError("Prediction input Series or DataFrame cannot be empty.")
            if hasattr(sample, "iloc"):
                raw_data = sample.iloc[0].to_dict()
            else:
                raw_data = sample.to_dict()
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
        elif isinstance(created_val, datetime):
            if created_val.tzinfo is None:
                formatted["created_at"] = created_val.replace(tzinfo=timezone.utc)
            else:
                formatted["created_at"] = created_val.astimezone(timezone.utc)
        else:
            # Parse date string safely
            str_val = str(created_val).strip()
            try:
                dt = datetime.fromisoformat(str_val.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    formatted["created_at"] = dt.replace(tzinfo=timezone.utc)
                else:
                    formatted["created_at"] = dt.astimezone(timezone.utc)
            except Exception:
                logger.warning("Invalid created_at timestamp '%s'; defaulting to current UTC.", created_val)
                formatted["created_at"] = datetime.now(timezone.utc)

        # Preserve run_id if provided
        if "run_id" in raw_data:
            formatted["run_id"] = raw_data["run_id"]

        return NormalizedSample(formatted)

    def predict_proba(
        self,
        sample: Any,
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

        norm_sample = self.validate_and_format_input(sample)

        # Pure Python inference using model_weights.json
        if self.weights is not None:
            row = norm_sample.iloc[0]
            repo = row.get("repository", "unknown")
            branch = row.get("branch", "unknown")
            wf = row.get("workflow_name", "unknown")
            event = row.get("event", "unknown")

            created_dt = row.get("created_at")
            hour = created_dt.hour
            dow = created_dt.weekday()
            is_weekend = 1.0 if dow >= 5 else 0.0

            ext = self.weights.get("extractor_stats", {})
            repo_s = ext.get("repo_stats", {}).get(repo)
            repo_rate = float(repo_s["rate"]) if repo_s else 0.0

            b_stats = ext.get("branch_stats", {})
            branch_s = b_stats.get(f"{repo}::{branch}") or b_stats.get((repo, branch))
            if branch_s:
                branch_rate = float(branch_s["rate"])
                consec = float(branch_s.get("consecutive_failures", 0))
            else:
                branch_rate = repo_rate
                consec = 0.0

            wf_stats = ext.get("workflow_stats", {})
            wf_s = wf_stats.get(f"{repo}::{wf}") or wf_stats.get((repo, wf))
            wf_rate = float(wf_s["rate"]) if wf_s else 0.0

            # One-hot encoding
            ohe: List[float] = []
            cat_features = self.weights.get(
                "categorical_features",
                ["repository", "branch", "workflow_name", "event"]
            )
            cat_categories = self.weights.get("categorical_categories", {})
            for col in cat_features:
                val = row.get(col, "unknown")
                for expected in cat_categories.get(col, []):
                    ohe.append(1.0 if val == expected else 0.0)

            # Numerical standardization
            raw_nums = [float(hour), float(dow), is_weekend, repo_rate, branch_rate, wf_rate, consec]
            means = self.weights["numerical_scaler_means"]
            scales = self.weights["numerical_scaler_scales"]
            medians = self.weights.get("numerical_imputer_medians", [0.0] * len(raw_nums))

            scaled: List[float] = []
            for val, m, s, med in zip(raw_nums, means, scales, medians):
                clean_v = med if val is None else float(val)
                scaled.append((clean_v - m) / s)

            feat_vec = ohe + scaled
            intercept = self.weights["intercept"]
            coefs = self.weights["coefficients"]
            z = intercept + sum(c * x for c, x in zip(coefs, feat_vec))

            prob = 1.0 / (1.0 + math.exp(-z))
            prob_bounded = max(0.0, min(1.0, prob))
            return round(prob_bounded, 4)

        # Fallback to binary pipeline (if loaded via joblib)
        try:
            import pandas as pd  # lazy import
            df = pd.DataFrame([norm_sample.iloc[0]])
        except Exception:
            df = norm_sample

        try:
            probabilities = self.pipeline.predict_proba(df)
            prob_failure = float(probabilities[0][1])
        except Exception as err:
            raise RuntimeError(f"Model inference execution failed: {err}") from err

        prob_bounded = max(0.0, min(1.0, prob_failure))
        return round(prob_bounded, 4)

    def predict(
        self,
        sample: Any,
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
        norm_sample = self.validate_and_format_input(sample)
        prob = self.predict_proba(sample)

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
            col: str(norm_sample.iloc[0][col])
            for col in PRE_RUN_FEATURE_COLUMNS
            if col in norm_sample.columns
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
        sample: Any,
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
