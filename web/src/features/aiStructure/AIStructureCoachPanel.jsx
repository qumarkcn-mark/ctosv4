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
const CONTEXT_POLL_WINDOW_MS = 30_000
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
  const [reminders, setReminders] = useState([])
  const [outcomeReview, setOutcomeReview] = useState(null)
  const [activeSessionId, setActiveSessionId] = useState('')
  const mountedRef = useRef(true)
  const symbolRef = useRef(symbol)

  const displayName = symbolName || symbol
  const pollingActive = pollUntil > Date.now() && !['fresh', 'failed'].includes(status?.status)
  const reasoningContext = status?.context || null
  const aiReasoningReady = isAiReasoningReady(status)
  const canAsk = Boolean(status?.context && aiReasoningReady)
  const displayStatus = status?.context && !aiReasoningReady
    ? `reasoning-${aiReasoningStatus(status) || 'pending'}`
    : (status?.status || 'idle')

  const applyWorkspaceSymbolState = useCallback((state) => {
    if (!state || !sameSymbol(state.symbol, symbolRef.current)) return
    setStatus(normalizeWorkspaceStatus(state))
    setReminders(state.reminders?.items || [])
    setOutcomeReview({
      symbol: state.symbol,
      count: state.outcomes?.count || 0,
      items: state.outcomes?.items || [],
      memory: state.outcomes?.memory || {},
    })
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

  const loadOutcomeReview = useCallback(async () => {
    if (!symbol) return
    try {
      const json = await apiJson(`${API_BASE}/ai-structure/outcomes/${encodeURIComponent(symbol)}?limit=8`)
      if (mountedRef.current) setOutcomeReview(json.data)
    } catch {
      if (mountedRef.current) setOutcomeReview(null)
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
      const sessionsJson = await apiJson(`${API_BASE}/ai-structure/chat/sessions/${encodeURIComponent(symbol)}`)
      const latestSession = sessionsJson.data?.sessions?.[0]
      if (!latestSession?.session_id) return
      const messagesJson = await apiJson(
        `${API_BASE}/ai-structure/chat/messages?session_id=${encodeURIComponent(latestSession.session_id)}`,
      )
      const restored = restoreChatMessages(messagesJson.data?.messages || [])
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
  }, [symbol, loadChartEvidence])

  useEffect(() => {
    mountedRef.current = true
    symbolRef.current = symbol
    setMessages([])
    setInput('')
    setError('')
    setStatus(null)
    setPollUntil(0)
    setPendingQuestion('')
    setReminders([])
    setOutcomeReview(null)
    setActiveSessionId('')
    onEvidenceContext?.(null)
    if (sameSymbol(workspaceSymbolState?.symbol, symbol)) {
      applyWorkspaceSymbolState(workspaceSymbolState)
    } else {
      loadStatus()
      loadReminders()
      loadOutcomeReview()
    }
    loadChatHistory()
    return () => {
      mountedRef.current = false
    }
  }, [
    symbol,
    loadStatus,
    loadReminders,
    loadOutcomeReview,
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
    if (status?.status === 'fresh' || status?.status === 'failed') {
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
    if (!canAsk) {
      setPendingQuestion(question)
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
    }
  }, [input, loading, symbol, canAsk, prewarm, loadStatus, loadChartEvidence, activeSessionId])

  useEffect(() => {
    if (!pendingQuestion || loading || !canAsk) return
    ask(pendingQuestion)
  }, [pendingQuestion, loading, canAsk, ask])

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
      await loadOutcomeReview()
    } catch (err) {
      setError(err?.message || '提醒状态更新失败')
    }
  }, [loadReminders, loadOutcomeReview])

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
    pendingQuestion,
    pollingActive,
  }), [status, booting, workspaceLoading, canAsk, pendingQuestion, pollingActive])

  return (
    <section className="ai-structure-panel">
      <header className="ai-structure-head">
        <div>
          <span className="ai-structure-kicker">AI Native V5</span>
          <h3>{displayName}</h3>
        </div>
        <span className={`ai-structure-status ai-structure-status--${displayStatus}`}>
          {statusLabel}
        </span>
      </header>

      <PipelineStatus items={pipelineItems} />

      <StatusNotice
        status={status}
        pollingActive={pollingActive}
        onRetry={regenerateReasoning}
        retrying={regenerating}
      />

      <ReasoningBrief context={reasoningContext} />

      <ReminderStatus reminders={reminders} onAck={ackReminder} />

      <OutcomeReviewStatus review={outcomeReview} />

      <div className="ai-structure-quick">
        {QUICK_QUESTIONS.map((item) => (
          <button key={item} type="button" onClick={() => ask(item)} disabled={loading || !symbol}>
            {item}
          </button>
        ))}
      </div>

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

      <div className="ai-structure-messages">
        {messages.map((item, index) => (
          <Message
            key={`${item.role}-${index}`}
            item={item}
            onReminder={createReminder}
          />
        ))}
      </div>

      {error && <div className="ai-structure-error">{error}</div>}

      <form className="ai-structure-input" onSubmit={(event) => {
        event.preventDefault()
        ask()
      }}>
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={canAsk ? '问：跌破哪里就不看了？' : '直接提问，会先生成结构上下文'}
          disabled={loading || !symbol}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          {loading ? '...' : '问'}
        </button>
      </form>
    </section>
  )
}

