"""Analytics computation service for CI/CD pipeline telemetry data."""

from collections import defaultdict
from datetime import datetime, timezone
import statistics
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from ..db.models import PipelineRunModel


def _filter_runs(
    db: Session,
    repository: Optional[str] = None,
    workflow_name: Optional[str] = None,
    branch: Optional[str] = None,
) -> List[PipelineRunModel]:
    """Helper to query pipeline runs with common query filters."""
    query = db.query(PipelineRunModel)
    if repository:
        query = query.filter(PipelineRunModel.repository == repository)
    if workflow_name:
        query = query.filter(PipelineRunModel.workflow_name == workflow_name)
    if branch:
        query = query.filter(PipelineRunModel.branch == branch)
    return query.order_by(PipelineRunModel.created_at.asc()).all()


def get_analytics_summary(
    db: Session,
    repository: Optional[str] = None,
) -> Dict[str, Any]:
    """Calculate overall pipeline execution summary and success/failure rates.

    Safe against empty databases and division by zero.
    """
    runs = _filter_runs(db, repository=repository)
    total_runs = len(runs)

    if total_runs == 0:
        return {
            "total_runs": 0,
            "success_count": 0,
            "failure_count": 0,
            "other_count": 0,
            "success_rate_percent": 0.0,
            "failure_rate_percent": 0.0,
            "average_duration_seconds": None,
            "min_duration_seconds": None,
            "max_duration_seconds": None,
            "median_duration_seconds": None,
        }

    success_count = sum(1 for r in runs if r.conclusion == "success")
    failure_count = sum(1 for r in runs if r.conclusion == "failure")
    other_count = total_runs - (success_count + failure_count)

    success_rate = round((success_count / total_runs) * 100, 2)
    failure_rate = round((failure_count / total_runs) * 100, 2)

    durations = [r.duration_seconds for r in runs if r.duration_seconds is not None]
    avg_duration = round(sum(durations) / len(durations), 2) if durations else None
    min_duration = round(min(durations), 2) if durations else None
    max_duration = round(max(durations), 2) if durations else None
    median_duration = round(statistics.median(durations), 2) if durations else None

    return {
        "total_runs": total_runs,
        "success_count": success_count,
        "failure_count": failure_count,
        "other_count": other_count,
        "success_rate_percent": success_rate,
        "failure_rate_percent": failure_rate,
        "average_duration_seconds": avg_duration,
        "min_duration_seconds": min_duration,
        "max_duration_seconds": max_duration,
        "median_duration_seconds": median_duration,
    }


def get_execution_trends(
    db: Session,
    repository: Optional[str] = None,
    days: Optional[int] = 30,
) -> List[Dict[str, Any]]:
    """Aggregate pipeline execution volume and success/failure trends grouped by date.

    Returns chronologically sorted daily buckets suitable for time-series charts.
    """
    runs = _filter_runs(db, repository=repository)
    if not runs:
        return []

    # Group runs by ISO date (YYYY-MM-DD)
    date_buckets: Dict[str, List[PipelineRunModel]] = defaultdict(list)
    for r in runs:
        if r.created_at:
            dt_key = r.created_at.strftime("%Y-%m-%d")
            date_buckets[dt_key].append(r)

    # Convert to sorted list of date entries
    sorted_dates = sorted(date_buckets.keys())
    if days and len(sorted_dates) > days:
        sorted_dates = sorted_dates[-days:]

    trends: List[Dict[str, Any]] = []
    for dt_str in sorted_dates:
        bucket_runs = date_buckets[dt_str]
        total = len(bucket_runs)
        successes = sum(1 for r in bucket_runs if r.conclusion == "success")
        failures = sum(1 for r in bucket_runs if r.conclusion == "failure")
        durations = [r.duration_seconds for r in bucket_runs if r.duration_seconds is not None]

        trends.append({
            "date": dt_str,
            "total_runs": total,
            "success_count": successes,
            "failure_count": failures,
            "other_count": total - (successes + failures),
            "success_rate_percent": round((successes / total) * 100, 2) if total else 0.0,
            "failure_rate_percent": round((failures / total) * 100, 2) if total else 0.0,
            "average_duration_seconds": round(sum(durations) / len(durations), 2) if durations else None,
        })

    return trends


