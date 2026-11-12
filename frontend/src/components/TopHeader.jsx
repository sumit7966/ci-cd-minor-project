import React from 'react';

const SECTION_TITLES = {
  dashboard: 'Dashboard',
  pipelines: 'Pipelines',
  predictions: 'ML Predictions',
  optimizations: 'Optimizations',
  settings: 'Settings',
};

const TopHeader = ({
  activeSection = 'dashboard',
  onOpenMobileMenu,
  isMobileMenuOpen = false,
}) => {
  const title = SECTION_TITLES[activeSection] || 'Dashboard';

  return (
    <header className="top-header">
      <div className="header-left">
        <button
          type="button"
          className="mobile-menu-btn"
          onClick={onOpenMobileMenu}
          aria-label="Open navigation menu"
          aria-expanded={isMobileMenuOpen}
          aria-controls="app-sidebar"
        >
          ☰
        </button>
        <div className="header-title">{title}</div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
        <div style={{ position: 'relative' }}>
          <span style={{ fontSize: '1.2rem' }}>🔔</span>
          <span style={{ position: 'absolute', top: 0, right: 0, width: '8px', height: '8px', background: 'var(--accent-red)', borderRadius: '50%' }}></span>
        </div>
        <div style={{ width: '32px', height: '32px', background: 'var(--accent-blue)', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 'bold' }}>
          U
        </div>
      </div>
    </header>
  );
};

export default TopHeader;
