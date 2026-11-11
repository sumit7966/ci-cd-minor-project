import React from 'react';
import Dashboard from './Dashboard';
import Sidebar from './components/Sidebar';
import TopHeader from './components/TopHeader';
import './index.css';

function App() {
  return (
    <div className="app-layout">
      <Sidebar />
      <div className="main-content">
        <TopHeader />
        <Dashboard />
      </div>
    </div>
  );
}

export default App;
