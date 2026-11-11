import React, { useState, useEffect } from 'react';
import Overview from './components/Overview';
import PipelineHistory from './components/PipelineHistory';
import FailurePrediction from './components/FailurePrediction';
import Optimization from './components/Optimization';
import PipelineDetails from './components/PipelineDetails';

const Dashboard = () => {
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Simulate dashboard initialization
    const timer = setTimeout(() => setLoading(false), 800);
    return () => clearTimeout(timer);
  }, []);

  if (loading) {
    return (
      <div className="dashboard-scroll-area state-container" style={{ height: '100%' }}>
        <div className="spinner"></div>
        <h2>Loading Dashboard</h2>
        <p>Fetching latest CI/CD intelligence data...</p>
      </div>
    );
  }

  return (
    <div className="dashboard-scroll-area">
      <div className="dashboard-header" style={{ marginBottom: '1.5rem', borderBottom: 'none' }}>
        <h1 style={{ fontSize: '1.5rem' }}>Dashboard</h1>
      </div>
      
      <Overview />
      
      <div className="grid-main">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          <FailurePrediction />
          <Optimization />
          <PipelineHistory />
        </div>
        <div>
          <PipelineDetails />
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
