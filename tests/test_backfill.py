"""Tests for Phase 2D Step 3 historical job and step telemetry backfill."""

from datetime import datetime, timezone
import io
import json
import urllib.error
from typing import Any, Callable, Dict, Generator, List, Optional
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.data_collection.backfill import backfill_job_telemetry, main as backfill_cli_main
from backend.app.data_collection.github_collector import (
    GitHubAPIError,
    GitHubWorkflowRunCollector,
    MissingTokenError,
    RateLimitExceededError,
    RepositoryNotFoundError,
    RunTelemetryUnavailableError,
)
from backend.app.db.models import PipelineJobModel, PipelineRunModel, PipelineStepModel
from backend.app.db.session import Base, get_db
from backend.app.main import app


class _FakeHTTPResponse:
    """Minimal urllib HTTP response context manager for mocked GitHub REST calls."""

    def __init__(
        self,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        return False


def _http_error(
    url: str,
    code: int,
    reason: str,
    headers: Optional[Dict[str, str]] = None,
) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url=url,
        code=code,
        msg=reason,
        hdrs=headers or {},
        fp=io.BytesIO(b""),
    )


def _make_job_payload(
    *,
    job_id: int,
    run_id: int,
    job_name: str = "build-and-test",
    run_attempt: int = 1,
    conclusion: str = "success",
    steps: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "id": job_id,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "name": job_name,
        "status": "completed",
        "conclusion": conclusion,
        "created_at": "2026-04-01T10:00:00Z",
        "started_at": "2026-04-01T10:00:10Z",
        "completed_at": "2026-04-01T10:01:10Z",
        "runner_name": "ubuntu-latest",
        "steps": steps
        if steps is not None
        else [
            {
                "number": 1,
                "name": "Checkout code",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-04-01T10:00:10Z",
                "completed_at": "2026-04-01T10:00:15Z",
            },
            {
                "number": 2,
                "name": "Run pytest",
                "status": "completed",
                "conclusion": conclusion,
                "started_at": "2026-04-01T10:00:15Z",
                "completed_at": "2026-04-01T10:01:10Z",
            },
        ],
    }


def _seed_run(
    db: Session,
    *,
    run_id: int,
    repository: str = "owner/repo",
    status: str = "completed",
    conclusion: Optional[str] = "success",
    workflow_name: str = "CI Pipeline",
    duration_seconds: Optional[float] = 75.0,
    created_at: Optional[datetime] = None,
    failed_stage: Optional[str] = None,
    failure_type: Optional[str] = None,
) -> PipelineRunModel:
    run = PipelineRunModel(
        run_id=run_id,
        repository=repository,
        branch="main",
        commit_sha=f"sha-{run_id}",
        status=status,
        conclusion=conclusion,
        workflow_name=workflow_name,
        event="push",
        duration_seconds=duration_seconds,
        failed_stage=failed_stage,
        failure_type=failure_type,
        created_at=created_at or datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
        synced_at=datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc),
    )
    db.add(run)
    db.commit()
    return run


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Provide an isolated in-memory SQLite session bound to the FastAPI dependency override."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()

    def override_get_db() -> Generator[Session, None, None]:
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield session
    finally:
        app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_dry_run_without_probing_makes_no_network_or_db_writes(
    db_session: Session,
) -> None:
    """1. Dry-run without probing makes zero GitHub API calls and zero DB writes."""
    _seed_run(db_session, run_id=1001)
    _seed_run(db_session, run_id=1002, status="queued", conclusion=None, duration_seconds=None)

    with patch("urllib.request.urlopen") as mock_urlopen:
        result = backfill_job_telemetry(
            db=db_session,
            dry_run=True,
            probe_github=False,
        )

    mock_urlopen.assert_not_called()
    assert result["dry_run"] is True
    assert result["probe_github"] is False
    assert result["runs_matched"] == 2
    assert result["runs_targeted"] == 2
    assert result["runs_skipped"] == 1
    assert result["runs_fetched"] == 0
    assert result["jobs_inserted"] == 0
    assert result["jobs_updated"] == 0
    assert result["steps_inserted"] == 0
    assert result["steps_updated"] == 0
    assert db_session.query(PipelineJobModel).count() == 0
    assert db_session.query(PipelineStepModel).count() == 0


