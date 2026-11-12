/**
 * Centralized API client for the CI/CD Pipeline Failure Prediction & Optimization Platform.
 *
 * Uses `VITE_API_BASE_URL` when configured, otherwise defaults to relative paths (`""`)
 * so Vite's dev proxy (`/api` and `/health`) or same-origin deployments handle routing.
 */

const rawApiBaseUrl =
  typeof import.meta !== "undefined" && import.meta.env
    ? import.meta.env.VITE_API_BASE_URL || ""
    : "";

export const API_BASE_URL = rawApiBaseUrl.replace(/\/+$/, "");

const STATUS_ERROR_CODES = {
  400: "BAD_REQUEST",
  401: "UNAUTHORIZED",
  403: "FORBIDDEN",
  404: "NOT_FOUND",
  422: "VALIDATION_ERROR",
  429: "RATE_LIMITED",
  500: "INTERNAL_SERVER_ERROR",
  502: "BAD_GATEWAY",
  503: "SERVICE_UNAVAILABLE",
  504: "GATEWAY_TIMEOUT",
};

/**
 * Structured error representing failed HTTP requests, FastAPI errors, or network failures.
 */
export class ApiError extends Error {
  constructor(
    message,
    {
      status = 0,
      code = "API_ERROR",
      detail = null,
      validationErrors = null,
      isNetworkError = false,
      cause = null,
    } = {},
  ) {
    super(message, cause ? { cause } : undefined);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.validationErrors = validationErrors;
    this.isNetworkError = isNetworkError;
  }
}

/**
 * Return true when an error came from an aborted Fetch / AbortController signal.
 */
export function isAbortError(error) {
  return Boolean(error && error.name === "AbortError");
}

function formatValidationItems(items) {
  const messages = items
    .map((item) => {
      if (typeof item === "string") {
        return item;
      }
      if (!item || typeof item !== "object") {
        return null;
      }
      const loc = Array.isArray(item.loc)
        ? item.loc.filter((part) => !["body", "query", "path"].includes(String(part))).join(".")
        : "";
      const msg = typeof item.msg === "string" ? item.msg : JSON.stringify(item);
      return loc ? `${loc}: ${msg}` : msg;
    })
    .filter(Boolean);

  return messages.length > 0 ? messages.join("; ") : null;
}

function defaultStatusMessage(status, statusText) {
  if (status === 404) {
    return "Requested endpoint or resource was not found (404).";
  }
  if (status === 503) {
    return "Service is currently unavailable (503).";
  }
  if (status >= 500) {
    return `Backend server error (${status}). Ensure the API service is running.`;
  }
  return statusText ? `Request failed (${status} ${statusText}).` : `Request failed with status ${status}.`;
}

function normalizeFastApiError(payload, status, statusText) {
  let detail = null;
  let validationErrors = null;
  let message = null;

  if (payload && typeof payload === "object") {
    detail = payload.detail ?? null;

    if (Array.isArray(payload.detail)) {
      validationErrors = payload.detail;
      message = formatValidationItems(payload.detail);
    } else if (typeof payload.detail === "string" && payload.detail.trim()) {
      message = payload.detail.trim();
    } else if (typeof payload.message === "string" && payload.message.trim()) {
      message = payload.message.trim();
    }
  } else if (typeof payload === "string" && payload.trim()) {
    const trimmed = payload.trim();
    if (!/<html/i.test(trimmed)) {
      detail = trimmed;
      message = trimmed;
    }
  }

  return {
    detail,
    validationErrors,
    message: message || defaultStatusMessage(status, statusText),
  };
}

async function parseResponseBody(response) {
  if (response.status === 204) {
    return null;
  }

  const text = await response.text();
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function buildUrl(path, query) {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const searchParams = new URLSearchParams();

  if (query && typeof query === "object") {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") {
        searchParams.append(key, String(value));
      }
    }
  }

  const queryString = searchParams.toString();
  return `${API_BASE_URL}${normalizedPath}${queryString ? `?${queryString}` : ""}`;
}

/**
 * Execute an HTTP request against the backend API with normalized error handling.
 */
