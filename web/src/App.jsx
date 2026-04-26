import { useState } from 'react'
import Dashboard from './pages/Dashboard.jsx'
import ChanView from './pages/ChanView.jsx'
import BehaviorReport from './pages/BehaviorReport.jsx'
import SandTable from './pages/SandTable.jsx'
import RotationCompass from './pages/RotationCompass.jsx'
import ChanMatrix from './pages/ChanMatrix.jsx'
import Scanner from './pages/Scanner.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import './App.css'

function App() {
  const [page, setPage] = useState(
    () => localStorage.getItem('ct_last_page') || 'chan'
  )
  const [showSettings, setShowSettings] = useState(false)

  // ─── 全局活跃股票 (各板块共享) ───────────────────────────
  const [activeSymbol, setActiveSymbol] = useState(
    () => localStorage.getItem('lastViewedSymbol') || 'sh600519'
  )
  const [activeSymbolName, setActiveSymbolName] = useState(
    () => localStorage.getItem('lastViewedSymbolName') || '贵州茅台'
  )

  const setGlobalSymbol = (symbol, name) => {
    setActiveSymbol(symbol)
    setActiveSymbolName(name || symbol)
    localStorage.setItem('lastViewedSymbol', symbol)
    localStorage.setItem('lastViewedSymbolName', name || symbol)
  }

  // ─── 跨板块「去看盘」跳转 ───────────────────────────────
  const handleViewInChan = (symbol, name) => {
    setGlobalSymbol(symbol, name)
    navigate('chan')
  }

  const navigate = (p) => {
    setPage(p)
    localStorage.setItem('ct_last_page', p)
  }

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
            onClick={() => navigate('dashboard')}
          >
            📊 交易看板
          </button>
          <button
            className={`nav-tab ${page === 'analysis' ? 'active' : ''}`}
            onClick={() => navigate('analysis')}
          >
            📈 行为分析
          </button>
          <button
            className={`nav-tab ${page === 'scanner' ? 'active' : ''}`}
            onClick={() => navigate('scanner')}
          >
            🎯 今日机会
          </button>
          <button
            className={`nav-tab ${page === 'chan' ? 'active' : ''}`}
            onClick={() => navigate('chan')}
          >
            🔮 缠论看盘
          </button>
          <button
            className={`nav-tab ${page === 'rotation' ? 'active' : ''}`}
            onClick={() => navigate('rotation')}
          >
            🧭 调仓罗盘
          </button>
          <button
            className={`nav-tab ${page === 'sand-table' ? 'active' : ''}`}
            onClick={() => navigate('sand-table')}
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
        {page === 'dashboard' && <Dashboard onViewInChan={handleViewInChan} />}
        {page === 'analysis' && <BehaviorReport onViewInChan={handleViewInChan} />}
        {page === 'scanner' && <Scanner onViewInChan={handleViewInChan} />}
        {page === 'chan' && (
          <ChanView
            activeSymbol={activeSymbol}
            activeSymbolName={activeSymbolName}
            onSymbolChange={setGlobalSymbol}
          />
        )}
        {page === 'rotation' && <RotationCompass onViewInChan={handleViewInChan} />}
        {page === 'sand-table' && <SandTable />}
      </main>

      {/* 模态框层 */}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  )
}

export default App
