import React, { useState, useEffect } from 'react';
import { getOptimizationSummary, isAbortError } from '../services/api';
import { formatDuration, formatPercentage } from '../utils/formatters';

function getPriorityBadgeVariant(priority) {
  const normalized = typeof priority === 'string' ? priority.trim().toLowerCase() : '';
  if (normalized === 'high') {
    return 'danger';
  }
  if (normalized === 'medium') {
    return 'warning';
  }
  return 'info';
}

const Optimization = ({ repository }) => {
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

    getOptimizationSummary({ repository }, { signal: controller.signal })
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
  const data = fetchState.resolvedKey === requestKey ? fetchState.data : null;
  const error = fetchState.resolvedKey === requestKey ? fetchState.error : null;

  if (isLoading) {
    return (
      <div className="panel state-container">
        <div className="spinner"></div>
        <p>Analyzing pipelines for bottlenecks and recommendations...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel state-container">
        <div className="empty-icon" style={{ color: 'var(--accent-red)' }}>⚠️</div>
        <p className="status-error-text">{error.message || 'Failed to load optimization data.'}</p>
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

  const bottlenecks = data?.bottlenecks || {};
  const recommendations = Array.isArray(data?.recommendations) ? data.recommendations : [];
  const stageMetricEntries =
    bottlenecks.stage_metrics && typeof bottlenecks.stage_metrics === 'object'
      ? Object.entries(bottlenecks.stage_metrics)
      : [];
  const potentialBottlenecks = Array.isArray(bottlenecks.potential_bottlenecks)
    ? bottlenecks.potential_bottlenecks
    : [];

  const isCompletelyEmpty =
    stageMetricEntries.length === 0 &&
    potentialBottlenecks.length === 0 &&
    recommendations.length === 0 &&
    bottlenecks.average_pipeline_duration === 0;

  if (!data || isCompletelyEmpty) {
    return (
      <div className="panel state-container">
        <div className="empty-icon">✨</div>
        <p>{bottlenecks.message || 'No pipeline runs available for optimization analysis yet.'}</p>
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
    <div className="panel">
      <div className="panel-title">
        <span>Optimization & Bottlenecks</span>
        <button type="button" className="btn-secondary" onClick={handleRetry}>
          Refresh
        </button>
      </div>

      <div className="opt-summary-grid">
        <div className="opt-summary-card">
          <span className="stat-title" style={{ marginBottom: 0 }}>
            Average Pipeline Duration
          </span>
          <span style={{ fontSize: '1.35rem', fontWeight: 700, color: 'var(--accent-purple)' }}>
            {formatDuration(bottlenecks.average_pipeline_duration, 'N/A')}
          </span>
        </div>
        <div className="opt-summary-card">
          <span className="stat-title" style={{ marginBottom: 0 }}>
            Slowest Stage
          </span>
          <span style={{ fontSize: '1.1rem', fontWeight: 600, color: 'var(--text-primary)' }}>
            {bottlenecks.slowest_stage || 'Unavailable'}
          </span>
        </div>
        {bottlenecks.telemetry_coverage && (
          <div className="opt-summary-card">
            <span className="stat-title" style={{ marginBottom: 0 }}>
              Telemetry Coverage
            </span>
            <span style={{ fontSize: '1.1rem', fontWeight: 600, color: 'var(--accent-blue)' }}>
              {bottlenecks.telemetry_coverage.runs_with_stage_telemetry} / {bottlenecks.telemetry_coverage.total_runs_analyzed} runs
            </span>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
              {bottlenecks.telemetry_coverage.stage_executions_analyzed} stage executions analyzed
            </span>
          </div>
        )}
      </div>

      {stageMetricEntries.length > 0 ? (
        <>
          <h3 className="section-subtitle">Stage Metrics</h3>
          <div className="stage-metrics-grid">
            {stageMetricEntries.map(([stageName, metrics]) => (
              <div key={stageName} className="opt-card">
                <div className="opt-header">
                  <span className="opt-title">{stageName}</span>
                  <span className="badge info">
                    {formatPercentage(metrics.stage_failure_rate, { isRatio: true, decimals: 1 })} fail
                  </span>
                </div>
                <div className="opt-meta" style={{ flexWrap: 'wrap' }}>
                  <span>Avg: {formatDuration(metrics.average_stage_duration, 'N/A')}</span>
                  <span>Max: {formatDuration(metrics.maximum_stage_duration, 'N/A')}</span>
                  <span>Executed: {metrics.total_executions ?? 'N/A'}</span>
                  {typeof metrics.skipped_count === 'number' && metrics.skipped_count > 0 && (
                    <span>Skipped: {metrics.skipped_count}</span>
                  )}
                  {typeof metrics.incomplete_count === 'number' && metrics.incomplete_count > 0 && (
                    <span>Incomplete: {metrics.incomplete_count}</span>
                  )}
                  <span>Failures: {metrics.stage_failure_count ?? 0}</span>
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        bottlenecks.message && <div className="info-banner">{bottlenecks.message}</div>
      )}

      {potentialBottlenecks.length > 0 && (
        <>
          <h3 className="section-subtitle">Potential Bottlenecks</h3>
          <div className="optimization-cards" style={{ marginBottom: '1rem' }}>
            {potentialBottlenecks.map((item, idx) => (
              <div key={`${item.stage}-${idx}`} className="opt-card">
                <div className="opt-header">
                  <span className="opt-title">Stage: {item.stage}</span>
                  <span className="badge warning">Bottleneck</span>
                </div>
                {Array.isArray(item.reasons) && item.reasons.length > 0 && (
                  <p className="opt-desc" style={{ marginBottom: 0 }}>
                    {item.reasons.join(' ')}
                  </p>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      <h3 className="section-subtitle">Recommendations</h3>
      {recommendations.length === 0 ? (
        <div className="opt-card">
          <p className="muted-note" style={{ margin: 0 }}>
            No optimization recommendations right now for the analyzed runs.
          </p>
        </div>
      ) : (
        <div className="optimization-cards">
          {recommendations.map((rec, idx) => {
            const priorityVariant = getPriorityBadgeVariant(rec.priority);
            return (
              <div key={`${rec.title}-${rec.affected_stage || 'pipeline'}-${idx}`} className="opt-card">
                <div className="opt-header">
                  <span className="opt-title">{rec.title}</span>
                  <div className="opt-badges">
                    <span className={`badge ${priorityVariant} preserve-case`}>
                      {rec.priority || 'unknown'}
                    </span>
                    <span className="badge info preserve-case">
                      {rec.recommendation_type || 'general'}
                    </span>
                  </div>
                </div>
                <p className="opt-desc">{rec.description}</p>
                {rec.reason && (
                  <p className="opt-reason">
                    <strong>Reason:</strong> {rec.reason}
                  </p>
                )}
                <div className="opt-meta">
                  <span>
                    Affected Stage: {rec.affected_stage ? rec.affected_stage : 'Pipeline-wide'}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};

export default Optimization;
