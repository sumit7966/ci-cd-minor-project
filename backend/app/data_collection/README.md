# CI/CD Data Collection Module

## 1. Overview & Purpose

The **Data Collection Module** extracts, parses, categorizes, and validates telemetry from GitHub Actions CI/CD workflow runs. It interfaces with the GitHub REST API and normalizes raw execution data into structured `PipelineRunRecord` Pydantic models.

This historical and real-time execution dataset serves as the core foundation for:
- **Bottleneck Identification**: Tracking job and step duration trends over time.
- **Failure Prediction (ML)**: Supplying labeled pipeline execution data (`success`, `test_failure`, `build_failure`, `dependency_failure`) to train machine learning models.
- **Optimization Recommendations**: Pinpointing fragile steps and high-failure stages.

---

## 2. Required Environment Variables

To protect credentials and comply with project security rules (never hardcoding secrets), configuration is loaded from environment variables:

| Variable | Required | Description | Example |
| :--- | :--- | :--- | :--- |
| `GITHUB_REPOSITORY` | Recommended | Target repository slug in `owner/repo` format. | `sumit7966/ci-cd-minor-project` |
| `GITHUB_TOKEN` | Optional* | GitHub Personal Access Token (PAT) with `repo` or `actions:read` permissions. | `ghp_xxxxxxxxxxxxxxxxxxxx` |

*\*Note: `GITHUB_TOKEN` is optional for public repositories (subject to GitHub unauthenticated rate limits of 60 requests/hour), but recommended for higher limits (5,000 requests/hour). Local testing can also use the built-in mock path (`use_mock=True`) with no token required.*

---

## 3. Data Flow: GitHub Actions to PipelineRunRecord

```
  ┌────────────────────────────────────────────────────────┐
  │                   GitHub REST API                      │
  │  GET /repos/{owner}/{repo}/actions/runs                │
  │  GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs   │
  └──────────────────────────┬─────────────────────────────┘
                             │
                             ▼
  ┌────────────────────────────────────────────────────────┐
  │           GitHubWorkflowRunCollector                   │
  │  1. Ingests raw JSON payload                           │
  │  2. Calculates duration_seconds (updated_at - created) │
  │  3. Inspects step conclusions for failed stages        │
  │  4. Maps failure: test, build, dependency, unknown     │
  └──────────────────────────┬─────────────────────────────┘
                             │
                             ▼
  ┌────────────────────────────────────────────────────────┐
  │               PipelineRunRecord (Schema)               │
  │  Validates types, non-empty strings, timestamps        │
  └────────────────────────────────────────────────────────┘
```

### Failure Stage Mapping
When a workflow concludes with `failure`, the collector queries job step metadata:
- If a failed step name contains `dependenc`: `failed_stage = "dependency"`, `failure_type = "dependency_failure"`.
- If a failed step name contains `build`: `failed_stage = "build"`, `failure_type = "build_failure"`.
- If a failed step name contains `test`: `failed_stage = "test"`, `failure_type = "test_failure"`.
- If triggered via `workflow_dispatch` with an intentional `failure_type` input, that category is preserved.
- If the exact stage cannot be determined: `failed_stage = "unknown"`, `failure_type = "unknown"`.

---

## 4. Fields Collected

Each workflow run is structured into a validated `PipelineRunRecord` model:

| Field | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| `run_id` | `int` | Unique GitHub Actions workflow run identifier | `1450123456` |
| `repository` | `str` | Repository path in `owner/repo` format | `sumit7966/ci-cd-minor-project` |
| `branch` | `str` | Git branch triggering the pipeline | `member1` |
| `commit_sha` | `str` | Full Git commit hash triggering the workflow | `1450bb3859663efd1bf379b3ee909ebdf28a7e0d` |
| `status` | `str` | Current workflow state (`queued`, `in_progress`, `completed`) | `completed` |
| `conclusion` | `str` (optional) | Final outcome (`success`, `failure`, `cancelled`, `skipped`) | `failure` |
| `workflow_name` | `str` | Workflow title defined in GitHub Actions | `CI Pipeline` |
| `event` | `str` | Event trigger type (`push`, `pull_request`, `workflow_dispatch`) | `workflow_dispatch` |
| `duration_seconds` | `float` (optional) | Total execution duration in seconds | `12.3` |
| `failed_stage` | `str` (optional) | Specific pipeline stage that failed (`dependency`, `build`, `test`) | `test` |
| `failure_type` | `str` (optional) | Controlled failure category (`none`, `dependency_failure`, etc.) | `test_failure` |
| `created_at` | `datetime` | UTC timestamp when workflow execution started | `2026-09-09T10:45:00Z` |

---

## 5. Usage Examples

### Local / Offline Testing (Mock Path - No Token Required)

```python
from backend.app.data_collection import collect_workflow_runs

# Collect mock workflow runs covering success and all failure types
records = collect_workflow_runs(use_mock=True)

for record in records:
    print(f"Run #{record.run_id}: status={record.status}, conclusion={record.conclusion}, failure_type={record.failure_type}")
```

### Live Collection via GitHub REST API

```python
import os
from backend.app.data_collection import GitHubWorkflowRunCollector

# Initialize using environment variables or parameters
collector = GitHubWorkflowRunCollector(
    repository=os.getenv("GITHUB_REPOSITORY", "sumit7966/ci-cd-minor-project"),
    github_token=os.getenv("GITHUB_TOKEN"),
)

# Fetch recent workflow runs with automatic job step inspection
runs = collector.fetch_workflow_runs(limit=10)
for run in runs:
    print(run.model_dump())
```

---

## 6. Error Handling

The collector implements specific exception classes for robust API error recovery:
- `MissingTokenError`: Token was invalid or lacked authorization (401).
- `RepositoryNotFoundError`: Target repository was not found or is private without token access (404).
- `RateLimitExceededError`: GitHub API hourly rate limit was exhausted (403/429), reporting the reset timestamp.
- `GitHubAPIError`: Network failures, server errors (5xx), or malformed responses.
