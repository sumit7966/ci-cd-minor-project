import React, { useState, useEffect } from 'react';
import { optimizationData } from '../mockData';

const Optimization = () => {
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Simulate loading state
    const timer = setTimeout(() => setLoading(false), 1000);
    return () => clearTimeout(timer);
  }, []);

  const getPriorityBadge = (priority) => {
    if (priority === 'High') return <span className="badge danger">{priority}</span>;
    if (priority === 'Medium') return <span className="badge warning">{priority}</span>;
    return <span className="badge info">{priority}</span>;
  };

  if (loading) {
    return (
      <div className="panel state-container">
        <div className="spinner"></div>
        <p>Analyzing pipelines for bottlenecks...</p>
      </div>
    );
  }

  if (!optimizationData || optimizationData.length === 0) {
    return (
      <div className="panel state-container">
        <div className="empty-icon">✨</div>
        <p>No optimization recommendations right now. Pipelines are running smoothly!</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h2 className="panel-title">Optimization Recommendations</h2>
      <div className="optimization-cards">
        {optimizationData.map((opt, idx) => (
          <div key={idx} className="opt-card">
            <div className="opt-header">
              <span className="opt-title">Bottleneck: {opt.bottleneck}</span>
              {getPriorityBadge(opt.priority)}
            </div>
            <p className="opt-desc">{opt.recommendations}</p>
            <div className="opt-meta">
              <span>Avg Duration: {opt.duration}</span>
              <span style={{ color: 'var(--accent-warning)' }}>Failure Freq: {opt.frequency}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default Optimization;
