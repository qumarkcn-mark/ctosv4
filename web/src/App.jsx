import { useState, useEffect } from 'react'
import Dashboard from './pages/Dashboard.jsx'
import './App.css'

function App() {
  const [page, setPage] = useState('dashboard')

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
            disabled
          >
            🔮 缠论看盘
          </button>
        </nav>
        <div className="status-bar-right">
          <span className="status-dot online"></span>
          <span className="text-secondary">系统在线</span>
        </div>
      </header>

      {/* 主内容区 */}
      <main className="main-content">
        {page === 'dashboard' && <Dashboard />}
        {page === 'analysis' && (
          <div className="placeholder-page">
            <h2>📈 行为分析</h2>
            <p className="text-secondary">Phase 4 实现 — 需要积累交易数据</p>
          </div>
        )}
        {page === 'chan' && (
          <div className="placeholder-page">
            <h2>🔮 缠论看盘</h2>
            <p className="text-secondary">Phase 3 实现 — 从 V3 移植缠论引擎</p>
          </div>
        )}
      </main>
    </div>
  )
}

export default App
