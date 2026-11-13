"""Machine Learning module for CI/CD Pipeline Failure Prediction.

Responsible for feature extraction, data access, model training, persistence, and failure risk prediction.
Uses lazy loading for training/data modules to keep production inference lightweight.
"""

from typing import Any

from .features import (
    ALL_FEATURE_COLUMNS,
    CATEGORICAL_COLUMNS,
    HISTORICAL_NUMERIC_COLUMNS,
    LEAKAGE_COLUMNS,
    NUMERIC_COLUMNS,
    PRE_RUN_CATEGORICAL_COLUMNS,
    PRE_RUN_FEATURE_COLUMNS,
    TEMPORAL_NUMERIC_COLUMNS,
)
from .persistence import (
    compute_file_sha256,
    get_model_metadata,
    get_weights_path,
    has_weights,
    is_model_trained,
    list_model_versions,
    load_weights,
)
from .predictor import (
    InvalidPredictionInputError,
    ModelNotTrainedError,
    ModelUnavailableError,
    PipelineFailurePredictor,
    get_predictor,
)

# Lazy attribute loading for training and heavy data-processing utilities (PEP 562)
_DATA_EXPORTS = {
    "CANONICAL_COLUMNS",
    "REQUIRED_TELEMETRY_COLUMNS",
    "clean_pipeline_runs_dataframe",
    "export_pipeline_runs_to_csv",
    "extract_completed_runs",
    "load_pipeline_runs_from_csv",
    "load_pipeline_runs_from_db",
    "load_pipeline_runs_from_records",
    "prepare_dataset",
}

_TRAIN_EXPORTS = {
    "compare_models",
    "train_failure_model",
    "train_random_forest",
}

_FEATURE_TRANSFORMER_EXPORTS = {
    "PipelineRunFeatureExtractor",
    "build_feature_preprocessor",
    "build_preprocessor",
}

_PERSISTENCE_BINARY_EXPORTS = {
    "load_model",
    "save_model",
}


def __getattr__(name: str) -> Any:
    """Lazily load training and data processing components when explicitly accessed."""
    if name in _DATA_EXPORTS:
        from . import data
        return getattr(data, name)
    if name in _TRAIN_EXPORTS:
        from . import train
        return getattr(train, name)
    if name in _FEATURE_TRANSFORMER_EXPORTS:
        from . import features
        return getattr(features, name)
    if name in _PERSISTENCE_BINARY_EXPORTS:
        from . import persistence
        return getattr(persistence, name)
    raise AttributeError(f"module 'ml' has no attribute '{name}'")


__all__ = [
    "CANONICAL_COLUMNS",
    "REQUIRED_TELEMETRY_COLUMNS",
    "clean_pipeline_runs_dataframe",
    "export_pipeline_runs_to_csv",
    "extract_completed_runs",
    "load_pipeline_runs_from_csv",
    "load_pipeline_runs_from_db",
    "load_pipeline_runs_from_records",
    "prepare_dataset",
    "ALL_FEATURE_COLUMNS",
    "CATEGORICAL_COLUMNS",
    "HISTORICAL_NUMERIC_COLUMNS",
    "NUMERIC_COLUMNS",
    "PRE_RUN_CATEGORICAL_COLUMNS",
    "PRE_RUN_FEATURE_COLUMNS",
    "TEMPORAL_NUMERIC_COLUMNS",
    "PipelineRunFeatureExtractor",
    "build_feature_preprocessor",
    "build_preprocessor",
    "LEAKAGE_COLUMNS",
    "compute_file_sha256",
    "get_model_metadata",
    "get_weights_path",
    "has_weights",
    "is_model_trained",
    "list_model_versions",
    "load_model",
    "load_weights",
    "save_model",
    "PipelineFailurePredictor",
    "ModelUnavailableError",
    "ModelNotTrainedError",
    "InvalidPredictionInputError",
    "get_predictor",
    "train_failure_model",
    "train_random_forest",
    "compare_models",
]
