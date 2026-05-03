import { useState } from 'react'
import Dashboard from './pages/Dashboard.jsx'
import ChanView from './pages/ChanView.jsx'
import ReviewTrainingPage from './pages/ReviewTrainingPage.jsx'
import RotationCompass from './pages/RotationCompass.jsx'
import Scanner from './pages/Scanner.jsx'
import DailyPlaybook from './pages/DailyPlaybook.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import './App.css'

const PAGE_ALIASES = {
  analysis: 'review',
  'sand-table': 'review',
  rotation: 'playbook',
}

function normalizePage(page) {
  return PAGE_ALIASES[page] || page || 'playbook'
}

function App() {
  const [page, setPage] = useState(
    () => normalizePage(localStorage.getItem('ct_last_page')) || 'playbook'
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
    const nextPage = normalizePage(p)
    setPage(nextPage)
    localStorage.setItem('ct_last_page', nextPage)
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
            className={`nav-tab ${page === 'playbook' ? 'active' : ''}`}
            onClick={() => navigate('playbook')}
          >
            作战台
          </button>
          <button
            className={`nav-tab ${page === 'chan' ? 'active' : ''}`}
            onClick={() => navigate('chan')}
          >
            雷达工作台
          </button>
          <button
            className={`nav-tab ${page === 'scanner' ? 'active' : ''}`}
            onClick={() => navigate('scanner')}
          >
            机会池
          </button>
          <button
            className={`nav-tab ${page === 'dashboard' ? 'active' : ''}`}
            onClick={() => navigate('dashboard')}
          >
            交易账本
          </button>
          <button
            className={`nav-tab ${page === 'review' ? 'active' : ''}`}
            onClick={() => navigate('review')}
          >
            复盘训练
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
        {page === 'dashboard' && <Dashboard onViewInChan={handleViewInChan} onOpenRotation={() => setPage('rotation')} />}
        {page === 'playbook' && <DailyPlaybook onViewInChan={handleViewInChan} onOpenRotation={() => setPage('rotation')} />}
        {page === 'scanner' && <Scanner onViewInChan={handleViewInChan} />}
        {page === 'chan' && (
          <ChanView
            activeSymbol={activeSymbol}
            activeSymbolName={activeSymbolName}
            onSymbolChange={setGlobalSymbol}
          />
        )}
        {page === 'rotation' && <RotationCompass onViewInChan={handleViewInChan} />}
        {page === 'review' && <ReviewTrainingPage />}
      </main>

      {/* 模态框层 */}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  )
}

export default App
