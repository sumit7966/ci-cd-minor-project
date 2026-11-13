"""Dedicated historical backfill service for GitHub Actions job and step telemetry."""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from ..db.models import PipelineRunModel
from ..db.repository import (
    get_completed_run_ids_with_jobs,
    get_runs_for_backfill,
    preview_run_job_telemetry_diff,
    sync_run_job_telemetry,
)
from ..db.session import get_engine, init_db
from sqlalchemy.orm import sessionmaker
from .github_collector import (
    GitHubAPIError,
    GitHubCollectorError,
    GitHubWorkflowRunCollector,
    MissingTokenError,
    RateLimitExceededError,
    RunTelemetryUnavailableError,
    sanitize_error_message,
)
from .schemas import PipelineJobRecord

_UNSTARTED_STATUSES = {"queued", "waiting", "pending"}


def _sanitize_error_detail(message: str, token: Optional[str]) -> str:
    """Strip any accidental token values or bearer headers from error messages."""
    return sanitize_error_message(message or "Unknown telemetry backfill error", token)


def _is_unstarted_queued_run(
    status: Optional[str],
    conclusion: Optional[str],
    duration_seconds: Optional[float],
) -> bool:
    """Return True when a stored run has not started executing jobs yet."""
    normalized_status = (status or "").strip().lower()
    if normalized_status not in _UNSTARTED_STATUSES:
        return False
    if conclusion:
        return False
    if duration_seconds is not None and duration_seconds > 0:
        return False
    return True


