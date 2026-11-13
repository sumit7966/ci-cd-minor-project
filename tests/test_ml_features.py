"""Comprehensive test suite for ML Preprocessing and Feature Engineering layer.

Validates:
1. Target mapping: binary classification (success = 0, failure = 1) and exclusion of incomplete runs.
2. Validation of required fields and clear reporting of malformed data.
3. Missing numeric and categorical imputation.
4. Safe categorical encoding, including unseen categories at inference.
5. Scikit-learn Pipeline and ColumnTransformer reusability.
6. Strict leakage prevention (excluding final status, conclusion, failure_type, failed_stage, duration).
7. Chronological historical features computed strictly from earlier completed runs.
8. Consistent feature column names and order between training and inference.
"""

from datetime import datetime, timezone
import unittest
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from ml.data import (
    clean_pipeline_runs_dataframe,
    extract_completed_runs,
    load_pipeline_runs_from_records,
    prepare_dataset,
)
from ml.features import (
    ALL_FEATURE_COLUMNS,
    CATEGORICAL_COLUMNS,
    HISTORICAL_NUMERIC_COLUMNS,
    LEAKAGE_COLUMNS,
    NUMERIC_COLUMNS,
    TEMPORAL_NUMERIC_COLUMNS,
    PipelineRunFeatureExtractor,
    build_feature_preprocessor,
    build_preprocessor,
)


