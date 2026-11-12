import React, { useState, useEffect } from 'react';
import Overview from './components/Overview';
import PipelineHistory from './components/PipelineHistory';
import FailurePrediction from './components/FailurePrediction';
import Optimization from './components/Optimization';
import PipelineDetails from './components/PipelineDetails';
import {
  API_BASE_URL,
  getHealth,
  getPredictionStatus,
  isAbortError,
} from './services/api';
import { formatTimestamp, formatTokenLabel } from './utils/formatters';

function resolveHealthState(result) {
  if (result.status === 'fulfilled') {
    const data = result.value || {};
    const isHealthy = data.status === 'healthy';
    return {
      state: isHealthy ? 'healthy' : 'degraded',
      badgeLabel: formatTokenLabel(data.status, 'Unknown'),
      badgeVariant: isHealthy ? 'success' : 'warning',
      status: data.status ?? 'unknown',
      database: data.database ?? 'unknown',
      errorMessage: null,
    };
  }

  return {
    state: 'unavailable',
    badgeLabel: 'Unavailable',
    badgeVariant: 'danger',
    status: 'unavailable',
    database: null,
    errorMessage: result.reason?.message || 'Unable to reach backend health endpoint.',
  };
}

function resolvePredictionState(result) {
  if (result.status === 'fulfilled') {
    const data = result.value || {};
    const isReady = data.status === 'ready' && data.model_available === true;
    return {
      state: isReady ? 'ready' : 'unavailable',
      badgeLabel: isReady ? 'Ready' : 'Unavailable',
      badgeVariant: isReady ? 'success' : 'warning',
      status: data.status ?? (isReady ? 'ready' : 'unavailable'),
      modelAvailable: Boolean(data.model_available),
      modelVersion: data.model_version ?? null,
      modelType: data.model_type ?? null,
      trainedAt: data.trained_at ?? null,
      artifactSha256: data.artifact_sha256 ?? null,
      httpStatus: 200,
      errorMessage: isReady ? null : 'Prediction model artifact is not loaded on the backend.',
    };
  }

  const err = result.reason;
  const httpStatus = err?.status || null;
  let errorMessage = err?.message || 'Prediction status endpoint is unavailable.';

  if (httpStatus === 404) {
    errorMessage = 'Prediction service endpoint returned HTTP 404 (not integrated in active backend).';
  } else if (httpStatus === 503) {
    errorMessage = err?.message || 'Prediction model is currently unavailable (HTTP 503).';
  }

  return {
    state: 'unavailable',
    badgeLabel: 'Unavailable',
    badgeVariant: 'danger',
    status: 'unavailable',
    modelAvailable: false,
    modelVersion: null,
    modelType: null,
    trainedAt: null,
    artifactSha256: null,
    httpStatus,
    errorMessage,
  };
}

