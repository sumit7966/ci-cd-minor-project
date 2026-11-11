import React, { useState, useEffect } from 'react';
import { pipelineDetails } from '../mockData';

const PipelineDetails = () => {
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Simulate loading state
    const timer = setTimeout(() => setLoading(false), 900);
    return () => clearTimeout(timer);
  }, []);

  const getStageStatus = (status) => {
    if (status === 'Success') {
      return <span style={{ color: 'var(--accent-green)', fontWeight: 'bold' }}>✓</span>;
    }
    if (status === 'Failed') {
      return <span style={{ color: 'var(--accent-red)', fontWeight: 'bold' }}>✗</span>;
    }
    return <span style={{ color: 'var(--text-secondary)' }}>-</span>;
  };

  if (loading) {
    return (
      <div className="panel state-container" style={{ height: '100%' }}>
        <div className="spinner"></div>
        <p>Loading pipeline details...</p>
      </div>
    );
  }

  if (!pipelineDetails) {
    return (
      <div className="panel state-container" style={{ height: '100%' }}>
        <div className="empty-icon">🖱️</div>
        <p>Select a pipeline from the history to view details.</p>
      </div>
    );
  }

  return (
    <div className="panel" style={{ height: '100%' }}>
      <h2 className="panel-title">Pipeline Details</h2>
      
      <div style={{ marginBottom: '1.5rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        <div>
          <span style={{ color: 'var(--text-secondary)' }}>Run ID: </span>
          <span style={{ color: 'var(--text-primary)', fontWeight: 'bold' }}>{pipelineDetails.id}</span>
        </div>
        <div>
          <span style={{ color: 'var(--text-secondary)' }}>Branch: </span>
          <span style={{ color: 'var(--accent-blue)' }}>{pipelineDetails.branch}</span>
        </div>
        <div>
          <span style={{ color: 'var(--text-secondary)' }}>Triggered By: </span>
          <span>{pipelineDetails.triggeredBy}</span>
        </div>
        <div style={{ marginTop: '0.5rem' }}>
          {pipelineDetails.status === 'Failed' 
            ? <span className="badge danger">Pipeline Failed</span> 
            : <span className="badge success">Pipeline Success</span>}
        </div>
      </div>

      <h3 style={{ fontSize: '0.9rem', textTransform: 'uppercase', marginBottom: '1rem', color: 'var(--text-secondary)' }}>Stages</h3>
      <div className="stage-list">
        {pipelineDetails.stages.map((stage, idx) => (
          <div key={idx} className="stage-item" style={{ borderLeftColor: stage.status === 'Failed' ? 'var(--accent-red)' : (stage.status === 'Success' ? 'var(--accent-green)' : 'transparent') }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
              {getStageStatus(stage.status)}
              <span style={{ fontWeight: 500 }}>{stage.name}</span>
            </div>
            <span style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>{stage.duration}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

export default PipelineDetails;
