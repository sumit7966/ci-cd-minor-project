import React, { useState, useEffect, useRef } from 'react';
import {
  getPredictionStatus,
  isAbortError,
  predictPipelineFailure,
} from '../services/api';
import {
  formatPercentage,
  formatTimestamp,
  formatTokenLabel,
} from '../utils/formatters';

const INITIAL_FORM = {
  repository: '',
  branch: '',
  workflow_name: '',
  event: 'push',
  created_at: '',
  run_id: '',
};

const EVENT_PRESETS = [
  'push',
  'pull_request',
  'workflow_dispatch',
  'schedule',
  'release',
];

function classifyStatusResult(data) {
  if (data && data.status === 'ready' && data.model_available === true) {
    return {
      kind: 'ready',
      badgeLabel: 'Ready',
      badgeVariant: 'success',
      data,
      httpStatus: 200,
      message: null,
    };
  }

  return {
    kind: 'unavailable',
    badgeLabel: 'Unavailable',
    badgeVariant: 'warning',
    data: data || null,
    httpStatus: 200,
    message: 'ML failure prediction model is currently unavailable (model not loaded on backend).',
  };
}

function classifyStatusError(err) {
  if (err?.isNetworkError) {
    return {
      kind: 'network_error',
      badgeLabel: 'Network Error',
      badgeVariant: 'danger',
      data: null,
      httpStatus: 0,
      message: err.message || 'Unable to reach the prediction service.',
    };
  }

  const httpStatus = err?.status || null;
  let message = err?.message || 'ML failure prediction service is unavailable.';
  if (httpStatus === 404) {
    message =
      'Prediction endpoint returned HTTP 404. Member 2 ML prediction routes are not integrated on the current backend.';
  } else if (httpStatus === 503) {
    message =
      err?.message ||
      'Prediction model is unavailable (HTTP 503). Ensure a trained model artifact is available on the backend.';
  }

  return {
    kind: 'unavailable',
    badgeLabel: 'Unavailable',
    badgeVariant: 'danger',
    data: null,
    httpStatus,
    message,
  };
}

function getRiskBadgeVariant(riskLevel) {
  const normalized = typeof riskLevel === 'string' ? riskLevel.trim().toLowerCase() : '';
  if (normalized === 'high') {
    return 'danger';
  }
  if (normalized === 'medium') {
    return 'warning';
  }
  if (normalized === 'low') {
    return 'success';
  }
  return 'info';
}

function getProbabilityColor(prob) {
  if (typeof prob !== 'number' || !Number.isFinite(prob)) {
    return 'var(--text-primary)';
  }
  if (prob >= 0.65) {
    return 'var(--accent-red)';
  }
  if (prob >= 0.35) {
    return 'var(--accent-warning)';
  }
  return 'var(--accent-green)';
}

function validatePredictionForm(form) {
  const errors = {};

  if (!form.repository.trim()) {
    errors.repository = 'Repository is required (e.g. owner/repo).';
  }
  if (!form.branch.trim()) {
    errors.branch = 'Branch name is required.';
  }
  if (!form.workflow_name.trim()) {
    errors.workflow_name = 'Workflow name is required.';
  }
  if (!form.event.trim()) {
    errors.event = 'Trigger event is required.';
  }

  if (form.created_at.trim()) {
    const parsedDate = new Date(form.created_at.trim());
    if (Number.isNaN(parsedDate.getTime())) {
      errors.created_at = 'Enter a valid date and time.';
    }
  }

  if (form.run_id.trim()) {
    const parsedRunId = Number(form.run_id.trim());
    if (!Number.isInteger(parsedRunId) || parsedRunId <= 0) {
      errors.run_id = 'Run ID must be a positive integer.';
    }
  }

  return errors;
}

