"""Analytics package providing pipeline telemetry metrics, trends, and duration aggregations."""

from .service import (
    get_analytics_summary,
    get_complete_analytics,
    get_duration_analytics,
    get_execution_trends,
    get_failure_analytics,
)

__all__ = [
    "get_analytics_summary",
    "get_execution_trends",
    "get_failure_analytics",
    "get_duration_analytics",
    "get_complete_analytics",
]