def test_dry_run_with_probe_github_counts_diff_without_persisting(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2. Dry-run with probing fetches GitHub read-only, estimates inserts/updates, writes zero DB rows."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    _seed_run(db_session, run_id=2001)

    job_payload = _make_job_payload(job_id=91001, run_id=2001)
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _FakeHTTPResponse(
            {"total_count": 1, "jobs": [job_payload]},
            headers={"x-ratelimit-remaining": "4999", "x-ratelimit-reset": "1800000000"},
        )
        result = backfill_job_telemetry(
            db=db_session,
            dry_run=True,
            probe_github=True,
        )

    assert mock_urlopen.call_count == 1
    assert result["dry_run"] is True
    assert result["probe_github"] is True
    assert result["runs_fetched"] == 1
    assert result["jobs_inserted"] == 1
    assert result["jobs_updated"] == 0
    assert result["steps_inserted"] == 2
    assert result["steps_updated"] == 0
    assert result["rate_limit_remaining"] == 4999
    assert db_session.query(PipelineJobModel).count() == 0
    assert db_session.query(PipelineStepModel).count() == 0

    # Also verify dry_run+probe_github predicts updates when child telemetry already exists.
    existing_job = PipelineJobModel(
        run_id=2001,
        github_job_id=91001,
        job_key="gh:91001",
        job_name="build-and-test",
        run_attempt=1,
        status="completed",
        conclusion="success",
    )
    db_session.add(existing_job)
    db_session.flush()
    db_session.add(
        PipelineStepModel(
            job_id=existing_job.id,
            run_id=2001,
            step_number=1,
            step_name="Checkout code",
            status="completed",
            conclusion="success",
        )
    )
    db_session.commit()

    with patch("urllib.request.urlopen") as mock_urlopen_update:
        mock_urlopen_update.return_value = _FakeHTTPResponse({"total_count": 1, "jobs": [job_payload]})
        probe_diff = backfill_job_telemetry(
            db=db_session,
            dry_run=True,
            probe_github=True,
            refresh_jobs=True,
        )

    assert probe_diff["jobs_inserted"] == 0
    assert probe_diff["jobs_updated"] == 1
    assert probe_diff["steps_inserted"] == 1
    assert probe_diff["steps_updated"] == 1
    assert db_session.query(PipelineJobModel).count() == 1
    assert db_session.query(PipelineStepModel).count() == 1


def test_backfill_targets_exact_stored_run_ids_without_listing_runs(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3. Targets exact historical DB run IDs directly via /jobs without /actions/runs listing."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    _seed_run(db_session, run_id=123456789)

    requested_urls: List[str] = []

    def fake_urlopen(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        requested_urls.append(req.full_url)
        return _FakeHTTPResponse(
            {"total_count": 1, "jobs": [_make_job_payload(job_id=501, run_id=123456789)]}
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = backfill_job_telemetry(db=db_session, dry_run=False)

    assert requested_urls == [
        "https://api.github.com/repos/owner/repo/actions/runs/123456789/jobs?per_page=100&page=1"
    ]
    assert not any(url.endswith("/actions/runs") or "/actions/runs?" in url for url in requested_urls)
    assert result["runs_fetched"] == 1
    assert result["jobs_inserted"] == 1
    assert result["steps_inserted"] == 2


def test_filtering_pagination_and_multi_repository_grouping(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4. Supports repository filtering, run_ids filtering, limit/offset pagination, and multi-repo grouping."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    _seed_run(
        db_session,
        run_id=3001,
        repository="org-a/service-one",
        created_at=datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc),
    )
    _seed_run(
        db_session,
        run_id=3002,
        repository="org-a/service-one",
        created_at=datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
    )
    _seed_run(
        db_session,
        run_id=3003,
        repository="org-b/service-two",
        created_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
    )

    requested_urls: List[str] = []

    def fake_urlopen(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        requested_urls.append(req.full_url)
        if "org-a/service-one/actions/runs/3002" in req.full_url:
            return _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=602, run_id=3002)]})
        if "org-a/service-one/actions/runs/3001" in req.full_url:
            return _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=601, run_id=3001)]})
        return _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=603, run_id=3003)]})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        page1 = backfill_job_telemetry(db=db_session, limit=2, offset=0, dry_run=False)
        filtered = backfill_job_telemetry(
            db=db_session,
            repository="org-a/service-one",
            run_ids=[3001],
            dry_run=False,
        )

    assert page1["runs_matched"] == 3
    assert page1["runs_targeted"] == 2
    assert page1["runs_fetched"] == 2
    assert any("org-b/service-two/actions/runs/3003/jobs" in u for u in requested_urls)
    assert any("org-a/service-one/actions/runs/3002/jobs" in u for u in requested_urls)

    assert filtered["runs_matched"] == 1
    assert filtered["runs_targeted"] == 1
    assert filtered["runs_fetched"] == 1
    assert any("org-a/service-one/actions/runs/3001/jobs" in u for u in requested_urls)