def get_failure_analytics(
    db: Session,
    repository: Optional[str] = None,
) -> Dict[str, Any]:
    """Calculate failure distributions categorized by failure_type and failed_stage.

    Provides percentage shares and identifies the primary failure culprits.
    """
    runs = _filter_runs(db, repository=repository)
    failed_runs = [r for r in runs if r.conclusion == "failure" or (r.failure_type and r.failure_type != "none")]
    total_failures = len(failed_runs)

    if total_failures == 0:
        return {
            "total_failures": 0,
            "most_frequent_failure_type": None,
            "most_frequent_failed_stage": None,
            "by_failure_type": [],
            "by_failed_stage": [],
        }

    # Count by failure_type
    type_counts: Dict[str, int] = defaultdict(int)
    stage_counts: Dict[str, int] = defaultdict(int)

    for r in failed_runs:
        f_type = r.failure_type or "unknown"
        type_counts[f_type] += 1

        f_stage = r.failed_stage or "unknown"
        stage_counts[f_stage] += 1

    # Format sorted breakdown lists
    by_type = [
        {
            "failure_type": k,
            "count": v,
            "percentage": round((v / total_failures) * 100, 2),
        }
        for k, v in sorted(type_counts.items(), key=lambda item: item[1], reverse=True)
    ]

    by_stage = [
        {
            "stage": k,
            "count": v,
            "percentage": round((v / total_failures) * 100, 2),
        }
        for k, v in sorted(stage_counts.items(), key=lambda item: item[1], reverse=True)
    ]

    most_frequent_type = by_type[0]["failure_type"] if by_type else None
    most_frequent_stage = by_stage[0]["stage"] if by_stage else None

    return {
        "total_failures": total_failures,
        "most_frequent_failure_type": most_frequent_type,
        "most_frequent_failed_stage": most_frequent_stage,
        "by_failure_type": by_type,
        "by_failed_stage": by_stage,
    }


def get_duration_analytics(
    db: Session,
    repository: Optional[str] = None,
) -> Dict[str, Any]:
    """Calculate pipeline duration metrics across workflows and branches.

    Includes overall summary, per-workflow benchmarks, and per-branch averages.
    """
    runs = _filter_runs(db, repository=repository)
    durations = [r.duration_seconds for r in runs if r.duration_seconds is not None]

    if not durations:
        return {
            "overall": {
                "total_runs_with_duration": 0,
                "average_duration_seconds": None,
                "min_duration_seconds": None,
                "max_duration_seconds": None,
                "median_duration_seconds": None,
            },
            "by_workflow": [],
            "by_branch": [],
        }

    overall_stats = {
        "total_runs_with_duration": len(durations),
        "average_duration_seconds": round(sum(durations) / len(durations), 2),
        "min_duration_seconds": round(min(durations), 2),
        "max_duration_seconds": round(max(durations), 2),
        "median_duration_seconds": round(statistics.median(durations), 2),
    }

    # Group by workflow_name
    workflow_groups: Dict[str, List[float]] = defaultdict(list)
    branch_groups: Dict[str, List[PipelineRunModel]] = defaultdict(list)

    for r in runs:
        if r.duration_seconds is not None and r.workflow_name:
            workflow_groups[r.workflow_name].append(r.duration_seconds)
        if r.branch:
            branch_groups[r.branch].append(r)

    by_workflow = []
    for wf_name, wf_durations in workflow_groups.items():
        by_workflow.append({
            "workflow_name": wf_name,
            "total_runs": len(wf_durations),
            "average_duration_seconds": round(sum(wf_durations) / len(wf_durations), 2),
            "min_duration_seconds": round(min(wf_durations), 2),
            "max_duration_seconds": round(max(wf_durations), 2),
        })
    by_workflow.sort(key=lambda x: x["total_runs"], reverse=True)

    by_branch = []
    for br_name, br_runs in branch_groups.items():
        br_durations = [r.duration_seconds for r in br_runs if r.duration_seconds is not None]
        successes = sum(1 for r in br_runs if r.conclusion == "success")
        total_br = len(br_runs)
        by_branch.append({
            "branch": br_name,
            "total_runs": total_br,
            "success_rate_percent": round((successes / total_br) * 100, 2) if total_br else 0.0,
            "average_duration_seconds": round(sum(br_durations) / len(br_durations), 2) if br_durations else None,
            "min_duration_seconds": round(min(br_durations), 2) if br_durations else None,
            "max_duration_seconds": round(max(br_durations), 2) if br_durations else None,
        })
    by_branch.sort(key=lambda x: x["total_runs"], reverse=True)

    return {
        "overall": overall_stats,
        "by_workflow": by_workflow,
        "by_branch": by_branch,
    }


def get_complete_analytics(
    db: Session,
    repository: Optional[str] = None,
    days: Optional[int] = 30,
) -> Dict[str, Any]:
    """Assemble all analytical dimensions into a single unified dashboard payload."""
    return {
        "summary": get_analytics_summary(db, repository=repository),
        "trends": get_execution_trends(db, repository=repository, days=days),
        "failures": get_failure_analytics(db, repository=repository),
        "duration": get_duration_analytics(db, repository=repository),
    }
