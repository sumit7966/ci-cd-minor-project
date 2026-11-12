# Member 2 ML Component: CI/CD Pipeline Failure Prediction

This document provides complete, truthful technical documentation for **Member 2 (Lakshay Yadav)**'s Machine Learning failure-prediction subsystem in the CI/CD Pipeline Failure Prediction & Optimization Platform.

---

## 1. Purpose & Scope

The purpose of the ML component is to predict the probability of a CI/CD pipeline failure **before or at the moment a workflow run is triggered**, enabling developers to:
- Detect risky commits, branches, and events before running expensive CI test matrices.
- Provide real-time risk tiers (`low`, `medium`, `high`) and confidence scores.
- Supply historical failure risk indicators to Member 3's optimization and dashboard subsystem.

**Scope Boundaries**:
- **Member 1 (Tarun Kumar Vashisth)**: CI/CD telemetry collection, GitHub Actions synchronization, database ORM models, and storage.
- **Member 2 (Lakshay Yadav)**: ML data access, preprocessing, leakage prevention, training, evaluation, model versioning/persistence, inference service, and prediction API (`/api/predictions`).
- **Member 3 (Sumit)**: Pipeline optimization, bottleneck detection, and React frontend dashboard.

---

## 2. Data Sources & Target-Label Mapping