def test_parent_pipeline_runs_rows_are_never_modified(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5. Parent pipeline_runs fields remain untouched during historical child telemetry backfill."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    original_created = datetime(2026, 3, 15, 7, 30, tzinfo=timezone.utc)
    original_synced = datetime(2026, 3, 16, 11, 0, tzinfo=timezone.utc)
    run = _seed_run(
        db_session,
        run_id=4001,
        repository="owner/repo",
        status="completed",
        conclusion="failure",
        workflow_name="Original Workflow Name",
        duration_seconds=99.5,
        created_at=original_created,
        failed_stage="OriginalStage",
        failure_type="Test Failure",
    )
    run.synced_at = original_synced
    db_session.commit()

    failing_job = _make_job_payload(job_id=701, run_id=4001, job_name="Renamed CI", conclusion="failure")
    with patch(
        "urllib.request.urlopen",
        return_value=_FakeHTTPResponse({"total_count": 1, "jobs": [failing_job]}),
    ):
        result = backfill_job_telemetry(db=db_session, dry_run=False)

    assert result["jobs_inserted"] == 1
    db_session.expire_all()
    reloaded = db_session.query(PipelineRunModel).filter(PipelineRunModel.run_id == 4001).one()
    assert reloaded.workflow_name == "Original Workflow Name"
    assert reloaded.duration_seconds == 99.5
    assert reloaded.failed_stage == "OriginalStage"
    assert reloaded.failure_type == "Test Failure"
    assert reloaded.synced_at.replace(tzinfo=None) == original_synced.replace(tzinfo=None)
    assert db_session.query(PipelineRunModel).count() == 1


def test_per_run_transaction_isolation_preserves_committed_runs_on_db_or_api_error(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """6. Failure on run 2 rolls back only run 2 while preserving run 1 and continuing to run 3."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    _seed_run(db_session, run_id=5001, created_at=datetime(2026, 4, 1, 10, 3, tzinfo=timezone.utc))
    _seed_run(db_session, run_id=5002, created_at=datetime(2026, 4, 1, 10, 2, tzinfo=timezone.utc))
    _seed_run(db_session, run_id=5003, created_at=datetime(2026, 4, 1, 10, 1, tzinfo=timezone.utc))

    def fake_urlopen(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        if "/runs/5001/" in req.full_url:
            return _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=801, run_id=5001)]})
        if "/runs/5002/" in req.full_url:
            return _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=802, run_id=5002)]})
        return _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=803, run_id=5003)]})

    from backend.app.data_collection import backfill as backfill_mod

    original_sync = backfill_mod.sync_run_job_telemetry

    def flaky_sync(db: Session, run_model: PipelineRunModel, jobs: Any, **kwargs: Any) -> Dict[str, int]:
        if run_model.run_id == 5002:
            raise RuntimeError("Simulated DB write failure on run 5002")
        return original_sync(db, run_model, jobs, **kwargs)

    with (
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        patch("backend.app.data_collection.backfill.sync_run_job_telemetry", side_effect=flaky_sync),
    ):
        result = backfill_job_telemetry(db=db_session, dry_run=False)

    assert result["runs_targeted"] == 3
    assert result["runs_fetched"] == 3
    assert result["runs_failed"] == 1
    assert result["jobs_inserted"] == 2
    assert result["steps_inserted"] == 4
    assert result["stopped_early"] is False
    assert [f["run_id"] for f in result["failed_runs"]] == [5002]
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 5001).count() == 1
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 5002).count() == 0
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 5003).count() == 1


def test_idempotent_rerun_and_refresh_jobs_behavior(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """7. refresh_jobs=False skips completed backfilled runs; refresh_jobs=True updates in place without duplicates."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    _seed_run(db_session, run_id=6001)

    initial_job = _make_job_payload(
        job_id=901,
        run_id=6001,
        steps=[
            {
                "number": 1,
                "name": "Checkout",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-04-01T10:00:10Z",
                "completed_at": "2026-04-01T10:00:15Z",
            },
            {
                "number": 2,
                "name": "Obsolete step",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-04-01T10:00:15Z",
                "completed_at": "2026-04-01T10:00:25Z",
            },
        ],
    )
    updated_job = _make_job_payload(
        job_id=901,
        run_id=6001,
        steps=[
            {
                "number": 1,
                "name": "Checkout Updated",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-04-01T10:00:10Z",
                "completed_at": "2026-04-01T10:00:20Z",
            },
        ],
    )

    with patch(
        "urllib.request.urlopen",
        return_value=_FakeHTTPResponse({"total_count": 1, "jobs": [initial_job]}),
    ):
        first = backfill_job_telemetry(db=db_session, dry_run=False, refresh_jobs=False)

    assert first["jobs_inserted"] == 1
    assert first["steps_inserted"] == 2

    with patch("urllib.request.urlopen") as mock_urlopen:
        skipped = backfill_job_telemetry(db=db_session, dry_run=False, refresh_jobs=False)

    mock_urlopen.assert_not_called()
    assert skipped["runs_skipped"] == 1
    assert skipped["runs_fetched"] == 0

    with patch(
        "urllib.request.urlopen",
        return_value=_FakeHTTPResponse({"total_count": 1, "jobs": [updated_job]}),
    ):
        refreshed = backfill_job_telemetry(db=db_session, dry_run=False, refresh_jobs=True)

    assert refreshed["runs_skipped"] == 0
    assert refreshed["runs_fetched"] == 1
    assert refreshed["jobs_inserted"] == 0
    assert refreshed["jobs_updated"] == 1
    assert refreshed["steps_inserted"] == 0
    assert refreshed["steps_updated"] == 1
    assert db_session.query(PipelineJobModel).count() == 1
    steps = db_session.query(PipelineStepModel).all()
    assert len(steps) == 1
    assert steps[0].step_name == "Checkout Updated"


def test_http_404_and_410_handling_preserves_existing_telemetry_and_continues(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """8. HTTP 404 (missing/permission) and 410 (gone) map accurately and do not abort or delete telemetry."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    _seed_run(db_session, run_id=7001, created_at=datetime(2026, 4, 1, 10, 3, tzinfo=timezone.utc))
    _seed_run(db_session, run_id=7002, created_at=datetime(2026, 4, 1, 10, 2, tzinfo=timezone.utc))
    _seed_run(db_session, run_id=7003, created_at=datetime(2026, 4, 1, 10, 1, tzinfo=timezone.utc))

    existing_job = PipelineJobModel(
        run_id=7001,
        github_job_id=111,
        job_key="gh:111",
        job_name="existing-job",
        run_attempt=1,
        status="completed",
        conclusion="success",
    )
    db_session.add(existing_job)
    db_session.commit()

    def fake_urlopen(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        if "/runs/7001/" in req.full_url:
            raise _http_error(req.full_url, 404, "Not Found")
        if "/runs/7002/" in req.full_url:
            raise _http_error(req.full_url, 410, "Gone")
        return _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=703, run_id=7003)]})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = backfill_job_telemetry(db=db_session, dry_run=False, refresh_jobs=True)

    assert result["runs_failed"] == 2
    assert result["runs_fetched"] == 1
    assert result["stopped_early"] is False
    errors_by_run = {entry["run_id"]: entry for entry in result["failed_runs"]}
    assert errors_by_run[7001]["error_type"] == RunTelemetryUnavailableError.__name__
    assert "permission" in errors_by_run[7001]["detail"].lower()
    assert "deleted" in errors_by_run[7001]["detail"].lower()
    assert errors_by_run[7002]["error_type"] == RunTelemetryUnavailableError.__name__
    assert "410" in errors_by_run[7002]["detail"]
    # Existing telemetry for 7001 preserved, 7003 persisted cleanly
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 7001).count() == 1
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 7003).count() == 1


def test_missing_token_fails_safely_and_credentials_never_leak(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """9. Missing GITHUB_TOKEN blocks write mode cleanly with 401; secret strings never appear in responses."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    _seed_run(db_session, run_id=8001)

    with pytest.raises(MissingTokenError):
        backfill_job_telemetry(db=db_session, dry_run=False)

    client = TestClient(app)
    resp = client.post("/api/pipelines/backfill-telemetry?dry_run=false")
    assert resp.status_code == 401
    assert db_session.query(PipelineJobModel).count() == 0

    secret = "ghp_SUPER_SECRET_VALUE_999"
    monkeypatch.setenv("GITHUB_TOKEN", secret)

    def auth_error(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        raise _http_error(req.full_url, 401, f"Bad credentials Bearer {secret}")

    with patch("urllib.request.urlopen", side_effect=auth_error):
        api_resp = client.post("/api/pipelines/backfill-telemetry?dry_run=false")

    assert api_resp.status_code == 200
    payload_text = json.dumps(api_resp.json())
    assert secret not in payload_text
    assert api_resp.json()["stopped_early"] is True
    assert "401" in api_resp.json()["stop_reason"]

    # Also prove _sanitize_error_detail redacts tokens if an upstream error message echoes them.
    def leaky_transport_error(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        raise urllib.error.URLError(f"Upstream proxy echoed Bearer {secret}")

    with patch("urllib.request.urlopen", side_effect=leaky_transport_error):
        leak_check_resp = client.post("/api/pipelines/backfill-telemetry?dry_run=false")

    assert leak_check_resp.status_code == 200
    leak_body = leak_check_resp.json()
    assert secret not in json.dumps(leak_body)
    assert "[REDACTED]" in leak_body["failed_runs"][0]["detail"]


def test_rate_limit_stops_cleanly_preserving_earlier_committed_runs(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """10. HTTP 403/429 rate limit stops processing subsequent runs and keeps earlier committed runs."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    _seed_run(db_session, run_id=9001, created_at=datetime(2026, 4, 1, 10, 3, tzinfo=timezone.utc))
    _seed_run(db_session, run_id=9002, created_at=datetime(2026, 4, 1, 10, 2, tzinfo=timezone.utc))
    _seed_run(db_session, run_id=9003, created_at=datetime(2026, 4, 1, 10, 1, tzinfo=timezone.utc))

    calls: List[str] = []

    def fake_urlopen(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        calls.append(req.full_url)
        if "/runs/9001/" in req.full_url:
            return _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=911, run_id=9001)]})
        raise _http_error(
            req.full_url,
            403,
            "API rate limit exceeded",
            headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1800000123"},
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = backfill_job_telemetry(db=db_session, dry_run=False)

    assert len(calls) == 2
    assert result["stopped_early"] is True
    assert "rate limit" in (result["stop_reason"] or "").lower()
    assert result["runs_fetched"] == 1
    assert result["runs_failed"] == 1
    assert result["jobs_inserted"] == 1
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 9001).count() == 1
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 9003).count() == 0


def test_proactive_zero_rate_limit_header_stops_before_next_run(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When X-RateLimit-Remaining reaches 0 on 200 OK, stop cleanly before failing the next run."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    _seed_run(db_session, run_id=9501, created_at=datetime(2026, 4, 1, 10, 2, tzinfo=timezone.utc))
    _seed_run(db_session, run_id=9502, created_at=datetime(2026, 4, 1, 10, 1, tzinfo=timezone.utc))

    calls: List[str] = []

    def fake_urlopen(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        calls.append(req.full_url)
        return _FakeHTTPResponse(
            {"total_count": 1, "jobs": [_make_job_payload(job_id=951, run_id=9501)]},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1800000999"},
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = backfill_job_telemetry(db=db_session, dry_run=False)

    assert len(calls) == 1
    assert result["stopped_early"] is True
    assert result["runs_fetched"] == 1
    assert result["runs_failed"] == 0
    assert result["rate_limit_remaining"] == 0
    assert result["rate_limit_reset"] == 1800000999
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 9501).count() == 1
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 9502).count() == 0


def test_incomplete_pagination_or_malformed_payload_preserves_existing_children(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """11. Incomplete pagination or malformed payloads preserve existing stored child rows."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    _seed_run(db_session, run_id=10001)
    existing_job = PipelineJobModel(
        run_id=10001,
        github_job_id=222,
        job_key="gh:222",
        job_name="preserved-job",
        run_attempt=1,
        status="completed",
        conclusion="success",
    )
    db_session.add(existing_job)
    db_session.flush()
    db_session.add(
        PipelineStepModel(
            job_id=existing_job.id,
            run_id=10001,
            step_number=1,
            step_name="Preserved step",
            status="completed",
            conclusion="success",
        )
    )
    db_session.commit()

    # Case A: total_count says 3 jobs, API returned only 1 without next page
    with patch(
        "urllib.request.urlopen",
        return_value=_FakeHTTPResponse({"total_count": 3, "jobs": [_make_job_payload(job_id=222, run_id=10001)]}),
    ):
        result = backfill_job_telemetry(db=db_session, dry_run=False, refresh_jobs=True)

    assert result["runs_failed"] == 1
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 10001).count() == 1
    assert db_session.query(PipelineStepModel).filter(PipelineStepModel.run_id == 10001).count() == 1


def test_complete_empty_job_response_preserves_existing_unless_reconcile_empty_enabled(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """12. Complete empty responses do not delete telemetry unless empty reconciliation is explicitly enabled."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    _seed_run(db_session, run_id=11001)
    db_session.add(
        PipelineJobModel(
            run_id=11001,
            github_job_id=333,
            job_key="gh:333",
            job_name="existing-job",
            run_attempt=1,
            status="completed",
            conclusion="success",
        )
    )
    db_session.commit()

    empty_complete = _FakeHTTPResponse({"total_count": 0, "jobs": []})
    with patch("urllib.request.urlopen", return_value=empty_complete):
        safe_result = backfill_job_telemetry(
            db=db_session,
            dry_run=False,
            refresh_jobs=True,
            reconcile_empty=False,
        )

    assert safe_result["runs_fetched"] == 1
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 11001).count() == 1

    with patch(
        "urllib.request.urlopen",
        return_value=_FakeHTTPResponse({"total_count": 0, "jobs": []}),
    ):
        reconcile_result = backfill_job_telemetry(
            db=db_session,
            dry_run=False,
            refresh_jobs=True,
            reconcile_empty=True,
        )

    assert reconcile_result["runs_fetched"] == 1
    assert db_session.query(PipelineJobModel).filter(PipelineJobModel.run_id == 11001).count() == 0


def test_backfill_api_endpoint_default_dry_run_and_cli_smoke(
    db_session: Session,
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify POST /api/pipelines/backfill-telemetry defaults to dry_run=true and CLI runs safely without token flags."""
    _seed_run(db_session, run_id=12001)
    client = TestClient(app)

    with patch("urllib.request.urlopen") as mock_urlopen:
        resp = client.post("/api/pipelines/backfill-telemetry")

    mock_urlopen.assert_not_called()
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert body["runs_matched"] == 1
    assert body["runs_targeted"] == 1

    invalid_bounds = client.post("/api/pipelines/backfill-telemetry?limit=999")
    assert invalid_bounds.status_code == 422

    # CLI smoke test against temporary SQLite DB file (dry-run by default)
    cli_engine = create_engine(f"sqlite:///{tmp_path / 'cli_backfill.db'}")
    try:
        with (
            patch("backend.app.data_collection.backfill.get_engine", return_value=cli_engine),
            patch("urllib.request.urlopen") as cli_urlopen,
        ):
            exit_code = backfill_cli_main(
                ["--repository", "owner/repo", "--limit", "10", "--offset", "0"]
            )
        cli_urlopen.assert_not_called()
        assert exit_code == 0
    finally:
        cli_engine.dispose()


def test_http_502_retries_with_backoff_then_succeeds(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 502 triggers exponential backoff (~1s, 2s) and succeeds cleanly without duplicates."""
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    orig_synced = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
    run = _seed_run(
        db_session,
        run_id=13001,
        workflow_name="Keep Name",
        failed_stage="KeepStage",
        failure_type="KeepType",
    )
    run.synced_at = orig_synced
    db_session.commit()

    attempts = 0
    sleeps: List[float] = []

    def flaky_502(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise _http_error(
                req.full_url,
                502,
                "Bad Gateway",
                headers={"X-GitHub-Request-Id": f"REQ-502-{attempts}"},
            )
        return _FakeHTTPResponse(
            {"total_count": 1, "jobs": [_make_job_payload(job_id=1301, run_id=13001)]},
            headers={"X-GitHub-Request-Id": "REQ-200-OK"},
        )

    with (
        patch("urllib.request.urlopen", side_effect=flaky_502),
        patch("time.sleep", side_effect=sleeps.append),
        patch("random.uniform", return_value=0.1),
    ):
        result = backfill_job_telemetry(db=db_session, dry_run=False)

    assert attempts == 3
    assert sleeps == pytest.approx([1.1, 2.1])
    assert result["runs_fetched"] == 1
    assert result["runs_failed"] == 0
    assert result["jobs_inserted"] == 1
    assert result["steps_inserted"] == 2
    assert db_session.query(PipelineJobModel).count() == 1
    assert db_session.query(PipelineStepModel).count() == 2

    db_session.expire_all()
    reloaded = db_session.query(PipelineRunModel).filter(PipelineRunModel.run_id == 13001).one()
    assert reloaded.workflow_name == "Keep Name"
    assert reloaded.failed_stage == "KeepStage"
    assert reloaded.failure_type == "KeepType"
    assert reloaded.synced_at.replace(tzinfo=None) == orig_synced.replace(tzinfo=None)


def test_http_503_504_and_500_retries_across_all_three_backoff_tiers() -> None:
    """HTTP 503, 504, and 500 retry up to 3 times with ~1s, ~2s, ~4s backoff before succeeding."""
    collector = GitHubWorkflowRunCollector(repository="owner/repo", github_token="tok-123")
    responses = [
        _http_error("https://api.github.com/x", 503, "Service Unavailable"),
        _http_error("https://api.github.com/x", 504, "Gateway Timeout"),
        _http_error("https://api.github.com/x", 500, "Internal Server Error"),
        _FakeHTTPResponse(
            {"total_count": 1, "jobs": [_make_job_payload(job_id=503, run_id=1)]},
            headers={"X-GitHub-Request-Id": "REQ-FINAL-OK"},
        ),
    ]
    sleeps: List[float] = []

    with (
        patch("urllib.request.urlopen", side_effect=responses) as mock_open,
        patch("time.sleep", side_effect=sleeps.append),
        patch("random.uniform", return_value=0.2),
    ):
        jobs = collector.fetch_jobs_for_run(1)

    assert mock_open.call_count == 4
    assert sleeps == pytest.approx([1.2, 2.2, 4.2])
    assert len(jobs) == 1
    assert collector.last_request_id == "REQ-FINAL-OK"


def test_transient_retries_exhausted_captures_request_id_and_sanitizes_secrets(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After 3 retries (4 attempts) on 502, captures X-GitHub-Request-Id safely and redacts tokens."""
    secret = "ghp_RETRY_SECRET_ABC123"
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    _seed_run(db_session, run_id=14001)

    sleeps: List[float] = []

    def always_502(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        raise _http_error(
            req.full_url,
            502,
            f"Bad Gateway Bearer {secret} ?access_token={secret}",
            headers={"X-GitHub-Request-Id": f"REQ-502-FAIL-{secret}"},
        )

    with (
        patch("urllib.request.urlopen", side_effect=always_502) as mock_open,
        patch("time.sleep", side_effect=sleeps.append),
        patch("random.uniform", return_value=0.0),
    ):
        result = backfill_job_telemetry(db=db_session, dry_run=False)

    assert mock_open.call_count == 4
    assert sleeps == pytest.approx([1.0, 2.0, 4.0])
    assert result["runs_fetched"] == 0
    assert result["runs_failed"] == 1
    assert len(result["failed_runs"]) == 1

    failed_entry = result["failed_runs"][0]
    assert failed_entry["run_id"] == 14001
    assert failed_entry["error_type"] == "GitHubAPIError"
    assert "4 attempts" in failed_entry["detail"]
    assert "[REDACTED]" in failed_entry["request_id"]
    serialized = json.dumps(result)
    assert secret not in serialized


@pytest.mark.parametrize(
    ("status_code", "headers", "expected_exc"),
    [
        (401, {}, MissingTokenError),
        (403, {}, GitHubAPIError),
        (404, {}, RunTelemetryUnavailableError),
        (410, {}, RunTelemetryUnavailableError),
        (429, {"Retry-After": "60"}, RateLimitExceededError),
    ],
)
def test_non_retryable_status_codes_fail_fast_without_retries(
    status_code: int,
    headers: Dict[str, str],
    expected_exc: type,
) -> None:
    """HTTP 401, 403, 404, 410, and 429 never retry (1 call total, 0 retry sleeps)."""
    collector = GitHubWorkflowRunCollector(repository="owner/repo", github_token="tok-abc")
    sleeps: List[float] = []

    def fail_once(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        raise _http_error(
            req.full_url,
            status_code,
            f"HTTP {status_code}",
            headers={"X-GitHub-Request-Id": f"REQ-{status_code}", **headers},
        )

    with (
        patch("urllib.request.urlopen", side_effect=fail_once) as mock_open,
        patch("time.sleep", side_effect=sleeps.append),
    ):
        with pytest.raises(expected_exc):
            collector.fetch_jobs_for_run(999, raise_run_unavailable=True)

    assert mock_open.call_count == 1
    assert sleeps == []


def test_transient_timeout_retries_and_non_transient_url_error_fails_fast() -> None:
    """Transient TimeoutError and socket-timeout URLError retry; non-transient connection refused fails fast."""
    collector = GitHubWorkflowRunCollector(repository="owner/repo", github_token="tok-abc")
    sleeps: List[float] = []
    outcomes = [
        TimeoutError("timed out"),
        urllib.error.URLError(TimeoutError("The read operation timed out")),
        _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=777, run_id=1)]}),
    ]

    with (
        patch("urllib.request.urlopen", side_effect=outcomes) as mock_open,
        patch("time.sleep", side_effect=sleeps.append),
        patch("random.uniform", return_value=0.05),
    ):
        jobs = collector.fetch_jobs_for_run(1)

    assert mock_open.call_count == 3
    assert sleeps == pytest.approx([1.05, 2.05])
    assert len(jobs) == 1

    # Non-transient URLError must fail immediately without sleeping/retrying.
    refused_sleeps: List[float] = []
    with (
        patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ) as mock_refused,
        patch("time.sleep", side_effect=refused_sleeps.append),
    ):
        with pytest.raises(GitHubAPIError):
            collector.fetch_jobs_for_run(2)

    assert mock_refused.call_count == 1
    assert refused_sleeps == []


def test_backfill_request_pacing_and_cli_repeatable_run_ids_skip_existing(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI --apply --run-id targets only failed IDs, skips stored completed runs, spaces calls (~0.2s), avoids duplicates."""
    sqlite_path = tmp_path / "targeted_retry.db"
    sqlite_url = f"sqlite:///{sqlite_path}"
    monkeypatch.setenv("DATABASE_URL", sqlite_url)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    engine = create_engine(sqlite_url)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with SessionLocal() as setup_db:
        _seed_run(setup_db, run_id=38126273805, created_at=datetime(2026, 10, 11, 8, 51, 13, tzinfo=timezone.utc))
        _seed_run(setup_db, run_id=38126299919, created_at=datetime(2026, 10, 11, 8, 51, 39, tzinfo=timezone.utc))
        _seed_run(setup_db, run_id=38126294564, created_at=datetime(2026, 10, 11, 8, 51, 34, tzinfo=timezone.utc))
        _seed_run(setup_db, run_id=99999999999, created_at=datetime(2026, 10, 11, 8, 50, 0, tzinfo=timezone.utc))

        existing_job = PipelineJobModel(
            run_id=38126273805,
            github_job_id=114431187596,
            job_key="github:114431187596",
            job_name="build-and-test",
            run_attempt=1,
            status="completed",
            conclusion="failure",
        )
        setup_db.add(existing_job)
        setup_db.flush()
        setup_db.add(
            PipelineStepModel(
                job_id=existing_job.id,
                run_id=38126273805,
                step_number=1,
                step_name="Checkout",
                status="completed",
                conclusion="success",
            )
        )
        setup_db.commit()

    requested_urls: List[str] = []
    sleeps: List[float] = []

    def fake_urlopen(req: Any, timeout: int = 15) -> _FakeHTTPResponse:
        requested_urls.append(req.full_url)
        if "/38126299919/" in req.full_url:
            return _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=101, run_id=38126299919)]})
        return _FakeHTTPResponse({"total_count": 1, "jobs": [_make_job_payload(job_id=102, run_id=38126294564)]})

    with (
        patch("backend.app.data_collection.backfill.get_engine", return_value=engine),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
        patch("time.sleep", side_effect=sleeps.append),
    ):
        exit_code = backfill_cli_main(
            [
                "--apply",
                "--run-id",
                "38126273805",
                "--run-id",
                "38126299919",
                "--run-id",
                "38126294564",
                "--limit",
                "10",
                "--offset",
                "0",
            ]
        )

    assert exit_code == 0
    assert len(requested_urls) == 2
    assert any("/runs/38126299919/jobs" in u for u in requested_urls)
    assert any("/runs/38126294564/jobs" in u for u in requested_urls)
    assert not any("/38126273805/" in u or "/99999999999/" in u for u in requested_urls)
    assert sleeps == pytest.approx([0.2])

    with SessionLocal() as verify_db:
        assert verify_db.query(PipelineRunModel).count() == 4
        assert verify_db.query(PipelineJobModel).count() == 3
        assert verify_db.query(PipelineStepModel).count() == 5
    engine.dispose()
