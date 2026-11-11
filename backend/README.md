# Backend: Telemetry Collection, PostgreSQL Storage & FastAPI API

This module implements the telemetry ingestion and API layer for the CI/CD Pipeline Failure Prediction & Optimization Platform. It collects execution runs from GitHub Actions, stores them in PostgreSQL (avoiding duplicate records), and exposes a RESTful API for downstream analysis and frontend consumption.

---

## Features

- **GitHub Actions Collector**: Retrieves workflow run metrics, status, duration, and pinpointed failure types/stages.
- **PostgreSQL Database Storage**: Persists pipeline runs to the `pipeline_runs` table using SQLAlchemy ORM.
- **Deduplication Logic**: Safe upserts based on `run_id` to update status transitions (e.g., `in_progress` -> `completed`) without primary key collisions.
- **FastAPI REST API**: High-performance asynchronous API endpoints with automatic OpenAPI documentation.
- **Docker Compose Integration**: Containerized PostgreSQL database with volume persistence and healthcheck.
- **Zero Hard-Coded Secrets**: Fully configured via environment variables.

---

## Database Schema (`pipeline_runs`)

| Column Name | Type | Constraints | Description |
|---|---|---|---|
| `run_id` | BIGINT | PRIMARY KEY, INDEX | Unique GitHub Actions workflow run identifier |
| `repository` | VARCHAR(255) | NOT NULL, INDEX | Repository slug in `owner/repo` format |
| `branch` | VARCHAR(255) | NOT NULL, INDEX | Triggering branch name |
| `commit_sha` | VARCHAR(64) | NOT NULL | Git commit SHA |
| `status` | VARCHAR(64) | NOT NULL, INDEX | Run state (`completed`, `in_progress`, etc.) |
| `conclusion` | VARCHAR(64) | NULL, INDEX | Final outcome (`success`, `failure`, `cancelled`, etc.) |
| `workflow_name` | VARCHAR(255) | NOT NULL, INDEX | CI workflow name |
| `event` | VARCHAR(64) | NOT NULL | Event trigger (`push`, `workflow_dispatch`, etc.) |
| `duration_seconds` | FLOAT | NULL | Execution duration in seconds |
| `failed_stage` | VARCHAR(64) | NULL, INDEX | Failed stage (`dependency`, `build`, `test`, `unknown`) |
| `failure_type` | VARCHAR(64) | NULL, INDEX | Categorized failure type (`dependency_failure`, etc.) |
| `created_at` | TIMESTAMPTZ | NOT NULL, INDEX | Run creation timestamp in UTC |
| `synced_at` | TIMESTAMPTZ | NOT NULL | Timestamp when telemetry was saved/updated |

---

## Environment Variables

Copy `.env.example` to `.env` and configure:

| Variable | Default / Format | Description |
|---|---|---|
| `POSTGRES_USER` | `postgres` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `postgres` | PostgreSQL password |
| `POSTGRES_DB` | `cicd_platform` | PostgreSQL database name |
| `POSTGRES_HOST` | `localhost` | Database host (or `db` within Docker) |
| `POSTGRES_PORT` | `5432` | Database port |
| `DATABASE_URL` | *(Optional)* | Full SQLAlchemy database connection URL |
| `GITHUB_REPOSITORY` | `owner/repository_name` | Target GitHub repository in `owner/repo` format |
| `GITHUB_TOKEN` | *(Optional for public repos)* | GitHub Personal Access Token (PAT) with `repo` / `actions:read` scope |

---

## API Endpoints

Interactive Swagger UI documentation is available at `http://localhost:8000/docs`.

### 1. Collect / Sync Pipeline Runs
- **Endpoint**: `POST /api/pipelines/sync`
- **Query Parameters**:
  - `limit` (int, default: 10, range: 1-100): Number of runs to fetch.
  - `use_mock` (bool, default: false): Ingest mock dataset without network requests.
  - `repository` (string, optional): Target repository override.
- **Response**: Summary of fetched, inserted, and updated runs.

### 2. List Pipeline Runs
- **Endpoint**: `GET /api/pipelines`
- **Query Parameters**:
  - `limit` (int, default: 50, range: 1-100)
  - `offset` (int, default: 0)
  - `repository`, `branch`, `status`, `conclusion`, `workflow_name`, `failure_type`
- **Response**: Array of `PipelineRunRecord` objects ordered by creation date descending.

### 3. Get Single Pipeline Run
- **Endpoint**: `GET /api/pipelines/{run_id}`
- **Response**: `PipelineRunRecord` object or `404 Not Found`.

### 4. Pipeline Execution Statistics
- **Endpoint**: `GET /api/pipelines/stats`
- **Query Parameters**:
  - `repository` (string, optional)
- **Response**: Aggregated statistics including total runs, success/failure counts, rates, average duration, and distributions.

### 5. Pipeline Analytics (Dashboard & Optimization Engine Ready)
- **Overview Endpoint**: `GET /api/pipelines/analytics/overview` (or `GET /api/pipelines/analytics`)
  - Full composite bundle containing summary, trends, failures, and duration breakdowns.
- **Summary Metrics**: `GET /api/pipelines/analytics/summary`
  - Returns execution totals, success/failure percentages, and duration benchmarks.
- **Execution Trends**: `GET /api/pipelines/analytics/trends?days=30`
  - Chronological daily breakdown (`total_runs`, `success_count`, `failure_count`, `success_rate_percent`, `average_duration_seconds`) formatted for time-series charts.
- **Failure Breakdown**: `GET /api/pipelines/analytics/failures`
  - Categorical rankings and percentage distributions by `failure_type` and `failed_stage`.
- **Duration Benchmarks**: `GET /api/pipelines/analytics/duration`
  - Average, min, max, and median durations overall, grouped by `workflow_name`, and grouped by `branch`.

---

## Running Locally

### Step 1: Start PostgreSQL via Docker Compose
```bash
docker compose up -d db
```

Verify that the database container is running and healthy:
```bash
docker compose ps
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Run the FastAPI Server
```bash
python -m uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

### Step 4: Run Tests
```bash
python -m pytest -v
```

### Step 5: Real GitHub Actions Synchronization Flow

To sync telemetry from your real GitHub Actions repository:

1. **Set Environment Variables Securely (Session-Only)**:
   - **PowerShell**:
     ```powershell
     $env:GITHUB_REPOSITORY = "owner/repository_name"
     $env:GITHUB_TOKEN = "your_personal_access_token_here"
     ```
   - **Bash / Linux / macOS**:
     ```bash
     export GITHUB_REPOSITORY="owner/repository_name"
     export GITHUB_TOKEN="your_personal_access_token_here"
     ```
   *(Alternatively, configure them in your local `.env` file, which is ignored by Git).*

2. **Trigger Real Synchronization**:
   ```bash
   curl -X POST "http://127.0.0.1:8000/api/pipelines/sync?use_mock=false&limit=10"
   ```
   Or explicitly pass the repository override:
   ```bash
   curl -X POST "http://127.0.0.1:8000/api/pipelines/sync?use_mock=false&repository=owner/repository_name&limit=10"
   ```

3. **Verify Synced Pipeline Telemetry & Statistics**:
   ```bash
   # Check list of synced runs
   curl "http://127.0.0.1:8000/api/pipelines?limit=10"

   # Check execution statistics
   curl "http://127.0.0.1:8000/api/pipelines/stats"

   # Check analytics summary
   curl "http://127.0.0.1:8000/api/pipelines/analytics/summary"
   ```
