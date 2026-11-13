"""Comprehensive tests for Phase 2D Step 2: job and step collection, parsing, persistence, and sync API."""

from datetime import datetime, timezone
import io
import json
import os
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.data_collection import (
    GitHubWorkflowRunCollector,
    PipelineJobRecord,
    PipelineRunRecord,
    PipelineStepRecord,
    RateLimitExceededError,
)
from backend.app.data_collection.storage import sync_github_runs_to_db
from backend.app.db import (
    Base,
    PipelineRunModel,
    get_completed_run_ids_with_jobs,
    get_jobs_for_run,
    get_steps_for_run,
    upsert_pipeline_runs,
)
from backend.app.db.session import get_db
from backend.app.main import app


def _make_response(payload: dict, link_header: str = None) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    headers = {}
    if link_header is not None:
        headers["Link"] = link_header
    resp.headers = headers
    resp.__enter__.return_value = resp
    return resp


class TestJobAndStepCollector(unittest.TestCase):
    """Tests covering GitHub job/step pagination, timing, and failure classification."""

    def setUp(self) -> None:
        self.repo = "owner/repo"
        self.collector = GitHubWorkflowRunCollector(
            repository=self.repo,
            github_token="secret-test-token-do-not-leak",
        )

    def test_fetch_jobs_for_non_queued_conclusions_and_skip_only_unstarted_queued(self):
        runs_payload = {
            "workflow_runs": [
                {
                    "id": 101,
                    "name": "CI",
                    "head_branch": "main",
                    "head_sha": "aaaa1111",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "success",
                    "created_at": "2026-09-10T10:00:00Z",
                    "updated_at": "2026-09-10T10:01:00Z",
                },
                {
                    "id": 102,
                    "name": "CI",
                    "head_branch": "main",
                    "head_sha": "bbbb2222",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "failure",
                    "created_at": "2026-09-10T10:02:00Z",
                    "updated_at": "2026-09-10T10:03:00Z",
                },
                {
                    "id": 103,
                    "name": "CI",
                    "head_branch": "main",
                    "head_sha": "cccc3333",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "cancelled",
                    "created_at": "2026-09-10T10:04:00Z",
                    "updated_at": "2026-09-10T10:04:30Z",
                },
                {
                    "id": 104,
                    "name": "CI",
                    "head_branch": "main",
                    "head_sha": "dddd4444",
                    "event": "push",
                    "status": "completed",
                    "conclusion": "timed_out",
                    "created_at": "2026-09-10T10:05:00Z",
                    "updated_at": "2026-09-10T10:15:00Z",
                },
                {
                    "id": 105,
                    "name": "CI",
                    "head_branch": "main",
                    "head_sha": "eeee5555",
                    "event": "push",
                    "status": "queued",
                    "conclusion": None,
                    "run_started_at": None,
                    "created_at": "2026-09-10T10:20:00Z",
                    "updated_at": "2026-09-10T10:20:00Z",
                },
            ]
        }

        requested_job_run_ids = []

        def fake_urlopen(req, timeout=15):
            url = req.full_url
            if "/actions/runs?" in url:
                return _make_response(runs_payload)
            if "/actions/runs/101/jobs" in url:
                requested_job_run_ids.append(101)
                return _make_response({
                    "total_count": 1,
                    "jobs": [{
                        "id": 5101,
                        "name": "build",
                        "status": "completed",
                        "conclusion": "success",
                        "steps": [{"number": 1, "name": "Run pytest", "status": "completed", "conclusion": "success"}],
                    }],
                })
            if "/actions/runs/102/jobs" in url:
                requested_job_run_ids.append(102)
                return _make_response({
                    "total_count": 1,
                    "jobs": [{
                        "id": 5102,
                        "name": "test",
                        "status": "completed",
                        "conclusion": "failure",
                        "steps": [{"number": 1, "name": "Run pytest", "status": "completed", "conclusion": "failure"}],
                    }],
                })
            if "/actions/runs/103/jobs" in url:
                requested_job_run_ids.append(103)
                return _make_response({
                    "total_count": 1,
                    "jobs": [{
                        "id": 5103,
                        "name": "test",
                        "status": "completed",
                        "conclusion": "cancelled",
                        "steps": [{"number": 1, "name": "Run pytest", "status": "completed", "conclusion": "cancelled"}],
                    }],
                })
            if "/actions/runs/104/jobs" in url:
                requested_job_run_ids.append(104)
                return _make_response({
                    "total_count": 1,
                    "jobs": [{
                        "id": 5104,
                        "name": "test",
                        "status": "completed",
                        "conclusion": "timed_out",
                        "steps": [{"number": 1, "name": "Run pytest", "status": "completed", "conclusion": "timed_out"}],
                    }],
                })
            raise AssertionError(f"Unexpected URL requested: {url}")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            records = self.collector.fetch_workflow_runs(limit=10, fetch_jobs=True)

        self.assertEqual(requested_job_run_ids, [101, 102, 103, 104])
        by_id = {r.run_id: r for r in records}
        self.assertEqual(len(by_id[101].jobs), 1)
        self.assertEqual(by_id[101].failure_type, "none")

        self.assertEqual(len(by_id[102].jobs), 1)
        self.assertEqual(by_id[102].failed_stage, "test")
        self.assertEqual(by_id[102].failure_type, "test_failure")

        self.assertEqual(len(by_id[103].jobs), 1)
        self.assertIsNone(by_id[103].failed_stage)
        self.assertEqual(by_id[103].failure_type, "none")

        self.assertEqual(len(by_id[104].jobs), 1)
        self.assertEqual(by_id[104].failed_stage, "test")
        self.assertEqual(by_id[104].failure_type, "timed_out")

        self.assertEqual(by_id[105].jobs, [])
        self.assertEqual(by_id[105].status, "queued")

    def test_job_pagination_uses_per_page_100_and_follows_next_links(self):
        seen_urls = []
        page1_url = "https://api.github.com/repos/owner/repo/actions/runs/700/jobs?per_page=100&page=1"
        page2_url = "https://api.github.com/repos/owner/repo/actions/runs/700/jobs?per_page=100&page=2"

        def fake_urlopen(req, timeout=15):
            seen_urls.append(req.full_url)
            if req.full_url == page1_url:
                return _make_response(
                    {"total_count": 2, "jobs": [{"id": 1, "name": "job-1", "status": "completed", "steps": []}]},
                    link_header=f'<{page2_url}>; rel="next", <{page2_url}>; rel="last"',
                )
            if req.full_url == page2_url:
                return _make_response(
                    {"total_count": 2, "jobs": [{"id": 2, "name": "job-2", "status": "completed", "steps": []}]},
                )
            raise AssertionError(f"Unexpected URL: {req.full_url}")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            jobs = self.collector.fetch_jobs_for_run(700)

        self.assertEqual(seen_urls, [page1_url, page2_url])
        self.assertEqual([j["id"] for j in jobs], [1, 2])

    def test_durations_missing_timestamps_skipped_steps_and_negative_delta_safety(self):
        job_payload = {
            "id": 9001,
            "name": "verify",
            "run_attempt": 2,
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-10T12:00:00Z",
            "started_at": "2026-09-10T12:00:04.500Z",
            "completed_at": "2026-09-10T12:01:04.500Z",
            "runner_name": "GitHub Actions 4",
            "steps": [
                {
                    "number": 1,
                    "name": "Install Dependencies",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-09-10T12:00:05Z",
                    "completed_at": "2026-09-10T12:00:25.250Z",
                },
                {
                    "number": 2,
                    "name": "Skipped Step",
                    "status": "completed",
                    "conclusion": "skipped",
                    "started_at": "2026-09-10T12:00:26Z",
                    "completed_at": "2026-09-10T12:00:27Z",
                },
                {
                    "number": 3,
                    "name": "In Progress Step",
                    "status": "in_progress",
                    "conclusion": None,
                    "started_at": "2026-09-10T12:00:28Z",
                    "completed_at": None,
                },
                {
                    "number": 4,
                    "name": "Missing Timestamps Step",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "number": 5,
                    "name": "Clock Skew Step",
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-09-10T12:00:50Z",
                    "completed_at": "2026-09-10T12:00:40Z",
                },
            ],
        }

        parsed = self.collector.parse_job_record(job_payload)
        self.assertEqual(parsed.queue_duration_seconds, 4.5)
        self.assertEqual(parsed.execution_duration_seconds, 60.0)
        self.assertEqual(parsed.runner_name, "GitHub Actions 4")
        self.assertEqual(parsed.run_attempt, 2)

        steps = {s.step_number: s for s in parsed.steps}
        self.assertEqual(steps[1].duration_seconds, 20.25)
        self.assertEqual(steps[1].normalized_stage, "dependency")
        self.assertIsNone(steps[2].duration_seconds)
        self.assertIsNone(steps[3].duration_seconds)
        self.assertIsNone(steps[4].duration_seconds)
        self.assertIsNone(steps[5].duration_seconds)

        # Negative job queue/execution delta must return None rather than 0.0 or negative.
        skewed_job = self.collector.parse_job_record({
            "id": 9002,
            "name": "skewed",
            "status": "completed",
            "created_at": "2026-09-10T12:00:10Z",
            "started_at": "2026-09-10T12:00:05Z",
            "completed_at": "2026-09-10T12:00:02Z",
            "steps": [],
        })
        self.assertIsNone(skewed_job.queue_duration_seconds)
        self.assertIsNone(skewed_job.execution_duration_seconds)

    def test_failure_classification_edge_cases(self):
        base_run = {
            "id": 3001,
            "name": "CI",
            "head_branch": "main",
            "head_sha": "abc1234",
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-09-10T10:00:00Z",
            "updated_at": "2026-09-10T10:01:00Z",
        }

        # 1. Dependency installation containing the word "test" must map to dependency, not test.
        dep_run = self.collector.parse_workflow_run(
            base_run,
            jobs_data=[{
                "id": 1,
                "name": "ci",
                "status": "completed",
                "conclusion": "failure",
                "steps": [{"number": 1, "name": "Install test dependencies", "status": "completed", "conclusion": "failure"}],
            }],
        )
        self.assertEqual(dep_run.failed_stage, "dependency")
        self.assertEqual(dep_run.failure_type, "dependency_failure")
        self.assertEqual(dep_run.jobs[0].steps[0].step_name, "Install test dependencies")

        # 2. Lint failure must not map to test_failure.
        lint_run = self.collector.parse_workflow_run(
            base_run,
            jobs_data=[{
                "id": 2,
                "name": "quality",
                "status": "completed",
                "conclusion": "failure",
                "steps": [{"number": 1, "name": "Run ESLint & Style Tests", "status": "completed", "conclusion": "failure"}],
            }],
        )
        self.assertEqual(lint_run.failed_stage, "lint")
        self.assertEqual(lint_run.failure_type, "lint_failure")

        # 3. Unrecognized failed step preserves original step_name and uses unknown.
        opaque_run = self.collector.parse_workflow_run(
            base_run,
            jobs_data=[{
                "id": 3,
                "name": "custom",
                "status": "completed",
                "conclusion": "failure",
                "steps": [{"number": 1, "name": "Invoke internal helper script", "status": "completed", "conclusion": "failure"}],
            }],
        )
        self.assertIsNone(opaque_run.jobs[0].steps[0].normalized_stage)
        self.assertEqual(opaque_run.jobs[0].steps[0].step_name, "Invoke internal helper script")
        self.assertEqual(opaque_run.failed_stage, "unknown")
        self.assertEqual(opaque_run.failure_type, "unknown")

        # 4. Timed out without failing step stage uses unknown + timed_out.
        timed_out_run = self.collector.parse_workflow_run(
            {**base_run, "conclusion": "timed_out"},
            jobs_data=[],
        )
        self.assertEqual(timed_out_run.failed_stage, "unknown")
        self.assertEqual(timed_out_run.failure_type, "timed_out")

        # 5. Startup failure handled cleanly even with empty jobs list.
        startup_run = self.collector.parse_workflow_run(
            {**base_run, "conclusion": "startup_failure"},
            jobs_data=[],
        )
        self.assertEqual(startup_run.failed_stage, "startup")
        self.assertEqual(startup_run.failure_type, "startup_failure")

        # 6. Continue-on-error failed step inside a successful run must not force pipeline failure_type.
        advisory_run = self.collector.parse_workflow_run(
            {**base_run, "conclusion": "success"},
            jobs_data=[{
                "id": 4,
                "name": "ci",
                "status": "completed",
                "conclusion": "success",
                "steps": [{"number": 1, "name": "Run pytest", "status": "completed", "conclusion": "failure"}],
            }],
        )
        self.assertIsNone(advisory_run.failed_stage)
        self.assertEqual(advisory_run.failure_type, "none")
        self.assertEqual(advisory_run.jobs[0].steps[0].conclusion, "failure")

    def test_rate_limit_during_job_fetch_surfaces_explicitly_without_faking_failure(self):
        runs_payload = {
            "workflow_runs": [{
                "id": 4001,
                "name": "CI",
                "head_branch": "main",
                "head_sha": "abc1234",
                "event": "push",
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-09-10T10:00:00Z",
                "updated_at": "2026-09-10T10:01:00Z",
            }]
        }

        rate_limit_err = urllib.error.HTTPError(
            url="https://api.github.com/repos/owner/repo/actions/runs/4001/jobs",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "60"},  # type: ignore
            fp=io.BytesIO(b'{"message": "rate limit exceeded"}'),
        )

        def fake_urlopen(req, timeout=15):
            if "/jobs" in req.full_url:
                raise rate_limit_err
            return _make_response(runs_payload)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with self.assertRaises(RateLimitExceededError) as ctx:
                self.collector.fetch_workflow_runs(limit=5, fetch_jobs=True)

        self.assertNotIn("secret-test-token-do-not-leak", str(ctx.exception))


