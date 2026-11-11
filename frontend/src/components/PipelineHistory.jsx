import React, { useState, useEffect } from 'react';
import { pipelineHistory } from '../mockData';

const PipelineHistory = () => {
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Simulate loading state
    const timer = setTimeout(() => setLoading(false), 1200);
    return () => clearTimeout(timer);
  }, []);

  const getStatusBadge = (status) => {
    if (status === 'Success') return <span className="badge success">{status}</span>;
    if (status === 'Failed') return <span className="badge danger">{status}</span>;
    return <span className="badge info">{status}</span>;
  };
  
  // Fake utility to parse '12m 45s' to a percentage based on max 20m (for visualization)
  const getDurationPercentage = (durationStr) => {
    const minsMatch = durationStr.match(/(\d+)m/);
    if (!minsMatch) return 10; // default minimum
    const mins = parseInt(minsMatch[1]);
    const percentage = Math.min(Math.max((mins / 20) * 100, 5), 100);
    return percentage;
  };

  if (loading) {
    return (
      <div className="panel state-container">
        <div className="spinner"></div>
        <p>Loading pipeline history...</p>
      </div>
    );
  }

  if (!pipelineHistory || pipelineHistory.length === 0) {
    return (
      <div className="panel state-container">
        <div className="empty-icon">📭</div>
        <p>No pipeline history found.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2 className="panel-title">Pipeline History</h2>
      <div className="table-container">
        <table>
          <thead>
            <tr>
              <th>Run ID</th>
              <th>Branch</th>
              <th>Status</th>
              <th>Duration</th>
              <th>Failed Stage</th>
              <th>Timestamp</th>
            </tr>
          </thead>
          <tbody>
            {pipelineHistory.map((run) => (
              <tr key={run.id}>
                <td style={{ fontWeight: 500, color: 'var(--accent-blue)' }}>{run.id}</td>
                <td>{run.branch}</td>
                <td>{getStatusBadge(run.status)}</td>
                <td>
                  <div>{run.duration}</div>
                  <div className="duration-bar-bg">
                    <div className="duration-bar-fill" style={{ width: `${getDurationPercentage(run.duration)}%`, background: run.status === 'Failed' ? 'var(--accent-red)' : 'var(--accent-blue)' }}></div>
                  </div>
                </td>
                <td style={{ color: run.failedStage !== '-' ? 'var(--accent-red)' : 'inherit' }}>
                  {run.failedStage}
                </td>
                <td style={{ color: 'var(--text-secondary)' }}>{run.timestamp}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default PipelineHistory;
