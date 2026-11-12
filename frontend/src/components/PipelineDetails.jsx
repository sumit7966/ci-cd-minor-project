import React, { useState, useEffect } from 'react';
import { ApiError, getPipelineById, isAbortError } from '../services/api';
import {
  formatCommitSha,
  formatDuration,
  formatTimestamp,
  formatTokenLabel,
  getRunStatusMeta,
} from '../utils/formatters';

const PipelineDetails = ({ runId = null }) => {
  const [refreshCount, setRefreshCount] = useState(0);
  const requestKey = runId !== null && runId !== undefined ? `${runId}:${refreshCount}` : null;

  const [fetchState, setFetchState] = useState({
    resolvedKey: null,
    run: null,
    error: null,
  });

  useEffect(() => {
    if (runId === null || runId === undefined) {
      return undefined;
    }

    const controller = new AbortController();
    let isActive = true;
    const currentKey = `${runId}:${refreshCount}`;

    getPipelineById(runId, { signal: controller.signal })
      .then((record) => {
        if (!isActive || controller.signal.aborted) {
          return;
        }
        setFetchState({ resolvedKey: currentKey, run: record, error: null });
      })
      .catch((error) => {
        if (!isActive || isAbortError(error) || controller.signal.aborted) {
          return;
        }
        setFetchState({ resolvedKey: currentKey, run: null, error });
      });

    return () => {
      isActive = false;
      controller.abort();
    };
  }, [runId, refreshCount]);

  const handleRetry = () => {
    setRefreshCount((prev) => prev + 1);
  };

  if (requestKey === null) {
    return (
      <div className="panel state-container" style={{ height: '100%' }}>
        <div className="empty-icon">🖱️</div>
        <p>Select a pipeline run from the history table to view telemetry details.</p>
      </div>
    );
  }

  const isLoading = fetchState.resolvedKey !== requestKey;
  const run = fetchState.resolvedKey === requestKey ? fetchState.run : null;
  const error = fetchState.resolvedKey === requestKey ? fetchState.error : null;

  if (isLoading) {
    return (
      <div className="panel state-container" style={{ height: '100%' }}>
        <div className="spinner"></div>
        <p>Loading details for run #{runId}...</p>
      </div>
    );
  }

  if (error) {
    const isNotFound = error instanceof ApiError && error.status === 404;
    return (
      <div className="panel state-container" style={{ height: '100%' }}>
        <div className="empty-icon" style={{ color: isNotFound ? 'var(--accent-warning)' : 'var(--accent-red)' }}>
          {isNotFound ? '🔍' : '⚠️'}
        </div>
        <p style={{ fontWeight: 600, margin: '0 0 0.4rem 0', color: 'var(--text-primary)' }}>
          {isNotFound ? `Pipeline Run #${runId} Not Found` : 'Unable to Load Pipeline Details'}
        </p>
        <p className="status-error-text">{error.message}</p>
        <button
          type="button"
          className="btn-secondary"
          onClick={handleRetry}
          style={{ marginTop: '1rem' }}
        >
          Retry
        </button>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="panel state-container" style={{ height: '100%' }}>
        <div className="empty-icon">🔍</div>
        <p>No details returned for pipeline run #{runId}.</p>
      </div>
    );
  }

  const statusMeta = getRunStatusMeta(run.status, run.conclusion);
  const hasFailureType = Boolean(run.failure_type && run.failure_type !== 'none');
  const hasFailureTelemetry = Boolean(run.failed_stage || hasFailureType);

  return (
    <div className="panel" style={{ height: '100%' }}>
      <div className="panel-title">
        <span>Pipeline Details</span>
        <button type="button" className="btn-secondary" onClick={handleRetry}>
          Refresh
        </button>
      </div>

      <div style={{ marginBottom: '1rem' }}>
        <span className={`badge ${statusMeta.variant}`}>{statusMeta.label}</span>
      </div>

      <div className="kv-list">
        <div className="kv-row">
          <span className="kv-label">Run ID</span>
          <span className="kv-value mono-text" style={{ fontWeight: 700 }}>
            #{run.run_id}
          </span>
        </div>
        <div className="kv-row">
          <span className="kv-label">Repository</span>
          <span className="kv-value mono-text">{run.repository || 'N/A'}</span>
        </div>
        <div className="kv-row">
          <span className="kv-label">Workflow</span>
          <span className="kv-value">{run.workflow_name || 'N/A'}</span>
        </div>
        <div className="kv-row">
          <span className="kv-label">Branch</span>
          <span className="kv-value" style={{ color: 'var(--accent-blue)' }}>
            {run.branch || 'N/A'}
          </span>
        </div>
        <div className="kv-row">
          <span className="kv-label">Commit SHA</span>
          <span className="kv-value mono-text" title={run.commit_sha || undefined}>
            {formatCommitSha(run.commit_sha)}
          </span>
        </div>
        <div className="kv-row">
          <span className="kv-label">Event</span>
          <span className="kv-value">{formatTokenLabel(run.event, 'N/A')}</span>
        </div>
        <div className="kv-row">
          <span className="kv-label">Status</span>
          <span className="kv-value">{formatTokenLabel(run.status, 'N/A')}</span>
        </div>
        <div className="kv-row">
          <span className="kv-label">Conclusion</span>
          <span className="kv-value">
            {run.conclusion ? formatTokenLabel(run.conclusion, 'N/A') : 'N/A'}
          </span>
        </div>
        <div className="kv-row">
          <span className="kv-label">Duration</span>
          <span className="kv-value">{formatDuration(run.duration_seconds, 'N/A')}</span>
        </div>
        <div className="kv-row">
          <span className="kv-label">Created At</span>
          <span className="kv-value">{formatTimestamp(run.created_at, 'N/A')}</span>
        </div>
      </div>

      <h3 className="section-subtitle">Failure Telemetry</h3>
      <div className="stage-list">
        <div
          className="stage-item"
          style={{
            borderLeftColor: run.failed_stage ? 'var(--accent-red)' : 'var(--panel-border)',
          }}
        >
          <span style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Failed Stage</span>
          <span style={{ fontWeight: 500 }}>
            {run.failed_stage ? formatTokenLabel(run.failed_stage, 'N/A') : 'None'}
          </span>
        </div>
        <div
          className="stage-item"
          style={{
            borderLeftColor: hasFailureType ? 'var(--accent-red)' : 'var(--panel-border)',
          }}
        >
          <span style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>Failure Type</span>
          <span style={{ fontWeight: 500 }}>
            {run.failure_type ? formatTokenLabel(run.failure_type, 'None') : 'None'}
          </span>
        </div>
      </div>
      {!hasFailureTelemetry && (
        <p className="muted-note">
          No failure stage or error category was recorded for this run.
        </p>
      )}
    </div>
  );
};

export default PipelineDetails;
