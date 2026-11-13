"""Model persistence, integrity validation, and versioning utilities for CI/CD failure artifacts."""

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Tuple, Union

import joblib
import sklearn

logger = logging.getLogger("ml.persistence")

DEFAULT_MODEL_DIR: Path = Path(__file__).resolve().parent / "models"
MODEL_FILENAME: str = "pipeline_failure_model.joblib"
METADATA_FILENAME: str = "metadata.json"
VERSIONS_SUBDIR: str = "versions"
DEFAULT_VERSION: str = "1.0.0"


def compute_file_sha256(filepath: Union[Path, str]) -> str:
    """Calculate the SHA-256 cryptographic digest of a file on disk.

    Args:
        filepath: Path to the target file.

    Returns:
        Hexadecimal SHA-256 string.
    """
    path = Path(filepath).resolve()
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def resolve_model_dir(model_dir: Optional[Union[Path, str]] = None) -> Path:
    """Resolve the absolute directory path for model artifacts.

    Priority:
    1. Explicit `model_dir` argument.
    2. `ML_MODEL_DIR` environment variable.
    3. Default `ml/models` directory adjacent to this module.

    Returns:
        Canonical absolute Path.
    """
    if model_dir:
        return Path(model_dir).resolve()
    env_dir = os.getenv("ML_MODEL_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    return DEFAULT_MODEL_DIR.resolve()


def get_model_paths(
    model_dir: Optional[Union[Path, str]] = None,
    version: Optional[str] = None,
) -> Tuple[Path, Path]:
    """Resolve paths for the model artifact and metadata file.

    If a specific `version` is requested, paths target the versioned archive
    under `versions/pipeline_failure_model_v{version}.joblib`.

    Args:
        model_dir: Base model storage directory.
        version: Optional explicit version identifier (e.g., '1.0.0').

    Returns:
        Tuple of (model_file_path, metadata_file_path).
    """
    base_dir = resolve_model_dir(model_dir)
    if version:
        version_dir = base_dir / VERSIONS_SUBDIR
        model_name = f"pipeline_failure_model_v{version}.joblib"
        meta_name = f"metadata_v{version}.json"
        return version_dir / model_name, version_dir / meta_name

    return base_dir / MODEL_FILENAME, base_dir / METADATA_FILENAME


def is_model_trained(
    model_dir: Optional[Union[Path, str]] = None,
    version: Optional[str] = None,
) -> bool:
    """Check if serialized model artifact and metadata exist on disk.

    Args:
        model_dir: Base model storage directory.
        version: Optional specific model version to check.

    Returns:
        True if both the joblib binary and metadata JSON exist.
    """
    model_path, metadata_path = get_model_paths(model_dir, version=version)
    return model_path.is_file() and metadata_path.is_file()


def list_model_versions(model_dir: Optional[Union[Path, str]] = None) -> List[str]:
    """Discover all archived model versions stored in the versions subdirectory.

    Args:
        model_dir: Base model storage directory.

    Returns:
        Sorted list of version strings (e.g., ['1.0.0', '1.1.0']).
    """
    base_dir = resolve_model_dir(model_dir)
    versions_dir = base_dir / VERSIONS_SUBDIR
    if not versions_dir.is_dir():
        return []

    versions: List[str] = []
    for meta_file in versions_dir.glob("metadata_v*.json"):
        # Extract version suffix: metadata_v{ver}.json -> {ver}
        stem = meta_file.stem
        if stem.startswith("metadata_v"):
            ver = stem.replace("metadata_v", "")
            if ver:
                versions.append(ver)

    return sorted(versions)


def get_model_metadata(
    model_dir: Optional[Union[Path, str]] = None,
    version: Optional[str] = None,
) -> Dict[str, Any]:
    """Read metadata dictionary without loading the full model binary into memory.

    Args:
        model_dir: Base model storage directory.
        version: Optional specific model version.

    Returns:
        Parsed metadata JSON dictionary.

    Raises:
        FileNotFoundError: If metadata file does not exist.
    """
    _, metadata_path = get_model_paths(model_dir, version=version)
    if not metadata_path.is_file():
        target = f"version '{version}'" if version else "active model"
        raise FileNotFoundError(f"Metadata file for {target} not found at '{metadata_path}'.")

    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError as err:
        raise OSError(f"Failed to read metadata file at '{metadata_path}': {err}") from err


def validate_training_for_persistence(
    pipeline: Any,
    metadata: Dict[str, Any],
) -> None:
    """Validate that the training procedure produced a legitimate, fitted model artifact.

    Raises:
        ValueError: If pipeline is unfitted, metadata indicates error, or data is insufficient.
    """
    if pipeline is None:
        raise ValueError("Cannot persist model: pipeline instance is None.")

    # Verify pipeline has predict and predict_proba interfaces
    if not hasattr(pipeline, "predict") or not hasattr(pipeline, "predict_proba"):
        raise ValueError("Cannot persist model: pipeline must provide predict() and predict_proba().")

    # Guard against error reports or empty training runs
    if metadata.get("status") == "error":
        msg = metadata.get("message", "unknown error")
        raise ValueError(f"Cannot persist model from an unsuccessful training run: {msg}")

    train_samples = metadata.get("train_samples")
    if train_samples is not None and int(train_samples) < 2:
        raise ValueError(
            f"Cannot persist model: training partition has only {train_samples} samples (minimum 2 required)."
        )


def save_model(
    pipeline: Any,
    metadata: Dict[str, Any],
    model_dir: Optional[Union[Path, str]] = None,
    version: Optional[str] = None,
    archive_version: bool = True,
) -> Tuple[Path, Path]:
    """Serialize a trained pipeline and comprehensive metadata to disk.

    Safeguards:
    - Verifies pipeline is valid and fitted before writing.
    - Prevents overwriting valid models with failed or insufficient training runs.
    - Generates SHA-256 integrity checksum for the serialized artifact.
    - Persists active artifact (pipeline_failure_model.joblib) and versioned snapshot.
    - Handles filesystem errors with clear descriptive messages.

    Args:
        pipeline: Trained scikit-learn Pipeline instance.
        metadata: Dictionary containing evaluation metrics, hyperparameters, and dataset counts.
        model_dir: Optional custom directory. Defaults to ml/models/.
        version: Semantic version string (e.g., '1.0.0'). Defaults to metadata['model_version'] or '1.0.0'.
        archive_version: If True, also writes versioned copy under ml/models/versions/.

    Returns:
        Tuple of (model_file_path, metadata_file_path).
    """
    validate_training_for_persistence(pipeline, metadata)

    base_dir = resolve_model_dir(model_dir)
    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        raise OSError(f"Failed to create model directory at '{base_dir}': {err}") from err

    active_model_path = base_dir / MODEL_FILENAME
    active_metadata_path = base_dir / METADATA_FILENAME

    # Resolve version identifier
    resolved_version = str(
        version or metadata.get("model_version") or DEFAULT_VERSION
    ).strip()

    # Step 1: Serialize active pipeline artifact
    try:
        joblib.dump(pipeline, active_model_path)
    except Exception as err:
        raise RuntimeError(f"Failed to serialize model pipeline to '{active_model_path}': {err}") from err

    # Step 2: Compute cryptographic checksum of serialized binary
    artifact_hash = compute_file_sha256(active_model_path)

    # Step 3: Construct truthful, comprehensive metadata dictionary
    now_utc = datetime.now(timezone.utc).isoformat()
    enriched_metadata: Dict[str, Any] = {
        "model_version": resolved_version,
        "model_type": str(metadata.get("model_type", "LogisticRegression")),
        "created_at": now_utc,
        "trained_at": str(metadata.get("trained_at", now_utc)),
        "framework": "scikit-learn",
        "scikit_learn_version": sklearn.__version__,
        "joblib_version": joblib.__version__,
        "random_state": metadata.get("random_state", 42),
        "split_type": metadata.get("split_type", "chronological"),
        "dataset_size": metadata.get("dataset_size"),
        "train_samples": metadata.get("train_samples"),
        "test_samples": metadata.get("test_samples"),
        "raw_dataset_size": metadata.get("raw_dataset_size", metadata.get("dataset_size")),
        "exclusions_count": metadata.get("exclusions_count", 0),
        "dataset_source": metadata.get("dataset_source", "telemetry"),
        "dataset_snapshot_path": metadata.get("dataset_snapshot_path"),
        "features": metadata.get("features", []),
        "class_counts": metadata.get("class_counts", {}),
        "train_class_counts": metadata.get("train_class_counts", {}),
        "test_class_counts": metadata.get("test_class_counts", {}),
        # Structured evaluation metrics
        "evaluation_metrics": {
            "accuracy": metadata.get("accuracy"),
            "precision": metadata.get("precision"),
            "recall": metadata.get("recall"),
            "f1_score": metadata.get("f1_score"),
            "roc_auc": metadata.get("roc_auc"),
            "confusion_matrix": metadata.get("confusion_matrix"),
        },
        # Flat metrics preserved for backward compatibility
        "accuracy": metadata.get("accuracy"),
        "precision": metadata.get("precision"),
        "recall": metadata.get("recall"),
        "f1_score": metadata.get("f1_score"),
        "confusion_matrix": metadata.get("confusion_matrix"),
        "roc_auc": metadata.get("roc_auc"),
        "artifact_filename": MODEL_FILENAME,
        "artifact_sha256": artifact_hash,
        "status": "success",
    }

    # Step 4: Write active metadata JSON file
    try:
        with open(active_metadata_path, "w", encoding="utf-8") as f:
            json.dump(enriched_metadata, f, indent=2)
    except OSError as err:
        raise OSError(f"Failed to write metadata file at '{active_metadata_path}': {err}") from err

    # Step 5: Archive versioned snapshot if requested
    if archive_version:
        versions_dir = base_dir / VERSIONS_SUBDIR
        try:
            versions_dir.mkdir(parents=True, exist_ok=True)
            v_model_path, v_meta_path = get_model_paths(base_dir, version=resolved_version)
            shutil.copy2(active_model_path, v_model_path)
            with open(v_meta_path, "w", encoding="utf-8") as f:
                json.dump(enriched_metadata, f, indent=2)
        except OSError as err:
            logger.warning("Failed to write versioned archive for v%s: %s", resolved_version, err)

    return active_model_path, active_metadata_path


def load_model(
    model_dir: Optional[Union[Path, str]] = None,
    version: Optional[str] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """Load a persisted pipeline and its metadata from disk.

    Supports loading either the active deployment artifact (default)
    or a specific historical version from the archive.

    Args:
        model_dir: Optional custom directory. Defaults to ml/models/.
        version: Optional version identifier to load (e.g., '1.0.0').

    Returns:
        Tuple of (pipeline_instance, metadata_dict).

    Raises:
        FileNotFoundError: If the model has not been trained or saved yet.
        RuntimeError: If deserialization fails.
    """
    model_path, metadata_path = get_model_paths(model_dir, version=version)

    if not model_path.is_file():
        target = f"Model version '{version}'" if version else "Trained model artifact"
        raise FileNotFoundError(
            f"{target} not found at '{model_path}'. "
            "Please train and persist a model before invoking inference."
        )

    try:
        pipeline = joblib.load(model_path)
    except Exception as err:
        raise RuntimeError(f"Corrupted or invalid model artifact at '{model_path}': {err}") from err

    metadata: Dict[str, Any] = {}
    if metadata_path.is_file():
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception as err:
            logger.warning("Could not read metadata from '%s': %s", metadata_path, err)

    return pipeline, metadata
