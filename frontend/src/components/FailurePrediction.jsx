import React, { useState, useEffect } from 'react';
import { failurePredictions } from '../mockData';

const FailurePrediction = () => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    // Simulate loading
    const timer = setTimeout(() => {
      // randomly simulate an error state sometimes? No, let's just make it load normally, 
      // but have the error state handler in place for demonstration.
      setLoading(false);
    }, 1500);
    return () => clearTimeout(timer);
  }, []);

  const getRiskBadge = (status) => {
    if (status === 'High Risk') return <span className="badge danger">{status}</span>;
    if (status === 'Medium Risk') return <span className="badge warning">{status}</span>;
    return <span className="badge success">{status}</span>;
  };

  const getProbabilityColor = (probString) => {
    const val = parseInt(probString);
    if (val > 70) return 'var(--accent-red)';
    if (val > 40) return 'var(--accent-warning)';
    return 'var(--accent-green)';
  };

  if (loading) {
    return (
      <div className="panel state-container">
        <div className="spinner"></div>
        <p>Loading ML predictions...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="panel state-container">
        <div className="empty-icon" style={{ color: 'var(--accent-red)' }}>⚠️</div>
        <p style={{ color: 'var(--accent-red)' }}>{error}</p>
        <button style={{ marginTop: '1rem', padding: '0.5rem 1rem', background: 'var(--panel-border)', border: 'none', color: 'white', borderRadius: '4px', cursor: 'pointer' }}>Retry</button>
      </div>
    );
  }

  if (!failurePredictions || failurePredictions.length === 0) {
    return (
      <div className="panel state-container">
        <div className="empty-icon">🛡️</div>
        <p>No high-risk pipelines predicted.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2 className="panel-title">Active Runs Failure Prediction (ML)</h2>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Run ID</th>
              <th>Branch</th>
              <th>Probability</th>
              <th>Predicted Stage</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {failurePredictions.map((pred) => (
              <tr key={pred.id}>
                <td style={{ fontWeight: 500, color: 'var(--accent-blue)' }}>{pred.id}</td>
                <td>{pred.branch}</td>
                <td style={{ fontWeight: 'bold', color: getProbabilityColor(pred.failureProbability) }}>
                  {pred.failureProbability}
                </td>
                <td>{pred.predictedStage}</td>
                <td>{getRiskBadge(pred.status)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default FailurePrediction;
