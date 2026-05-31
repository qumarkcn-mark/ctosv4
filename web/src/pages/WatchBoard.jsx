import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { apiFetch, apiJson } from '../api/client.js'
import StockSearch from '../components/StockSearch.jsx'
import WatchCard from '../components/WatchCard.jsx'
import {
  buildIntradayReviewQuestion,
  computeStateMachineState,
  intradayReviewLabel,
} from '../utils/watchboardState.js'
import './WatchBoard.css'

const GROUPS = ['自选', '备选']
const QUICK_QUESTIONS = ['现在怎么看？', '跌破哪里失效？', '企稳看哪里？', '要不要减仓？']

function isTradingTime(now = new Date()) {
  const day = now.getDay()
  if (day === 0 || day === 6) return false
  const minutes = now.getHours() * 60 + now.getMinutes()
  return minutes >= 9 * 60 + 30 && minutes <= 15 * 60
}

function flattenGroups(groups) {
  return groups.flatMap((group) => group.items || [])
}

function priceKey(symbol) {
  return String(symbol || '').replace('.', '')
}

function mergePrice(item, prices, previousPrices = {}) {
  const quote = prices[item.symbol] || prices[priceKey(item.symbol)]
  const previousQuote = previousPrices[item.symbol] || previousPrices[priceKey(item.symbol)]
  if (!quote) return item
  return {
    ...item,
    price: quote.price ?? item.price,
    previous_price: previousQuote?.price ?? item.previous_price,
    change_pct: quote.change_pct ?? item.change_pct,
    price_data: {
      ...(item.price_data || {}),
      ...quote,
    },
  }
}

function chatQuestionPayload(item, question, thinkingEnabled = false) {
  const price = Number(item?.price || item?.price_data?.price || 0)
  return {
    symbol: item?.symbol || '',
    question,
    current_price: price > 0 ? price : undefined,
    quote_time: item?.price_data?.quote_time || undefined,
    change_pct: item?.change_pct ?? item?.price_data?.change_pct,
    price_source: price > 0 ? 'watchboard_quote' : undefined,
    thinking_enabled: thinkingEnabled,
  }
}

function formatPrice(value) {
  const num = Number(value || 0)
  if (!num) return '--'
  return num >= 100 ? num.toFixed(2) : num.toFixed(3).replace(/0$/, '')
}

function formatChatTime(value) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value).replace('T', ' ').slice(5, 16)
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  const hour = `${date.getHours()}`.padStart(2, '0')
  const minute = `${date.getMinutes()}`.padStart(2, '0')
  return `${month}-${day} ${hour}:${minute}`
}

function formatMetaTime(value) {
  if (!value) return ''
  const text = String(value).replace('T', ' ').replace(/\+\d\d:\d\d|Z$/g, '').trim()
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) return text.slice(5, 16)
  return text.slice(0, 16)
}

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds || 0)))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  const rest = `${total % 60}`.padStart(2, '0')
  return `${minutes}m${rest}s`
}

function buildReasoningStatus(freshness, running, nowMs = Date.now(), summary = {}) {
  if (running) {
    const started = Date.parse(running.startedAt || new Date().toISOString())
    const elapsed = Number.isFinite(started) ? (nowMs - started) / 1000 : 0
    return {
      tone: 'active',
      label: '生成中',
      detail: `LLM 推理 ${formatElapsed(elapsed)}`,
      compact: `LLM ${formatElapsed(elapsed)}`,
      actionLabel: '生成中',
      rows: [
        ['当前阶段', 'LLM 推理'],
        ['已用时间', formatElapsed(elapsed)],
      ],
    }
  }
  const status = freshness?.status || 'missing'
  const generatedAt = formatMetaTime(freshness?.generated_at || summary?.generated_at)
  const dataAsOf = formatMetaTime(freshness?.data_as_of || summary?.data_as_of)
  const latestAsOf = formatMetaTime(freshness?.latest_snapshot_as_of)
  const quoteTime = formatMetaTime(freshness?.quote_time)
  const rows = [
    ['推演生成', generatedAt || '无'],
    ['推演基于', dataAsOf || '无'],
    ['最新结构', latestAsOf || '无'],
    ['当前价', quoteTime || '无'],
  ]
  if (status === 'stale') {
    return {
      tone: 'stale',
      label: '旧推演',
      detail: freshness?.detail || '结构已更新，推演待刷新',
      compact: dataAsOf ? `基于 ${dataAsOf}` : '结构待刷新',
      actionLabel: '更新推演',
      rows,
    }
  }
  if (status === 'ready' || (!freshness && generatedAt)) {
    return {
      tone: 'ready',
      label: '最新推演',
      detail: freshness?.detail || '结构与推演一致',
      compact: [generatedAt, dataAsOf ? `基于 ${dataAsOf}` : ''].filter(Boolean).join(' · '),
      actionLabel: '刷新推演',
      rows,
    }
  }
  if (status === 'failed') {
    return { tone: 'failed', label: '推演失败', detail: '需要重新生成', compact: '需重试', actionLabel: '重试', rows }
  }
  return { tone: 'idle', label: '无推演', detail: '尚未生成完整推演', compact: '待生成', actionLabel: '生成推演', rows }
}

