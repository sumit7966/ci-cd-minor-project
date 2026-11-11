import React from 'react';

const TopHeader = () => {
  return (
    <div className="top-header">
      <div className="header-title">Overview</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
        <div style={{ position: 'relative' }}>
          <span style={{ fontSize: '1.2rem', cursor: 'pointer' }}>🔔</span>
          <span style={{ position: 'absolute', top: 0, right: 0, width: '8px', height: '8px', background: 'var(--accent-red)', borderRadius: '50%' }}></span>
        </div>
        <div style={{ width: '32px', height: '32px', background: 'var(--accent-blue)', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold' }}>
          U
        </div>
      </div>
    </div>
  );
};

export default TopHeader;
