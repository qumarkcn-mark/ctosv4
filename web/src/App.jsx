import { useState, useEffect } from 'react'
import Dashboard from './pages/Dashboard.jsx'
import ChanView from './pages/ChanView.jsx'
import BehaviorReport from './pages/BehaviorReport.jsx'
import SandTable from './pages/SandTable.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import './App.css'

function App() {
  const [page, setPage] = useState('dashboard')
  const [showSettings, setShowSettings] = useState(false)

  return (
    <div className="app">
      {/* 顶部状态栏 */}
      <header className="status-bar">
        <div className="status-bar-left">
          <span className="logo">CT-OS</span>
          <span className="version">V4.0</span>
          <span className="tagline">交易教练</span>
        </div>
        <nav className="nav-tabs">
          <button
            className={`nav-tab ${page === 'dashboard' ? 'active' : ''}`}
            onClick={() => setPage('dashboard')}
          >
            📊 交易看板
          </button>
          <button
            className={`nav-tab ${page === 'analysis' ? 'active' : ''}`}
            onClick={() => setPage('analysis')}
          >
            📈 行为分析
          </button>
          <button
            className={`nav-tab ${page === 'chan' ? 'active' : ''}`}
            onClick={() => setPage('chan')}
          >
            🔮 缠论看盘
          </button>
          <button
            className={`nav-tab ${page === 'sand-table' ? 'active' : ''}`}
            onClick={() => setPage('sand-table')}
          >
            🎮 模拟训练
          </button>
        </nav>
        <div className="status-bar-right">
          <button className="settings-btn" onClick={() => setShowSettings(true)} title="系统设置">
            ⚙️
          </button>
          <span className="status-dot online"></span>
          <span className="text-secondary">系统在线</span>
        </div>
      </header>

      {/* 主内容区 */}
      <main className="main-content">
        {page === 'dashboard' && <Dashboard />}
        {page === 'analysis' && <BehaviorReport />}
        {page === 'chan' && <ChanView />}
        {page === 'sand-table' && <SandTable />}
      </main>

      {/* 模态框层 */}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  )
}

export default App

