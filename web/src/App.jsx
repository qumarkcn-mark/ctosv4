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
  const [tdxSyncState, setTdxSyncState] = useState({ status: 'idle', message: '' })
  const [dismissedSyncNoticeKey, setDismissedSyncNoticeKey] = useState('')

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

  const handlePostmarketSync = useCallback(async () => {
    if (tdxSyncState.status === 'running') return
    setDismissedSyncNoticeKey('')
    setTdxSyncState({ status: 'running', message: '正在检查 TDX 本地盘后数据...' })
    try {
      const response = await apiFetch('/api/data/tdx/sync/postmarket', { method: 'POST' })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) {
        throw new Error(payload.detail || payload.message || `盘后同步失败 ${response.status}`)
      }
      const nextStatus = payload.status || 'success'
      setTdxSyncState({
        status: nextStatus,
        message: payload.message || postmarketSyncMessage(payload),
        jobId: payload.job_id || '',
      })
    } catch (err) {
      setTdxSyncState({
        status: 'error',
        message: err.message || 'TDX 盘后同步失败',
      })
    }
  }, [tdxSyncState.status])

  const loadPostmarketSyncStatus = useCallback(async () => {
    try {
      const response = await apiFetch('/api/data/tdx/sync/postmarket/latest')
      if (!response.ok) return
      const payload = await response.json().catch(() => ({}))
      if (!payload?.status) {
        setTdxSyncState((prev) => (prev.status === 'running' ? { status: 'idle', message: '' } : prev))
        return
      }
      setTdxSyncState((prev) => {
        if (prev.status !== 'running' && payload.status === 'running') return prev
        const noticeKey = postmarketSyncNoticeKey(payload)
        const message = noticeKey && noticeKey === dismissedSyncNoticeKey
          ? ''
          : payload.message || postmarketSyncMessage(payload)
        return {
          status: payload.status,
          message,
          jobId: payload.job_id || prev.jobId || '',
        }
      })
    } catch (err) {
      console.warn('读取 TDX 盘后同步状态失败:', err)
    }
  }, [dismissedSyncNoticeKey])

  useEffect(() => {
    void loadPostmarketSyncStatus()
  }, [loadPostmarketSyncStatus])

  useEffect(() => {
    if (tdxSyncState.status !== 'running') return undefined
    const timer = setInterval(() => {
      void loadPostmarketSyncStatus()
    }, 3000)
    return () => clearInterval(timer)
  }, [loadPostmarketSyncStatus, tdxSyncState.status])

  useEffect(() => {
    if (!tdxSyncState.message || tdxSyncState.status === 'running') return undefined
    const timer = setTimeout(() => {
      setDismissedSyncNoticeKey(postmarketSyncNoticeKey(tdxSyncState))
      setTdxSyncState((prev) => ({ ...prev, message: '' }))
    }, 8000)
    return () => clearTimeout(timer)
  }, [tdxSyncState])

  const dismissPostmarketSyncNotice = useCallback(() => {
    setDismissedSyncNoticeKey(postmarketSyncNoticeKey(tdxSyncState))
    setTdxSyncState((prev) => ({ ...prev, message: '' }))
  }, [tdxSyncState])

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
          <span className="status-dot online"></span>
          <span className="text-secondary">系统在线</span>
          <button
            className={`postmarket-sync-btn is-${tdxSyncState.status}`}
            onClick={handlePostmarketSync}
            disabled={tdxSyncState.status === 'running'}
            title={tdxSyncState.message || '同步 Windows TDX 盘后数据'}
          >
            {postmarketSyncLabel(tdxSyncState.status)}
          </button>
          <button className="settings-btn" onClick={() => setShowSettings(true)} title="系统设置">
            ⚙️
          </button>
        </div>
      </header>
      {tdxSyncState.message && (
        <div className={`global-sync-toast is-${tdxSyncState.status}`} role="status">
          <span>{tdxSyncState.message}</span>
          <button
            className="global-sync-toast__close"
            type="button"
            onClick={dismissPostmarketSyncNotice}
            aria-label="关闭同步提示"
            title="关闭"
          >
            ×
          </button>
        </div>
      )}

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

function postmarketSyncLabel(status) {
  if (status === 'running') return '同步中'
  if (status === 'success') return '今日已同步'
  if (status === 'stale') return 'TDX未更新'
  if (status === 'partial') return '部分同步'
  if (status === 'error') return '同步失败'
  if (status === 'empty') return '无同步项'
  return '盘后同步'
}

function postmarketSyncMessage(payload) {
  const latest = payload?.tdx_status?.latest || {}
  if (payload?.status === 'ready') return `TDX 已更新，日线 ${latest.day || '-'}，5分 ${latest.m5 || '-'}`
  if (payload?.status === 'stale') return payload.message || 'TDX 本地数据还没到今天'
  if (payload?.status === 'partial') return payload.message || 'TDX 数据部分更新'
  return payload?.message || 'TDX 盘后同步完成'
}

function postmarketSyncNoticeKey(payload) {
  if (!payload) return ''
  return [
    payload.jobId || payload.job_id || '',
    payload.status || '',
    payload.finished_at || '',
    payload.message || '',
  ].join('|')
}

export default App