def backfill_job_telemetry(
    db: Session,
    *,
    repository: Optional[str] = None,
    run_ids: Optional[List[int]] = None,
    limit: int = 100,
    offset: int = 0,
    refresh_jobs: bool = False,
    dry_run: bool = True,
    probe_github: bool = False,
    reconcile_empty: bool = False,
    github_token: Optional[str] = None,
    request_delay_seconds: float = 0.2,
) -> Dict[str, Any]:
    """Backfill job and step telemetry strictly for existing PipelineRunModel records.

    This service queries exact run_id targets from PostgreSQL/SQLAlchemy storage,
    groups them by repository, fetches job telemetry directly via
    ``GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs``, and persists only
    ``pipeline_jobs`` and ``pipeline_steps`` in per-run transactions without
    creating, updating, or deleting parent ``pipeline_runs`` rows.
    """
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500.")
    if offset < 0:
        raise ValueError("offset must be >= 0.")
    if request_delay_seconds < 0:
        raise ValueError("request_delay_seconds must be >= 0.")

    resolved_token = (github_token or os.getenv("GITHUB_TOKEN") or "").strip() or None
    if not dry_run and not resolved_token:
        raise MissingTokenError(
            "GITHUB_TOKEN must be configured in the backend environment when dry_run=False."
        )

    runs_matched, target_runs = get_runs_for_backfill(
        db=db,
        repository=repository,
        run_ids=run_ids,
        limit=limit,
        offset=offset,
    )
    runs_targeted = len(target_runs)

    completed_with_jobs = (
        get_completed_run_ids_with_jobs(db=db, repository=repository)
        if not refresh_jobs
        else set()
    )

    # Snapshot scalar run metadata upfront so per-run commit/rollback cycles never
    # trigger stale attribute reads across SQLAlchemy session state transitions.
    grouped_targets: Dict[str, List[Tuple[int, str, Optional[str], Optional[float]]]] = {}
    for run_model in target_runs:
        repo_slug = (run_model.repository or "").strip()
        grouped_targets.setdefault(repo_slug, []).append(
            (
                int(run_model.run_id),
                run_model.status,
                run_model.conclusion,
                run_model.duration_seconds,
            )
        )

    runs_fetched = 0
    runs_skipped = 0
    runs_failed = 0
    jobs_inserted = 0
    jobs_updated = 0
    steps_inserted = 0
    steps_updated = 0
    stopped_early = False
    stop_reason: Optional[str] = None
    failed_runs: List[Dict[str, Any]] = []
    last_rate_limit_remaining: Optional[int] = None
    last_rate_limit_reset: Optional[int] = None

    should_fetch_github = (not dry_run) or probe_github
    has_made_github_request = False

    for repo_slug, repo_targets in grouped_targets.items():
        if stopped_early:
            break

        collector: Optional[GitHubWorkflowRunCollector] = None
        if should_fetch_github:
            if not repo_slug or "/" not in repo_slug:
                for run_id, _status, _conclusion, _dur in repo_targets:
                    runs_failed += 1
                    failed_runs.append(
                        {
                            "run_id": run_id,
                            "repository": repo_slug,
                            "error_type": "InvalidRepository",
                            "detail": f"Stored repository '{repo_slug}' is not in 'owner/repo' format.",
                        }
                    )
                continue
            collector = GitHubWorkflowRunCollector(
                repository=repo_slug,
                github_token=resolved_token,
            )

        for run_id, run_status, run_conclusion, run_duration in repo_targets:
            if _is_unstarted_queued_run(run_status, run_conclusion, run_duration):
                runs_skipped += 1
                continue

            if not refresh_jobs and run_id in completed_with_jobs:
                runs_skipped += 1
                continue

            if not should_fetch_github:
                continue

            assert collector is not None
            if (
                collector.last_rate_limit_remaining is not None
                and collector.last_rate_limit_remaining <= 0
            ):
                stopped_early = True
                stop_reason = (
                    f"GitHub API rate limit exhausted (remaining=0, "
                    f"reset={collector.last_rate_limit_reset or 'unknown'})."
                )
                break

            try:
                if has_made_github_request and request_delay_seconds > 0:
                    time.sleep(request_delay_seconds)
                has_made_github_request = True

                raw_jobs = collector.fetch_jobs_for_run(
                    run_id,
                    raise_run_unavailable=True,
                )
                last_rate_limit_remaining = collector.last_rate_limit_remaining
                last_rate_limit_reset = collector.last_rate_limit_reset

                parsed_jobs: List[PipelineJobRecord] = []
                for raw_job in raw_jobs:
                    if not isinstance(raw_job, dict):
                        raise GitHubAPIError(
                            f"Malformed job payload entry returned for run {run_id}."
                        )
                    parsed_jobs.append(
                        collector.parse_job_record(raw_job)
                    )

                runs_fetched += 1

                if dry_run:
                    diff_counts = preview_run_job_telemetry_diff(
                        db=db,
                        run_id=run_id,
                        jobs=parsed_jobs,
                    )
                else:
                    run_row = (
                        db.query(PipelineRunModel)
                        .filter(PipelineRunModel.run_id == run_id)
                        .one_or_none()
                    )
                    if run_row is None:
                        raise RuntimeError(
                            f"Target pipeline run {run_id} was not found during persistence."
                        )
                    diff_counts = sync_run_job_telemetry(
                        db=db,
                        run_model=run_row,
                        jobs=parsed_jobs,
                        prune_missing=True,
                        allow_empty_reconcile=reconcile_empty,
                    )
                    db.commit()

                jobs_inserted += diff_counts["jobs_inserted"]
                jobs_updated += diff_counts["jobs_updated"]
                steps_inserted += diff_counts["steps_inserted"]
                steps_updated += diff_counts["steps_updated"]

            except (RateLimitExceededError, MissingTokenError) as exc:
                if not dry_run:
                    db.rollback()
                last_rate_limit_remaining = collector.last_rate_limit_remaining
                last_rate_limit_reset = collector.last_rate_limit_reset
                safe_detail = _sanitize_error_detail(str(exc), resolved_token)
                runs_failed += 1
                failed_entry: Dict[str, Any] = {
                    "run_id": run_id,
                    "repository": repo_slug,
                    "error_type": type(exc).__name__,
                    "detail": safe_detail,
                }
                req_id = getattr(exc, "request_id", None) or collector.last_request_id
                if req_id:
                    failed_entry["request_id"] = req_id
                failed_runs.append(failed_entry)
                stopped_early = True
                stop_reason = safe_detail
                break
            except (RunTelemetryUnavailableError, GitHubCollectorError, Exception) as exc:
                if not dry_run:
                    db.rollback()
                last_rate_limit_remaining = collector.last_rate_limit_remaining
                last_rate_limit_reset = collector.last_rate_limit_reset
                runs_failed += 1
                failed_entry = {
                    "run_id": run_id,
                    "repository": repo_slug,
                    "error_type": type(exc).__name__,
                    "detail": _sanitize_error_detail(str(exc), resolved_token),
                }
                req_id = getattr(exc, "request_id", None) or collector.last_request_id
                if req_id:
                    failed_entry["request_id"] = req_id
                failed_runs.append(failed_entry)

    return {
        "repository": repository,
        "dry_run": dry_run,
        "probe_github": probe_github,
        "refresh_jobs": refresh_jobs,
        "reconcile_empty": reconcile_empty,
        "limit": limit,
        "offset": offset,
        "runs_matched": runs_matched,
        "runs_targeted": runs_targeted,
        "runs_fetched": runs_fetched,
        "runs_skipped": runs_skipped,
        "runs_failed": runs_failed,
        "jobs_inserted": jobs_inserted,
        "jobs_updated": jobs_updated,
        "steps_inserted": steps_inserted,
        "steps_updated": steps_updated,
        "stopped_early": stopped_early,
        "stop_reason": stop_reason,
        "failed_runs": failed_runs,
        "rate_limit_remaining": last_rate_limit_remaining,
        "rate_limit_reset": last_rate_limit_reset,
    }


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint for historical job and step telemetry backfill (reads GITHUB_TOKEN from env)."""
    parser = argparse.ArgumentParser(
        description=(
            "Backfill GitHub Actions job and step telemetry for existing stored pipeline runs. "
            "Defaults to --dry-run; pass --apply to persist changes using GITHUB_TOKEN from env."
        )
    )
    parser.add_argument("--repository", type=str, default=None, help="Filter by owner/repo")
    parser.add_argument(
        "--run-id",
        type=int,
        action="append",
        dest="run_ids",
        default=None,
        help="Target specific pipeline run_id (can be repeated)",
    )
    parser.add_argument("--limit", type=int, default=100, help="Batch size (1-500)")
    parser.add_argument("--offset", type=int, default=0, help="Batch offset (>=0)")
    parser.add_argument(
        "--refresh-jobs",
        action="store_true",
        help="Re-fetch and update jobs/steps even for completed runs that already have telemetry",
    )
    parser.add_argument(
        "--probe-github",
        action="store_true",
        help="During dry-run, fetch GitHub jobs read-only without modifying PostgreSQL",
    )
    parser.add_argument(
        "--reconcile-empty",
        action="store_true",
        help="Allow complete empty job responses to prune previously stored jobs/steps",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist fetched job/step telemetry to PostgreSQL (requires GITHUB_TOKEN in env)",
    )

    args = parser.parse_args(argv)
    if args.limit < 1 or args.limit > 500:
        parser.error("--limit must be between 1 and 500")
    if args.offset < 0:
        parser.error("--offset must be >= 0")

    engine = get_engine(os.getenv("DATABASE_URL"))
    init_db(engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with session_local() as db:
        result = backfill_job_telemetry(
            db=db,
            repository=args.repository,
            run_ids=args.run_ids,
            limit=args.limit,
            offset=args.offset,
            refresh_jobs=args.refresh_jobs,
            dry_run=not args.apply,
            probe_github=args.probe_github,
            reconcile_empty=args.reconcile_empty,
        )

    print(json.dumps(result, indent=2))
    return 1 if result["stopped_early"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