const FailurePrediction = () => {
  const [statusRefreshCount, setStatusRefreshCount] = useState(0);
  const [statusFetchState, setStatusFetchState] = useState({
    resolvedKey: null,
    statusInfo: null,
  });

  const [form, setForm] = useState(INITIAL_FORM);
  const [fieldErrors, setFieldErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [prediction, setPrediction] = useState(null);

  const submitAbortRef = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    let isActive = true;
    const currentKey = statusRefreshCount;

    getPredictionStatus({ signal: controller.signal })
      .then((data) => {
        if (!isActive || controller.signal.aborted) {
          return;
        }
        setStatusFetchState({
          resolvedKey: currentKey,
          statusInfo: classifyStatusResult(data),
        });
      })
      .catch((error) => {
        if (!isActive || isAbortError(error) || controller.signal.aborted) {
          return;
        }
        setStatusFetchState({
          resolvedKey: currentKey,
          statusInfo: classifyStatusError(error),
        });
      });

    return () => {
      isActive = false;
      controller.abort();
    };
  }, [statusRefreshCount]);

  useEffect(() => {
    return () => {
      if (submitAbortRef.current) {
        submitAbortRef.current.abort();
      }
    };
  }, []);

  const isStatusLoading = statusFetchState.resolvedKey !== statusRefreshCount;
  const statusInfo =
    statusFetchState.resolvedKey === statusRefreshCount
      ? statusFetchState.statusInfo
      : null;

  const handleRetryStatus = () => {
    setSubmitError(null);
    setStatusRefreshCount((prev) => prev + 1);
  };

  const handleFieldChange = (event) => {
    const { name, value } = event.target;
    setForm((prev) => ({ ...prev, [name]: value }));
    if (fieldErrors[name]) {
      setFieldErrors((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    }
    if (submitError) {
      setSubmitError(null);
    }
  };

  const handleResetForm = () => {
    if (submitAbortRef.current) {
      submitAbortRef.current.abort();
    }
    setForm(INITIAL_FORM);
    setFieldErrors({});
    setSubmitError(null);
    setPrediction(null);
    setSubmitting(false);
  };

  const handleSubmit = async (event) => {
    event.preventDefault();

    const validationErrors = validatePredictionForm(form);
    if (Object.keys(validationErrors).length > 0) {
      setFieldErrors(validationErrors);
      return;
    }

    if (submitAbortRef.current) {
      submitAbortRef.current.abort();
    }
    const controller = new AbortController();
    submitAbortRef.current = controller;

    const payload = {
      repository: form.repository.trim(),
      branch: form.branch.trim(),
      workflow_name: form.workflow_name.trim(),
      event: form.event.trim(),
    };

    if (form.created_at.trim()) {
      payload.created_at = new Date(form.created_at.trim()).toISOString();
    }
    if (form.run_id.trim()) {
      payload.run_id = Number(form.run_id.trim());
    }

    setSubmitting(true);
    setSubmitError(null);

    try {
      const result = await predictPipelineFailure(payload, { signal: controller.signal });
      if (controller.signal.aborted) {
        return;
      }
      setPrediction(result);
    } catch (error) {
      if (isAbortError(error) || controller.signal.aborted) {
        return;
      }

      let errorMessage = error?.message || 'Prediction request failed.';
      if (error?.isNetworkError) {
        errorMessage =
          'Network error: unable to reach the prediction endpoint. Verify backend connectivity.';
      } else if (error?.status === 404) {
        errorMessage =
          'Prediction endpoint returned HTTP 404. Member 2 ML prediction service is unavailable on this backend.';
      } else if (error?.status === 503) {
        errorMessage =
          error.message || 'Prediction model is unavailable (HTTP 503).';
      }

      setSubmitError(errorMessage);
    } finally {
      if (!controller.signal.aborted) {
        setSubmitting(false);
      }
    }
  };

  if (isStatusLoading) {
    return (
      <div className="panel state-container">
        <div className="spinner"></div>
        <p>Checking ML prediction model status...</p>
      </div>
    );
  }

  if (statusInfo?.kind === 'network_error') {
    return (
      <div className="panel">
        <div className="panel-title">
          <span>Pre-Run Failure Prediction (ML)</span>
          <span className={`badge ${statusInfo.badgeVariant}`}>{statusInfo.badgeLabel}</span>
        </div>
        <div className="state-container">
          <div className="empty-icon" style={{ color: 'var(--accent-red)' }}>⚠️</div>
          <p className="status-error-text">{statusInfo.message}</p>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleRetryStatus}
            style={{ marginTop: '1rem' }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!statusInfo || statusInfo.kind !== 'ready') {
    return (
      <div className="panel">
        <div className="panel-title">
          <span>Pre-Run Failure Prediction (ML)</span>
          <span className={`badge ${statusInfo?.badgeVariant || 'danger'}`}>
            {statusInfo?.badgeLabel || 'Unavailable'}
          </span>
        </div>
        <div className="state-container">
          <div className="empty-icon">🧠</div>
          <p style={{ margin: '0 0 0.4rem 0', fontWeight: 600, color: 'var(--text-primary)' }}>
            Failure Prediction Model Unavailable
          </p>
          <p className="status-error-text">
            {statusInfo?.message || 'Prediction service is unavailable.'}
          </p>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleRetryStatus}
            style={{ marginTop: '1rem' }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const evaluatedEntries =
    prediction?.features_evaluated && typeof prediction.features_evaluated === 'object'
      ? Object.entries(prediction.features_evaluated)
      : [];

  return (
    <div className="panel">
      <div className="panel-title">
        <span>Pre-Run Failure Prediction (ML)</span>
        <div className="opt-badges">
          <span className="badge success">{statusInfo.badgeLabel}</span>
          <button type="button" className="btn-secondary" onClick={handleRetryStatus}>
            Refresh Status
          </button>
        </div>
      </div>

      <div className="prediction-notice">
        <strong>Pre-run risk estimate:</strong> Evaluates failure probability before execution starts
        using trigger metadata only. This does not represent an actual pipeline execution outcome or
        predict a failing stage.
        {(statusInfo.data?.model_type || statusInfo.data?.model_version) && (
          <div style={{ marginTop: '0.35rem', color: 'var(--text-primary)' }}>
            Active model: <strong>{statusInfo.data.model_type || 'Unknown'}</strong>
            {statusInfo.data.model_version ? ` (v${statusInfo.data.model_version})` : ''}
          </div>
        )}
      </div>

      <form onSubmit={handleSubmit} noValidate>
        <div className="prediction-form-grid">
          <div className="form-field">
            <label className="form-label" htmlFor="pred-repository">
              Repository *
            </label>
            <input
              id="pred-repository"
              name="repository"
              type="text"
              className="form-input"
              placeholder="owner/repo"
              value={form.repository}
              onChange={handleFieldChange}
              disabled={submitting}
              required
            />
            {fieldErrors.repository && (
              <span className="field-error">{fieldErrors.repository}</span>
            )}
          </div>

          <div className="form-field">
            <label className="form-label" htmlFor="pred-branch">
              Branch *
            </label>
            <input
              id="pred-branch"
              name="branch"
              type="text"
              className="form-input"
              placeholder="main"
              value={form.branch}
              onChange={handleFieldChange}
              disabled={submitting}
              required
            />
            {fieldErrors.branch && <span className="field-error">{fieldErrors.branch}</span>}
          </div>

          <div className="form-field">
            <label className="form-label" htmlFor="pred-workflow">
              Workflow Name *
            </label>
            <input
              id="pred-workflow"
              name="workflow_name"
              type="text"
              className="form-input"
              placeholder="CI Pipeline"
              value={form.workflow_name}
              onChange={handleFieldChange}
              disabled={submitting}
              required
            />
            {fieldErrors.workflow_name && (
              <span className="field-error">{fieldErrors.workflow_name}</span>
            )}
          </div>

          <div className="form-field">
            <label className="form-label" htmlFor="pred-event">
              Trigger Event *
            </label>
            <input
              id="pred-event"
              name="event"
              type="text"
              list="prediction-event-options"
              className="form-input"
              placeholder="push"
              value={form.event}
              onChange={handleFieldChange}
              disabled={submitting}
              required
            />
            <datalist id="prediction-event-options">
              {EVENT_PRESETS.map((preset) => (
                <option key={preset} value={preset} />
              ))}
            </datalist>
            {fieldErrors.event && <span className="field-error">{fieldErrors.event}</span>}
          </div>

          <div className="form-field">
            <label className="form-label" htmlFor="pred-created-at">
              Trigger Timestamp (Optional)
            </label>
            <input
              id="pred-created-at"
              name="created_at"
              type="datetime-local"
              className="form-input"
              value={form.created_at}
              onChange={handleFieldChange}
              disabled={submitting}
            />
            {fieldErrors.created_at && (
              <span className="field-error">{fieldErrors.created_at}</span>
            )}
          </div>

          <div className="form-field">
            <label className="form-label" htmlFor="pred-run-id">
              Run ID Correlation (Optional)
            </label>
            <input
              id="pred-run-id"
              name="run_id"
              type="number"
              min="1"
              step="1"
              className="form-input"
              placeholder="Optional integer ID"
              value={form.run_id}
              onChange={handleFieldChange}
              disabled={submitting}
            />
            {fieldErrors.run_id && <span className="field-error">{fieldErrors.run_id}</span>}
          </div>
        </div>

        <div className="form-actions">
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? 'Evaluating Risk...' : 'Predict Failure Risk'}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleResetForm}
            disabled={submitting}
          >
            Reset
          </button>
        </div>
      </form>

      {submitError && (
        <div className="info-banner" style={{ marginTop: '1rem', borderColor: 'rgba(239, 68, 68, 0.4)', color: '#f87171', background: 'rgba(239, 68, 68, 0.1)' }}>
          {submitError}
        </div>
      )}

      {prediction && (
        <div className="prediction-result-card">
          <div className="opt-header">
            <span className="opt-title">
              Pre-Run Prediction:{' '}
              {prediction.is_failure_predicted ? 'Failure Risk Flagged' : 'Low Failure Risk'}
              {' '}
              (Class {prediction.predicted_class})
            </span>
            <span className={`badge ${getRiskBadgeVariant(prediction.risk_level)} preserve-case`}>
              risk: {prediction.risk_level}
            </span>
          </div>

          <div className="prediction-metrics-grid">
            <div className="opt-summary-card">
              <span className="stat-title" style={{ marginBottom: 0 }}>
                Failure Probability
              </span>
              <span
                style={{
                  fontSize: '1.35rem',
                  fontWeight: 700,
                  color: getProbabilityColor(prediction.failure_probability),
                }}
              >
                {formatPercentage(prediction.failure_probability, { isRatio: true, decimals: 1 })}
              </span>
            </div>

            <div className="opt-summary-card">
              <span className="stat-title" style={{ marginBottom: 0 }}>
                Success Probability
              </span>
              <span style={{ fontSize: '1.35rem', fontWeight: 700, color: 'var(--accent-green)' }}>
                {formatPercentage(prediction.success_probability, { isRatio: true, decimals: 1 })}
              </span>
            </div>

            <div className="opt-summary-card">
              <span className="stat-title" style={{ marginBottom: 0 }}>
                Confidence Score
              </span>
              <span style={{ fontSize: '1.35rem', fontWeight: 700, color: 'var(--accent-blue)' }}>
                {formatPercentage(prediction.confidence_score, { isRatio: true, decimals: 1 })}
              </span>
            </div>
          </div>

          <div className="kv-list">
            <div className="kv-row">
              <span className="kv-label">Predicted Outcome Flag</span>
              <span className="kv-value">
                {prediction.is_failure_predicted
                  ? 'Failure Predicted (is_failure_predicted = true)'
                  : 'Success Predicted (is_failure_predicted = false)'}
              </span>
            </div>
            {prediction.model_metadata && (
              <>
                <div className="kv-row">
                  <span className="kv-label">Model Type</span>
                  <span className="kv-value">{prediction.model_metadata.model_type || 'N/A'}</span>
                </div>
                <div className="kv-row">
                  <span className="kv-label">Model Version</span>
                  <span className="kv-value">{prediction.model_metadata.model_version || 'N/A'}</span>
                </div>
                {prediction.model_metadata.trained_at && (
                  <div className="kv-row">
                    <span className="kv-label">Trained At</span>
                    <span className="kv-value">
                      {formatTimestamp(prediction.model_metadata.trained_at)}
                    </span>
                  </div>
                )}
              </>
            )}
          </div>

          {evaluatedEntries.length > 0 && (
            <>
              <h3 className="section-subtitle">Features Evaluated</h3>
              <div className="kv-list">
                {evaluatedEntries.map(([key, val]) => (
                  <div key={key} className="kv-row">
                    <span className="kv-label">{formatTokenLabel(key)}</span>
                    <span className="kv-value mono-text">{String(val)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default FailurePrediction;
