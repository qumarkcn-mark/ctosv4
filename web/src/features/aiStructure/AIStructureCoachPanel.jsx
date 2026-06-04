import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API_BASE } from '../../config.js'
import { apiFetch, apiJson } from '../../api/client.js'
import './AIStructureCoachPanel.css'

const QUICK_QUESTIONS = [
  '我现在能买吗？',
  '跌破哪里就不看了？',
  '走势怎么生长？',
  '这里有没有背驰？',
  '帮我设提醒',
]
const CONTEXT_LEVELS = ['week', 'day', '30', '5']
const CONTEXT_POLL_WINDOW_MS = 300_000
const CONTEXT_POLL_INTERVAL_MS = 2_000
const DATA_DIAGNOSTICS_POLL_INTERVAL_MS = 15_000
const LEVEL_LABELS = {
  week: '周线',
  day: '日线',
  30: '30分',
  5: '5分',
}

export default function AIStructureCoachPanel({
  symbol,
  symbolName,
  workspaceSymbolState,
  workspaceLoading = false,
  onWorkspaceRefresh,
  deferLoad = false,
}) {
  const [status, setStatus] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [booting, setBooting] = useState(false)
  const [regenerating, setRegenerating] = useState(false)
  const [pollUntil, setPollUntil] = useState(0)
  const [error, setError] = useState('')
  const [pendingQuestion, setPendingQuestion] = useState('')
  const [activeQuestion, setActiveQuestion] = useState('')
  const [commandStartedAt, setCommandStartedAt] = useState(0)
  const [now, setNow] = useState(Date.now())
  const [suggestionsOpen, setSuggestionsOpen] = useState(false)
  const [reminders, setReminders] = useState([])
  const [activeSessionId, setActiveSessionId] = useState('')
  const [unifiedReasoning, setUnifiedReasoning] = useState(null)
  const [intradaySnapshot, setIntradaySnapshot] = useState(null)
  const [dataDiagnostics, setDataDiagnostics] = useState(null)
  const mountedRef = useRef(true)
  const symbolRef = useRef(symbol)
  const messagesRef = useRef(null)

  const displayName = symbolName || symbol
  const pollingActive = pollUntil > Date.now() && shouldPollContextStatus(status)
  const reasoningContext = status?.context || null
  const detailReasoningContext = unifiedReasoning
  const aiReasoningReady = isAiReasoningReady(status)
  const canAsk = Boolean(!deferLoad && status?.context && aiReasoningReady)
  const displayStatus = status?.context && !aiReasoningReady
    ? `reasoning-${aiReasoningStatus(status) || 'pending'}`
    : (status?.status || 'idle')
  const commandActive = loading || Boolean(pendingQuestion && !canAsk)
  const dataLineage = useMemo(
    () => buildDataLineageModel(status, detailReasoningContext || reasoningContext, messages, intradaySnapshot, dataDiagnostics),
    [status, detailReasoningContext, reasoningContext, messages, intradaySnapshot, dataDiagnostics],
  )
  const reasoningStale = dataLineage.reasoningStale

  const applyWorkspaceSymbolState = useCallback((state) => {
    if (!state || !sameSymbol(state.symbol, symbolRef.current)) return
    const nextStatus = normalizeWorkspaceStatus(state)
    setStatus((prev) => (shouldApplyWorkspaceStatus(prev, nextStatus) ? nextStatus : prev))
    setReminders(state.reminders?.items || [])
  }, [])

  const loadStatus = useCallback(async () => {
    if (!symbol) return null
    try {
      const levels = CONTEXT_LEVELS.join(',')
      const json = await apiJson(`${API_BASE}/ai-structure/contexts/status/${encodeURIComponent(symbol)}?levels=${levels}`)
      if (mountedRef.current) setStatus(json.data)
      return json.data || null
    } catch (err) {
      if (mountedRef.current) setError(err?.message || 'AI 结构状态读取失败')
      return null
    }
  }, [symbol])

  const loadUnifiedReasoning = useCallback(async () => {
    if (!symbol) return null
    try {
      const json = await apiJson(`${API_BASE}/ai-structure/unified-reasoning/full/${encodeURIComponent(symbol)}`)
      const data = json.data || {}
      const summary = data.summary || {}
      const next = {
        context_id: data.context_id || '',
        run_id: data.run_id || '',
        prompt_version: 'unified_reasoning.v2.full_text',
        updated_at: data.updated_at || '',
        source_snapshot_ids: data.source_snapshot_ids || [],
        snapshots: data.source_snapshots || [],
        latest_snapshot_as_of_by_level: data.latest_snapshot_as_of_by_level || {},
        summary_text: summary.coach_summary || summary.card_summary || '',
        main_level: summary.main_level || '',
        reasoning: {
          ...summary,
          version: 'unified_reasoning.v2.full_text',
          reasoning_meta: { provider: 'llm', llm_status: 'success' },
          full_text: data.full_text || '',
        },
      }
      if (mountedRef.current && sameSymbol(data.symbol || symbol, symbolRef.current)) {
        setUnifiedReasoning(next)
      }
      return next
    } catch {
      if (mountedRef.current && sameSymbol(symbol, symbolRef.current)) {
        setUnifiedReasoning(null)
      }
      return null
    }
  }, [symbol])

  const loadReminders = useCallback(async () => {
    if (!symbol) return
    try {
      const json = await apiJson(`${API_BASE}/ai-structure/reminders/${encodeURIComponent(symbol)}`)
      if (mountedRef.current) setReminders(json.data?.items || [])
    } catch {
      if (mountedRef.current) setReminders([])
    }
  }, [symbol])

  const loadIntradaySnapshot = useCallback(async () => {
    if (!symbol) return
    const requestedSymbol = symbol
    try {
      const json = await apiJson(
        `${API_BASE}/ai-structure/intraday-snapshot/${encodeURIComponent(symbol)}?recent_bar_count=0`,
      )
      if (mountedRef.current && sameSymbol(symbolRef.current, requestedSymbol)) {
        setIntradaySnapshot(json.data || null)
      }
    } catch {
      if (mountedRef.current && sameSymbol(symbolRef.current, requestedSymbol)) {
        setIntradaySnapshot(null)
      }
    }
  }, [symbol])

  const loadDataDiagnostics = useCallback(async () => {
    if (!symbol) return
    const requestedSymbol = symbol
    try {
      const json = await apiJson(`${API_BASE}/data/diagnostics/${encodeURIComponent(symbol)}`)
      if (mountedRef.current && sameSymbol(symbolRef.current, requestedSymbol)) {
        setDataDiagnostics(json.data || json || null)
      }
    } catch {
      if (mountedRef.current && sameSymbol(symbolRef.current, requestedSymbol)) {
        setDataDiagnostics(null)
      }
    }
  }, [symbol])

  const loadCurrentQuote = useCallback(async () => {
    if (!symbol) return {}
    try {
      const quote = await apiJson(`${API_BASE}/data/price/${encodeURIComponent(symbol)}`)
      const price = Number(quote?.price || 0)
      return {
        current_price: price > 0 ? price : undefined,
        quote_time: quote?.quote_time || undefined,
        change_pct: quote?.change_pct,
        price_source: price > 0 ? 'ai_workspace_quote' : undefined,
      }
    } catch {
      return {}
    }
  }, [symbol])

  const patchStreamingMessage = useCallback((streamingKey, patch) => {
    setMessages((prev) => prev.map((item) => (
      item.streamingKey === streamingKey ? { ...item, ...patch } : item
    )))
  }, [])

  const appendStreamingMessage = useCallback((streamingKey, delta) => {
    if (!delta) return
    setMessages((prev) => prev.map((item) => {
      if (item.streamingKey !== streamingKey) return item
      const answer = item.answer || {}
      return {
        ...item,
        answer: {
          ...answer,
          coach_answer: `${answer.coach_answer || answer.answer || ''}${delta}`,
        },
      }
    }))
  }, [])

  const streamAnswer = useCallback(async ({ question, quotePayload, streamingKey }) => {
    const response = await apiFetch(`${API_BASE}/ai-structure/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol,
        question,
        session_id: activeSessionId || undefined,
        ...quotePayload,
      }),
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
          appendStreamingMessage(streamingKey, event.data?.content || '')
        } else if (event.event === 'done') {
          doneMeta = event.data || {}
        } else if (event.event === 'error') {
          throw new Error(event.data?.message || '流式问答失败')
        }
      }
    }
    return doneMeta || {}
  }, [symbol, activeSessionId, appendStreamingMessage])

  const loadChatHistory = useCallback(async (contextId = '') => {
    if (!symbol) return
    const requestedSymbol = symbol
    try {
      const sessionsJson = await apiJson(`${API_BASE}/ai-structure/chat/sessions/${encodeURIComponent(symbol)}`)
      const latestSession = sessionsJson.data?.sessions?.[0]
      if (!latestSession?.session_id) return
      const currentContextId = contextId || ''
      if (currentContextId && latestSession.latest_context_id !== currentContextId) {
        if (mountedRef.current && symbolRef.current === requestedSymbol) {
          setActiveSessionId('')
          setMessages([])
        }
        return
      }
      const messagesJson = await apiJson(
        `${API_BASE}/ai-structure/chat/messages?session_id=${encodeURIComponent(latestSession.session_id)}`,
      )
      const restored = restoreChatMessages(
        (messagesJson.data?.messages || []).filter((row) => !currentContextId || row.context_id === currentContextId)
      )
      if (mountedRef.current && symbolRef.current === requestedSymbol) {
        setActiveSessionId(latestSession.session_id)
        setMessages(restored)
      }
    } catch {
      if (mountedRef.current && symbolRef.current === requestedSymbol) {
        setActiveSessionId('')
        setMessages([])
      }
    }
  }, [symbol])

  useEffect(() => {
    mountedRef.current = true
    symbolRef.current = symbol
    setMessages([])
    setInput('')
    setError('')
    setStatus(null)
    setPollUntil(0)
    setPendingQuestion('')
    setCommandStartedAt(0)
    setSuggestionsOpen(false)
    setReminders([])
    setActiveSessionId('')
    setUnifiedReasoning(null)
    setIntradaySnapshot(null)
    setDataDiagnostics(null)
    if (deferLoad) {
      return () => {
        mountedRef.current = false
      }
    }
    let cancelled = false
    const loadPanelData = async () => {
      if (sameSymbol(workspaceSymbolState?.symbol, symbol)) {
        applyWorkspaceSymbolState(workspaceSymbolState)
      }
      const nextStatus = await loadStatus()
      if (cancelled) return
      await Promise.all([
        loadUnifiedReasoning(),
        loadReminders(),
        loadIntradaySnapshot(),
        loadChatHistory(nextStatus?.context?.context_id || ''),
      ])
    }
    loadPanelData()
    return () => {
      cancelled = true
      mountedRef.current = false
    }
  }, [
    symbol,
    deferLoad,
    workspaceSymbolState,
    applyWorkspaceSymbolState,
    loadStatus,
    loadUnifiedReasoning,
    loadReminders,
    loadIntradaySnapshot,
    loadChatHistory,
  ])

  useEffect(() => {
    if (!symbol) return
    loadDataDiagnostics()
  }, [symbol, loadDataDiagnostics])

  useEffect(() => {
    if (!symbol) return undefined
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return
      loadDataDiagnostics()
    }, DATA_DIAGNOSTICS_POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [symbol, loadDataDiagnostics])

  useEffect(() => {
    if (deferLoad) return
    if (sameSymbol(workspaceSymbolState?.symbol, symbol)) {
      applyWorkspaceSymbolState(workspaceSymbolState)
    }
  }, [symbol, deferLoad, workspaceSymbolState, applyWorkspaceSymbolState])

  useEffect(() => {
    if (!symbol || !pollUntil) return undefined
    if (!shouldPollContextStatus(status)) {
      setPollUntil(0)
      return undefined
    }
    if (Date.now() >= pollUntil) {
      setPollUntil(0)
      return undefined
    }
    const timer = window.setInterval(() => {
      if (Date.now() >= pollUntil) {
        setPollUntil(0)
        return
      }
      loadStatus()
    }, CONTEXT_POLL_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [symbol, pollUntil, status?.status, loadStatus])

  const prewarm = useCallback(async () => {
    if (!symbol || deferLoad || booting || pollingActive) return
    setBooting(true)
    setError('')
    try {
      await apiJson(`${API_BASE}/ai-structure/pipeline/ensure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbols: [symbol],
          levels: CONTEXT_LEVELS,
          reason: 'web_ai_structure_manual_context',
          allow_context_enqueue: true,
        }),
      })
      onWorkspaceRefresh?.({ ensurePipeline: true })
      setPollUntil(Date.now() + CONTEXT_POLL_WINDOW_MS)
      await loadStatus()
    } catch (err) {
      setError(err?.message || '预热失败，先更新 K 线后再试')
    } finally {
      setBooting(false)
    }
  }, [symbol, deferLoad, booting, pollingActive, loadStatus, onWorkspaceRefresh])

  const regenerateReasoning = useCallback(async () => {
    if (!symbol || deferLoad || regenerating) return
    setRegenerating(true)
    setError('')
    try {
      await apiJson(`${API_BASE}/ai-structure/unified-reasoning/trigger`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbols: [symbol],
          levels: CONTEXT_LEVELS,
          trigger_reason: 'manual_full_reasoning',
          force: true,
        }),
      })
      await loadUnifiedReasoning()
      await loadStatus()
    } catch (err) {
      setError(err?.message || '重新生成推演失败')
    } finally {
      setRegenerating(false)
    }
  }, [symbol, deferLoad, regenerating, loadStatus, loadUnifiedReasoning])

  const ask = useCallback(async (questionText = input) => {
    const question = questionText.trim()
    if (!question || loading || !symbol) return
    setSuggestionsOpen(false)
    if (!canAsk) {
      setPendingQuestion(question)
      setActiveQuestion(question)
      setInput('')
      setMessages((prev) => {
        const next = [...prev]
        if (!next.some((item) => item.role === 'user' && item.pending && item.text === question)) {
          next.push({ role: 'user', text: question, pending: true })
        }
        return next
      })
      await prewarm()
      return
    }
    setLoading(true)
    setActiveQuestion(question)
    setError('')
    setInput('')
    const streamingKey = `assistant-${Date.now()}`
    setMessages((prev) => [
      ...prev.filter((item) => !(item.role === 'user' && item.pending && item.text === question)),
      { role: 'user', text: question },
      { role: 'assistant', answer: { coach_answer: '' }, streamingKey, isStreaming: true },
    ])
    try {
      const quotePayload = await loadCurrentQuote()
      const doneMeta = await streamAnswer({ question, quotePayload, streamingKey })
      patchStreamingMessage(streamingKey, { isStreaming: false })
      if (doneMeta.session_id) setActiveSessionId(doneMeta.session_id)
      setPendingQuestion('')
      await loadChatHistory()
      await loadStatus()
      await loadIntradaySnapshot()
      await loadDataDiagnostics()
    } catch (err) {
      patchStreamingMessage(streamingKey, { isStreaming: false })
      setError(err?.message || 'AI 问答失败')
    } finally {
      setLoading(false)
      setActiveQuestion('')
    }
  }, [input, loading, symbol, canAsk, prewarm, loadStatus, activeSessionId, loadCurrentQuote, streamAnswer, patchStreamingMessage, loadChatHistory, loadIntradaySnapshot, loadDataDiagnostics])

  useEffect(() => {
    if (!pendingQuestion || loading || !canAsk) return
    ask(pendingQuestion)
  }, [pendingQuestion, loading, canAsk, ask])

  useEffect(() => {
    const node = messagesRef.current
    if (!node) return
    node.scrollTop = node.scrollHeight
  }, [messages])

  useEffect(() => {
    if (!commandActive) {
      setCommandStartedAt(0)
      return undefined
    }
    setCommandStartedAt((value) => value || Date.now())
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [commandActive])

  const createReminder = useCallback(async (answer, candidate) => {
    if (!answer?.session_id || !answer?.message_id || !candidate?.evidence_id) return
    setError('')
    try {
      const json = await apiJson(`${API_BASE}/ai-structure/reminders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: answer.session_id,
          message_id: answer.message_id,
          evidence_id: candidate.evidence_id,
        }),
      })
      setMessages((prev) => [...prev, {
        role: 'system',
        text: json.data?.duplicate ? '提醒已存在' : '提醒已创建',
      }])
      await loadReminders()
    } catch (err) {
      setError(err?.message || '提醒创建失败')
    }
  }, [loadReminders])

  const ackReminder = useCallback(async (reminder, action) => {
    if (!reminder?.id) return
    const labels = {
      handled: '已标记处理',
      continue_watch: '继续观察',
      ignored: '已忽略，后续会进入复盘',
    }
    setError('')
    try {
      await apiJson(`${API_BASE}/ai-structure/reminders/${reminder.id}/ack`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      })
      setMessages((prev) => [...prev, { role: 'system', text: labels[action] || '提醒已更新' }])
      await loadReminders()
    } catch (err) {
      setError(err?.message || '提醒状态更新失败')
    }
  }, [loadReminders])

  const statusLabel = useMemo(() => {
    if (workspaceLoading && !status) return '启动中'
    if (pollingActive) return '生成中'
    if (!status) return '检测中'
    const aiStatus = aiReasoningStatus(status)
    if (status.context && ['failed', 'unavailable'].includes(aiStatus)) return '推演暂未完成'
    if (status.context && aiStatus !== 'success') return '推演中'
    if (status.status === 'fresh') return '结构就绪'
    if (status.status === 'stale') return '结构待刷新'
    if (status.status === 'pending') return '生成中'
    if (status.status === 'failed') return '生成失败'
    if (status.status === 'no_snapshot') return '待生成'
    return status.status || '未知'
  }, [status, pollingActive, workspaceLoading])

  const pipelineItems = useMemo(() => buildPipelineItems(status, {
    booting: booting || (workspaceLoading && !status),
    canAsk,
    loading,
    activeQuestion,
    pendingQuestion,
    pollingActive,
    dataDiagnostics,
  }), [status, booting, workspaceLoading, canAsk, loading, activeQuestion, pendingQuestion, pollingActive, dataDiagnostics])

  return (
    <section className="ai-structure-panel">
      <header className="ai-structure-head">
        <div>
          <span className="ai-structure-kicker">AI Native V5</span>
          <h3>{displayName}</h3>
        </div>
        <div className="ai-structure-head-actions">
          {symbol && (
            <button
              type="button"
              onClick={regenerateReasoning}
              disabled={regenerating || !symbol}
            >
              {regenerating ? '生成中' : reasoningStale ? '更新推演' : '重新生成'}
            </button>
          )}
          <span className={`ai-structure-status ai-structure-status--${displayStatus}`}>
            {statusLabel}
          </span>
        </div>
      </header>


      <ReasoningBrief context={detailReasoningContext} status={status} />

      {commandActive && (
        <ThinkingStatusBar
          phase={loading ? 'thinking' : 'context'}
          elapsedSeconds={elapsedSeconds(commandStartedAt, now)}
        />
      )}

      {!canAsk && (
        <div className="ai-structure-empty">
          <p>{emptyText(status, pollingActive)}</p>
          <button type="button" onClick={prewarm} disabled={booting || pollingActive || !symbol}>
            {booting ? '入队中' : pollingActive ? '生成中' : '生成上下文'}
          </button>
        </div>
      )}

      {pendingQuestion && !canAsk && (
        <div className="ai-structure-pending">
          <span>已收到问题</span>
          <strong>{pendingQuestion}</strong>
          <em>结构上下文就绪后自动回答</em>
        </div>
      )}

      <div className="ai-structure-messages" ref={messagesRef}>
        {messages.map((item, index) => (
          <Message
            key={`${item.role}-${index}`}
            item={item}
            context={reasoningContext}
          />
        ))}
      </div>

      {error && <div className="ai-structure-error">{error}</div>}

      <form className="ai-structure-input" onSubmit={(event) => {
        event.preventDefault()
        ask()
      }}>
        {suggestionsOpen && !loading && (
          <div className="ai-structure-input-suggestions">
            {QUICK_QUESTIONS.map((item) => (
              <button
                key={item}
                type="button"
                onMouseDown={(event) => {
                  event.preventDefault()
                  ask(item)
                }}
              >
                {item}
              </button>
            ))}
          </div>
        )}
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={canAsk ? '问：跌破哪里就不看了？' : '直接提问，会先生成结构上下文'}
          onFocus={() => setSuggestionsOpen(true)}
          onBlur={() => window.setTimeout(() => setSuggestionsOpen(false), 120)}
          disabled={loading || !symbol}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          {loading ? '...' : '问'}
        </button>
      </form>
    </section>
  )
}

function ReasoningBrief({ context, status }) {
  if (!context) return null
  if (!isAiReasoningReady({ context })) return null
  const reasoning = context.reasoning || context
  const isUnified = String(reasoning.version || context.prompt_version || '').startsWith('unified_reasoning')
  const growth = reasoning.trend_growth || {}
  const summary = panelSummary(reasoning, context)
  const mainLevel = formatLevel(reasoning.main_level || context.main_level)
  const growthText = isUnified ? '' : buildGrowthText(growth)
  if (!summary && !growthText && !mainLevel) return null
  return (
    <section className="ai-reasoning-brief" aria-label="AI 当前推演">
      <div className="ai-reasoning-brief-head">
        <strong>当前推演</strong>
        <span>{mainLevel ? `主观察：${mainLevel}` : 'AI 推演已完成'}</span>
      </div>
      {summary && <p>{summary}</p>}
      {growthText && (
        <div className="ai-reasoning-growth">
          <span>走势如何生长</span>
          <em>{growthText}</em>
        </div>
      )}
    </section>
  )
}

function WatchPlanPanel({ context, status, reasoningStale = false }) {
  if (!context) return null
  if (!isAiReasoningReady({ context })) return null
  if (reasoningStale) return null
  if (isStructureReasoningStale(status)) return null
  const reasoning = context.reasoning || context
  const plan = buildWatchPlan(reasoning, context)
  if (!plan.mainTask && !plan.levels.length && !plan.tPlan) return null
  return (
    <section className="ai-watch-plan" aria-label="AI 关键分支">
      <div className="ai-watch-plan-head">
        <strong>关键分支</strong>
        <span>结合盘中走势复核</span>
      </div>
      {plan.mainTask && <p>{plan.mainTask}</p>}
      {!!plan.levels.length && (
        <div className="ai-watch-plan-levels">
          {plan.levels.map((item, index) => (
            <div key={`${item.type}-${item.price}-${index}`} className={`ai-watch-plan-level ai-watch-plan-level--${item.tone}`}>
              <div className="ai-watch-plan-level-price">
                <span>{item.label}</span>
                <strong>{formatPlanPrice(item.price)}</strong>
              </div>
              <em>{item.note}</em>
            </div>
          ))}
        </div>
      )}
      {plan.tPlan && (
        <div className="ai-watch-plan-t">
          <span>盘中做T观察</span>
          <em>{plan.tPlan}</em>
        </div>
      )}
    </section>
  )
}

function isStructureReasoningStale(status) {
  if (!status) return false
  return (
    status.status === 'stale' ||
    Boolean(status.stale_levels?.length) ||
    statusReason(status) === 'SOURCE_SNAPSHOT_CHANGED'
  )
}

function buildWatchPlan(reasoning = {}, context = {}) {
  const watchPlan = reasoning.watch_plan || {}
  const growth = reasoning.trend_growth || {}
  const mainTask = String(
    watchPlan.main_task ||
    watchPlan.card?.summary ||
    reasoning.card_summary ||
    reasoning.one_liner ||
    growth.next_confirmation ||
    growth.current_state ||
    '',
  ).trim()
  const keyLevels = Array.isArray(watchPlan.key_levels) ? watchPlan.key_levels : []
  return {
    mainTask,
    levels: keyLevels
      .map(normalizeWatchLevel)
      .filter((item) => item.price > 0)
      .slice(0, 4),
    tPlan: watchPlan.t_plan?.enabled ? String(watchPlan.t_plan?.note || watchPlan.t_plan?.plan || '只在触发关键位后再结合分时确认').trim() : '',
  }
}

function normalizeWatchLevel(item = {}) {
  const trigger = String(item.trigger || item.type || '')
  const price = Number(item.price ?? item.level ?? 0)
  const rawType = String(item.type || '')
  const isSupport = rawType.includes('support') || trigger === 'price_below'
  const note = String(item.note || item.after || item.message_on_trigger || '').trim()
  return {
    type: rawType || (isSupport ? 'support' : 'pressure'),
    tone: isSupport ? 'support' : 'pressure',
    label: isSupport ? '下方支撑' : '上方压力',
    price,
    note: note || (isSupport ? '跌破后看是否快速收回' : '站上后看是否回踩确认'),
  }
}

function formatPlanPrice(value) {
  const num = Number(value || 0)
  if (!Number.isFinite(num) || num <= 0) return '--'
  return num >= 100 ? num.toFixed(2) : num.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
}

function DataLineageStrip({ model }) {
  const items = model?.items || []
  if (!items.length) return null
  return (
    <div className="ai-data-lineage" aria-label="推演、结构图和盘中观察的数据状态">
      {items.map((item) => (
        <span key={item.key} className={`ai-data-lineage-pill ai-data-lineage-pill--${item.tone}`}>
          <em>{item.label}</em>
          <strong>{item.value}</strong>
        </span>
      ))}
    </div>
  )
}

function buildDataLineageModel(status, context, messages = [], intradaySnapshot = null, dataDiagnostics = null) {
  const freshness = primaryFreshness(status, context)
  const latestFreshness = latestStructureFreshness(status, context)
  const reasoningStale = Boolean(context && (freshness.stale || status?.status === 'stale'))
  return {
    items: buildDataLineageItems(status, context, messages, intradaySnapshot, dataDiagnostics, {
      freshness,
      latestFreshness,
      reasoningStale,
    }),
    reasoningStale,
    freshness,
    latestFreshness,
  }
}

function buildDataLineageItems(status, context, messages = [], intradaySnapshot = null, dataDiagnostics = null, model = {}) {
  const items = []
  const freshness = model.freshness || primaryFreshness(status, context)
  const latestFreshness = model.latestFreshness || latestStructureFreshness(status, context)
  const reasoningStale = Boolean(model.reasoningStale)
  const ai = aiReasoning(status)
  if (!context) {
    items.push({ key: 'reasoning', label: '推演', value: '待生成', tone: 'muted' })
  } else if (!ai.ready) {
    items.push({ key: 'reasoning', label: '推演', value: ai.status === 'failed' ? '失败' : '生成中', tone: ai.status === 'failed' ? 'error' : 'warn' })
  } else {
    items.push({
      key: 'reasoning',
      label: '推演',
      value: reasoningStale ? `旧 ${freshness.label}` : freshness.label === '待生成' ? formatAsOf(context.updated_at) || '已生成' : freshness.label,
      tone: reasoningStale ? 'warn' : 'ready',
    })
  }

  const snapshotValue = latestFreshness.label && latestFreshness.label !== '待生成'
    ? latestFreshness.label
    : status?.missing_levels?.length
      ? `缺${formatLevels(status.missing_levels)}`
      : '待生成'
  items.push({
    key: 'snapshot',
    label: '快照',
    value: snapshotValue,
    tone: status?.missing_levels?.length ? 'warn' : 'muted',
  })
  items.push({
    key: 'preview',
    label: '图',
    value: context ? (reasoningStale ? 'AI待重跑' : '同源') : 'preview',
    tone: context ? (reasoningStale ? 'warn' : 'ready') : 'muted',
  })

  const intraday = formatIntradayDiagnosticsStatus(dataDiagnostics)
    || formatIntradaySnapshotStatus(intradaySnapshot)
    || latestIntradayCoverageStatus(messages)
  if (intraday?.value) {
    items.push({ key: 'intraday', label: '盘中', ...intraday })
  }
  return items
}

function formatIntradayDiagnosticsStatus(diagnostics) {
  if (!diagnostics) return null
  const routing = diagnostics.routing || {}
  const preview = diagnostics.intraday_preview || {}
  const sampler = diagnostics.sampler || {}
  const readiness = diagnostics.readiness || {}
  const activeRows = Number(preview.active_rows || 0)
  if (activeRows > 0 || routing.m1_display_primary === 'intraday_bars') {
    return {
      value: `实时 ${activeRows || ''}根`.trim(),
      tone: 'ready',
    }
  }
  if (routing.m1_display_primary === 'tdx_lake') {
    return {
      value: formatAsOf(diagnostics.official_1m?.display_last_at || '') || 'TDX历史',
      tone: readiness.status === 'waiting' ? 'warn' : 'muted',
    }
  }
  if (sampler.last_error) {
    return {
      value: compactDataReason(sampler.last_error),
      tone: 'error',
    }
  }
  return null
}

function formatIntradaySnapshotStatus(snapshot) {
  if (!snapshot) return null
  const date = formatAsOf(snapshot.date || '')
  if (!snapshot.available) {
    return {
      value: date ? `${date} 无数据` : '无数据',
      tone: 'warn',
    }
  }
  const coverage = snapshot.coverage || {}
  const quality = String(coverage.quality || '')
  const count = Number(coverage.bar_count || 0)
  const coverageLabel = quality === 'complete_from_open'
    ? '完整'
    : quality === 'partial'
      ? '部分'
      : quality || '未知'
  const countLabel = count > 0 ? ` ${count}根` : ''
  return {
    value: `${date || '最新'} ${coverageLabel}${countLabel}`,
    tone: quality === 'complete_from_open' ? 'ready' : quality === 'partial' ? 'warn' : 'muted',
  }
}

function latestIntradayCoverageStatus(messages = []) {
  const assistant = [...messages].reverse().find((item) => item?.role === 'assistant' && item.answer)
  const quality = assistant?.answer?.intraday_observation?.coverage?.quality
  if (!quality) return null
  const text = String(quality)
  return {
    value: text,
    tone: text === 'full' || text === 'complete_from_open' ? 'ready' : text === 'none' ? 'muted' : 'warn',
  }
}

function buildFreshnessRows(status, context) {
  const contextRows = (context?.raw_context?.snapshots || context?.snapshots || []).map((item) => ({
    level: item.level,
    data_as_of: item.data_as_of,
    status: 'fresh',
  }))
  const latestRows = status?.level_freshness || []
  const latestByLevel = new Map(latestRows.map((item) => [item.level, item]))
  Object.entries(context?.latest_snapshot_as_of_by_level || {}).forEach(([level, dataAsOf]) => {
    if (!latestByLevel.has(level)) {
      latestByLevel.set(level, { level, data_as_of: dataAsOf, status: 'fresh' })
    }
  })
  const rows = contextRows.length ? contextRows : latestRows
  const stale = new Set(status?.stale_levels || [])
  return rows
    .filter((item) => item?.level && item?.data_as_of)
    .map((item) => {
      const latest = latestByLevel.get(item.level)
      return {
        level: item.level,
        data_as_of: item.data_as_of,
        stale: stale.has(item.level)
          || ['stale', 'pending', 'failed'].includes(item.status)
          || Boolean(latest?.data_as_of && latest.data_as_of !== item.data_as_of),
      }
    })
}

function activationMeta(state) {
  const sources = new Set((state?.sources || []).map((item) => String(item || '').toLowerCase()))
  if (state?.has_position || sources.has('positions')) {
    return { label: '持仓', tone: 'auto' }
  }
  if (sources.has('recent_chat')) {
    return { label: '最近问过', tone: 'auto' }
  }
  if (sources.has('watchlist')) {
    return { label: '手动生成', tone: 'manual' }
  }
  return { label: '手动生成', tone: 'manual' }
}

function primaryFreshness(status, context) {
  const rows = buildFreshnessRows(status, context)
  if (!rows.length) return { label: '待生成', stale: false }
  const reasoning = context?.reasoning || context || {}
  const preferredLevel = reasoning.main_level || context?.main_level || '30'
  const preferred = rows.find((item) => String(item.level) === String(preferredLevel))
    || rows.find((item) => String(item.level) === '30')
    || rows.find((item) => String(item.level) === 'day')
    || rows[0]
  return {
    label: `${formatLevel(preferred.level)} ${formatAsOf(preferred.data_as_of)}`,
    stale: Boolean(preferred.stale),
  }
}

function latestStructureFreshness(status, context) {
  const latestRows = [
    ...(status?.level_freshness || []),
    ...Object.entries(context?.latest_snapshot_as_of_by_level || {}).map(([level, dataAsOf]) => ({
      level,
      data_as_of: dataAsOf,
      status: 'fresh',
    })),
  ]
  const unique = []
  const seen = new Set()
  latestRows.forEach((item) => {
    if (!item?.level || !item?.data_as_of || seen.has(item.level)) return
    seen.add(item.level)
    unique.push(item)
  })
  return primaryFreshness(
    { ...status, stale_levels: [], status: unique.length ? 'fresh' : status?.status },
    { snapshots: unique },
  )
}

function panelSummary(reasoning = {}, context = {}) {
  const raw = String(
    reasoning.front_panel_text ||
    context.front_panel_text ||
    reasoning.coach_summary ||
    context.coach_summary ||
    context.summary_text ||
    '',
  ).trim()
  const badMarkers = ['收到数据', '看了数据', '开始', '请坐', '下面', '我的分析']
  const preferredMarkers = ['当前', '核心', '结构', '走势', '中枢', '三买', '三卖', '回拉', '突破', '跌破', '观察']
  const parts = raw
    .replace(/[*_`]+/g, '')
    .split(/[。！？\n]/)
    .map((item) => item.replace(/^[#>*\-\d.、\s]+/, '').trim())
    .filter(Boolean)
  const preferred = parts.find((item) => !badMarkers.some((marker) => item.includes(marker)) && preferredMarkers.some((marker) => item.includes(marker)))
  if (preferred) return preferred
  const clean = parts.find((item) => !badMarkers.some((marker) => item.includes(marker)))
  if (clean) return clean
  const boundary = context.boundary || reasoning.boundary || {}
  const primary = formatLevel(boundary.primary_level || context.main_level || reasoning.main_level)
  return primary ? `${primary}结构已生成，围绕触发线和失败线观察` : ''
}

function buildGrowthText(growth = {}) {
  const items = [
    growth.growth_path,
    growth.next_confirmation ? `下一步确认：${growth.next_confirmation}` : '',
    growth.failure_path ? `风险演化：${growth.failure_path}` : '',
  ]
  return items.map((item) => String(item || '').trim()).filter(Boolean).join('\n')
}

function PipelineStatus({ items }) {
  return (
    <div className="ai-pipeline" aria-label="AI 结构数据流水线状态">
      {items.map((item) => (
        <div
          key={item.key}
          className={`ai-pipeline-step ai-pipeline-step--${item.tone}`}
          title={item.title || `${item.label}：${item.detail}`}
        >
          <span className="ai-pipeline-dot" aria-hidden="true" />
          <strong>{item.label}</strong>
          <em>{item.detail}</em>
        </div>
      ))}
    </div>
  )
}

function AutoActivationStrip({ workspaceSymbolState, status, context }) {
  const activation = activationMeta(workspaceSymbolState)
  const freshness = primaryFreshness(status, context)
  return (
    <div className="ai-auto-activation" aria-label="AI 自动推演范围与数据截止">
      <div className={`ai-auto-activation-badge ai-auto-activation-badge--${activation.tone}`}>
        <span>自动推演</span>
        <strong>{activation.label}</strong>
      </div>
      <div className={`ai-auto-activation-cutoff ${freshness.stale ? 'is-stale' : ''}`}>
        <span>数据截止</span>
        <strong>{freshness.label}</strong>
      </div>
    </div>
  )
}

function ThinkingStatusBar({ phase, elapsedSeconds }) {
  const isThinking = phase === 'thinking'
  const slowHint = elapsedSeconds >= 20
  const label = isThinking ? 'Think 正在推演' : '正在读取推演上下文'
  const detail = slowHint
    ? '结构问题会稍慢一些'
    : isThinking
      ? '结合完整推演与当前价格'
      : '读取结构、边界和持仓背景'
  return (
    <div className="ai-thinking-bar" aria-live="polite">
      <span className="ai-thinking-bar-light" aria-hidden="true" />
      <div className="ai-thinking-bar-main">
        <strong>{label}</strong>
        <em>{detail}</em>
      </div>
      <span className="ai-thinking-bar-time">{elapsedSeconds}s</span>
    </div>
  )
}

function StatusNotice({ status, pollingActive, onRetry, retrying, hasUnifiedReasoning = false }) {
  const notice = statusNotice(status, pollingActive, { hasUnifiedReasoning })
  if (!notice) return null
  return (
    <div className={`ai-status-notice ai-status-notice--${notice.tone}`}>
      <div className="ai-status-notice-copy">
        <strong>{notice.title}</strong>
        <span>{notice.text}</span>
      </div>
      {notice.retryable && (
        <button type="button" onClick={onRetry} disabled={retrying}>
          {retrying ? '生成中' : '重新生成'}
        </button>
      )}
    </div>
  )
}

function ReminderStatus({ reminders, onAck }) {
  if (!reminders?.length) return null
  const active = reminders.filter((item) => item.status === 'ACTIVE')
  const triggered = reminders.filter((item) => item.status === 'TRIGGERED')
  return (
    <div className="ai-reminder-status" aria-label="AI 结构提醒">
      <div className="ai-reminder-status-head">
        <strong>提醒</strong>
        <span>{active.length} 个盯盘中{triggered.length ? ` / ${triggered.length} 个已触发` : ''}</span>
      </div>
      <div className="ai-reminder-status-list">
        {reminders.slice(0, 4).map((item) => (
          <div key={item.dedupe_key} className={`ai-reminder-chip ai-reminder-chip--${item.status.toLowerCase()}`}>
            <div className="ai-reminder-chip-main">
              <span>{item.direction === 'ABOVE' ? '上破' : '跌破'} {Number(item.trigger_price || 0).toFixed(2)}</span>
              <em>{reminderStatusLabel(item.status)}</em>
            </div>
            {item.status === 'TRIGGERED' && (
              <div className="ai-reminder-actions" aria-label="提醒后续处理">
                <button type="button" onClick={() => onAck(item, 'handled')}>已处理</button>
                <button type="button" onClick={() => onAck(item, 'continue_watch')}>继续观察</button>
                <button type="button" onClick={() => onAck(item, 'ignored')}>忽略</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function reminderStatusLabel(status) {
  if (status === 'TRIGGERED') return '已触发'
  if (status === 'ACKED_HANDLED') return '已处理'
  if (status === 'ACKED_CONTINUE_WATCH') return '继续观察'
  if (status === 'ACKED_IGNORED') return '已忽略'
  return '盯盘中'
}

function Message({ item, context }) {
  if (item.role === 'user') {
    return (
      <div className={`ai-msg ai-msg--user ${item.pending ? 'ai-msg--pending' : ''}`}>
        {item.text}
      </div>
    )
  }
  if (item.role === 'system') {
    return <div className="ai-msg ai-msg--system">{item.text}</div>
  }
  const answer = item.answer || {}
  const answerText = answer.coach_answer || answer.answer || ''
  const dataFreshnessMeta = formatAnswerDataFreshness(answer.data_freshness)
  const hasDisclaimerInAnswer = String(answerText).includes(answer.risk_disclaimer || '')
  return (
    <div className="ai-msg ai-msg--assistant">
      <div className="ai-msg-content">{renderCoachText(answerText)}</div>
      {dataFreshnessMeta && (
        <div className={`ai-msg-data-meta ai-msg-data-meta--${dataFreshnessMeta.tone}`}>
          {dataFreshnessMeta.items.map((item) => <span key={item}>{item}</span>)}
        </div>
      )}
      {answer.risk_disclaimer && !hasDisclaimerInAnswer && <span>{answer.risk_disclaimer}</span>}
    </div>
  )
}

function formatAnswerDataFreshness(freshness) {
  if (!freshness) return null
  const intraday = freshness.intraday_basis || {}
  const postmarket = freshness.postmarket_1m_basis || {}
  const structureStatus = String(freshness.structure_status || '')
  const source = intraday.source
    ? '盘中观察'
    : postmarket.available
      ? 'TDX历史'
      : freshness.current_price_source
        ? '价格'
        : ''
  if (!source && !structureStatus) return null
  const asOf = formatAsOf(intraday.as_of || postmarket.date || freshness.context?.updated_at || '')
  const quality = String((intraday.coverage || {}).quality || (postmarket.coverage || {}).quality || '')
  const structure = structureStatus === 'fresh'
    ? '结构fresh'
    : structureStatus
      ? '结构待刷新'
      : ''
  const items = [
    source,
    quality ? formatDataQuality(quality) : '',
    structure,
    asOf,
  ].filter(Boolean)
  const tone = structureStatus && structureStatus !== 'fresh'
    ? 'warn'
    : quality === 'partial'
      ? 'warn'
      : 'ready'
  return items.length ? { items, tone } : null
}

function formatDataQuality(value) {
  if (value === 'complete_from_open' || value === 'full') return '完整'
  if (value === 'partial') return '部分'
  if (value === 'none') return '无盘中'
  return value
}

function renderCoachText(text) {
  const lines = String(text || '').split('\n')
  const blocks = []
  let listItems = []

  const flushList = () => {
    if (!listItems.length) return
    blocks.push(
      <ul key={`list-${blocks.length}`} className="ai-msg-list">
        {listItems.map((item, index) => <li key={`${item}-${index}`}>{cleanMarkdownText(item)}</li>)}
      </ul>,
    )
    listItems = []
  }

  lines.forEach((rawLine) => {
    const line = rawLine.trim()
    if (!line) {
      flushList()
      return
    }
    if (/^-{3,}$/.test(line)) {
      flushList()
      blocks.push(<hr key={`hr-${blocks.length}`} />)
      return
    }
    if (line.startsWith('## ')) {
      flushList()
      blocks.push(<strong key={`h-${blocks.length}`} className="ai-msg-heading">{cleanMarkdownText(line.slice(3))}</strong>)
      return
    }
    if (line.startsWith('### ')) {
      flushList()
      blocks.push(<strong key={`h-${blocks.length}`} className="ai-msg-subheading">{cleanMarkdownText(line.slice(4))}</strong>)
      return
    }
    if (/^[-*]\s+/.test(line)) {
      listItems.push(line.replace(/^[-*]\s+/, ''))
      return
    }
    if (/^\d+\.\s+/.test(line)) {
      listItems.push(line.replace(/^\d+\.\s+/, ''))
      return
    }
    if (line.startsWith('|')) {
      flushList()
      blocks.push(<code key={`table-${blocks.length}`} className="ai-msg-table-line">{line}</code>)
      return
    }
    flushList()
    blocks.push(<p key={`p-${blocks.length}`}>{cleanMarkdownText(line)}</p>)
  })
  flushList()

  return blocks.length ? blocks : <p>{text}</p>
}

function cleanMarkdownText(text) {
  return String(text || '')
    .replace(/\*\*/g, '')
    .replace(/`/g, '')
    .trim()
}

function isLiveReminderCandidate(answer, candidate, context) {
  if (!candidate?.evidence_id) return false
  if (answer?.context_id && context?.context_id && answer.context_id !== context.context_id) return false
  const level = candidate.level || answer?.chart_focus?.level || ''
  const levelItem = (((context?.boundary || {}).levels || {})[level] || {})
  const currentPrice = Number(levelItem.current_price || 0)
  const triggerPrice = Number(candidate.trigger_price || 0)
  if (!currentPrice || !triggerPrice) return true
  if (candidate.direction === 'ABOVE') return currentPrice < triggerPrice
  if (candidate.direction === 'BELOW') return currentPrice > triggerPrice
  return true
}

function restoreChatMessages(rows) {
  return rows.flatMap((row) => {
    const items = []
    if (row.question_text) {
      items.push({ role: 'user', text: row.question_text })
    }
    if (row.answer) {
      items.push({ role: 'assistant', answer: row.answer })
    }
    return items
  })
}

function parseSseEvent(raw) {
  const lines = String(raw || '').split('\n')
  let event = 'message'
  const dataLines = []
  lines.forEach((line) => {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  })
  if (!dataLines.length) return null
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) }
  } catch {
    return { event, data: { content: dataLines.join('\n') } }
  }
}

function elapsedSeconds(startedAt, now) {
  if (!startedAt) return 0
  return Math.max(0, Math.floor((now - startedAt) / 1000))
}

function normalizeWorkspaceStatus(state) {
  const contextStatus = state?.context_status || {}
  const latestContext = state?.latest_context || null
  return {
    ...contextStatus,
    context: latestContext,
    symbol: contextStatus.symbol || state?.symbol,
    status: contextStatus.status || (latestContext ? 'fresh' : 'no_snapshot'),
    stale_reason: contextStatus.stale_reason || latestContext?.stale_reason || '',
    missing_levels: contextStatus.missing_levels || [],
    stale_levels: contextStatus.stale_levels || [],
    level_freshness: contextStatus.level_freshness || [],
    job: contextStatus.job || null,
  }
}

function shouldApplyWorkspaceStatus(prev, next) {
  if (!next) return false
  if (!prev) return true
  const prevReady = isAiReasoningReady(prev)
  const nextReady = isAiReasoningReady(next)
  const prevTime = statusFreshnessTime(prev)
  const nextTime = statusFreshnessTime(next)
  if (prevReady && !nextReady && !hasActiveContextJob(next)) return false
  if (prev.status === 'fresh' && next.status === 'pending' && !hasActiveContextJob(next)) return false
  if (prevTime && nextTime && nextTime < prevTime) return false
  if (prev.context?.context_id && next.context?.context_id && prev.context.context_id === next.context.context_id) {
    return nextReady || !prevReady || hasActiveContextJob(next)
  }
  return true
}

function statusFreshnessTime(status) {
  const value = status?.context?.updated_at
    || status?.updated_at
    || status?.job?.updated_at
    || status?.job?.created_at
    || ''
  const time = Date.parse(value)
  return Number.isFinite(time) ? time : 0
}

function sameSymbol(left, right) {
  if (!left || !right) return false
  return String(left).replace('.', '').toLowerCase() === String(right).replace('.', '').toLowerCase()
}

function failureText(status) {
  const reason = status?.stale_reason || status?.job?.error_code || 'UNKNOWN'
  if (reason === 'CZSC_UNAVAILABLE') return 'CZSC 结构引擎不可用。先检查依赖或 worker 配置。'
  if (reason === 'NO_DATA') return '缺少可用 K 线。先同步数据，再生成结构上下文。'
  if (reason === 'TIMEOUT') return '结构任务超时。后台会重试，稍后再问。'
  if (reason === 'SOURCE_SNAPSHOT_CHANGED') return '结构快照已更新，AI 上下文待刷新。'
  return `结构上下文生成失败：${reason}`
}

function emptyText(status, pollingActive) {
  if (status?.status === 'failed') return failureText(status)
  const ai = aiReasoning(status)
  if (status?.context && !ai.ready) return ai.message || 'AI 推演暂未完成，当前不展示本地算法边界。系统会在下一次刷新时重新生成完整推演。'
  if (pollingActive || status?.status === 'pending') return '后台正在生成结构上下文，完成后会自动回答已排队的问题。'
  if (status?.missing_levels?.length) return `缺少 ${formatLevels(status.missing_levels)} 的 CZSC 快照。可以先生成上下文，后台会补齐。`
  return '还没有结构上下文。可以直接提问，我会先生成再回答。'
}

function statusNotice(status, pollingActive, options = {}) {
  const hasUnifiedReasoning = Boolean(options.hasUnifiedReasoning)
  if (!status && !pollingActive) return null
  const reason = statusReason(status)
  const ai = aiReasoning(status)
  if (status?.context && !ai.ready) {
    if (pollingActive || hasActiveContextJob(status)) {
      return {
        tone: 'working',
        title: 'AI 推演生成中',
        text: '新的 AI 推演任务已经触发，后台完成后会自动替换当前结果。',
      }
    }
    return {
      tone: ['failed', 'unavailable'].includes(ai.status) ? 'error' : 'working',
      title: ai.title || 'AI 推演暂未完成',
      text: ai.message || 'AI 推演暂未完成，当前不展示本地算法边界。系统会在下一次刷新时重新生成完整推演。',
      retryable: ['failed', 'unavailable'].includes(ai.status),
    }
  }
  if (reason === 'NO_DATA') {
    return {
      tone: 'error',
      title: '缺少 K 线',
      text: '当前级别没有可用 K 线，先同步数据，暂时不做结构判断。',
    }
  }
  if (reason === 'CZSC_UNAVAILABLE') {
    return {
      tone: 'error',
      title: 'CZSC 不可用',
      text: '结构引擎或 worker 未就绪，页面不会回退到旧 radar。',
    }
  }
  if (status?.status === 'stale') {
    if (status.stale_levels?.length) {
      return {
        tone: hasActiveContextJob(status) ? 'working' : 'warn',
        title: hasActiveContextJob(status) ? '结构快照刷新中' : '图结构已更新，AI推演待重跑',
        text: `${formatLevels(status.stale_levels)} 已有更新结构，当前完整推演仍基于上一版；点“更新推演”后会用最新同源结构重新推演。`,
      }
    }
    if (hasUnifiedReasoning) return null
    return {
      tone: 'warn',
      title: '图结构已更新，AI推演待重跑',
      text: 'K线和CZSC结构已经更新，完整推演还停在上一版；点“更新推演”后再复核触发线和失败线。',
    }
  }
  if (status?.status === 'failed') {
    return {
      tone: 'error',
      title: '结构生成失败',
      text: failureText(status),
    }
  }
  if (pollingActive || status?.status === 'pending') {
    return {
      tone: 'working',
      title: '后台生成中',
      text: 'K 线、CZSC 快照和 AI 上下文正在排队生成，页面请求不会同步跑重型结构计算。',
    }
  }
  if (status?.missing_levels?.length && status?.context) {
    return {
      tone: 'warn',
      title: '部分级别缺失',
      text: `缺少 ${formatLevels(status.missing_levels)} 的 CZSC 快照，当前回答只基于已生成级别；补齐后再复核结构边界。`,
    }
  }
  if (status?.status === 'no_snapshot' || status?.missing_levels?.length) {
    return {
      tone: 'warn',
      title: '等待结构快照',
      text: `缺少 ${formatLevels(status?.missing_levels || []) || '目标级别'} 的 CZSC 快照，生成后才能回答结构问题。`,
    }
  }
  return null
}

function buildPipelineItems(status, flags) {
  const { booting, canAsk, loading, activeQuestion, pendingQuestion, pollingActive, dataDiagnostics } = flags
  const reason = statusReason(status)
  const isFailed = status?.status === 'failed'
  const isNoData = reason === 'NO_DATA'
  const isCzscUnavailable = reason === 'CZSC_UNAVAILABLE'
  const hasMissingLevels = Boolean(status?.missing_levels?.length)
  // 分离快照和上下文的工作状态；没有 job 时不能把“待生成”误显示为“生成中”。
  const snapshotNeedsWork = Boolean(status?.missing_levels?.length || status?.stale_levels?.length || status?.status === 'no_snapshot')
  const isSnapshotWorking = Boolean(snapshotNeedsWork && (booting || pollingActive || hasActiveContextJob(status)))
  const isContextWorking = hasActiveContextJob(status)
  const contextTone = contextStatusTone(status, isContextWorking)

  return [
    {
      key: 'kline',
      label: 'K线',
      tone: !status ? 'checking' : isNoData ? 'error' : booting ? 'working' : 'ready',
      detail: !status ? '检测中' : isNoData ? '缺数据' : booting ? '同步中' : '已接入',
    },
    buildDataPipelineItem(dataDiagnostics),
    {
      key: 'snapshot',
      label: 'CZSC快照',
      tone: snapshotStatusTone(status, isSnapshotWorking),
      detail: snapshotStatusDetail(status, { hasMissingLevels, isCzscUnavailable, isFailed, isNoData, isWorking: isSnapshotWorking }),
    },
    {
      key: 'context',
      label: 'AI上下文',
      tone: contextTone,
      detail: contextStatusDetail(status, { canAsk, isFailed, isWorking: isContextWorking }),
    },
    {
      key: 'chat',
      label: '问答',
      tone: loading || pendingQuestion ? 'working' : canAsk ? 'ready' : 'waiting',
      detail: loading ? '回答中' : activeQuestion || pendingQuestion ? '已收到' : canAsk ? '可提问' : '可先问',
    },
  ]
}

function buildDataPipelineItem(diagnostics) {
  if (!diagnostics) {
    return {
      key: 'data',
      label: '数据',
      tone: 'checking',
      detail: '检测中',
    }
  }
  const readiness = diagnostics.readiness || {}
  const routing = diagnostics.routing || {}
  const official = diagnostics.official_1m || {}
  const preview = diagnostics.intraday_preview || {}
  const status = String(readiness.status || 'unknown')
  const reason = String(readiness.reason || '')
  const m1Route = String(routing.m1_display_primary || '')
  const formalRoute = String(routing.formal_czsc_primary || '')
  const routeLabel = m1Route === 'intraday_bars'
    ? '实时1m'
    : m1Route === 'tdx_lake'
      ? 'TDX历史'
      : m1Route === 'missing'
        ? '缺1m'
        : '未知'
  const tone = status === 'blocked' || m1Route === 'missing'
    ? 'error'
    : status === 'ready'
      ? 'ready'
      : status === 'waiting'
        ? 'warn'
        : 'checking'
  const detail = status === 'blocked'
    ? compactDataReason(reason)
    : routeLabel
  return {
    key: 'data',
    label: '数据',
    tone,
    detail,
    title: [
      `数据：${detail}`,
      formalRoute ? `结构：${formalRoute}` : '',
      official.display_last_at ? `TDX 1m：${official.display_last_at}` : '',
      preview.last_active_at ? `盘中：${preview.last_active_at}` : '',
      reason ? `状态：${reason}` : '',
    ].filter(Boolean).join('\n'),
  }
}

function compactDataReason(reason) {
  if (!reason) return '异常'
  if (reason.includes('BRIDGE')) return '桥异常'
  if (reason.includes('TIMEOUT')) return '超时'
  if (reason.includes('NO_VALID')) return '等实流'
  return '异常'
}

function snapshotStatusTone(status, isWorking) {
  if (!status) return 'checking'
  if (status.status === 'failed') return 'error'
  if (isWorking) return 'working'
  if (status.missing_levels?.length || status.status === 'no_snapshot') return 'waiting'
  return 'ready'
}

function snapshotStatusDetail(status, flags) {
  const { hasMissingLevels, isCzscUnavailable, isFailed, isNoData, isWorking } = flags
  if (!status) return '检测中'
  if (isCzscUnavailable) return '不可用'
  if (isNoData) return '缺K线'
  if (isFailed) return '失败'
  if (status?.stale_levels?.length && hasActiveContextJob(status)) return '快照刷新中'
  if (isWorking) return '生成中'
  if (hasMissingLevels) return `缺${formatLevels(status.missing_levels)}`
  if (status.status === 'no_snapshot') return '待生成'
  return '已就绪'
}

function contextStatusTone(status, isWorking) {
  if (!status) return 'checking'
  if (status.status === 'failed') return 'error'
  const ai = aiReasoning(status)
  if (isWorking) return 'working'
  if (status.context && ['failed', 'unavailable'].includes(ai.status)) return 'error'
  if (status.context && !ai.ready) return 'working'
  if (status.status === 'stale') return 'warn'
  if (status.context) return 'ready'
  if (isWorking) return 'working'
  return 'waiting'
}

function contextStatusDetail(status, flags) {
  const { canAsk, isFailed, isWorking } = flags
  if (!status) return '检测中'
  if (isFailed) return '失败'
  const ai = aiReasoning(status)
  if (isWorking && status?.context && !ai.ready) return 'LLM推演中'
  if (isWorking) return status?.stale_levels?.length ? '等待快照' : '生成中'
  if (status.context && ['failed', 'unavailable'].includes(ai.status)) return '推演失败'
  if (status.context && !ai.ready) return '推演中'
  if (status.status === 'stale') return '待刷新'
  if (canAsk) return '已就绪'
  if (isWorking) return '生成中'
  return '未生成'
}

function shouldPollContextStatus(status) {
  if (!status) return true
  if (hasActiveContextJob(status)) return true
  const ai = aiReasoning(status)
  if (status.context && ai.ready) return false
  if (status.context && ['failed', 'unavailable'].includes(ai.status)) return false
  return !['fresh', 'failed'].includes(status.status)
}

function hasActiveContextJob(status) {
  return ['PENDING', 'RUNNING', 'FAILED_RETRYABLE'].includes(status?.job?.status)
}

function aiReasoning(statusOrContext) {
  const directStatus = statusOrContext?.reasoning_status
  if (directStatus) return directStatus
  const context = statusOrContext?.context || statusOrContext
  if (!context) return { status: 'pending', ready: false, title: 'AI 推演生成中', message: 'AI 推演正在生成中，完成后会自动展示完整走势推演。' }
  const meta = (context.reasoning && context.reasoning.reasoning_meta) || context.reasoning_meta || {}
  if (meta.provider === 'llm' && meta.llm_status === 'success') return { status: 'success', ready: true }
  if (meta.llm_status === 'failed') {
    return {
      status: 'failed',
      ready: false,
      title: 'AI 推演暂未完成',
      message: 'AI 推演返回异常，当前不展示本地算法边界。系统会在下一次刷新时重新生成完整推演。',
    }
  }
  if (meta.provider === 'local_fallback' || meta.llm_status === 'not_invoked' || !meta.llm_status) {
    return {
      status: 'unavailable',
      ready: false,
      title: 'AI 推演暂未完成',
      message: 'AI 推演暂未完成，当前不展示本地算法边界。系统会在下一次刷新时重新生成完整推演。',
    }
  }
  return { status: meta.llm_status || 'pending', ready: false, title: 'AI 推演生成中', message: 'AI 推演正在生成中，完成后会自动展示完整走势推演。' }
}

function aiReasoningStatus(statusOrContext) {
  return aiReasoning(statusOrContext).status
}

function isAiReasoningReady(statusOrContext) {
  return Boolean(aiReasoning(statusOrContext).ready)
}

function statusReason(status) {
  return status?.stale_reason || status?.job?.error_code || ''
}

function formatLevels(levels = []) {
  return levels.map((level) => LEVEL_LABELS[level] || level).join('/')
}

function formatLevel(level) {
  const normalized = normalizeLevelKey(level)
  return normalized ? LEVEL_LABELS[normalized] : ''
}

function formatAsOf(value) {
  const text = String(value || '').trim()
  if (!text) return ''
  return text.length >= 16 ? text.slice(5, 16) : text.slice(5, 10) || text
}

function normalizeLevelKey(level) {
  const text = String(level || '').trim().toLowerCase()
  if (!text) return ''
  if (LEVEL_LABELS[text]) return text
  if (text.includes('周') || text.includes('week')) return 'week'
  if (text.includes('日') || text.includes('day')) return 'day'
  if (text.includes('30')) return '30'
  if (text.includes('5')) return '5'
  return ''
}
