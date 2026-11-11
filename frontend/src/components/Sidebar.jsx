import React from 'react';

const Sidebar = () => {
  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <h2>CI/CD Platform</h2>
      </div>
      <div className="sidebar-nav">
        <div className="nav-item active">
          <span>📊</span> Dashboard
        </div>
        <div className="nav-item">
          <span>⚙️</span> Pipelines
        </div>
        <div className="nav-item">
          <span>🧠</span> ML Predictions
        </div>
        <div className="nav-item">
          <span>⚡</span> Optimizations
        </div>
        <div className="nav-item">
          <span>🔧</span> Settings
        </div>
      </div>
    </div>
  );
};

export default Sidebar;
