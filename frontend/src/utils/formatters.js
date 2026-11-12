/**
 * Reusable display formatters for CI/CD telemetry, analytics, and ML prediction values.
 *
 * All formatters safely handle null, undefined, empty, and invalid inputs without mutating
 * source payloads from the backend.
 */

const OUTCOME_BADGES = {
  success: { label: "Success", variant: "success" },
  failure: { label: "Failed", variant: "danger" },
  timed_out: { label: "Timed Out", variant: "danger" },
  action_required: { label: "Action Required", variant: "warning" },
  cancelled: { label: "Cancelled", variant: "warning" },
  canceled: { label: "Cancelled", variant: "warning" },
  skipped: { label: "Skipped", variant: "info" },
  neutral: { label: "Neutral", variant: "info" },
  completed: { label: "Completed", variant: "info" },
  in_progress: { label: "In Progress", variant: "info" },
  queued: { label: "Queued", variant: "warning" },
  requested: { label: "Requested", variant: "warning" },
  waiting: { label: "Waiting", variant: "warning" },
};

/**
 * Convert a snake_case or kebab-case identifier into Title Case words.
 */
export function formatTokenLabel(value, fallback = "N/A") {
  if (typeof value !== "string") {
    return fallback;
  }

  const trimmed = value.trim();
  if (!trimmed) {
    return fallback;
  }

  return trimmed
    .replace(/[_-]+/g, " ")
    .split(/\s+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

/**
 * Format duration in seconds into a compact human-readable string (e.g., "14m 32s", "45s").
 */
export function formatDuration(seconds, fallback = "N/A") {
  if (seconds === null || seconds === undefined || seconds === "") {
    return fallback;
  }

  const numeric = typeof seconds === "number" ? seconds : Number(seconds);
  if (!Number.isFinite(numeric) || numeric < 0) {
    return fallback;
  }

  if (numeric === 0) {
    return "0s";
  }

  if (numeric < 1) {
    return `${Number(numeric.toFixed(1))}s`;
  }

  const totalSeconds = Math.round(numeric);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainingSeconds = totalSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m ${remainingSeconds}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${remainingSeconds}s`;
  }
  return `${remainingSeconds}s`;
}

/**
 * Format a numeric percentage.
 *
 * Set `isRatio: true` for 0.0-1.0 inputs (such as failure_probability or stage_failure_rate).
 * Leave `isRatio: false` (default) for 0-100 backend `*_rate_percent` fields.
 */
export function formatPercentage(
  value,
  { decimals = 1, isRatio = false, fallback = "N/A" } = {},
) {
  if (value === null || value === undefined || value === "") {
    return fallback;
  }

  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) {
    return fallback;
  }

  const scaled = isRatio ? numeric * 100 : numeric;
  const safeDecimals = Math.min(Math.max(0, Number(decimals) || 0), 4);
  return `${Number(scaled.toFixed(safeDecimals))}%`;
}

/**
 * Format an ISO-8601 timestamp string into a concise local date/time label.
 */
export function formatTimestamp(isoString, fallback = "N/A") {
  if (!isoString) {
    return fallback;
  }

  const date = isoString instanceof Date ? isoString : new Date(isoString);
  if (Number.isNaN(date.getTime())) {
    return fallback;
  }

  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(date);
  } catch {
    return date.toISOString();
  }
}

/**
 * Shorten a Git commit SHA to a fixed prefix length (default 7 characters).
 */
export function formatCommitSha(sha, length = 7, fallback = "N/A") {
  if (typeof sha !== "string") {
    return fallback;
  }

  const trimmed = sha.trim();
  if (!trimmed) {
    return fallback;
  }

  const safeLength = Math.max(1, Number(length) || 7);
  return trimmed.slice(0, safeLength);
}

/**
 * Resolve normalized label and CSS badge variant for workflow status/conclusion values.
 */
export function getRunStatusMeta(status, conclusion = null) {
  const normalizedConclusion =
    typeof conclusion === "string" && conclusion.trim()
      ? conclusion.trim().toLowerCase()
      : null;
  const normalizedStatus =
    typeof status === "string" && status.trim()
      ? status.trim().toLowerCase()
      : null;

  const key = normalizedConclusion || normalizedStatus;
  if (!key) {
    return { label: "Unknown", variant: "info" };
  }

  if (OUTCOME_BADGES[key]) {
    return OUTCOME_BADGES[key];
  }

  return {
    label: formatTokenLabel(key, "Unknown"),
    variant: "info",
  };
}

/**
 * Format a pipeline run status/conclusion pair into a single display label.
 */
export function formatRunStatus(status, conclusion = null) {
  return getRunStatusMeta(status, conclusion).label;
}
