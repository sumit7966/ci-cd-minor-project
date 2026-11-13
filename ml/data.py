"""Data extraction, cleaning, and dataset preparation for CI/CD pipeline failure modeling.

This module provides the data access layer for Member 2's ML component, including:
- Loading telemetry directly from PostgreSQL or SQLite via SQLAlchemy sessions.
- Ingestion and validation from raw dictionary records.
- Deduplication of pipeline runs by unique run_id.
- Strict isolation of completed runs ('success' / 'failure') for supervised binary classification.
- Graceful handling of missing fields, malformed records, and invalid timestamps.
- Export and import of static CSV snapshots for reproducible offline ML development.
- Target leakage prevention enforcing pre-run feature isolation.
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd
from sqlalchemy.orm import Session

from .features import LEAKAGE_COLUMNS, PRE_RUN_FEATURE_COLUMNS

logger = logging.getLogger("ml.data")

# Mandatory schema columns for pipeline execution telemetry
REQUIRED_TELEMETRY_COLUMNS: List[str] = [
    "run_id",
    "repository",
    "branch",
    "status",
    "workflow_name",
    "event",
    "created_at",
]

# Canonical telemetry schema preserving actual database column names
CANONICAL_COLUMNS: List[str] = [
    "run_id",
    "repository",
    "branch",
    "commit_sha",
    "status",
    "conclusion",
    "workflow_name",
    "event",
    "duration_seconds",
    "failed_stage",
    "failure_type",
    "created_at",
    "synced_at",
]


def load_pipeline_runs_from_db(
    db: Session,
    repository: Optional[str] = None,
) -> pd.DataFrame:
    """Query historical pipeline runs from the database into a validated pandas DataFrame.

    Uses the active database session and existing PipelineRunModel ORM.
    Database credentials are read exclusively from environment variables via
    backend.app.db.config.get_database_url.

    Args:
        db: Active SQLAlchemy database session.
        repository: Optional filter by repository slug ('owner/repo').

    Returns:
        DataFrame containing stored pipeline execution telemetry.
    """
    from backend.app.db.models import PipelineRunModel

    query = db.query(PipelineRunModel)
    if repository:
        query = query.filter(PipelineRunModel.repository == repository)

    runs = query.all()
    if not runs:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    data = [run.to_dict() for run in runs]
    df = pd.DataFrame(data)
    return clean_pipeline_runs_dataframe(df)


def load_pipeline_runs_from_records(
    records: List[Dict[str, Any]],
) -> pd.DataFrame:
    """Convert a list of pipeline run dictionaries or schema dumps into a DataFrame.

    Applies data validation, timestamp coercion, and deduplication.

    Args:
        records: List of pipeline run attribute dictionaries.

    Returns:
        Cleaned DataFrame with appropriate column dtypes.
    """
    if not records:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    df = pd.DataFrame(records)
    return clean_pipeline_runs_dataframe(df)


def clean_pipeline_runs_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Clean, sanitize, and validate pipeline telemetry records.

    Safeguards applied:
    - Verifies presence of essential columns.
    - Drops malformed records lacking required fields (run_id, repository, branch, status).
    - Parses and normalizes timestamps ('created_at', 'synced_at') to UTC, dropping invalid NaT rows.
    - Handles duplicate run IDs, keeping the most recently synced/created record.
    - Preserves exact existing column names and status/conclusion values.

    Args:
        df: Raw telemetry DataFrame.

    Returns:
        Sanitized DataFrame.
    """
    if df.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    cleaned = df.copy()

    # Ensure required columns are present in DataFrame
    missing_cols = [c for c in REQUIRED_TELEMETRY_COLUMNS if c not in cleaned.columns]
    if missing_cols:
        raise ValueError(
            f"Input DataFrame is missing required telemetry columns: {missing_cols}"
        )

    # Filter out rows with null/empty required fields
    for col in ["run_id", "repository", "branch", "status"]:
        cleaned = cleaned[cleaned[col].notna()]
        if col in ["repository", "branch", "status"]:
            cleaned = cleaned[cleaned[col].astype(str).str.strip() != ""]

    if cleaned.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    # Coerce run_id to integer
    cleaned["run_id"] = pd.to_numeric(cleaned["run_id"], errors="coerce")
    cleaned = cleaned[cleaned["run_id"].notna()]
    cleaned["run_id"] = cleaned["run_id"].astype("int64")

    # Parse created_at and drop unparseable timestamps
    cleaned["created_at"] = pd.to_datetime(cleaned["created_at"], utc=True, errors="coerce")
    cleaned = cleaned[cleaned["created_at"].notna()]

    # Parse synced_at if present
    if "synced_at" in cleaned.columns:
        cleaned["synced_at"] = pd.to_datetime(cleaned["synced_at"], utc=True, errors="coerce")

    # Parse duration_seconds as float if present
    if "duration_seconds" in cleaned.columns:
        cleaned["duration_seconds"] = pd.to_numeric(cleaned["duration_seconds"], errors="coerce")

    # Deduplicate by run_id: sort by synced_at or created_at descending and keep latest
    sort_cols = []
    if "synced_at" in cleaned.columns and cleaned["synced_at"].notna().any():
        sort_cols.append("synced_at")
    sort_cols.append("created_at")

    cleaned = cleaned.sort_values(by=sort_cols, ascending=True)
    cleaned = cleaned.drop_duplicates(subset=["run_id"], keep="last")

    # Reset index
    cleaned = cleaned.reset_index(drop=True)
    return cleaned


