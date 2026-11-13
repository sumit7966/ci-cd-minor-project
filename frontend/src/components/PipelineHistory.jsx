import React, { useState, useEffect } from 'react';
import { getPipelines, isAbortError } from '../services/api';
import { formatDuration, formatTimestamp, getRunStatusMeta } from '../utils/formatters';

const PAGE_SIZE = 10;

function computeDurationPercent(durationSeconds, maxDurationSeconds) {
  if (
    typeof durationSeconds !== 'number' ||
    !Number.isFinite(durationSeconds) ||
    durationSeconds < 0
  ) {
    return null;
  }

  if (durationSeconds === 0) {
    return 4;
  }

  const ceiling = maxDurationSeconds > 0 ? maxDurationSeconds : 60;
  return Math.min(Math.max((durationSeconds / ceiling) * 100, 6), 100);
}

const PipelineHistory = ({ selectedRunId = null, onSelectRun, repository }) => {
  const [page, setPage] = useState(0);
  const [refreshCount, setRefreshCount] = useState(0);
  const offset = page * PAGE_SIZE;
  const requestKey = `${repository || ''}:${page}:${refreshCount}`;

  const [fetchState, setFetchState] = useState({
    resolvedKey: null,
    runs: [],
    hasNextPage: false,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    let isActive = true;
    const currentOffset = page * PAGE_SIZE;
    const currentKey = `${repository || ''}:${page}:${refreshCount}`;

    getPipelines(
      {
        limit: PAGE_SIZE + 1,
        offset: currentOffset,
        repository,
      },
      { signal: controller.signal },
    )
      .then((records) => {
        if (!isActive || controller.signal.aborted) {
          return;
        }
        const safeRecords = Array.isArray(records) ? records : [];
        setFetchState({
          resolvedKey: currentKey,
          runs: safeRecords.slice(0, PAGE_SIZE),
          hasNextPage: safeRecords.length > PAGE_SIZE,
          error: null,
        });
      })
      .catch((error) => {
        if (!isActive || isAbortError(error) || controller.signal.aborted) {
          return;
        }
        setFetchState({
          resolvedKey: currentKey,
          runs: [],
          hasNextPage: false,
          error,
        });
      });

    return () => {
      isActive = false;
      controller.abort();
    };
  }, [page, refreshCount, repository]);

  const isLoading = fetchState.resolvedKey !== requestKey;
  const runs = fetchState.resolvedKey === requestKey ? fetchState.runs : [];
  const hasNextPage = fetchState.resolvedKey === requestKey ? fetchState.hasNextPage : false;
  const error = fetchState.resolvedKey === requestKey ? fetchState.error : null;

  const handleRetry = () => {
    setRefreshCount((prev) => prev + 1);
  };

  const handlePrevPage = () => {
    setPage((prev) => Math.max(0, prev - 1));
  };

  const handleNextPage = () => {
    if (hasNextPage) {
      setPage((prev) => prev + 1);
    }
  };

  if (isLoading) {
    return (
      <div className="panel state-container">
        <div className="spinner"></div>
        <p>Loading pipeline history...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel state-container">
        <div className="empty-icon" style={{ color: 'var(--accent-red)' }}>⚠️</div>
        <p className="status-error-text">{error.message || 'Failed to load pipeline history.'}</p>
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

  if (runs.length === 0) {
    return (
      <div className="panel state-container">
        <div className="empty-icon">📭</div>
        <p>{page > 0 ? 'No more pipeline runs on this page.' : 'No pipeline history found.'}</p>
        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '1rem' }}>
          {page > 0 && (
            <button type="button" className="btn-secondary" onClick={handlePrevPage}>
              Previous Page
            </button>
          )}
          <button type="button" className="btn-secondary" onClick={handleRetry}>
            Refresh
          </button>
        </div>
      </div>
    );
  }

  const numericDurations = runs
    .map((run) => run.duration_seconds)
    .filter((value) => typeof value === 'number' && Number.isFinite(value) && value > 0);
  const maxDurationSeconds = numericDurations.length > 0 ? Math.max(...numericDurations) : 60;

  return (
    <div className="panel">
      <div className="panel-title">
        <span>Pipeline History</span>
        <button type="button" className="btn-secondary" onClick={handleRetry}>
          Refresh
        </button>
      </div>

      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Run ID</th>
              <th>Repository</th>
              <th>Branch</th>
              <th>Workflow</th>
              <th>Status</th>
              <th>Duration</th>
              <th>Created At</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const statusMeta = getRunStatusMeta(run.status, run.conclusion);
              const isSelected = selectedRunId === run.run_id;
              const durationPct = computeDurationPercent(run.duration_seconds, maxDurationSeconds);
              const barColor =
                statusMeta.variant === 'danger'
                  ? 'var(--accent-red)'
                  : statusMeta.variant === 'warning'
                    ? 'var(--accent-warning)'
                    : 'var(--accent-blue)';

              return (
                <tr
                  key={run.run_id}
                  className={`table-row-clickable${isSelected ? ' table-row-selected' : ''}`}
                  onClick={() => onSelectRun?.(run.run_id)}
                >
                  <td>
                    <button
                      type="button"
                      className="run-id-btn"
                      onClick={(event) => {
                        event.stopPropagation();
                        onSelectRun?.(run.run_id);
                      }}
                      aria-pressed={isSelected}
                      aria-label={`Inspect pipeline run ${run.run_id}`}
                    >
                      #{run.run_id}
                    </button>
                  </td>
                  <td className="mono-text">{run.repository || 'N/A'}</td>
                  <td>{run.branch || 'N/A'}</td>
                  <td>{run.workflow_name || 'N/A'}</td>
                  <td>
                    <span className={`badge ${statusMeta.variant}`}>{statusMeta.label}</span>
                  </td>
                  <td>
                    <div>{formatDuration(run.duration_seconds, 'N/A')}</div>
                    {durationPct !== null && (
                      <div className="duration-bar-bg">
                        <div
                          className="duration-bar-fill"
                          style={{ width: `${durationPct}%`, background: barColor }}
                        ></div>
                      </div>
                    )}
                  </td>
                  <td style={{ color: 'var(--text-secondary)' }}>
                    {formatTimestamp(run.created_at, 'N/A')}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="pagination-bar">
        <span className="pagination-info">
          Page {page + 1} · Showing {offset + 1}–{offset + runs.length}
        </span>
        <div className="pagination-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={handlePrevPage}
            disabled={page === 0}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={handleNextPage}
            disabled={!hasNextPage}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
};

export default PipelineHistory;
