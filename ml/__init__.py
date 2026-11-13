"""Machine Learning module for CI/CD Pipeline Failure Prediction.

Responsible for feature extraction, data access, model training, persistence, and failure risk prediction.
"""

from .data import (
    CANONICAL_COLUMNS,
    REQUIRED_TELEMETRY_COLUMNS,
    clean_pipeline_runs_dataframe,
    export_pipeline_runs_to_csv,
    extract_completed_runs,
    load_pipeline_runs_from_csv,
    load_pipeline_runs_from_db,
    load_pipeline_runs_from_records,
    prepare_dataset,
)
from .features import (
    ALL_FEATURE_COLUMNS,
    CATEGORICAL_COLUMNS,
    HISTORICAL_NUMERIC_COLUMNS,
    LEAKAGE_COLUMNS,
    NUMERIC_COLUMNS,
    PRE_RUN_CATEGORICAL_COLUMNS,
    PRE_RUN_FEATURE_COLUMNS,
    TEMPORAL_NUMERIC_COLUMNS,
    PipelineRunFeatureExtractor,
    build_feature_preprocessor,
    build_preprocessor,
)
from .persistence import (
    compute_file_sha256,
    get_model_metadata,
    is_model_trained,
    list_model_versions,
    load_model,
    save_model,
)
from .predictor import (
    InvalidPredictionInputError,
    ModelNotTrainedError,
    ModelUnavailableError,
    PipelineFailurePredictor,
    get_predictor,
)
from .train import compare_models, train_failure_model, train_random_forest

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
    "is_model_trained",
    "list_model_versions",
    "load_model",
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
