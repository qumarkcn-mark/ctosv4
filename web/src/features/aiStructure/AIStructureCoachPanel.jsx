import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API_BASE } from '../../config.js'
import { apiJson } from '../../api/client.js'
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
  onEvidenceContext,
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
  const mountedRef = useRef(true)
  const symbolRef = useRef(symbol)
  const messagesRef = useRef(null)

  const displayName = symbolName || symbol
  const pollingActive = pollUntil > Date.now() && shouldPollContextStatus(status)
  const reasoningContext = status?.context || null
  const aiReasoningReady = isAiReasoningReady(status)
  const canAsk = Boolean(status?.context && aiReasoningReady)
  const displayStatus = status?.context && !aiReasoningReady
    ? `reasoning-${aiReasoningStatus(status) || 'pending'}`
    : (status?.status || 'idle')
  const commandActive = loading || Boolean(pendingQuestion && !canAsk)

  const applyWorkspaceSymbolState = useCallback((state) => {
    if (!state || !sameSymbol(state.symbol, symbolRef.current)) return
    const nextStatus = normalizeWorkspaceStatus(state)
    setStatus((prev) => (shouldApplyWorkspaceStatus(prev, nextStatus) ? nextStatus : prev))
    setReminders(state.reminders?.items || [])
  }, [])

  const loadStatus = useCallback(async () => {
    if (!symbol) return
    try {
      const levels = CONTEXT_LEVELS.join(',')
      const json = await apiJson(`${API_BASE}/ai-structure/contexts/status/${encodeURIComponent(symbol)}?levels=${levels}`)
      if (mountedRef.current) setStatus(json.data)
    } catch (err) {
      if (mountedRef.current) setError(err?.message || 'AI 结构状态读取失败')
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

  const loadChartEvidence = useCallback(async (answer) => {
    const focus = answer?.chart_focus
    if (!focus?.context_id || !focus?.level) return
    const params = new URLSearchParams({
      context_id: focus.context_id,
      level: focus.level,
      evidence_ids: (focus.evidence_ids || []).join(','),
    })
    const json = await apiJson(`${API_BASE}/ai-structure/chart-context/${encodeURIComponent(symbol)}?${params}`)
    if (mountedRef.current && symbolRef.current === symbol) {
      onEvidenceContext?.(json.data)
    }
  }, [symbol, onEvidenceContext])

  const loadChatHistory = useCallback(async () => {
    if (!symbol) return
    const requestedSymbol = symbol
    try {
      const levels = CONTEXT_LEVELS.join(',')
      const statusJson = await apiJson(`${API_BASE}/ai-structure/contexts/status/${encodeURIComponent(symbol)}?levels=${levels}`)
      const currentContextId = statusJson.data?.context?.context_id || ''
      const sessionsJson = await apiJson(`${API_BASE}/ai-structure/chat/sessions/${encodeURIComponent(symbol)}`)
      const latestSession = sessionsJson.data?.sessions?.[0]
      if (!latestSession?.session_id) return
      if (currentContextId && latestSession.latest_context_id !== currentContextId) {
        if (mountedRef.current && symbolRef.current === requestedSymbol) {
          setActiveSessionId('')
          setMessages([])
          onEvidenceContext?.(null)
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
        const lastAnswer = [...restored].reverse().find((item) => item.role === 'assistant')?.answer
        if (lastAnswer) {
          await loadChartEvidence(lastAnswer)
        }
      }
    } catch {
      if (mountedRef.current && symbolRef.current === requestedSymbol) {
        setActiveSessionId('')
        setMessages([])
      }
    }
  }, [symbol, loadChartEvidence, onEvidenceContext])

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
    onEvidenceContext?.(null)
    if (sameSymbol(workspaceSymbolState?.symbol, symbol)) {
      applyWorkspaceSymbolState(workspaceSymbolState)
    }
    loadStatus()
    loadReminders()
    loadChatHistory()
    return () => {
      mountedRef.current = false
    }
  }, [
    symbol,
    loadStatus,
    loadReminders,
    loadChatHistory,
    onEvidenceContext,
  ])

  useEffect(() => {
    if (sameSymbol(workspaceSymbolState?.symbol, symbol)) {
      applyWorkspaceSymbolState(workspaceSymbolState)
    }
  }, [symbol, workspaceSymbolState, applyWorkspaceSymbolState])

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
    if (!symbol || booting || pollingActive) return
    setBooting(true)
    setError('')
    try {
      await apiJson(`${API_BASE}/ai-structure/pipeline/ensure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: [symbol], levels: CONTEXT_LEVELS, reason: 'web_ai_structure_workspace' }),
      })
      onWorkspaceRefresh?.({ ensurePipeline: true })
      setPollUntil(Date.now() + CONTEXT_POLL_WINDOW_MS)
      await loadStatus()
    } catch (err) {
      setError(err?.message || '预热失败，先更新 K 线后再试')
    } finally {
      setBooting(false)
    }
  }, [symbol, booting, pollingActive, loadStatus, onWorkspaceRefresh])

  const regenerateReasoning = useCallback(async () => {
    if (!symbol || regenerating || pollingActive) return
    setRegenerating(true)
    setError('')
    try {
      await apiJson(`${API_BASE}/ai-structure/contexts/regenerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbols: [symbol],
          levels: CONTEXT_LEVELS,
          reason: 'web_ai_structure_regenerate',
          force_rebuild: true,
          priority: 100,
        }),
      })
      setPollUntil(Date.now() + CONTEXT_POLL_WINDOW_MS)
      await loadStatus()
    } catch (err) {
      setError(err?.message || '重新生成推演失败')
    } finally {
      setRegenerating(false)
    }
  }, [symbol, regenerating, pollingActive, loadStatus])

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
    try {
      const json = await apiJson(`${API_BASE}/ai-structure/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, question, session_id: activeSessionId || undefined }),
      })
      const answer = json.data
      setActiveSessionId(answer.session_id || activeSessionId)
      setMessages((prev) => [
        ...prev.filter((item) => !(item.role === 'user' && item.pending && item.text === question)),
        { role: 'user', text: question },
        { role: 'assistant', answer },
      ])
      setPendingQuestion('')
      await loadChartEvidence(answer)
      await loadStatus()
    } catch (err) {
      setError(err?.message || 'AI 问答失败')
    } finally {
      setLoading(false)
      setActiveQuestion('')
    }
  }, [input, loading, symbol, canAsk, prewarm, loadStatus, loadChartEvidence, activeSessionId])

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
  }), [status, booting, workspaceLoading, canAsk, loading, activeQuestion, pendingQuestion, pollingActive])

  return (
    <section className="ai-structure-panel">
      <header className="ai-structure-head">
        <div>
          <span className="ai-structure-kicker">AI Native V5</span>
          <h3>{displayName}</h3>
        </div>
        <div className="ai-structure-head-actions">
          {status?.context && (
            <button
              type="button"
              onClick={regenerateReasoning}
              disabled={regenerating || pollingActive || !symbol}
            >
              {regenerating || pollingActive ? '生成中' : '重新生成'}
            </button>
          )}
          <span className={`ai-structure-status ai-structure-status--${displayStatus}`}>
            {statusLabel}
          </span>
        </div>
      </header>

      <PipelineStatus items={pipelineItems} />

      <AutoActivationStrip
        workspaceSymbolState={workspaceSymbolState}
        status={status}
        context={reasoningContext}
      />

      <DataLineageStrip
        status={status}
        context={reasoningContext}
        messages={messages}
      />

      <StatusNotice
        status={status}
        pollingActive={pollingActive}
        onRetry={regenerateReasoning}
        retrying={regenerating}
      />

      <ReasoningBrief context={reasoningContext} status={status} />
      <WatchPlanPanel context={reasoningContext} />

      <ReminderStatus reminders={reminders} onAck={ackReminder} />

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
            onReminder={createReminder}
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
      {summary && <p className={isUnified ? 'is-one-line' : ''}>{summary}</p>}
      {growthText && (
        <div className="ai-reasoning-growth">
          <span>走势如何生长</span>
          <em>{growthText}</em>
        </div>
      )}
    </section>
  )
}

function WatchPlanPanel({ context }) {
  if (!context) return null
  if (!isAiReasoningReady({ context })) return null
  const reasoning = context.reasoning || context
  const plan = buildWatchPlan(reasoning, context)
  if (!plan.mainTask && !plan.levels.length && !plan.tPlan) return null
  return (
    <section className="ai-watch-plan" aria-label="AI 观察任务">
      <div className="ai-watch-plan-head">
        <strong>观察任务</strong>
        <span>关键位不是交易指令</span>
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

function buildWatchPlan(reasoning = {}, context = {}) {
  const watchPlan = reasoning.watch_plan || {}
  const monitorTriggers = (reasoning.monitor_conditions || {}).triggers || []
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
  const keyLevels = Array.isArray(watchPlan.key_levels) && watchPlan.key_levels.length
    ? watchPlan.key_levels
    : monitorTriggers.length
      ? monitorTriggers.map((item) => ({
        type: item.type === 'price_below' ? 'support' : 'pressure',
        price: item.level,
        trigger: item.type,
        note: item.message_on_trigger,
        after: item.action_on_trigger,
      }))
      : fallbackWatchLevels(reasoning, context)
  return {
    mainTask,
    levels: keyLevels
      .map(normalizeWatchLevel)
      .filter((item) => item.price > 0)
      .slice(0, 4),
    tPlan: watchPlan.t_plan?.enabled ? String(watchPlan.t_plan?.note || watchPlan.t_plan?.plan || '只在触发关键位后再结合分时确认').trim() : '',
  }
}

function fallbackWatchLevels(reasoning = {}, context = {}) {
  const textLevels = fallbackTextWatchLevels(reasoning)
  if (textLevels.length) return textLevels
  const level = reasoning.main_level || context.main_level || context.boundary?.primary_level || ''
  const center = (((context.boundary || {}).levels || {})[level] || {}).active_center || {}
  const zg = Number(center.zg || 0)
  const zd = Number(center.zd || 0)
  const items = []
  if (zg > 0) {
    items.push({
      type: 'pressure',
      price: zg,
      trigger: 'price_above',
      note: `站上${formatLevel(level)}中枢上沿后再看增强确认`,
    })
  }
  if (zd > 0) {
    items.push({
      type: 'support',
      price: zd,
      trigger: 'price_below',
      note: `跌破${formatLevel(level)}中枢下沿后看结构是否转弱`,
    })
  }
  return items
}

function fallbackTextWatchLevels(reasoning = {}) {
  const growth = reasoning.trend_growth || {}
  const text = [
    growth.next_confirmation,
    growth.growth_path,
    growth.failure_path,
    growth.current_state,
  ].map((item) => String(item || '').trim()).filter(Boolean).join('。')
  if (!text) return []
  const matches = [...text.matchAll(/(?:^|[^\d])(\d{1,4}(?:\.\d{1,3})?)(?=[^\d]|$)/g)]
  const items = []
  const seen = new Set()
  matches.forEach((match) => {
    const price = Number(match[1])
    if (!Number.isFinite(price) || price <= 0) return
    const key = price.toFixed(3)
    if (seen.has(key)) return
    const start = (match.index || 0) + String(match[0] || '').lastIndexOf(match[1])
    const end = start + match[1].length
    const before = text.slice(Math.max(0, start - 10), start)
    const after = text.slice(end, Math.min(text.length, end + 14))
    if (isTimeframeNumber(before, after)) return
    const nearby = `${before}${match[1]}${after}`
    const type = inferWatchLevelType(before, after)
    if (!type) return
    seen.add(key)
    items.push({
      type,
      price,
      trigger: type === 'support' ? 'price_below' : 'price_above',
      note: type === 'support'
        ? `跌破 ${formatPlanPrice(price)} 后观察是否转弱`
        : `站上 ${formatPlanPrice(price)} 后观察增强确认`,
    })
  })
  return items.slice(0, 4)
}

function isTimeframeNumber(before, after) {
  return /(?:^|[^A-Za-z])$/.test(before) && /^(f|分钟|分)/i.test(after)
}

function inferWatchLevelType(before, after) {
  const local = `${before}${after}`
  if (/跌破$|失守$|回踩$|支撑$|下沿$|防线$|承接$|考验$/.test(before)) return 'support'
  if (/突破$|站稳$|站上$|攻击$|压力$|上沿$|冲击$|上破$|挑战$|受阻于$/.test(before)) return 'pressure'
  if (/^(的突破|并站稳|后站稳|前高|压力|上沿)/.test(after)) return 'pressure'
  if (/^(不破|确认走弱|支撑|下沿|后回落|附近回落)/.test(after)) return 'support'
  if (/站稳|站上|突破|攻击|压力|上沿|冲击|上破|挑战|前高|受阻/.test(local)) return 'pressure'
  if (/跌破|失守|支撑|下沿|回踩|防线|不破|承接|回拉|考验/.test(local)) return 'support'
  return ''
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

function DataLineageStrip({ status, context, messages }) {
  const items = buildDataLineageItems(status, context, messages)
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

function buildDataLineageItems(status, context, messages = []) {
  const items = []
  const freshness = primaryFreshness(status, context)
  const ai = aiReasoning(status)
  if (!context) {
    items.push({ key: 'reasoning', label: '推演', value: '待生成', tone: 'muted' })
  } else if (!ai.ready) {
    items.push({ key: 'reasoning', label: '推演', value: ai.status === 'failed' ? '失败' : '生成中', tone: ai.status === 'failed' ? 'error' : 'warn' })
  } else {
    items.push({
      key: 'reasoning',
      label: '推演',
      value: freshness.label === '待生成' ? formatAsOf(context.updated_at) || '已生成' : freshness.label,
      tone: freshness.stale || status?.status === 'stale' ? 'warn' : 'ready',
    })
  }

  const latestRows = status?.level_freshness || []
  const latestPrimary = primaryFreshness(
    { ...status, stale_levels: [], status: latestRows.length ? 'fresh' : status?.status },
    { snapshots: latestRows },
  )
  const snapshotValue = latestPrimary.label && latestPrimary.label !== '待生成'
    ? latestPrimary.label
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
    value: 'preview',
    tone: 'muted',
  })

  const coverage = latestIntradayCoverage(messages)
  if (coverage) {
    items.push({
      key: 'intraday',
      label: '盘中',
      value: coverage,
      tone: coverage === 'full' ? 'ready' : coverage === 'none' ? 'muted' : 'warn',
    })
  }
  return items
}

function latestIntradayCoverage(messages = []) {
  const assistant = [...messages].reverse().find((item) => item?.role === 'assistant' && item.answer)
  const quality = assistant?.answer?.intraday_observation?.coverage?.quality
  return quality ? String(quality) : ''
}

function buildFreshnessRows(status, context) {
  const contextRows = (context?.raw_context?.snapshots || context?.snapshots || []).map((item) => ({
    level: item.level,
    data_as_of: item.data_as_of,
    status: 'fresh',
  }))
  const latestRows = status?.level_freshness || []
  const latestByLevel = new Map(latestRows.map((item) => [item.level, item]))
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
        <div key={item.key} className={`ai-pipeline-step ai-pipeline-step--${item.tone}`}>
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

function StatusNotice({ status, pollingActive, onRetry, retrying }) {
  const notice = statusNotice(status, pollingActive)
  if (!notice) return null
  return (
    <div className={`ai-status-notice ai-status-notice--${notice.tone}`}>
      <div className="ai-status-notice-copy">
        <strong>{notice.title}</strong>
        <span>{notice.text}</span>
      </div>
      {notice.retryable && (
        <button type="button" onClick={onRetry} disabled={retrying || pollingActive}>
          {retrying || pollingActive ? '生成中' : '重新生成'}
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

function Message({ item, onReminder, context }) {
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
  const reminderCandidates = (answer.suggested_reminders || []).filter((candidate) => (
    isLiveReminderCandidate(answer, candidate, context)
  ))
  const hasDisclaimerInAnswer = String(answerText).includes(answer.risk_disclaimer || '')
  return (
    <div className="ai-msg ai-msg--assistant">
      <div className="ai-msg-content">{renderCoachText(answerText)}</div>
      {!!reminderCandidates.length && (
        <div className="ai-reminder-list">
          {reminderCandidates.map((candidate) => (
            <button
              key={candidate.evidence_id}
              type="button"
              onClick={() => onReminder(answer, candidate)}
            >
              {candidate.direction === 'ABOVE' ? '上破' : '跌破'} {Number(candidate.trigger_price || 0).toFixed(2)}
            </button>
          ))}
        </div>
      )}
      {answer.risk_disclaimer && !hasDisclaimerInAnswer && <span>{answer.risk_disclaimer}</span>}
    </div>
  )
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

function statusNotice(status, pollingActive) {
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
        title: hasActiveContextJob(status) ? '结构快照刷新中' : '部分级别数据过期',
        text: `${formatLevels(status.stale_levels)} 结构还没追上最新 K 线，当前推演先按旧快照展示；刷新完成后会重跑上下文。`,
      }
    }
    return {
      tone: 'warn',
      title: '基于上一版结构',
      text: 'K 线或 CZSC 快照已有变化，当前回答会先引用上一版上下文；刷新完成后再复核触发线和失败线。',
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
  const { booting, canAsk, loading, activeQuestion, pendingQuestion, pollingActive } = flags
  const reason = statusReason(status)
  const isFailed = status?.status === 'failed'
  const isNoData = reason === 'NO_DATA'
  const isCzscUnavailable = reason === 'CZSC_UNAVAILABLE'
  const hasMissingLevels = Boolean(status?.missing_levels?.length)
  // 分离快照和上下文的工作状态，避免上下文生成中时快照也误显示"生成中"
  const isSnapshotWorking = booting || pollingActive || status?.status === 'pending'
  const isContextWorking = isSnapshotWorking || hasActiveContextJob(status)
  const contextTone = contextStatusTone(status, isContextWorking)

  return [
    {
      key: 'kline',
      label: 'K线',
      tone: !status ? 'checking' : isNoData ? 'error' : booting ? 'working' : 'ready',
      detail: !status ? '检测中' : isNoData ? '缺数据' : booting ? '同步中' : '已接入',
    },
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
