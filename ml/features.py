"""Feature engineering definitions, temporal extraction, and historical signal modeling.

This module provides the preprocessing and feature-engineering layer for Member 2's ML module:
- Direct pre-run categorical metadata (repository, branch, workflow_name, event).
- Temporal signals derived from trigger timestamp (hour, day_of_week, is_weekend).
- Chronological historical performance metrics computed strictly from earlier completed runs
  (hist_repo_failure_rate, hist_branch_failure_rate, hist_workflow_failure_rate, hist_consecutive_failures).
- Strict exclusion of all post-run outcome fields to eliminate target leakage.
- Reusable scikit-learn Pipeline and ColumnTransformer with imputation and safe encoding.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

# Categorical pre-run metadata columns (available before execution begins)
CATEGORICAL_COLUMNS: List[str] = [
    "repository",
    "branch",
    "workflow_name",
    "event",
]

# Temporal numeric columns derived from created_at trigger timestamp
TEMPORAL_NUMERIC_COLUMNS: List[str] = [
    "hour",
    "day_of_week",
    "is_weekend",
]

# Historical numeric columns derived strictly from earlier completed runs
HISTORICAL_NUMERIC_COLUMNS: List[str] = [
    "hist_repo_failure_rate",
    "hist_branch_failure_rate",
    "hist_workflow_failure_rate",
    "hist_consecutive_failures",
]

NUMERIC_COLUMNS: List[str] = TEMPORAL_NUMERIC_COLUMNS + HISTORICAL_NUMERIC_COLUMNS
ALL_FEATURE_COLUMNS: List[str] = CATEGORICAL_COLUMNS + NUMERIC_COLUMNS

# Input features acceptable before execution begins
PRE_RUN_CATEGORICAL_COLUMNS: List[str] = CATEGORICAL_COLUMNS
PRE_RUN_FEATURE_COLUMNS: List[str] = PRE_RUN_CATEGORICAL_COLUMNS + ["created_at"]

# Target leakage fields that are strictly forbidden from predictors
LEAKAGE_COLUMNS: List[str] = [
    "conclusion",
    "status",
    "duration_seconds",
    "failed_stage",
    "failure_type",
    "synced_at",
]


class PipelineRunFeatureExtractor(BaseEstimator, TransformerMixin):
    """Custom transformer extracting pre-run temporal features and historical signals.

    Ensures zero target leakage:
    - During training: Computes point-in-time historical failure metrics using expanding windows
      strictly shifted by 1 row (row i only sees runs from rows 0 to i-1).
    - During inference: Evaluates incoming samples against historical summary state learned during fit(),
      falling back safely to repository-level or global historical rates for unseen branches.
    - Guarantees consistent column names and order across training and inference.
    """

    def __init__(self) -> None:
        self.global_failure_rate_: float = 0.0
        self.repo_stats_: Dict[str, Dict[str, Any]] = {}
        self.branch_stats_: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.workflow_stats_: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.is_fitted_: bool = False

    def fit(self, X: pd.DataFrame, y: Optional[Union[pd.Series, np.ndarray]] = None):
        """Fit the extractor on training telemetry and learn historical failure distributions."""
        df = X.copy() if isinstance(X, pd.DataFrame) else pd.DataFrame(X)
        df = df.reset_index(drop=True)

        if y is not None:
            y_arr = np.asarray(y)
            self.global_failure_rate_ = float(np.mean(y_arr)) if len(y_arr) > 0 else 0.0

            # Compute repository-level statistics
            if "repository" in df.columns:
                for repo in df["repository"].dropna().unique():
                    mask = (df["repository"] == repo).to_numpy()
                    total = int(np.sum(mask))
                    failures = int(np.sum(y_arr[mask]))
                    self.repo_stats_[repo] = {
                        "total": total,
                        "failures": failures,
                        "rate": round(failures / total, 4) if total > 0 else 0.0,
                    }

            # Compute branch-level statistics
            if "repository" in df.columns and "branch" in df.columns:
                unique_pairs = df[["repository", "branch"]].drop_duplicates().to_numpy()
                for repo, branch in unique_pairs:
                    mask = ((df["repository"] == repo) & (df["branch"] == branch)).to_numpy()
                    total = int(np.sum(mask))
                    branch_y = y_arr[mask]
                    failures = int(np.sum(branch_y))
                    rate = round(failures / total, 4) if total > 0 else 0.0

                    # Count consecutive failures at the end of training
                    consecutive = 0
                    for outcome in reversed(branch_y):
                        if outcome == 1:
                            consecutive += 1
                        else:
                            break

                    self.branch_stats_[(repo, branch)] = {
                        "total": total,
                        "failures": failures,
                        "rate": rate,
                        "consecutive_failures": consecutive,
                    }

            # Compute workflow-level statistics
            if "repository" in df.columns and "workflow_name" in df.columns:
                unique_pairs = df[["repository", "workflow_name"]].drop_duplicates().to_numpy()
                for repo, wf in unique_pairs:
                    mask = ((df["repository"] == repo) & (df["workflow_name"] == wf)).to_numpy()
                    total = int(np.sum(mask))
                    wf_y = y_arr[mask]
                    failures = int(np.sum(wf_y))
                    rate = round(failures / total, 4) if total > 0 else 0.0
                    self.workflow_stats_[(repo, wf)] = {
                        "total": total,
                        "failures": failures,
                        "rate": rate,
                    }

        self.is_fitted_ = True
        return self

    def fit_transform(self, X: pd.DataFrame, y: Optional[Union[pd.Series, np.ndarray]] = None) -> pd.DataFrame:
        """Fit extractor and transform training telemetry chronologically without lookahead leakage."""
        self.fit(X, y)

        df = X.copy() if isinstance(X, pd.DataFrame) else pd.DataFrame(X)
        df = df.reset_index(drop=True)
        if y is None or len(df) == 0:
            return self.transform(df)

        y_arr = np.asarray(y)

        # Sort chronologically by created_at if present to enforce strict temporal order
        if "created_at" in df.columns:
            dt_series = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
            sort_order = np.argsort(dt_series.fillna(pd.Timestamp.min).to_numpy())
        else:
            sort_order = np.arange(len(df))

        # Expanding point-in-time historical trackers
        repo_history: Dict[str, List[int]] = {}
        branch_history: Dict[Tuple[str, str], List[int]] = {}
        workflow_history: Dict[Tuple[str, str], List[int]] = {}

        n_samples = len(df)
        hist_repo_rates = np.zeros(n_samples, dtype=float)
        hist_branch_rates = np.zeros(n_samples, dtype=float)
        hist_wf_rates = np.zeros(n_samples, dtype=float)
        hist_consec_fails = np.zeros(n_samples, dtype=int)

        # Calculate chronological stats shifted by 1 row
        for pos, idx in enumerate(sort_order):
            repo = str(df.iloc[idx].get("repository", "unknown"))
            branch = str(df.iloc[idx].get("branch", "unknown"))
            wf = str(df.iloc[idx].get("workflow_name", "unknown"))
            current_outcome = int(y_arr[idx])

            # 1. Historical repo failure rate (0.0 if no prior runs)
            prior_repo = repo_history.get(repo, [])
            hist_repo_rates[idx] = (
                round(sum(prior_repo) / len(prior_repo), 4) if prior_repo else 0.0
            )

            # 2. Historical branch failure rate & consecutive failures
            prior_branch = branch_history.get((repo, branch), [])
            hist_branch_rates[idx] = (
                round(sum(prior_branch) / len(prior_branch), 4)
                if prior_branch
                else hist_repo_rates[idx]
            )
            consec = 0
            for out in reversed(prior_branch):
                if out == 1:
                    consec += 1
                else:
                    break
            hist_consec_fails[idx] = consec

            # 3. Historical workflow failure rate (0.0 if no prior runs)
            prior_wf = workflow_history.get((repo, wf), [])
            hist_wf_rates[idx] = (
                round(sum(prior_wf) / len(prior_wf), 4) if prior_wf else 0.0
            )

            # Update history AFTER assigning features for the current row
            repo_history.setdefault(repo, []).append(current_outcome)
            branch_history.setdefault((repo, branch), []).append(current_outcome)
            workflow_history.setdefault((repo, wf), []).append(current_outcome)

        # Build feature DataFrame
        features = self._build_base_dataframe(df)
        features["hist_repo_failure_rate"] = hist_repo_rates
        features["hist_branch_failure_rate"] = hist_branch_rates
        features["hist_workflow_failure_rate"] = hist_wf_rates
        features["hist_consecutive_failures"] = hist_consec_fails

        return features[ALL_FEATURE_COLUMNS]

    def transform(self, X: Union[pd.DataFrame, Dict[str, Any]]) -> pd.DataFrame:
        """Transform incoming pre-run samples for inference using fitted historical distributions."""
        if isinstance(X, dict):
            df = pd.DataFrame([X])
        elif isinstance(X, pd.DataFrame):
            df = X.copy()
        else:
            df = pd.DataFrame(X)

        features = self._build_base_dataframe(df)

        n_samples = len(df)
        hist_repo_rates = np.zeros(n_samples, dtype=float)
        hist_branch_rates = np.zeros(n_samples, dtype=float)
        hist_wf_rates = np.zeros(n_samples, dtype=float)
        hist_consec_fails = np.zeros(n_samples, dtype=int)

        for i in range(n_samples):
            repo = str(df.iloc[i].get("repository", "unknown"))
            branch = str(df.iloc[i].get("branch", "unknown"))
            wf = str(df.iloc[i].get("workflow_name", "unknown"))

            # Repository rate lookup (with 0.0 fallback)
            repo_stat = self.repo_stats_.get(repo)
            repo_rate = repo_stat["rate"] if repo_stat else 0.0
            hist_repo_rates[i] = repo_rate

            # Branch rate lookup (with repo/0.0 fallback for unseen branches)
            branch_stat = self.branch_stats_.get((repo, branch))
            if branch_stat:
                hist_branch_rates[i] = branch_stat["rate"]
                hist_consec_fails[i] = branch_stat.get("consecutive_failures", 0)
            else:
                hist_branch_rates[i] = repo_rate
                hist_consec_fails[i] = 0

            # Workflow rate lookup (with 0.0 fallback)
            wf_stat = self.workflow_stats_.get((repo, wf))
            hist_wf_rates[i] = wf_stat["rate"] if wf_stat else 0.0

        features["hist_repo_failure_rate"] = hist_repo_rates
        features["hist_branch_failure_rate"] = hist_branch_rates
        features["hist_workflow_failure_rate"] = hist_wf_rates
        features["hist_consecutive_failures"] = hist_consec_fails

        return features[ALL_FEATURE_COLUMNS]

    def _build_base_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extract baseline categorical and temporal features, rejecting leakage fields."""
        features = pd.DataFrame(index=df.index)

        # Categorical columns
        for col in CATEGORICAL_COLUMNS:
            if col in df.columns:
                features[col] = df[col].astype(str).fillna("unknown")
            else:
                features[col] = "unknown"

        # Temporal columns from created_at
        now = datetime.now(timezone.utc)
        if "created_at" in df.columns:
            dt_series = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
            hours = dt_series.dt.hour.fillna(now.hour).astype(int)
            dows = dt_series.dt.dayofweek.fillna(now.weekday()).astype(int)
        else:
            n = len(df)
            hours = pd.Series(np.full(n, now.hour), index=df.index)
            dows = pd.Series(np.full(n, now.weekday()), index=df.index)

        features["hour"] = hours
        features["day_of_week"] = dows
        features["is_weekend"] = (dows >= 5).astype(int)

        return features


def build_feature_preprocessor() -> ColumnTransformer:
    """Build scikit-learn ColumnTransformer for imputing and encoding engineered features.

    - Categoricals: SimpleImputer ('unknown') + OneHotEncoder (handle_unknown='ignore')
    - Numerics: SimpleImputer (median) + StandardScaler
    """
    cat_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="constant", fill_value="unknown")),
            ("encoder", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )

    num_pipeline = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
        ]
    )

    return ColumnTransformer(
        transformers=[
            ("cat", cat_pipeline, CATEGORICAL_COLUMNS),
            ("num", num_pipeline, NUMERIC_COLUMNS),
        ],
        remainder="drop",
    )


def build_preprocessor() -> Pipeline:
    """Build the end-to-end preprocessing pipeline combining feature extraction and encoding."""
    return Pipeline(
        steps=[
            ("extractor", PipelineRunFeatureExtractor()),
            ("preprocessor", build_feature_preprocessor()),
        ]
    )
