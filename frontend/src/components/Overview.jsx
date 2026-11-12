import React, { useState, useEffect } from 'react';
import { getAnalyticsSummary, isAbortError } from '../services/api';
import { formatDuration, formatPercentage } from '../utils/formatters';

const Overview = ({ repository }) => {
  const [refreshCount, setRefreshCount] = useState(0);
  const requestKey = `${repository || ''}:${refreshCount}`;
  const [fetchState, setFetchState] = useState({
    resolvedKey: null,
    data: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    let isActive = true;
    const currentKey = `${repository || ''}:${refreshCount}`;

    getAnalyticsSummary({ repository }, { signal: controller.signal })
      .then((summary) => {
        if (!isActive || controller.signal.aborted) {
          return;
        }
        setFetchState({ resolvedKey: currentKey, data: summary, error: null });
      })
      .catch((error) => {
        if (!isActive || isAbortError(error) || controller.signal.aborted) {
          return;
        }
        setFetchState({ resolvedKey: currentKey, data: null, error });
      });

    return () => {
      isActive = false;
      controller.abort();
    };
  }, [repository, refreshCount]);

  const handleRetry = () => {
    setRefreshCount((prev) => prev + 1);
  };

  const isLoading = fetchState.resolvedKey !== requestKey;
  const summary = fetchState.resolvedKey === requestKey ? fetchState.data : null;
  const error = fetchState.resolvedKey === requestKey ? fetchState.error : null;

  if (isLoading) {
    return (
      <div className="panel state-container" style={{ marginBottom: '1.5rem' }}>
        <div className="spinner"></div>
        <p>Loading overview analytics...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel state-container" style={{ marginBottom: '1.5rem' }}>
        <div className="empty-icon" style={{ color: 'var(--accent-red)' }}>⚠️</div>
        <p className="status-error-text">{error.message || 'Failed to load pipeline overview.'}</p>
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

  if (!summary || summary.total_runs === 0) {
    return (
      <div className="panel state-container" style={{ marginBottom: '1.5rem' }}>
        <div className="empty-icon">📊</div>
        <p>No pipeline analytics data available yet.</p>
        <button
          type="button"
          className="btn-secondary"
          onClick={handleRetry}
          style={{ marginTop: '1rem' }}
        >
          Refresh
        </button>
      </div>
    );
  }

  return (
    <div className="overview-section">
      <div className="grid-overview">
        <div className="panel stat-card">
          <span className="stat-title">Total Pipeline Runs</span>
          <span className="stat-value" style={{ color: 'var(--accent-blue)' }}>
            {summary.total_runs}
          </span>
        </div>
        <div className="panel stat-card">
          <span className="stat-title">Success Rate</span>
          <span className="stat-value" style={{ color: 'var(--accent-green)' }}>
            {formatPercentage(summary.success_rate_percent)}
          </span>
        </div>
        <div className="panel stat-card">
          <span className="stat-title">Failure Rate</span>
          <span className="stat-value" style={{ color: 'var(--accent-red)' }}>
            {formatPercentage(summary.failure_rate_percent)}
          </span>
        </div>
        <div className="panel stat-card">
          <span className="stat-title">Average Duration</span>
          <span className="stat-value" style={{ color: 'var(--accent-purple)' }}>
            {formatDuration(summary.average_duration_seconds, 'N/A')}
          </span>
        </div>
      </div>
      <div className="dataset-clarification-note">
        <span className="dataset-clarification-icon" aria-hidden="true">ℹ️</span>
        <span>
          Dataset contains controlled failure-injection runs for evaluating pipeline failure prediction and optimization.
        </span>
      </div>
    </div>
  );
};

export default Overview;