class TestJobAndStepPersistenceAndAPI(unittest.TestCase):
    """Tests covering idempotent job/step persistence, incomplete protection, and sync API."""

    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        @event.listens_for(self.engine, "connect")
        def _enable_sqlite_fks(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

        def override_get_db():
            session = self.SessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _base_record(self, run_id: int = 9101, jobs=None, jobs_complete=None) -> PipelineRunRecord:
        record = PipelineRunRecord(
            run_id=run_id,
            repository="owner/repo",
            branch="main",
            commit_sha="1234abcd",
            status="completed",
            conclusion="success",
            workflow_name="CI",
            event="push",
            duration_seconds=30.0,
            failed_stage=None,
            failure_type="none",
            created_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
            jobs=jobs or [],
        )
        record._jobs_complete = jobs_complete
        return record

    def test_duplicate_safe_persistence_with_github_id_and_legacy_fallback(self):
        job_with_id = PipelineJobRecord(
            github_job_id=701,
            job_name="unit-tests",
            run_attempt=1,
            status="completed",
            conclusion="success",
            steps=[
                PipelineStepRecord(step_number=1, step_name="Checkout", status="completed", conclusion="success"),
                PipelineStepRecord(step_number=2, step_name="Run pytest", normalized_stage="test", status="completed", conclusion="success"),
            ],
        )
        legacy_job = PipelineJobRecord(
            github_job_id=None,
            job_name="legacy-job",
            run_attempt=None,
            status="completed",
            conclusion="success",
            steps=[
                PipelineStepRecord(step_number=1, step_name="Build", normalized_stage="build", status="completed", conclusion="success"),
            ],
        )

        record = self._base_record(run_id=9101, jobs=[job_with_id, legacy_job], jobs_complete=True)
        res1 = upsert_pipeline_runs(self.db, [record])
        self.assertEqual(res1["jobs_synced"], 2)
        self.assertEqual(res1["steps_synced"], 3)

        # Re-upsert modified versions + verify no duplicate jobs or steps are created.
        updated_job_with_id = PipelineJobRecord(
            github_job_id=701,
            job_name="unit-tests",
            run_attempt=1,
            status="completed",
            conclusion="failure",
            steps=[
                PipelineStepRecord(step_number=1, step_name="Checkout", status="completed", conclusion="success"),
                PipelineStepRecord(step_number=2, step_name="Run pytest", normalized_stage="test", status="completed", conclusion="failure", duration_seconds=12.0),
            ],
        )
        updated_legacy = PipelineJobRecord(
            github_job_id=None,
            job_name="legacy-job",
            run_attempt=1,
            status="completed",
            conclusion="success",
            steps=[
                PipelineStepRecord(step_number=1, step_name="Build Release", normalized_stage="build", status="completed", conclusion="success"),
            ],
        )
        record2 = self._base_record(run_id=9101, jobs=[updated_job_with_id, updated_legacy], jobs_complete=True)
        upsert_pipeline_runs(self.db, [record2])

        jobs = get_jobs_for_run(self.db, 9101)
        steps = get_steps_for_run(self.db, 9101)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(len(steps), 3)
        self.assertEqual(jobs[0].conclusion, "failure")
        self.assertEqual(steps[1].duration_seconds, 12.0)
        self.assertEqual(steps[2].step_name, "Build Release")

    def test_legacy_empty_jobs_preserves_existing_telemetry_and_complete_prunes_stale(self):
        initial_job_1 = PipelineJobRecord(
            github_job_id=801,
            job_name="lint",
            status="completed",
            conclusion="success",
            steps=[
                PipelineStepRecord(step_number=1, step_name="Flake8", normalized_stage="lint", status="completed", conclusion="success"),
                PipelineStepRecord(step_number=2, step_name="Ruff", normalized_stage="lint", status="completed", conclusion="success"),
            ],
        )
        initial_job_2 = PipelineJobRecord(
            github_job_id=802,
            job_name="stale-job",
            status="completed",
            conclusion="success",
            steps=[PipelineStepRecord(step_number=1, step_name="Old step", status="completed", conclusion="success")],
        )
        upsert_pipeline_runs(
            self.db,
            [self._base_record(run_id=9201, jobs=[initial_job_1, initial_job_2], jobs_complete=True)],
        )
        self.assertEqual(len(get_jobs_for_run(self.db, 9201)), 2)
        self.assertEqual(len(get_steps_for_run(self.db, 9201)), 3)

        # Legacy caller passes jobs=[] with default _jobs_complete=None -> must preserve existing children.
        legacy_empty_record = self._base_record(run_id=9201, jobs=[], jobs_complete=None)
        res_legacy = upsert_pipeline_runs(self.db, [legacy_empty_record])
        self.assertEqual(res_legacy["jobs_synced"], 0)
        self.assertEqual(len(get_jobs_for_run(self.db, 9201)), 2)
        self.assertEqual(len(get_steps_for_run(self.db, 9201)), 3)

        # Complete authoritative update with only job 801 step 1 -> prunes job 802 and step 2.
        pruned_job_1 = PipelineJobRecord(
            github_job_id=801,
            job_name="lint",
            status="completed",
            conclusion="success",
            steps=[
                PipelineStepRecord(step_number=1, step_name="Flake8", normalized_stage="lint", status="completed", conclusion="success"),
            ],
        )
        upsert_pipeline_runs(
            self.db,
            [self._base_record(run_id=9201, jobs=[pruned_job_1], jobs_complete=True)],
        )
        self.assertEqual([j.github_job_id for j in get_jobs_for_run(self.db, 9201)], [801])
        self.assertEqual([s.step_number for s in get_steps_for_run(self.db, 9201)], [1])

    def test_incomplete_api_response_preserves_existing_telemetry(self):
        existing_job = PipelineJobRecord(
            github_job_id=901,
            job_name="test",
            status="completed",
            conclusion="failure",
            steps=[
                PipelineStepRecord(step_number=1, step_name="Run pytest", normalized_stage="test", status="completed", conclusion="failure"),
            ],
        )
        upsert_pipeline_runs(
            self.db,
            [self._base_record(run_id=9301, jobs=[existing_job], jobs_complete=True)],
        )

        # Simulate sync where GitHub /jobs returns incomplete data (total_count=2 but only 1 job, or 500 on page 2).
        runs_payload = {
            "workflow_runs": [{
                "id": 9301,
                "name": "CI",
                "head_branch": "main",
                "head_sha": "1234abcd",
                "event": "push",
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-09-10T12:00:00Z",
                "updated_at": "2026-09-10T12:01:00Z",
            }]
        }
        incomplete_jobs_payload = {
            "total_count": 2,
            "jobs": [{"id": 901, "name": "test", "status": "completed", "steps": []}],
        }

        def fake_urlopen(req, timeout=15):
            if "/jobs" in req.full_url:
                if "page=2" in req.full_url:
                    raise urllib.error.HTTPError(
                        url=req.full_url,
                        code=500,
                        msg="Internal Server Error",
                        hdrs={},  # type: ignore
                        fp=io.BytesIO(b'{"message": "server error"}'),
                    )
                return _make_response(incomplete_jobs_payload)
            return _make_response(runs_payload)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            res = sync_github_runs_to_db(
                self.db,
                repository="owner/repo",
                github_token="secret-token-value",
                limit=5,
                use_mock=False,
                fetch_jobs=True,
                refresh_jobs=True,
            )

        self.assertEqual(res["updated"], 1)
        self.assertEqual(res["jobs_synced"], 0)
        self.assertEqual(res["steps_synced"], 0)
        # Stored job and step remain completely intact.
        self.assertEqual(len(get_jobs_for_run(self.db, 9301)), 1)
        self.assertEqual(len(get_steps_for_run(self.db, 9301)), 1)

    def test_sync_endpoint_fetch_jobs_refresh_jobs_and_avoid_redundant_calls(self):
        runs_payload = {
            "workflow_runs": [{
                "id": 9401,
                "name": "CI",
                "head_branch": "main",
                "head_sha": "abcd1234",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-09-10T12:00:00Z",
                "updated_at": "2026-09-10T12:00:40Z",
            }]
        }
        jobs_payload_v1 = {
            "total_count": 1,
            "jobs": [{
                "id": 9501,
                "name": "build-and-test",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-09-10T12:00:00Z",
                "started_at": "2026-09-10T12:00:05Z",
                "completed_at": "2026-09-10T12:00:35Z",
                "steps": [
                    {
                        "number": 1,
                        "name": "Checkout",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-09-10T12:00:05Z",
                        "completed_at": "2026-09-10T12:00:10Z",
                    },
                    {
                        "number": 2,
                        "name": "Run pytest",
                        "status": "completed",
                        "conclusion": "success",
                        "started_at": "2026-09-10T12:00:10Z",
                        "completed_at": "2026-09-10T12:00:35Z",
                    },
                ],
            }],
        }
        jobs_payload_v2 = {
            "total_count": 1,
            "jobs": [{
                **jobs_payload_v1["jobs"][0],
                "runner_name": "ubuntu-latest-v2",
            }],
        }

        job_fetch_calls = []

        def make_fake_urlopen(active_jobs_payload):
            def fake_urlopen(req, timeout=15):
                if "/jobs" in req.full_url:
                    job_fetch_calls.append(req.full_url)
                    return _make_response(active_jobs_payload)
                return _make_response(runs_payload)
            return fake_urlopen

        with patch.dict(os.environ, {"GITHUB_TOKEN": "top-secret-pat-value"}):
            # 1. fetch_jobs=false skips /jobs API calls cleanly.
            with patch("urllib.request.urlopen", side_effect=make_fake_urlopen(jobs_payload_v1)):
                resp0 = self.client.post("/api/pipelines/sync?use_mock=false&repository=owner/repo&limit=5&fetch_jobs=false")
            self.assertEqual(resp0.status_code, 200)
            self.assertEqual(len(job_fetch_calls), 0)
            self.assertEqual(resp0.json()["jobs_synced"], 0)
            self.assertEqual(resp0.json()["steps_synced"], 0)
            self.assertEqual(resp0.json()["data"]["jobs_synced"], 0)

            # 2. Initial fetch_jobs=true collects and persists 1 job and 2 steps.
            with patch("urllib.request.urlopen", side_effect=make_fake_urlopen(jobs_payload_v1)):
                resp1 = self.client.post("/api/pipelines/sync?use_mock=false&repository=owner/repo&limit=5&fetch_jobs=true")
            self.assertEqual(resp1.status_code, 200)
            body1 = resp1.json()
            self.assertEqual(len(job_fetch_calls), 1)
            self.assertEqual(body1["jobs_synced"], 1)
            self.assertEqual(body1["steps_synced"], 2)
            self.assertEqual(body1["data"]["jobs_synced"], 1)
            self.assertEqual(body1["data"]["steps_synced"], 2)
            self.assertEqual(get_completed_run_ids_with_jobs(self.db, repository="owner/repo"), {9401})
            self.assertNotIn("top-secret-pat-value", json.dumps(body1))

            # 3. Subsequent sync with refresh_jobs=false avoids repeated /jobs API call for stored completed run.
            with patch("urllib.request.urlopen", side_effect=make_fake_urlopen(jobs_payload_v2)):
                resp2 = self.client.post("/api/pipelines/sync?use_mock=false&repository=owner/repo&limit=5")
            self.assertEqual(resp2.status_code, 200)
            self.assertEqual(len(job_fetch_calls), 1)
            self.assertEqual(resp2.json()["jobs_synced"], 0)
            self.assertEqual(len(get_jobs_for_run(self.db, 9401)), 1)

            # 4. Explicit refresh_jobs=true re-fetches and updates stored job record without duplicates.
            with patch("urllib.request.urlopen", side_effect=make_fake_urlopen(jobs_payload_v2)):
                resp3 = self.client.post("/api/pipelines/sync?use_mock=false&repository=owner/repo&limit=5&refresh_jobs=true")
            self.assertEqual(resp3.status_code, 200)
            self.assertEqual(len(job_fetch_calls), 2)
            self.assertEqual(resp3.json()["jobs_synced"], 1)
            self.assertEqual(resp3.json()["steps_synced"], 2)

        with self.SessionLocal() as verify_session:
            stored_jobs = get_jobs_for_run(verify_session, 9401)
            self.assertEqual(len(stored_jobs), 1)
            self.assertEqual(stored_jobs[0].runner_name, "ubuntu-latest-v2")

    def test_sync_rate_limit_during_jobs_returns_429_without_credential_leak(self):
        runs_payload = {
            "workflow_runs": [{
                "id": 9505,
                "name": "CI",
                "head_branch": "main",
                "head_sha": "deadbeef",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-09-10T12:00:00Z",
                "updated_at": "2026-09-10T12:01:00Z",
            }]
        }
        rate_err = urllib.error.HTTPError(
            url="https://api.github.com/repos/owner/repo/actions/runs/9505/jobs",
            code=403,
            msg="Forbidden",
            hdrs={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700001234"},  # type: ignore
            fp=io.BytesIO(b'{"message": "rate limit exceeded"}'),
        )

        def fake_urlopen(req, timeout=15):
            if "/jobs" in req.full_url:
                raise rate_err
            return _make_response(runs_payload)

        with patch.dict(os.environ, {"GITHUB_TOKEN": "super-secret-token-xyz"}):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                resp = self.client.post("/api/pipelines/sync?use_mock=false&repository=owner/repo")

        self.assertEqual(resp.status_code, 429)
        self.assertIn("rate limit", resp.json()["detail"].lower())
        self.assertNotIn("super-secret-token-xyz", resp.text)
        # Run 9505 was never partially committed because fetch aborted on rate limit.
        self.assertEqual(self.db.query(PipelineRunModel).filter_by(run_id=9505).count(), 0)

    def test_transactional_batch_rollback_on_mid_batch_error(self):
        original_job = PipelineJobRecord(
            github_job_id=961,
            job_name="build",
            status="completed",
            conclusion="success",
            steps=[PipelineStepRecord(step_number=1, step_name="Compile", normalized_stage="build", status="completed", conclusion="success")],
        )
        upsert_pipeline_runs(
            self.db,
            [self._base_record(run_id=9601, jobs=[original_job], jobs_complete=True)],
        )

        updated_first = self._base_record(
            run_id=9601,
            jobs=[
                PipelineJobRecord(
                    github_job_id=961,
                    job_name="build",
                    status="completed",
                    conclusion="failure",
                    steps=[PipelineStepRecord(step_number=1, step_name="Compile Updated", normalized_stage="build", status="completed", conclusion="failure")],
                )
            ],
            jobs_complete=True,
        )
        second_run = self._base_record(run_id=9602, jobs=[], jobs_complete=True)

        real_fn = __import__(
            "backend.app.db.repository",
            fromlist=["_upsert_pipeline_run_with_stats"],
        )._upsert_pipeline_run_with_stats
        calls = {"count": 0}

        def failing_on_second(db, rec, *, replace_jobs=None):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("Simulated mid-batch DB failure")
            return real_fn(db, rec, replace_jobs=replace_jobs)

        with patch("backend.app.db.repository._upsert_pipeline_run_with_stats", side_effect=failing_on_second):
            with self.assertRaises(RuntimeError):
                upsert_pipeline_runs(self.db, [updated_first, second_run])

        # First run update and its child job/step changes must have rolled back cleanly.
        self.assertEqual(self.db.query(PipelineRunModel).filter_by(run_id=9602).count(), 0)
        restored_jobs = get_jobs_for_run(self.db, 9601)
        restored_steps = get_steps_for_run(self.db, 9601)
        self.assertEqual(restored_jobs[0].conclusion, "success")
        self.assertEqual(restored_steps[0].step_name, "Compile")


if __name__ == "__main__":
    unittest.main()