function restoreWatchboardChatMessages(rows, currentContextId = '') {
  return (rows || []).flatMap((row) => {
    const contextId = row.context_id || ''
    const isHistorical = Boolean(currentContextId && contextId && contextId !== currentContextId)
    const answer = row.answer || {}
    const answerText = answer.coach_answer || answer.answer || ''
    const items = []
    if (row.question_text) {
      items.push({
        role: 'user',
        content: row.question_text,
        contextId,
        isHistorical,
        createdAt: row.created_at || '',
      })
    }
    if (answerText) {
      items.push({
        role: 'assistant',
        content: answerText,
        contextId,
        isHistorical,
        createdAt: row.created_at || '',
      })
    }
    return items
  })
}

function parseSseEvent(chunk) {
  const lines = String(chunk || '').split('\n')
  let event = 'message'
  const dataLines = []
  for (const line of lines) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (!dataLines.length) return null
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) }
  } catch {
    return null
  }
}

export default function WatchBoard() {
  const [groups, setGroups] = useState([])
  const [prices, setPrices] = useState({})
  const [previousPrices, setPreviousPrices] = useState({})
  const [selected, setSelected] = useState(null)
  const [fullText, setFullText] = useState('')
  const [detailStatus, setDetailStatus] = useState('idle')
  const [drawerLoading, setDrawerLoading] = useState(false)
  const [reasoningRunning, setReasoningRunning] = useState(false)
  const [reasoningRun, setReasoningRun] = useState(null)
  const [nowTick, setNowTick] = useState(Date.now())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [targetGroup, setTargetGroup] = useState('自选')
  const [adding, setAdding] = useState(false)
  const [chatInput, setChatInput] = useState('')
  const [chatMessages, setChatMessages] = useState([])
  const [chatLoading, setChatLoading] = useState(false)
  const [chatHistoryLoading, setChatHistoryLoading] = useState(false)
  const [thinkingEnabled, setThinkingEnabled] = useState(false)
  const [activeDetailTab, setActiveDetailTab] = useState('reasoning')
  const chatLogRef = useRef(null)
  const autoOpenedRef = useRef(false)

  const allItems = useMemo(() => flattenGroups(groups), [groups])
  const displayGroups = useMemo(() => (
    groups.map((group) => ({
          ...group,
          items: (group.items || [])
        .map((item, index) => {
          const merged = mergePrice(item, prices, previousPrices)
          const tactical = computeStateMachineState(merged, merged.price, merged.previous_price)
          return {
            ...merged,
            _watchboardIndex: index,
            _tacticalState: tactical,
          }
        })
        .sort((a, b) => {
          const priorityDiff = (b._tacticalState?.priority || 0) - (a._tacticalState?.priority || 0)
          if (priorityDiff) return priorityDiff
          const aDistance = a._tacticalState?.distancePct
          const bDistance = b._tacticalState?.distancePct
          if (aDistance !== null && aDistance !== undefined && bDistance !== null && bDistance !== undefined) {
            const distanceDiff = aDistance - bDistance
            if (distanceDiff) return distanceDiff
          }
          return a._watchboardIndex - b._watchboardIndex
        }),
    }))
  ), [groups, prices, previousPrices])
  const selectedReasoningStatus = useMemo(() => (
    buildReasoningStatus(
      selected?.reasoning_freshness,
      reasoningRun?.symbol === selected?.symbol ? reasoningRun : null,
      nowTick,
      selected?.reasoning_summary || {},
    )
  ), [selected, reasoningRun, nowTick])
  const selectedLiveItem = useMemo(() => (
    selected ? mergePrice(selected, prices, previousPrices) : null
  ), [selected, prices, previousPrices])
  const selectedIntradayReviewQuestion = useMemo(() => (
    selectedLiveItem ? buildIntradayReviewQuestion(selectedLiveItem, selectedLiveItem.price, selectedLiveItem.previous_price) : ''
  ), [selectedLiveItem])
  const selectedIntradayReviewLabel = useMemo(() => (
    selectedLiveItem ? intradayReviewLabel(selectedLiveItem, selectedLiveItem.price, selectedLiveItem.previous_price) : ''
  ), [selectedLiveItem])

  const loadWatchboard = useCallback(async () => {
    setError('')
    try {
      const json = await apiJson('/api/ai-structure/watchboard')
      const nextGroups = json.data?.groups || []
      setGroups(nextGroups)
      return nextGroups
    } catch (err) {
      setError(err.message || '盯盘面板加载失败')
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  const pollPrices = useCallback(async () => {
    const symbols = allItems.map((item) => item.symbol).filter(Boolean)
    if (!symbols.length) return
    try {
      const json = await apiJson(`/api/data/prices?symbols=${symbols.map(encodeURIComponent).join(',')}`)
      const nextPrices = json.prices || {}
      setPrices((prevPrices) => {
        setPreviousPrices(prevPrices || {})
        return nextPrices
      })
    } catch (err) {
      console.error('盯盘价格刷新失败:', err)
    }
  }, [allItems])

  useEffect(() => {
    loadWatchboard()
  }, [loadWatchboard])

  useEffect(() => {
    if (!allItems.length) return
    const tick = () => {
      if (isTradingTime()) pollPrices()
    }
    tick()
    const timer = setInterval(tick, 5000)
    return () => clearInterval(timer)
  }, [allItems, pollPrices])

  useEffect(() => {
    if (activeDetailTab !== 'chat') return
    window.requestAnimationFrame(() => {
      const node = chatLogRef.current?.lastElementChild
      node?.scrollIntoView({ block: 'end' })
    })
  }, [activeDetailTab, chatMessages, chatLoading])

  useEffect(() => {
    if (!reasoningRun) return undefined
    const timer = setInterval(() => setNowTick(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [reasoningRun])

  const restoreChatHistory = useCallback(async (item) => {
    if (!item?.symbol) return
    const currentContextId = item.context_id || ''
    setChatHistoryLoading(true)
    try {
      const sessionsJson = await apiJson(`/api/ai-structure/chat/sessions/${encodeURIComponent(item.symbol)}`)
      const latestSession = sessionsJson.data?.sessions?.[0]
      if (!latestSession?.session_id) {
        setChatMessages([])
        return
      }
      const messagesJson = await apiJson(
        `/api/ai-structure/chat/messages?session_id=${encodeURIComponent(latestSession.session_id)}`,
      )
      const restored = restoreWatchboardChatMessages(messagesJson.data?.messages || [], currentContextId)
      setChatMessages(restored)
    } catch (err) {
      console.warn('问答追踪历史加载失败:', err)
      setChatMessages([])
    } finally {
      setChatHistoryLoading(false)
    }
  }, [])

  const openDetail = useCallback(async (item) => {
    const current = mergePrice(item, prices, previousPrices)
    setSelected(current)
    setFullText('')
    setDetailStatus('loading')
    setChatMessages([])
    setChatHistoryLoading(false)
    setActiveDetailTab('reasoning')
    setDrawerLoading(true)
    restoreChatHistory(current)
    try {
      const json = await apiJson(`/api/ai-structure/unified-reasoning/full/${encodeURIComponent(current.symbol)}`)
      setFullText(json.data?.full_text || '')
      setDetailStatus('ready')
    } catch (err) {
      setFullText('')
      setDetailStatus('missing')
    } finally {
      setDrawerLoading(false)
    }
  }, [prices, previousPrices, restoreChatHistory])

  useEffect(() => {
    if (autoOpenedRef.current || selected || loading) return
    const firstItem = displayGroups.flatMap((group) => group.items || [])[0]
    if (!firstItem) return
    autoOpenedRef.current = true
    openDetail(firstItem)
  }, [displayGroups, loading, openDetail, selected])

  const runReasoningForSelected = async () => {
    if (!selected) return
    setReasoningRunning(true)
    setReasoningRun({ symbol: selected.symbol, startedAt: new Date().toISOString(), phase: 'llm_reasoning' })
    setDetailStatus('loading')
    try {
      await apiJson('/api/ai-structure/unified-reasoning/trigger', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: [selected.symbol] }),
      })
      const json = await apiJson(`/api/ai-structure/unified-reasoning/full/${encodeURIComponent(selected.symbol)}`)
      setFullText(json.data?.full_text || '')
      setDetailStatus('ready')
      const nextGroups = await loadWatchboard()
      const updated = flattenGroups(nextGroups || []).find((item) => item.symbol === selected.symbol)
      if (updated) setSelected(mergePrice(updated, prices, previousPrices))
    } catch (err) {
      setDetailStatus('missing')
      setFullText('')
      setError(err.message || '统一推演生成失败')
    } finally {
      setReasoningRunning(false)
      setReasoningRun(null)
      setDrawerLoading(false)
    }
  }

  const addStock = async (stock) => {
    setAdding(true)
    setError('')
    try {
      await apiJson(`/api/watchlist/groups/${encodeURIComponent(targetGroup)}/stocks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: stock.symbol, name: stock.name }),
      })
      try {
        await apiJson('/api/ai-structure/unified-reasoning/trigger', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ symbols: [stock.symbol], trigger_reason: 'new_watchboard_symbol', force: true }),
        })
      } catch (err) {
        console.warn('新增股票后触发统一推演失败:', err)
      }
      await loadWatchboard()
    } catch (err) {
      setError(err.message || '添加股票失败')
    } finally {
      setAdding(false)
    }
  }

  const sendQuestion = async (question = chatInput) => {
    const text = String(question || '').trim()
    if (!text || !selected) return
    const selectedWithPrice = selectedLiveItem || mergePrice(selected, prices, previousPrices)
    setChatLoading(true)
    setActiveDetailTab('chat')
    setChatInput('')
    const sentAt = new Date().toISOString()
    const assistantKey = `assistant-${Date.now()}`
    setChatMessages((prev) => [
      ...prev,
      { role: 'user', content: text, contextId: selected.context_id || '', createdAt: sentAt },
      { role: 'assistant', content: '', contextId: selected.context_id || '', createdAt: new Date().toISOString(), streamingKey: assistantKey, isStreaming: true },
    ])
    try {
      await streamQuestionAnswer({ text, assistantKey, item: selectedWithPrice, thinking: thinkingEnabled })
    } catch (err) {
      try {
        const json = await apiJson('/api/ai-structure/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(chatQuestionPayload(selectedWithPrice, text, thinkingEnabled)),
        })
        patchStreamingMessage(assistantKey, {
          content: json.data?.coach_answer || '暂无回答',
          contextId: json.data?.context_id || selected.context_id || '',
          createdAt: json.data?.created_at || new Date().toISOString(),
          isStreaming: false,
        })
      } catch (fallbackErr) {
        patchStreamingMessage(assistantKey, {
          content: fallbackErr.message || err.message || '问答失败',
          isStreaming: false,
        })
      }
    } finally {
      setChatLoading(false)
    }
  }

  const runIntradayReview = () => {
    if (!selectedIntradayReviewQuestion || chatLoading) return
    sendQuestion(selectedIntradayReviewQuestion)
  }

  const patchStreamingMessage = useCallback((streamingKey, patch) => {
    setChatMessages((prev) => prev.map((message) => (
      message.streamingKey === streamingKey ? { ...message, ...patch } : message
    )))
  }, [])

  const appendStreamingMessage = useCallback((streamingKey, delta) => {
    if (!delta) return
    setChatMessages((prev) => prev.map((message) => (
      message.streamingKey === streamingKey
        ? { ...message, content: `${message.content || ''}${delta}` }
        : message
    )))
  }, [])

  const streamQuestionAnswer = useCallback(async ({ text, assistantKey, item, thinking = false }) => {
    const response = await apiFetch('/api/ai-structure/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(chatQuestionPayload(item || selected, text, thinking)),
    })
    if (!response.ok || !response.body) {
      throw new Error(`流式问答失败 ${response.status}`)
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let doneMeta = null
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        const event = parseSseEvent(part)
        if (!event) continue
        if (event.event === 'delta') {
          appendStreamingMessage(assistantKey, event.data?.content || '')
        } else if (event.event === 'done') {
          doneMeta = event.data || {}
        } else if (event.event === 'error') {
          throw new Error(event.data?.message || '流式问答失败')
        }
      }
    }
    patchStreamingMessage(assistantKey, {
      contextId: doneMeta?.context_id || selected.context_id || '',
      createdAt: doneMeta?.created_at || new Date().toISOString(),
      isStreaming: false,
    })
  }, [appendStreamingMessage, patchStreamingMessage, selected])

  const chatStatus = chatLoading
    ? { tone: 'active', label: '推演中', detail: '正在结合完整推演与当前价格' }
    : chatHistoryLoading
      ? { tone: 'active', label: '读取中', detail: '正在恢复历史问答' }
    : drawerLoading
      ? { tone: 'active', label: '读取中', detail: '正在读取完整推演上下文' }
      : detailStatus === 'ready'
        ? { tone: 'ready', label: '上下文就绪', detail: '基于完整推演回答' }
        : { tone: 'idle', label: '待生成', detail: '可先生成统一推演后再追问' }

  return (
    <div className={`watchboard-page ${selected ? 'has-detail' : ''}`}>
      <div className="watchboard-main">
        <header className="watchboard-toolbar">
          <div>
            <h1>V5 盯盘</h1>
            <p>每张卡只显示一条当前主线。仅供参考，不构成投资建议。</p>
          </div>
          <div className="watchboard-add">
            <StockSearch onSelect={addStock} />
            <select aria-label="添加到分组" value={targetGroup} onChange={(event) => setTargetGroup(event.target.value)}>
              {GROUPS.map((group) => <option key={group}>{group}</option>)}
            </select>
            <span className={isTradingTime() ? 'watchboard-live is-open' : 'watchboard-live'}>
              <i aria-hidden="true" />
              {isTradingTime() ? '盘中' : '非盘中'}
            </span>
          </div>
        </header>

        {error && <div className="watchboard-error">{error}</div>}
        {adding && <div className="watchboard-info">已添加，正在尝试触发统一推演...</div>}
        {loading && <div className="watchboard-empty">正在加载盯盘面板...</div>}

        {!loading && displayGroups.map((group) => (
          <section className="watchboard-section" key={group.type}>
            <div className="watchboard-section-title">
              <h2>{group.name}</h2>
              <span>{group.items?.length || 0} 只</span>
            </div>
            {group.items?.length ? (
              <div className="watchboard-grid">
                {group.items.map((item) => (
                  <WatchCard
                    key={`${group.type}:${item.symbol}`}
                    item={item}
                    currentPrice={item.price}
                    previousPrice={item.previous_price}
                    onClick={() => openDetail(item)}
                  />
                ))}
              </div>
            ) : (
              <div className="watchboard-empty">暂无{group.name}票</div>
            )}
          </section>
        ))}
      </div>

      {selected && (
        <aside className="watchboard-drawer" aria-label="盯盘详情">
          <div className="watchboard-drawer-header">
            <div>
              <strong>{selected.name || selected.symbol}</strong>
              <span>{selected.symbol} · {formatPrice(selected.price)}</span>
            </div>
            <button type="button" onClick={() => setSelected(null)}>关闭</button>
          </div>

          <div className="watchboard-scenarios">
            {(selected.reasoning_summary?.scenarios || []).map((scenario, index) => (
              <span key={`${scenario.name}-${index}`}>
                {scenario.name} {scenario.probability} · {scenario.brief}
              </span>
            ))}
          </div>

          <div className="watchboard-detail-bar">
            <div className="watchboard-detail-tabs" role="tablist" aria-label="盯盘详情内容">
              <button
                type="button"
                role="tab"
                aria-selected={activeDetailTab === 'reasoning'}
                className={activeDetailTab === 'reasoning' ? 'is-active' : ''}
                onClick={() => setActiveDetailTab('reasoning')}
              >
                完整推演
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeDetailTab === 'chat'}
                className={activeDetailTab === 'chat' ? 'is-active' : ''}
                onClick={() => setActiveDetailTab('chat')}
              >
                问答追踪
                {chatMessages.length ? <span>{chatMessages.length}</span> : null}
              </button>
            </div>
            <div className={`watchboard-tab-status is-${selectedReasoningStatus.tone}`}>
              <span aria-hidden="true" />
              <strong>{selectedReasoningStatus.label}</strong>
              <em>{selectedReasoningStatus.compact || selectedReasoningStatus.detail}</em>
              <button type="button" onClick={runReasoningForSelected} disabled={reasoningRunning}>
                {selectedReasoningStatus.actionLabel}
              </button>
            </div>
          </div>

          <div className="watchboard-drawer-body">
            {activeDetailTab === 'reasoning' ? (
              drawerLoading ? (
                <div className="watchboard-empty">正在读取完整推演...</div>
              ) : detailStatus === 'missing' ? (
                <div className="watchboard-missing-reasoning">
                  <strong>暂无完整推演</strong>
                  <p>这只票还没有生成 V5 统一推演。卡片可显示实时价格和持仓，但路径主线需要先生成推演。</p>
                  <button type="button" onClick={runReasoningForSelected} disabled={reasoningRunning}>
                    {reasoningRunning ? '生成中...' : '生成统一推演'}
                  </button>
                </div>
              ) : (
                <div className="watchboard-reasoning-content">
                  <ReactMarkdown>{fullText || '暂无完整推演。'}</ReactMarkdown>
                </div>
              )
            ) : activeDetailTab === 'chat' ? (
              <div className="watchboard-chat-panel">
                {chatMessages.length ? (
                  <div className="watchboard-chat-log" aria-live="polite" ref={chatLogRef}>
                    {chatMessages.map((message, index) => (
                      <article
                        key={`${message.role}-${index}`}
                        className={`watchboard-chat-message chat-${message.role} ${message.isHistorical ? 'is-historical' : ''}`}
                      >
                        <span>
                          {message.role === 'user' ? '你问' : '教练'}
                          {message.createdAt ? <time>{formatChatTime(message.createdAt)}</time> : null}
                          {message.isStreaming ? <em>生成中</em> : null}
                          {message.isHistorical ? <em>历史推演</em> : null}
                        </span>
                        <ReactMarkdown>{message.content || (message.isStreaming ? '...' : '')}</ReactMarkdown>
                      </article>
                    ))}
                    {chatLoading && !chatMessages.some((message) => message.isStreaming) && (
                      <article className="watchboard-chat-message chat-assistant is-loading">
                        <span>教练</span>
                        <p>正在结合完整推演和当前价格...</p>
                      </article>
                    )}
                  </div>
                ) : chatHistoryLoading ? (
                  <div className="watchboard-chat-empty">
                    <strong>正在恢复问答追踪</strong>
                    <p>会同时保留旧推演下的问题，并标注为历史推演。</p>
                  </div>
                ) : (
                  <div className="watchboard-chat-empty">
                    <strong>问一句路径问题</strong>
                    <p>适合问“跌破哪里失效”“企稳看哪里”“现在是机会还是风险”。回答会在这里完整展开。</p>
                  </div>
                )}
              </div>
            ) : null}
          </div>

          <div className="watchboard-chat">
            <div className="watchboard-quick">
              {selectedIntradayReviewQuestion ? (
                <button
                  className="is-intraday-review"
                  type="button"
                  onClick={runIntradayReview}
                  disabled={chatLoading}
                >
                  {selectedIntradayReviewLabel || '1m区间复核'}
                </button>
              ) : null}
              {QUICK_QUESTIONS.map((question) => (
                <button key={question} type="button" onClick={() => sendQuestion(question)} disabled={chatLoading}>
                  {question}
                </button>
              ))}
            </div>
            <div className={`watchboard-chat-status is-${chatStatus.tone}`} aria-live="polite">
              <span aria-hidden="true" />
              <strong>{chatStatus.label}</strong>
              <em>{chatStatus.detail}</em>
            </div>

            <form
              className="watchboard-chat-input"
              onSubmit={(event) => {
                event.preventDefault()
                sendQuestion()
              }}
            >
              <input
                value={chatInput}
                onChange={(event) => setChatInput(event.target.value)}
                placeholder={chatLoading ? (thinkingEnabled ? '深度推演中，请稍等...' : '推演中，请稍等...') : '问这只票接下来怎么看'}
                disabled={chatLoading}
              />
              <button
                type="button"
                className={`think-toggle${thinkingEnabled ? ' is-active' : ''}`}
                onClick={() => setThinkingEnabled((v) => !v)}
                title={thinkingEnabled ? '深度推演模式（DeepSeek-R1）— 点击关闭' : '开启深度推演模式（DeepSeek-R1）'}
              >
                🧠
              </button>
              <button type="submit" disabled={chatLoading || !chatInput.trim()}>
                {chatLoading ? '...' : '发送'}
              </button>
            </form>
          </div>
        </aside>
      )}
    </div>
  )
}
