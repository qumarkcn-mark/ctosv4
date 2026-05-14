import { useState } from 'react'
import Dashboard from './pages/Dashboard.jsx'
import ReviewTrainingPage from './pages/ReviewTrainingPage.jsx'
import AIStructureWorkspace from './pages/AIStructureWorkspace.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import { normalizeSymbolInput, readLastViewedSymbol } from './utils/symbolStorage.js'
import './App.css'

const PAGE_ALIASES = {
  analysis: 'review',
}

const VALID_PAGES = new Set(['dashboard', 'ai', 'review'])

function normalizePage(page) {
  const nextPage = PAGE_ALIASES[page] || page || 'ai'
  return VALID_PAGES.has(nextPage) ? nextPage : 'ai'
}

function App() {
  const [page, setPage] = useState(
    () => normalizePage(localStorage.getItem('ct_last_page')) || 'ai'
  )
  const [showSettings, setShowSettings] = useState(false)

  // ─── 全局活跃股票 (各板块共享) ───────────────────────────
  const [activeSymbol, setActiveSymbol] = useState(
    () => readLastViewedSymbol().symbol
  )
  const [activeSymbolName, setActiveSymbolName] = useState(
    () => readLastViewedSymbol().name
  )

  const setGlobalSymbol = (symbol, name) => {
    const next = normalizeSymbolInput(symbol, name)
    setActiveSymbol(next.symbol)
    setActiveSymbolName(next.name)
    localStorage.setItem('lastViewedSymbol', next.symbol)
    localStorage.setItem('lastViewedSymbolName', next.name)
  }

  // ─── 跨板块「去看盘」跳转 ───────────────────────────────
  const handleViewInAI = (symbol, name) => {
    setGlobalSymbol(symbol, name)
    navigate('ai')
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
            className={`nav-tab ${page === 'ai' ? 'active' : ''}`}
            onClick={() => navigate('ai')}
          >
            AI 教练
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
        {page === 'dashboard' && <Dashboard onViewInAI={handleViewInAI} onOpenAI={() => navigate('ai')} />}
        {page === 'ai' && (
          <AIStructureWorkspace
            activeSymbol={activeSymbol}
            activeSymbolName={activeSymbolName}
            onSymbolChange={setGlobalSymbol}
          />
        )}
        {page === 'review' && (
          <ReviewTrainingPage
            activeSymbol={activeSymbol}
            activeSymbolName={activeSymbolName}
          />
        )}
      </main>

      {/* 模态框层 */}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  )
}

export default App