def extract_completed_runs(df: pd.DataFrame) -> pd.DataFrame:
    """Filter telemetry to completed runs with definitive execution outcomes.

    Specifically:
    - Excludes unfinished runs (status != 'completed', or in_progress/queued).
    - Excludes runs without conclusions (conclusion is None or NaN).
    - Excludes ambiguous conclusions ('cancelled', 'skipped') from binary classification.
    - Retains only runs where status == 'completed' and conclusion in ('success', 'failure').

    Args:
        df: Telemetry DataFrame.

    Returns:
        Filtered DataFrame containing only eligible completed runs.
    """
    if df.empty:
        return pd.DataFrame(columns=df.columns)

    if "status" not in df.columns or "conclusion" not in df.columns:
        raise ValueError("DataFrame must contain 'status' and 'conclusion' columns.")

    completed_mask = (df["status"] == "completed") & df["conclusion"].isin(["success", "failure"])
    return df[completed_mask].copy().reset_index(drop=True)


def export_pipeline_runs_to_csv(
    df: pd.DataFrame,
    filepath: Union[str, Path],
    is_snapshot: bool = True,
) -> Path:
    """Export pipeline run telemetry to a static CSV file for offline ML development.

    Includes a metadata comment header clearly identifying the file as a snapshot.

    Args:
        df: Telemetry DataFrame to export.
        filepath: Target destination path for the CSV.
        is_snapshot: Whether to mark the file as a static snapshot in the header.

    Returns:
        Path to the saved CSV file.
    """
    target_path = Path(filepath)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    header_lines: List[str] = []
    if is_snapshot:
        now_utc = datetime.now(timezone.utc).isoformat()
        header_lines.append(f"# CI/CD Pipeline Telemetry Snapshot")
        header_lines.append(f"# Exported At (UTC): {now_utc}")
        header_lines.append(f"# Total Records: {len(df)}")
        header_lines.append(f"# Purpose: Offline ML Failure Prediction Development")
        header_lines.append(f"# Note: Static snapshot. Do not use as live database source.")

    csv_data = df.to_csv(index=False)

    with open(target_path, "w", encoding="utf-8", newline="") as f:
        for line in header_lines:
            f.write(line + "\n")
        f.write(csv_data)

    return target_path


def load_pipeline_runs_from_csv(filepath: Union[str, Path]) -> pd.DataFrame:
    """Load pipeline run telemetry from a CSV snapshot file.

    Safely skips comment header lines starting with '#' and applies
    data cleaning, timestamp parsing, and deduplication.

    Args:
        filepath: Path to the CSV snapshot file.

    Returns:
        Cleaned telemetry DataFrame.

    Raises:
        FileNotFoundError: If the CSV file does not exist.
    """
    target_path = Path(filepath)
    if not target_path.is_file():
        raise FileNotFoundError(f"Telemetry CSV file not found at: {target_path}")

    df = pd.read_csv(target_path, comment="#")
    return clean_pipeline_runs_dataframe(df)


def prepare_dataset(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.Series]:
    """Prepare pre-run feature matrix X and target vector y from pipeline telemetry.

    Applies the following data integrity safeguards:
    - Cleans raw input and resolves duplicates.
    - Strictly filters for completed runs having a definitive conclusion ('success' or 'failure').
    - Excludes unfinished, queued, cancelled, or skipped runs from the binary target labels.
    - Creates binary target 'is_failure' (1 for 'failure', 0 for 'success').
    - Strictly excludes all post-execution fields (duration, failure_type, failed_stage, status)
      to eliminate target leakage.
    - Retains only verified pre-execution features.

    Args:
        df: Raw telemetry DataFrame.

    Returns:
        Tuple of (X, y) as (features DataFrame, target Series).

    Raises:
        ValueError: If DataFrame is empty or contains no eligible completed runs.
    """
    if df.empty:
        raise ValueError("Cannot prepare dataset: input DataFrame is empty.")

    cleaned_df = clean_pipeline_runs_dataframe(df)
    completed_df = extract_completed_runs(cleaned_df)

    if completed_df.empty:
        raise ValueError(
            "Cannot prepare dataset: no completed runs with definitive conclusion ('success' or 'failure') found."
        )

    # Target: 1 for failure, 0 for success
    y = (completed_df["conclusion"] == "failure").astype(int)
    y.name = "is_failure"

    # Isolate pre-run features, guaranteeing that no leakage columns enter X
    available_pre_run_cols = [c for c in PRE_RUN_FEATURE_COLUMNS if c in completed_df.columns]
    X = completed_df[available_pre_run_cols].copy()

    # Double check no leakage columns exist in X
    leakage_present = set(X.columns).intersection(set(LEAKAGE_COLUMNS))
    if leakage_present:
        raise RuntimeError(
            f"Target leakage safeguard triggered! Found leakage columns in X: {leakage_present}"
        )

    return X, y
