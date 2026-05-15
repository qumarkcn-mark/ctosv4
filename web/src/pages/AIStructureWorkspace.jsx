import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API_BASE } from '../config.js'
import { apiJson } from '../api/client.js'
import StockSearch from '../components/StockSearch.jsx'
import WatchlistPanel from '../components/WatchlistPanel.jsx'
import AIStructureCoachPanel from '../features/aiStructure/AIStructureCoachPanel.jsx'
import PriceEvidenceView from '../features/kline/PriceEvidenceView.jsx'
import { readLastViewedSymbol } from '../utils/symbolStorage.js'
import './AIStructureWorkspace.css'

const WORKSPACE_LEVELS = ['week', 'day', '30', '5']

export default function AIStructureWorkspace({ activeSymbol, activeSymbolName, onSymbolChange }) {
  const [localSymbol, setLocalSymbol] = useState(() => readLastViewedSymbol().symbol)
  const [localName, setLocalName] = useState(() => readLastViewedSymbol().name)
  const [aiEvidenceContext, setAiEvidenceContext] = useState(null)
  const [workspace, setWorkspace] = useState(null)
  const [workspaceLoading, setWorkspaceLoading] = useState(false)
  const [workspaceError, setWorkspaceError] = useState('')
  const watchlistRef = useRef(null)
  const symbol = activeSymbol ?? localSymbol
  const symbolName = activeSymbolName ?? localName
  const workspaceBySymbol = useMemo(() => {
    const map = new Map()
    ;(workspace?.symbols || []).forEach((item) => {
      symbolLookupKeys(item.symbol).forEach((key) => map.set(key, item))
    })
    return map
  }, [workspace])
  const activeWorkspaceState = workspaceBySymbol.get(symbol) || null

  const loadWorkspace = useCallback(async ({ ensurePipeline = false } = {}) => {
    setWorkspaceLoading(true)
    setWorkspaceError('')
    try {
      const json = await apiJson(`${API_BASE}/ai-structure/workspace/bootstrap`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sources: ['positions', 'recent_chat', 'watchlist'],
          levels: WORKSPACE_LEVELS,
          ensure_pipeline: ensurePipeline,
          reason: ensurePipeline ? 'web_workspace_refresh' : 'web_workspace_bootstrap',
        }),
      })
      setWorkspace(json.data)
      return json.data
    } catch (err) {
      setWorkspaceError(err?.message || 'AI 工作台启动失败')
      return null
    } finally {
      setWorkspaceLoading(false)
    }
  }, [])

  useEffect(() => {
    loadWorkspace()
  }, [loadWorkspace])

  const handleSelect = useCallback((stock) => {
    const nextSymbol = stock.symbol
    const nextName = stock.name || stock.symbol
    setAiEvidenceContext(null)
    if (onSymbolChange) {
      onSymbolChange(nextSymbol, nextName)
    } else {
      setLocalSymbol(nextSymbol)
      setLocalName(nextName)
      localStorage.setItem('lastViewedSymbol', nextSymbol)
      localStorage.setItem('lastViewedSymbolName', nextName)
    }
  }, [onSymbolChange])

  return (
    <div className="ai-workspace">
      <aside className="ai-workspace-watchlist">
        <WatchlistPanel
          ref={watchlistRef}
          activeSymbol={symbol}
          onSelect={handleSelect}
          workspace={workspace}
          workspaceLoading={workspaceLoading}
          onWorkspaceRefresh={loadWorkspace}
        />
      </aside>

      <section className="ai-workspace-main">
        <div className="ai-workspace-topbar">
          <div className="ai-workspace-search">
            <StockSearch onSelect={handleSelect} />
          </div>
          <div className="ai-workspace-symbol">
            <strong>{symbolName}</strong>
            <span>{symbol}</span>
          </div>
          <div className="ai-workspace-bootstrap">
            <span>{workspaceLoading ? 'AI池刷新中' : `AI池 ${workspace?.universe?.length || 0} 只`}</span>
            {workspaceError && <em>{workspaceError}</em>}
            <button type="button" onClick={() => loadWorkspace({ ensurePipeline: true })} disabled={workspaceLoading}>
              刷新
            </button>
          </div>
        </div>

        <div className="ai-workspace-body">
          <PriceEvidenceView
            symbol={symbol}
            symbolName={symbolName}
            chartContext={aiEvidenceContext}
          />
          <aside className="ai-workspace-coach">
            <AIStructureCoachPanel
              symbol={symbol}
              symbolName={symbolName}
              workspaceSymbolState={activeWorkspaceState}
              workspaceLoading={workspaceLoading}
              onWorkspaceRefresh={loadWorkspace}
              onEvidenceContext={setAiEvidenceContext}
            />
          </aside>
        </div>
      </section>
    </div>
  )
}

function symbolLookupKeys(symbol) {
  const text = String(symbol || '')
  const compact = text.replace('.', '')
  const keys = new Set([text, compact])
  if (/^(sh|sz)\d{6}$/i.test(compact)) {
    keys.add(`${compact.slice(0, 2).toLowerCase()}.${compact.slice(2)}`)
  }
  return [...keys].filter(Boolean)
}
