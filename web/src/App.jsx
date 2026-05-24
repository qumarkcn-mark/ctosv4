import { useCallback, useEffect, useRef, useState } from 'react'
import Dashboard from './pages/Dashboard.jsx'
import ReviewTrainingPage from './pages/ReviewTrainingPage.jsx'
import AIStructureWorkspace from './pages/AIStructureWorkspace.jsx'
import WatchBoard from './pages/WatchBoard.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import { apiFetch } from './api/client.js'
import { normalizeSymbolInput, readLastViewedSymbol } from './utils/symbolStorage.js'
import './App.css'

const PAGE_ALIASES = {
  analysis: 'review',
}

const VALID_PAGES = new Set(['dashboard', 'ai', 'watchboard', 'review'])
const PAGE_PATHS = {
  ai: '/ai',
  dashboard: '/dashboard',
  watchboard: '/watchboard',
  review: '/review',
}

const INITIAL_GROUP_PRIORITY = ['持仓', '重仓', '观察', '自选']

function pageFromPath(pathname) {
  const clean = String(pathname || '').replace(/\/+$/, '') || '/'
  if (clean === '/watchboard') return 'watchboard'
  if (clean === '/dashboard') return 'dashboard'
  if (clean === '/review') return 'review'
  if (clean === '/ai') return 'ai'
  return ''
}

function normalizePage(page) {
  const nextPage = PAGE_ALIASES[page] || page || 'ai'
  return VALID_PAGES.has(nextPage) ? nextPage : 'ai'
}

function pickInitialWatchSymbol(groups) {
  const normalizedGroups = Array.isArray(groups) ? groups : []
  const byName = new Map(normalizedGroups.map((group) => [group.name, group]))
  const orderedGroups = [
    ...INITIAL_GROUP_PRIORITY.map((name) => byName.get(name)).filter(Boolean),
    ...normalizedGroups.filter((group) => !INITIAL_GROUP_PRIORITY.includes(group.name)),
  ]

  for (const group of orderedGroups) {
    const stock = (group?.stocks || []).find((item) => item?.symbol)
    if (stock) return stock
  }
  return null
}

function App() {
  const [page, setPage] = useState(
    () => normalizePage(pageFromPath(window.location.pathname) || localStorage.getItem('ct_last_page')) || 'ai'
  )
  const [showSettings, setShowSettings] = useState(false)

  // ─── 全局活跃股票 (各板块共享) ───────────────────────────
  const [activeSymbol, setActiveSymbol] = useState(
    () => readLastViewedSymbol().symbol
  )
  const [activeSymbolName, setActiveSymbolName] = useState(
    () => readLastViewedSymbol().name
  )
  const activeSymbolRef = useRef(activeSymbol)

  useEffect(() => {
    activeSymbolRef.current = activeSymbol
  }, [activeSymbol])

  const setGlobalSymbol = useCallback((symbol, name) => {
    const next = normalizeSymbolInput(symbol, name)
    if (!next.symbol) return
    setActiveSymbol(next.symbol)
    setActiveSymbolName(next.name)
    localStorage.setItem('lastViewedSymbol', next.symbol)
    localStorage.setItem('lastViewedSymbolName', next.name)
  }, [])

  useEffect(() => {
    if (activeSymbolRef.current) return
    let cancelled = false

    async function bootstrapInitialSymbol() {
      try {
        const response = await apiFetch('/api/watchlist')
        if (!response.ok) return
        const groups = await response.json()
        const stock = pickInitialWatchSymbol(groups)
        if (!cancelled && stock?.symbol && !activeSymbolRef.current) {
          setGlobalSymbol(stock.symbol, stock.name || stock.symbol)
        }
      } catch (err) {
        console.warn('初始化股票选择失败:', err)
      }
    }

    void bootstrapInitialSymbol()
    return () => {
      cancelled = true
    }
  }, [setGlobalSymbol])

  // ─── 跨板块「去看盘」跳转 ───────────────────────────────
  const handleViewInAI = (symbol, name) => {
    setGlobalSymbol(symbol, name)
    navigate('ai')
  }

  const navigate = (p) => {
    const nextPage = normalizePage(p)
    setPage(nextPage)
    localStorage.setItem('ct_last_page', nextPage)
    const nextPath = PAGE_PATHS[nextPage] || '/ai'
    if (window.location.pathname !== nextPath) {
      window.history.pushState({ page: nextPage }, '', nextPath)
    }
  }

  useEffect(() => {
    const handlePopState = () => {
      setPage(normalizePage(pageFromPath(window.location.pathname) || localStorage.getItem('ct_last_page')))
    }
    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [])

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
            className={`nav-tab ${page === 'watchboard' ? 'active' : ''}`}
            onClick={() => navigate('watchboard')}
          >
            盯盘
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
        {page === 'watchboard' && <WatchBoard />}
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