function ReasoningBrief({ context }) {
  if (!context) return null
  if (!isAiReasoningReady({ context })) return null
  const reasoning = context.reasoning || context
  const growth = reasoning.trend_growth || {}
  const summary = reasoning.coach_summary || context.coach_summary || context.summary_text || ''
  const mainLevel = formatLevel(reasoning.main_level || context.main_level)
  const growthText = buildGrowthText(growth)
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

function buildGrowthText(growth = {}) {
  const items = [
    growth.growth_path,
    growth.next_confirmation ? `下一步确认：${growth.next_confirmation}` : '',
    growth.failure_path ? `失败路径：${growth.failure_path}` : '',
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

function OutcomeReviewStatus({ review }) {
  const items = review?.items || []
  const stats = review?.memory?.stats || {}
  if (!items.length && !stats.total_outcomes) return null
  return (
    <div className="ai-outcome-review" aria-label="AI 结构复盘">
      <div className="ai-outcome-review-head">
        <strong>复盘</strong>
        <span>{stats.total_outcomes || items.length} 次 / {stats.mistake_count_30d || 0} 个纪律问题</span>
      </div>
      {!!review?.memory?.profile?.active_warnings?.length && (
        <div className="ai-outcome-warning">
          {review.memory.profile.active_warnings[0].text}
        </div>
      )}
      <div className="ai-outcome-list">
        {items.slice(0, 4).map((item) => (
          <div key={item.outcome_id} className={`ai-outcome-item ai-outcome-item--${item.outcome}`}>
            <div className="ai-outcome-main">
              <span>{outcomeLabel(item)}</span>
              <em>{formatOutcomeTime(item.checked_at)}</em>
            </div>
            <div className="ai-outcome-meta">
              <span>{branchTypeLabel(item.branch?.branch_type)}</span>
              <span>{item.settlement_window || 'manual'}</span>
              <span>{outcomePrice(item)}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function outcomeLabel(item) {
  if (item.is_mistake) return '失效未处理'
  if (item.outcome === 'triggered') return '触发'
  if (item.outcome === 'invalidated') return '失效'
  if (item.outcome === 'expired') return '过期'
  return '观察中'
}

function branchTypeLabel(type) {
  if (type === 'observe_breakout') return '突破观察'
  if (type === 'invalidation_watch') return '失效观察'
  if (type === 'holding_defense') return '持仓防守'
  return type || '结构分支'
}

function outcomePrice(item) {
  const price = item.triggered_price || item.invalidated_price || item.trigger_price
  return Number(price || 0) > 0 ? Number(price).toFixed(2) : '--'
}

function formatOutcomeTime(value) {
  if (!value) return ''
  const text = String(value)
  return text.slice(5, 16).replace('T', ' ')
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

function Message({ item, onReminder }) {
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
  return (
    <div className="ai-msg ai-msg--assistant">
      <p>{answer.coach_answer || answer.answer}</p>
      {!!answer.suggested_reminders?.length && (
        <div className="ai-reminder-list">
          {answer.suggested_reminders.map((candidate) => (
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
      <span>{answer.risk_disclaimer}</span>
    </div>
  )
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
    job: contextStatus.job || null,
  }
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
  const { booting, canAsk, pendingQuestion, pollingActive } = flags
  const reason = statusReason(status)
  const isFailed = status?.status === 'failed'
  const isNoData = reason === 'NO_DATA'
  const isCzscUnavailable = reason === 'CZSC_UNAVAILABLE'
  const hasMissingLevels = Boolean(status?.missing_levels?.length)
  const isWorking = booting || pollingActive || status?.status === 'pending'
  const contextTone = contextStatusTone(status, isWorking)

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
      tone: snapshotStatusTone(status, isWorking),
      detail: snapshotStatusDetail(status, { hasMissingLevels, isCzscUnavailable, isFailed, isNoData, isWorking }),
    },
    {
      key: 'context',
      label: 'AI上下文',
      tone: contextTone,
      detail: contextStatusDetail(status, { canAsk, isFailed, isWorking }),
    },
    {
      key: 'chat',
      label: '问答',
      tone: canAsk ? 'ready' : pendingQuestion ? 'working' : 'waiting',
      detail: canAsk ? '可提问' : pendingQuestion ? '已排队' : '可先问',
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
  if (isWorking) return '生成中'
  if (hasMissingLevels) return `缺${formatLevels(status.missing_levels)}`
  if (status.status === 'no_snapshot') return '待生成'
  return '已就绪'
}

function contextStatusTone(status, isWorking) {
  if (!status) return 'checking'
  if (status.status === 'failed') return 'error'
  const ai = aiReasoning(status)
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
  if (status.context && ['failed', 'unavailable'].includes(ai.status)) return '推演失败'
  if (status.context && !ai.ready) return '推演中'
  if (status.status === 'stale') return '待刷新'
  if (canAsk) return '已就绪'
  if (isWorking) return '生成中'
  return '未生成'
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
