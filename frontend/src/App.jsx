import React, { useState, useEffect, useCallback } from 'react';
import Dashboard from './Dashboard';
import Sidebar from './components/Sidebar';
import TopHeader from './components/TopHeader';
import './index.css';

function App() {
  const [activeSection, setActiveSection] = useState('dashboard');
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);

  const handleSelectSection = useCallback((sectionId) => {
    setActiveSection(sectionId);
    setIsMobileSidebarOpen(false);
  }, []);

  const handleOpenMobileSidebar = useCallback(() => {
    setIsMobileSidebarOpen(true);
  }, []);

  const handleCloseMobileSidebar = useCallback(() => {
    setIsMobileSidebarOpen(false);
  }, []);

  useEffect(() => {
    if (!isMobileSidebarOpen) {
      return undefined;
    }

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        setIsMobileSidebarOpen(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isMobileSidebarOpen]);

  return (
    <div className="app-layout">
      <Sidebar
        activeSection={activeSection}
        onSelectSection={handleSelectSection}
        isMobileOpen={isMobileSidebarOpen}
        onCloseMobile={handleCloseMobileSidebar}
      />
      <div className="main-content">
        <TopHeader
          activeSection={activeSection}
          onOpenMobileMenu={handleOpenMobileSidebar}
          isMobileMenuOpen={isMobileSidebarOpen}
        />
        <Dashboard activeSection={activeSection} />
      </div>
    </div>
  );
}

export default App;