class TestMLPreprocessingAndFeatureEngineering(unittest.TestCase):
    """Test suite verifying feature engineering, transformers, and leakage safeguards."""

    def _create_chronological_runs(self):
        """Create a chronologically ordered sequence of 6 telemetry runs on main and feature branches."""
        return [
            # Run 1: 10:00 - main branch success
            {
                "run_id": 1,
                "repository": "test/repo",
                "branch": "main",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "duration_seconds": 35.0,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-10T10:00:00Z",
            },
            # Run 2: 11:00 - main branch failure
            {
                "run_id": 2,
                "repository": "test/repo",
                "branch": "main",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "status": "completed",
                "conclusion": "failure",
                "duration_seconds": 20.0,
                "failed_stage": "test",
                "failure_type": "test_failure",
                "created_at": "2026-09-10T11:00:00Z",
            },
            # Run 3: 12:00 - main branch failure (consecutive=2)
            {
                "run_id": 3,
                "repository": "test/repo",
                "branch": "main",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "status": "completed",
                "conclusion": "failure",
                "duration_seconds": 15.0,
                "failed_stage": "build",
                "failure_type": "build_failure",
                "created_at": "2026-09-10T12:00:00Z",
            },
            # Run 4: 13:00 - feature branch success
            {
                "run_id": 4,
                "repository": "test/repo",
                "branch": "feature/login",
                "workflow_name": "CI Pipeline",
                "event": "pull_request",
                "status": "completed",
                "conclusion": "success",
                "duration_seconds": 40.0,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-10T13:00:00Z",
            },
            # Run 5: 14:00 - in_progress run (MUST be excluded)
            {
                "run_id": 5,
                "repository": "test/repo",
                "branch": "main",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "status": "in_progress",
                "conclusion": None,
                "duration_seconds": None,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-10T14:00:00Z",
            },
            # Run 6: 15:00 - cancelled run (MUST be excluded)
            {
                "run_id": 6,
                "repository": "test/repo",
                "branch": "main",
                "workflow_name": "CI Pipeline",
                "event": "push",
                "status": "completed",
                "conclusion": "cancelled",
                "duration_seconds": 5.0,
                "failed_stage": None,
                "failure_type": "none",
                "created_at": "2026-09-10T15:00:00Z",
            },
        ]

    def test_binary_target_mapping_and_exclusion(self) -> None:
        """Requirement 1: Verify binary target derivation (success=0, failure=1) and exclusion of incomplete runs."""
        df = load_pipeline_runs_from_records(self._create_chronological_runs())
        X, y = prepare_dataset(df)

        # 6 total runs -> 4 completed valid runs (runs 1, 2, 3, 4)
        self.assertEqual(len(y), 4)
        self.assertEqual(len(X), 4)

        # Labels: run 1 (success=0), run 2 (failure=1), run 3 (failure=1), run 4 (success=0)
        self.assertEqual(list(y), [0, 1, 1, 0])
        self.assertEqual(y.name, "is_failure")

    def test_required_fields_validation_and_reporting(self) -> None:
        """Requirement 2: Validate required fields and reject malformed missing data."""
        # Missing essential column 'repository'
        malformed_df = pd.DataFrame([
            {"branch": "main", "status": "completed", "workflow_name": "CI", "event": "push"}
        ])
        with self.assertRaises(ValueError) as ctx:
            clean_pipeline_runs_dataframe(malformed_df)
        self.assertIn("missing required telemetry columns", str(ctx.exception).lower())

        # Missing 'status' or 'conclusion' in completed runs extraction
        with self.assertRaises(ValueError):
            extract_completed_runs(pd.DataFrame([{"run_id": 1, "repository": "test/repo"}]))

    def test_missing_numeric_and_categorical_imputation(self) -> None:
        """Requirement 3: Verify missing numeric and categorical values are imputed cleanly without NaNs."""
        records = [
            {
                "repository": None,  # Missing categorical
                "branch": "main",
                "workflow_name": None,
                "event": "push",
                "created_at": None,  # Missing timestamp
            }
        ]
        preprocessor = build_preprocessor()
        # Transform un-fitted / default state
        transformed = preprocessor.fit_transform(pd.DataFrame(records))

        self.assertIsInstance(transformed, np.ndarray)
        self.assertEqual(transformed.shape[0], 1)
        # Ensure zero NaN or infinite values after transformation
        self.assertFalse(np.isnan(transformed).any())
        self.assertFalse(np.isinf(transformed).any())

    def test_unseen_categories_safe_encoding(self) -> None:
        """Requirement 4: Verify unseen categories at inference are encoded safely without raising errors."""
        train_data = pd.DataFrame([
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-10T10:00:00Z"},
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-10T11:00:00Z"},
        ])
        y_train = pd.Series([0, 1])

        preprocessor = build_preprocessor()
        preprocessor.fit(train_data, y_train)

        # Incoming inference sample with unseen repository, branch, workflow, and event
        test_unseen = pd.DataFrame([
            {
                "repository": "unseen-repo-xyz",
                "branch": "feature/unseen-branch",
                "workflow_name": "Deploy Nightly",
                "event": "workflow_dispatch",
                "created_at": "2026-09-11T12:00:00Z",
            }
        ])

        transformed = preprocessor.transform(test_unseen)
        self.assertIsInstance(transformed, np.ndarray)
        self.assertEqual(transformed.shape[0], 1)
        self.assertFalse(np.isnan(transformed).any())

    def test_pipeline_and_column_transformer_reusability(self) -> None:
        """Requirement 5: Verify reusable Pipeline and ColumnTransformer produce consistent output shapes."""
        preprocessor = build_preprocessor()
        self.assertIsInstance(preprocessor, Pipeline)
        self.assertIn("extractor", preprocessor.named_steps)
        self.assertIn("preprocessor", preprocessor.named_steps)

        df = load_pipeline_runs_from_records(self._create_chronological_runs())
        X, y = prepare_dataset(df)

        fitted_pipe = preprocessor.fit(X, y)
        transformed_train = fitted_pipe.transform(X)

        self.assertEqual(transformed_train.shape[0], len(X))
        self.assertGreater(transformed_train.shape[1], len(ALL_FEATURE_COLUMNS))

    def test_leakage_prevention(self) -> None:
        """Requirement 6: Verify all post-run outcome fields are strictly excluded from predictors."""
        df = load_pipeline_runs_from_records(self._create_chronological_runs())
        X, _ = prepare_dataset(df)

        for col in LEAKAGE_COLUMNS:
            self.assertNotIn(col, X.columns, f"Target leakage violation! Found '{col}' in X.")

        # Ensure feature extractor also excludes post-run columns from output
        extractor = PipelineRunFeatureExtractor()
        features = extractor.fit_transform(X, pd.Series([0, 1, 1, 0]))

        for col in LEAKAGE_COLUMNS:
            self.assertNotIn(col, features.columns, f"Leakage field '{col}' present in engineered features!")

    def test_chronological_historical_features(self) -> None:
        """Requirement 7: Verify historical features are computed strictly from earlier completed runs."""
        # 4 completed runs on main branch in order:
        # Run 1: success (y=0) -> prior runs: 0 -> hist_branch_failure_rate = 0.0, consec = 0
        # Run 2: failure (y=1) -> prior runs: 1 (success) -> hist_branch_failure_rate = 0.0, consec = 0
        # Run 3: failure (y=1) -> prior runs: 2 (success, fail) -> hist_branch_failure_rate = 0.5, consec = 1
        # Run 4: success (y=0) -> prior runs: 3 (success, fail, fail) -> hist_branch_failure_rate = 0.6667, consec = 2
        runs = [
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-10T10:00:00Z"},
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-10T11:00:00Z"},
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-10T12:00:00Z"},
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-10T13:00:00Z"},
        ]
        df = pd.DataFrame(runs)
        y = pd.Series([0, 1, 1, 0])

        extractor = PipelineRunFeatureExtractor()
        features = extractor.fit_transform(df, y)

        # Row 1 (first run): no prior runs
        self.assertEqual(features.iloc[0]["hist_consecutive_failures"], 0)
        self.assertEqual(features.iloc[0]["hist_branch_failure_rate"], 0.0)

        # Row 2 (second run): sees only Row 1 (success)
        self.assertEqual(features.iloc[1]["hist_consecutive_failures"], 0)
        self.assertEqual(features.iloc[1]["hist_branch_failure_rate"], 0.0)

        # Row 3 (third run): sees Row 1 (success) and Row 2 (failure) -> 1/2 = 0.5, consecutive=1
        self.assertEqual(features.iloc[2]["hist_consecutive_failures"], 1)
        self.assertEqual(features.iloc[2]["hist_branch_failure_rate"], 0.5)

        # Row 4 (fourth run): sees Row 1 (success), Row 2 (fail), Row 3 (fail) -> 2/3 = 0.6667, consecutive=2
        self.assertEqual(features.iloc[3]["hist_consecutive_failures"], 2)
        self.assertAlmostEqual(features.iloc[3]["hist_branch_failure_rate"], 0.6667, places=3)

    def test_feature_columns_and_order_consistency(self) -> None:
        """Requirement 8: Verify feature columns and their order remain identical between training and inference."""
        extractor = PipelineRunFeatureExtractor()

        train_data = pd.DataFrame([
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-10T10:00:00Z"},
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-10T11:00:00Z"},
        ])
        y = pd.Series([0, 1])

        train_features = extractor.fit_transform(train_data, y)

        inference_sample = pd.DataFrame([
            {"repository": "repo-a", "branch": "main", "workflow_name": "CI", "event": "push", "created_at": "2026-09-11T12:00:00Z"}
        ])
        inf_features = extractor.transform(inference_sample)

        self.assertEqual(list(train_features.columns), list(inf_features.columns))
        self.assertEqual(list(train_features.columns), ALL_FEATURE_COLUMNS)