export async function request(path, options = {}) {
  const {
    method = "GET",
    query,
    body,
    headers = {},
    signal,
  } = options;

  const url = buildUrl(path, query);
  const requestHeaders = {
    Accept: "application/json",
    ...headers,
  };

  let serializedBody;
  if (body !== undefined) {
    serializedBody = typeof body === "string" ? body : JSON.stringify(body);
    if (!requestHeaders["Content-Type"]) {
      requestHeaders["Content-Type"] = "application/json";
    }
  }

  let response;
  let payload;

  try {
    response = await fetch(url, {
      method,
      headers: requestHeaders,
      body: serializedBody,
      signal,
    });
    payload = await parseResponseBody(response);
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new ApiError(
      "Network error: unable to reach the backend API. Check your server connection.",
      {
        status: 0,
        code: "NETWORK_ERROR",
        isNetworkError: true,
        cause: error,
      },
    );
  }

  if (!response.ok) {
    const normalized = normalizeFastApiError(payload, response.status, response.statusText);
    throw new ApiError(normalized.message, {
      status: response.status,
      code: STATUS_ERROR_CODES[response.status] || "HTTP_ERROR",
      detail: normalized.detail,
      validationErrors: normalized.validationErrors,
    });
  }

  return payload;
}

/**
 * GET /api/pipelines — List stored pipeline runs with optional pagination and filters.
 */
export function getPipelines(params = {}, options = {}) {
  const {
    limit,
    offset,
    repository,
    branch,
    status,
    conclusion,
    workflow_name,
    failure_type,
  } = params;

  return request("/api/pipelines", {
    ...options,
    method: "GET",
    query: {
      limit,
      offset,
      repository,
      branch,
      status,
      conclusion,
      workflow_name,
      failure_type,
    },
  });
}

/**
 * GET /api/pipelines/{run_id} — Fetch a single pipeline run by integer run ID.
 */
export function getPipelineById(runId, options = {}) {
  if (runId === undefined || runId === null || String(runId).trim() === "") {
    return Promise.reject(
      new ApiError("Pipeline run_id is required.", {
        status: 400,
        code: "BAD_REQUEST",
      }),
    );
  }

  return request(`/api/pipelines/${encodeURIComponent(String(runId).trim())}`, {
    ...options,
    method: "GET",
  });
}

/**
 * GET /api/pipelines/stats — Fetch aggregated pipeline execution statistics.
 */
export function getPipelineStats(params = {}, options = {}) {
  return request("/api/pipelines/stats", {
    ...options,
    method: "GET",
    query: {
      repository: params.repository,
    },
  });
}

/**
 * GET /api/pipelines/analytics/summary — Fetch analytics summary metrics.
 */
export function getAnalyticsSummary(params = {}, options = {}) {
  return request("/api/pipelines/analytics/summary", {
    ...options,
    method: "GET",
    query: {
      repository: params.repository,
    },
  });
}

/**
 * GET /api/optimization — Fetch combined bottleneck analysis and optimization recommendations.
 */
export function getOptimizationSummary(params = {}, options = {}) {
  return request("/api/optimization", {
    ...options,
    method: "GET",
    query: {
      repository: params.repository,
    },
  });
}

/**
 * GET /api/bottlenecks — Fetch pipeline bottleneck analysis.
 */
export function getBottlenecks(params = {}, options = {}) {
  return request("/api/bottlenecks", {
    ...options,
    method: "GET",
    query: {
      repository: params.repository,
    },
  });
}

/**
 * GET /api/recommendations — Fetch explainable optimization recommendations.
 */
export function getRecommendations(params = {}, options = {}) {
  return request("/api/recommendations", {
    ...options,
    method: "GET",
    query: {
      repository: params.repository,
    },
  });
}

/**
 * GET /api/predictions/status — Fetch ML failure prediction service readiness and metadata.
 * Exposes 404 (not integrated) or 503 (model unavailable) as ApiError without fabrication.
 */
export function getPredictionStatus(options = {}) {
  return request("/api/predictions/status", {
    ...options,
    method: "GET",
  });
}

/**
 * POST /api/predictions — Evaluate pre-run failure probability for a pipeline configuration.
 * Exposes 400/404/422/500/503 as ApiError without fabricating prediction results.
 */
export function predictPipelineFailure(payload, options = {}) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return Promise.reject(
      new ApiError("Prediction request payload must be a JSON object.", {
        status: 400,
        code: "BAD_REQUEST",
      }),
    );
  }

  return request("/api/predictions", {
    ...options,
    method: "POST",
    body: payload,
  });
}

/**
 * GET /health — Fetch backend application and database connectivity status.
 */
export function getHealth(options = {}) {
  return request("/health", {
    ...options,
    method: "GET",
  });
}
