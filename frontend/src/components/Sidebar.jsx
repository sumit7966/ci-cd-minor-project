import React from 'react';

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: '📊' },
  { id: 'pipelines', label: 'Pipelines', icon: '⚙️' },
  { id: 'predictions', label: 'ML Predictions', icon: '🧠' },
  { id: 'optimizations', label: 'Optimizations', icon: '⚡' },
  { id: 'settings', label: 'Settings', icon: '🔧' },
];

const Sidebar = ({
  activeSection = 'dashboard',
  onSelectSection,
  isMobileOpen = false,
  onCloseMobile,
}) => {
  const handleNavClick = (sectionId) => {
    if (onSelectSection) {
      onSelectSection(sectionId);
    }
    if (onCloseMobile) {
      onCloseMobile();
    }
  };

  return (
    <>
      {isMobileOpen && (
        <div
          className="sidebar-backdrop"
          onClick={onCloseMobile}
          aria-hidden="true"
        />
      )}
      <aside
        id="app-sidebar"
        className={`sidebar${isMobileOpen ? ' open' : ''}`}
        aria-label="Sidebar navigation"
      >
        <div className="sidebar-header">
          <h2>ShipSwift</h2>
          <button
            type="button"
            className="sidebar-close-btn"
            onClick={onCloseMobile}
            aria-label="Close navigation menu"
          >
            ✕
          </button>
        </div>
        <nav className="sidebar-nav" aria-label="Primary sections">
          {NAV_ITEMS.map((item) => {
            const isActive = activeSection === item.id;
            return (
              <button
                key={item.id}
                type="button"
                className={`nav-item${isActive ? ' active' : ''}`}
                onClick={() => handleNavClick(item.id)}
                aria-current={isActive ? 'page' : undefined}
              >
                <span aria-hidden="true">{item.icon}</span>
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>
    </>
  );
};

export default Sidebar;