### Data Sources
1. **PostgreSQL Database**:
   - ORM Model: `backend.app.db.models.PipelineRunModel` (table: `pipeline_runs`).
   - Query function: `ml.data.load_pipeline_runs_from_db(db, repository=None)`.
   - Credentials resolved strictly from environment variables (`DATABASE_URL`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`). No hardcoded secrets.
2. **Static CSV Snapshot**:
   - File path: `data/pipeline_runs_snapshot.csv`.
   - Reader: `ml.data.load_pipeline_runs_from_csv(csv_path)`.
   - Preserves offline reproducibility without database or network dependencies.

### Target-Label Mapping ($y$)
Supervised binary classification requires ground-truth completed runs:
- `conclusion == "failure"` $\rightarrow$ **`y = 1`** (Positive class: Failure)
- `conclusion == "success"` $\rightarrow$ **`y = 0`** (Negative class: Success)

### Exclusion Rules (Unfinished & Ambiguous Runs)
To prevent noisy, unverified, or ambiguous target labels:
- **In-Progress runs** (`status != "completed"` or `conclusion is None`) are excluded.
- **Queued runs** (`status == "queued"`) are excluded.
- **Cancelled runs** (`conclusion == "cancelled"`) are excluded (interrupted externally, not a code/build failure).
- **Skipped runs** (`conclusion == "skipped"`) are excluded (not executed).

---

## 3. Feature List & Prediction-Time Availability

All features are strictly **pre-run features**, meaning they are known at the moment of trigger ($t_0$) before any build or test execution begins.

| Feature Name | Type | Extraction Source | Availability at Trigger Time |
| :--- | :--- | :--- | :--- |
| `repository` | Categorical | Git repository slug (`owner/repo`) | Known at trigger time |
| `branch` | Categorical | Git branch name (`main`, `member1`, etc.) | Known at trigger time |
| `workflow_name` | Categorical | CI workflow identifier (`CI Pipeline`) | Known at trigger time |
| `event` | Categorical | Trigger event (`push`, `pull_request`, `workflow_dispatch`) | Known at trigger time |
| `hour` | Numeric (0–23) | UTC hour extracted from `created_at` | Known at trigger time |
| `day_of_week` | Numeric (0–6) | UTC day of week extracted from `created_at` (0 = Monday) | Known at trigger time |
| `is_weekend` | Numeric (0 or 1) | Indicator whether run was triggered on Saturday/Sunday | Known at trigger time |
| `hist_repo_failure_rate` | Numeric (0.0–1.0) | Historical failure rate of the repository on earlier runs | Derived from completed prior runs |
| `hist_branch_failure_rate` | Numeric (0.0–1.0) | Historical failure rate of the branch on earlier runs | Derived from completed prior runs |
| `hist_workflow_failure_rate` | Numeric (0.0–1.0) | Historical failure rate of the workflow on earlier runs | Derived from completed prior runs |
| `hist_consecutive_failures` | Integer ($\ge 0$) | Count of consecutive failures on this branch before current run | Derived from completed prior runs |

---

## 4. Leakage Prevention & Historical Feature Construction

### Target Leakage Prevention
Post-execution outcome fields are strictly **excluded** from training features and prediction inputs:
- `status`: Lifecycle state (only becomes `"completed"` after the run finishes).
- `conclusion`: The prediction target itself.
- `duration_seconds`: Total runtime, unknown before the run completes.
- `failed_stage`: Failure stage (`test`, `build`, `dependency`), unknown before failure occurs.
- `failure_type`: Classified failure category, unknown before failure occurs.
- `synced_at`: Internal synchronization timestamp.

If any of these fields are accidentally supplied to the prediction endpoint or feature transformer, they are stripped before feature extraction.

### Expanding-Window Historical Statistics
To prevent lookahead leakage during training:
1. Telemetry is sorted chronologically by `created_at`.
2. For row $i$, historical statistics are calculated using an expanding window over strictly preceding rows: $\{j \mid j < i\}$.
3. Outcome $y_i$ of the current run is only added to the historical tracker **after** row $i$'s features are generated.
4. If a branch, repository, or workflow has no previous completed runs, historical rates default to `0.0`.

---

## 5. Data-Cleaning Rules & Actual Dataset Statistics

### Data-Cleaning Rules (`ml/data.py`)
- **Deduplication**: Rows with duplicate `run_id` values are resolved by sorting by `synced_at` or `created_at` and keeping the latest record.
- **Mandatory Fields**: Rows lacking `run_id`, `repository`, `branch`, or `status` are rejected.
- **Timestamp Parsing**: `created_at` is coerced using `pd.to_datetime(..., utc=True, errors="coerce")`; unparseable rows are dropped.
- **Categorical Normalization**: Null or whitespace-only categoricals are mapped to `"unknown"`.

### Actual Project Dataset Breakdown
Based on real project telemetry from PostgreSQL / `data/pipeline_runs_snapshot.csv`:
- **Total Raw Records**: 14 runs
- **Excluded Runs**: 0 (all 14 runs are completed with definitive conclusion)
- **Eligible Completed Runs**: 14
- **Class Counts**:
  - `success (0)`: 6 runs (42.86%)
  - `failure (1)`: 8 runs (57.14%)

---

## 6. Evaluation Split, Tested Models, Actual Metrics & Limitations

### Chronological Holdout Split
To reflect real-world deployment where models predict future runs from past runs:
- **Split Strategy**: Chronological ordering by `created_at` with an 80/20 train/test split.
- **Training Set**: 11 runs (6 success, 5 failure) — balanced, both classes present.
- **Test Set**: 3 runs (0 success, 3 failure) — chronologically latest runs.

### Tested Models & Comparison

#### Baseline Model: Logistic Regression (Selected for Production)
- **Pipeline**: `build_preprocessor()` (`ColumnTransformer` with `SimpleImputer`, `OneHotEncoder(handle_unknown='ignore')`, `StandardScaler`) + `LogisticRegression(solver='liblinear', class_weight='balanced', random_state=42)`.
- **Pre-processing Fit**: Fit strictly on training split only.

#### Candidate Model: Random Forest (Tested & Deferred)
- **Pipeline**: Identical `build_preprocessor()` + `RandomForestClassifier(n_estimators=100, class_weight='balanced', random_state=42)`.

### Actual Evaluation Results

| Metric | Logistic Regression (Baseline) | Random Forest (Candidate) |
| :--- | :--- | :--- |
| **Accuracy** | 1.0000 | 1.0000 |
| **Precision** | 1.0000 | 1.0000 |
| **Recall** | 1.0000 | 1.0000 |
| **F1 Score** | 1.0000 | 1.0000 |
| **ROC-AUC** | `null` *(Single class in test split)* | `null` *(Single class in test split)* |
| **Confusion Matrix** | `[[0, 0], [0, 3]]` | `[[0, 0], [0, 3]]` |

### Why Random Forest is Deferred
1. **Dataset Size Insufficiency**: Total sample size ($N=14$) is below the statistical threshold ($N \ge 30$) required for ensemble stability. An ensemble of 100 trees on 11 training rows is heavily over-parameterized.
2. **Single-Class Test Partition**: The chronological holdout split contains 3 test runs, all belonging to class `1` (failure). Because there are no negative samples (`0`), false positive rates, specificity, and ROC-AUC are mathematically undefined.
3. **Inconclusive Evidence**: Both models score identically ($1.0$ F1). Per Occam's razor, the simpler, interpretable Logistic Regression model is selected. Random Forest is deferred until more runs with diverse outcomes accumulate.

---

## 7. Model Artifact Path, Integrity & Versioning

### Artifact Storage Paths
- **Active Production Model**: `ml/models/pipeline_failure_model.joblib`
- **Active Production Metadata**: `ml/models/metadata.json`
- **Versioned Archive**: `ml/models/versions/pipeline_failure_model_v{version}.joblib`
- **Versioned Metadata Archive**: `ml/models/versions/metadata_v{version}.json`

### Cryptographic Integrity & Versioning
- Every training run computes the **SHA-256 cryptographic digest** of the `.joblib` binary file and records it in `metadata.json`.
- Current active model hash: `68c768b5148286c49b4be4f310ec23b8bfa33aff86e1c47c68039d018c318337`.
- `load_model(version="1.0.0")` supports loading specific historical versions from `ml/models/versions/`.
- `list_model_versions()` dynamically scans available archived versions.

### Protection Against Unsafe Overwrite
- `validate_training_for_persistence()` prevents saving if:
  - Pipeline is `None` or unfitted (missing `predict_proba`).
  - Metadata report contains `"status": "error"`.
  - Training split has fewer than 2 samples.
  - A valid artifact is **never overwritten** by an invalid or failed run.

---

## 8. Command Reference

### Environment Setup
Activate the project virtual environment before running commands:
```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

### Model Training Commands
```powershell
# Retrain using PostgreSQL database telemetry
python -m ml.train

# Retrain using static CSV snapshot
python -m ml.train --csv data/pipeline_runs_snapshot.csv

# Train with explicit semantic version
python -m ml.train --version 1.1.0

# Run side-by-side comparison with Random Forest
python -m ml.train --compare-rf --csv data/pipeline_runs_snapshot.csv
```

### Automated Testing Commands
```powershell
# Run all repository tests (116 tests)
pytest

# Run focused ML test modules
pytest tests/test_ml_data.py
pytest tests/test_ml_features.py
pytest tests/test_ml_train.py
pytest tests/test_ml_persistence.py
pytest tests/test_ml_predictor.py
pytest tests/test_api_predictions.py
```

### Backend Server Commands
```powershell
# Start FastAPI backend locally
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

# Start full Docker stack (PostgreSQL + Backend + Adminer)
docker compose up -d
```

---

## 9. Prediction API Schema & Error Behavior

The prediction service is exposed on the FastAPI backend at `POST /api/predictions`.

### Request Schema (`POST /api/predictions`)
```json
{
  "repository": "sumit7966/ci-cd-minor-project",
  "branch": "member1",
  "workflow_name": "CI Pipeline",
  "event": "push",
  "created_at": "2026-09-09T16:00:00Z",
  "run_id": 34322655257
}
```

| Field | Type | Required | Description |
| :--- | :--- | :---: | :--- |
| `repository` | String | Yes | Repository path in `owner/repo` format |
| `branch` | String | Yes | Git branch triggering the run |
| `workflow_name` | String | Yes | CI workflow name |
| `event` | String | Yes | Trigger event (`push`, `pull_request`, `workflow_dispatch`) |
| `created_at` | String (ISO-8601) | No | Trigger timestamp (defaults to current UTC time if omitted) |
| `run_id` | Integer | No | Optional workflow run ID for correlation |

*Note: Any extra outcome fields (e.g., `status`, `conclusion`, `duration_seconds`) are ignored to prevent data leakage.*

### Response Schema (`HTTP 200 OK`)
```json
{
  "status": "success",
  "predicted_class": 1,
  "is_failure_predicted": true,
  "failure_probability": 0.8392,
  "success_probability": 0.1608,
  "risk_level": "high",
  "confidence_score": 0.6784,
  "features_evaluated": {
    "repository": "sumit7966/ci-cd-minor-project",
    "branch": "member1",
    "workflow_name": "CI Pipeline",
    "event": "push",
    "created_at": "2026-09-09 16:00:00+00:00"
  },
  "model_metadata": {
    "model_version": "1.0.0",
    "model_type": "LogisticRegression",
    "trained_at": "2026-10-10T16:52:52.133159+00:00",
    "artifact_sha256": "68c768b5148286c49b4be4f310ec23b8bfa33aff86e1c47c68039d018c318337"
  }
}
```

### Risk Level Tiers
- **`high`**: `failure_probability >= 0.65`
- **`medium`**: `0.35 <= failure_probability < 0.65`
- **`low`**: `failure_probability < 0.35`
- **`confidence_score`**: $| \text{failure\_probability} - 0.5 | \times 2$ (scaled to $[0.0, 1.0]$).

### Status Endpoint (`GET /api/predictions/status`)
```json
{
  "status": "ready",
  "model_available": true,
  "model_version": "1.0.0",
  "model_type": "LogisticRegression",
  "trained_at": "2026-10-10T16:52:52.133159+00:00",
  "artifact_sha256": "68c768b5148286c49b4be4f310ec23b8bfa33aff86e1c47c68039d018c318337"
}
```

### API Error Handling

| Scenario | HTTP Status Code | Response Body |
| :--- | :---: | :--- |
| Missing required field (e.g., `branch`) | **422 Unprocessable Entity** | Detailed Pydantic field location and error message |
| Empty string for required field (`""`) | **422 Unprocessable Entity** | String should have at least 1 character |
| Model artifact missing or unready | **503 Service Unavailable** | `{"detail": "Failure prediction model is currently unavailable..."}` |
| Malformed input structure | **400 Bad Request** | `{"detail": "Prediction input must be a dict..."}` |
| Unseen category (new branch/workflow) | **200 OK** | Evaluated safely via `handle_unknown='ignore'`; returns valid prediction |

---

## 10. Missing-Artifact & Insufficient-Data Behavior

### Missing Artifacts
- `load_model()` raises `FileNotFoundError("Trained model artifact not found at '...'")`.
- `PipelineFailurePredictor.is_ready()` returns `False`.
- `PipelineFailurePredictor.predict()` raises `ModelUnavailableError`.
- `PipelineFailurePredictor.predict_safe()` returns `{"status": "model_unavailable", ...}` without throwing.
- FastAPI endpoint maps this to `HTTP 503 Service Unavailable`.

### Insufficient Training Data
- **0 runs in database**: `train_from_db()` aborts and returns `{"status": "error", "message": "Cannot train failure model: database contains 0 pipeline runs."}`.
- **$< 4$ total runs**: `train_failure_model()` raises `ValueError("Dataset has insufficient samples (< 4)")`.
- **Single-class data** (e.g. only successes): `train_failure_model()` raises `ValueError("Training requires both positive and negative outcomes")`.
- In all error scenarios, `save_model()` is blocked, preserving the existing valid model on disk.

---

## 11. Complete Reproduction Procedure

To reproduce the entire training, persistence, and prediction pipeline from a clean environment:

1. **Verify Database Records**:
   ```powershell
   docker exec cicd_postgres psql -U postgres -d cicd_platform -c "SELECT count(*) FROM pipeline_runs;"
   # Expected output: 14
   ```

2. **Train and Persist Model**:
   ```powershell
   python -m ml.train --csv data/pipeline_runs_snapshot.csv --version 1.0.0
   ```

3. **Verify Saved Artifacts**:
   ```powershell
   python -c "from ml.persistence import is_model_trained, get_model_metadata; print('Trained:', is_model_trained()); print('Version:', get_model_metadata()['model_version'])"
   ```

4. **Run Model Comparison Check**:
   ```powershell
   python -m ml.train --compare-rf --csv data/pipeline_runs_snapshot.csv
   ```

5. **Execute All Test Suites**:
   ```powershell
   pytest
   # Expected output: 116 passed
   ```

6. **Test Live Prediction Endpoint**:
   ```powershell
   python -c "from fastapi.testclient import TestClient; from backend.app.main import app; c = TestClient(app); r = c.post('/api/predictions', json={'repository': 'sumit7966/ci-cd-minor-project', 'branch': 'member1', 'workflow_name': 'CI Pipeline', 'event': 'push'}); print(r.status_code, r.json()['risk_level'])"
   # Expected output: 200 high
   ```