const SystemStatusPanel = () => {
  const [loading, setLoading] = useState(true);
  const [healthInfo, setHealthInfo] = useState(null);
  const [predictionInfo, setPredictionInfo] = useState(null);
  const [refreshCount, setRefreshCount] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let isActive = true;

    Promise.allSettled([
      getHealth({ signal: controller.signal }),
      getPredictionStatus({ signal: controller.signal }),
    ])
      .then(([healthResult, predictionResult]) => {
        if (!isActive || controller.signal.aborted) {
          return;
        }

        const healthAborted =
          healthResult.status === 'rejected' && isAbortError(healthResult.reason);
        const predictionAborted =
          predictionResult.status === 'rejected' && isAbortError(predictionResult.reason);

        if (healthAborted || predictionAborted) {
          return;
        }

        setHealthInfo(resolveHealthState(healthResult));
        setPredictionInfo(resolvePredictionState(predictionResult));
        setLoading(false);
      })
      .catch((error) => {
        if (!isActive || isAbortError(error) || controller.signal.aborted) {
          return;
        }
        setHealthInfo({
          state: 'unavailable',
          badgeLabel: 'Unavailable',
          badgeVariant: 'danger',
          status: 'unavailable',
          database: null,
          errorMessage: error?.message || 'Failed to query system status.',
        });
        setPredictionInfo({
          state: 'unavailable',
          badgeLabel: 'Unavailable',
          badgeVariant: 'danger',
          status: 'unavailable',
          modelAvailable: false,
          modelVersion: null,
          modelType: null,
          trainedAt: null,
          artifactSha256: null,
          httpStatus: error?.status || null,
          errorMessage: error?.message || 'Failed to query prediction service status.',
        });
        setLoading(false);
      });

    return () => {
      isActive = false;
      controller.abort();
    };
  }, [refreshCount]);

  const handleRetry = () => {
    setLoading(true);
    setRefreshCount((prev) => prev + 1);
  };

  return (
    <div className="panel">
      <div className="panel-title">
        <span>System Status</span>
        <button
          type="button"
          className="btn-secondary"
          onClick={handleRetry}
          disabled={loading}
        >
          {loading ? 'Checking...' : 'Retry'}
        </button>
      </div>

      {loading && !healthInfo && !predictionInfo ? (
        <div className="state-container">
          <div className="spinner"></div>
          <p>Checking backend health and ML prediction service status...</p>
        </div>
      ) : (
        <div className="status-grid">
          <div className="opt-card">
            <div className="opt-header">
              <span className="opt-title">Backend & Database Health</span>
              {healthInfo && (
                <span className={`badge ${healthInfo.badgeVariant}`}>
                  {healthInfo.badgeLabel}
                </span>
              )}
            </div>

            {healthInfo && (
              <div className="kv-list">
                <div className="kv-row">
                  <span className="kv-label">Endpoint</span>
                  <span className="kv-value mono-text">GET /health</span>
                </div>
                <div className="kv-row">
                  <span className="kv-label">API Base URL</span>
                  <span className="kv-value mono-text">
                    {API_BASE_URL || 'Relative (/api proxy)'}
                  </span>
                </div>
                <div className="kv-row">
                  <span className="kv-label">Service Status</span>
                  <span className="kv-value">{healthInfo.status}</span>
                </div>
                {healthInfo.database !== null && (
                  <div className="kv-row">
                    <span className="kv-label">Database</span>
                    <span className="kv-value mono-text">{healthInfo.database}</span>
                  </div>
                )}
                {healthInfo.errorMessage && (
                  <p className="status-error-text">{healthInfo.errorMessage}</p>
                )}
              </div>
            )}
          </div>

          <div className="opt-card">
            <div className="opt-header">
              <span className="opt-title">ML Prediction Service</span>
              {predictionInfo && (
                <span className={`badge ${predictionInfo.badgeVariant}`}>
                  {predictionInfo.badgeLabel}
                </span>
              )}
            </div>

            {predictionInfo && (
              <div className="kv-list">
                <div className="kv-row">
                  <span className="kv-label">Endpoint</span>
                  <span className="kv-value mono-text">GET /api/predictions/status</span>
                </div>
                <div className="kv-row">
                  <span className="kv-label">Service Status</span>
                  <span className="kv-value">{predictionInfo.status}</span>
                </div>
                <div className="kv-row">
                  <span className="kv-label">Model Available</span>
                  <span className="kv-value">
                    {predictionInfo.modelAvailable ? 'Yes' : 'No'}
                  </span>
                </div>
                {predictionInfo.modelType && (
                  <div className="kv-row">
                    <span className="kv-label">Model Type</span>
                    <span className="kv-value">{predictionInfo.modelType}</span>
                  </div>
                )}
                {predictionInfo.modelVersion && (
                  <div className="kv-row">
                    <span className="kv-label">Model Version</span>
                    <span className="kv-value">{predictionInfo.modelVersion}</span>
                  </div>
                )}
                {predictionInfo.trainedAt && (
                  <div className="kv-row">
                    <span className="kv-label">Trained At</span>
                    <span className="kv-value" title={predictionInfo.trainedAt}>
                      {formatTimestamp(predictionInfo.trainedAt)}
                    </span>
                  </div>
                )}
                {predictionInfo.artifactSha256 && (
                  <div className="kv-row kv-row-stacked">
                    <span className="kv-label">Artifact SHA-256</span>
                    <span
                      className="kv-value mono-text sha256-text"
                      title={predictionInfo.artifactSha256}
                    >
                      {predictionInfo.artifactSha256}
                    </span>
                  </div>
                )}
                {predictionInfo.errorMessage && (
                  <p className="status-error-text">{predictionInfo.errorMessage}</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

const SECTION_HEADINGS = {
  dashboard: 'Dashboard',
  pipelines: 'Pipelines',
  predictions: 'ML Predictions',
  optimizations: 'Optimizations',
  settings: 'Settings',
};

const Dashboard = ({ activeSection = 'dashboard' }) => {
  const [selectedRunId, setSelectedRunId] = useState(null);
  const heading = SECTION_HEADINGS[activeSection] || 'Dashboard';

  return (
    <main className="dashboard-scroll-area">
      <div className="dashboard-header" style={{ marginBottom: '1.5rem', borderBottom: 'none' }}>
        <h1 style={{ fontSize: '1.5rem', margin: 0 }}>{heading}</h1>
      </div>

      {activeSection === 'dashboard' && (
        <>
          <Overview />
          <div className="grid-main">
            <div className="stack-column">
              <FailurePrediction />
              <Optimization />
              <PipelineHistory
                selectedRunId={selectedRunId}
                onSelectRun={setSelectedRunId}
              />
            </div>
            <div>
              <PipelineDetails runId={selectedRunId} />
            </div>
          </div>
        </>
      )}

      {activeSection === 'pipelines' && (
        <div className="grid-main">
          <div>
            <PipelineHistory
              selectedRunId={selectedRunId}
              onSelectRun={setSelectedRunId}
            />
          </div>
          <div>
            <PipelineDetails runId={selectedRunId} />
          </div>
        </div>
      )}

      {activeSection === 'predictions' && <FailurePrediction />}

      {activeSection === 'optimizations' && <Optimization />}

      {activeSection === 'settings' && <SystemStatusPanel />}
    </main>
  );
};

export default Dashboard;
