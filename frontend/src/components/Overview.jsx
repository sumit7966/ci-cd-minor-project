import React from 'react';
import { overviewData } from '../mockData';

const Overview = () => {
  return (
    <div className="grid-overview">
      <div className="panel stat-card">
        <span className="stat-title">Total Pipeline Runs</span>
        <span className="stat-value" style={{ color: "var(--accent-blue)" }}>{overviewData.totalRuns}</span>
      </div>
      <div className="panel stat-card">
        <span className="stat-title">Success Rate</span>
        <span className="stat-value" style={{ color: "var(--accent-green)" }}>{overviewData.successRate}</span>
      </div>
      <div className="panel stat-card">
        <span className="stat-title">Failure Rate</span>
        <span className="stat-value" style={{ color: "var(--accent-red)" }}>{overviewData.failureRate}</span>
      </div>
      <div className="panel stat-card">
        <span className="stat-title">Average Duration</span>
        <span className="stat-value" style={{ color: "var(--accent-purple)" }}>{overviewData.avgDuration}</span>
      </div>
    </div>
  );
};

export default Overview;
