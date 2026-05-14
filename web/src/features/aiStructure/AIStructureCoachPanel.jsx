import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API_BASE } from '../../config.js'
import { apiJson } from '../../api/client.js'
import './AIStructureCoachPanel.css'

const QUICK_QUESTIONS = [
  '我现在能买吗？',
  '跌破哪里就不看了？',
  '我上次错在哪里？',
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

export default function AIStructureCoachPanel({ symbol, symbolName, onEvidenceContext }) {
  const [status, setStatus] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [booting, setBooting] = useState(false)
  const [pollUntil, setPollUntil] = useState(0)
  const [error, setError] = useState('')
  const [pendingQuestion, setPendingQuestion] = useState('')
  const [reminders, setReminders] = useState([])
  const [outcomeReview, setOutcomeReview] = useState(null)
  const [activeSessionId, setActiveSessionId] = useState('')
  const mountedRef = useRef(true)
  const symbolRef = useRef(symbol)

  const displayName = symbolName || symbol
  const canAsk = Boolean(status?.context)
  const pollingActive = pollUntil > Date.now() && !['fresh', 'failed'].includes(status?.status)

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
    loadStatus()
    loadReminders()
    loadOutcomeReview()
    loadChatHistory()
    return () => {
      mountedRef.current = false
    }
  }, [symbol, loadStatus, loadReminders, loadOutcomeReview, loadChatHistory, onEvidenceContext])

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
      setPollUntil(Date.now() + CONTEXT_POLL_WINDOW_MS)
      await loadStatus()
    } catch (err) {
      setError(err?.message || '预热失败，先更新 K 线后再试')
    } finally {
      setBooting(false)
    }
  }, [symbol, booting, pollingActive, loadStatus])

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
    if (pollingActive) return '生成中'
    if (!status) return '检测中'
    if (status.status === 'fresh') return '结构就绪'
    if (status.status === 'stale') return '结构待刷新'
    if (status.status === 'pending') return '生成中'
    if (status.status === 'failed') return '生成失败'
    if (status.status === 'no_snapshot') return '待生成'
    return status.status || '未知'
  }, [status, pollingActive])

  const pipelineItems = useMemo(() => buildPipelineItems(status, {
    booting,
    canAsk,
    pendingQuestion,
    pollingActive,
  }), [status, booting, canAsk, pendingQuestion, pollingActive])

  return (
    <section className="ai-structure-panel">
      <header className="ai-structure-head">
        <div>
          <span className="ai-structure-kicker">AI Native V5</span>
          <h3>{displayName}</h3>
        </div>
        <span className={`ai-structure-status ai-structure-status--${status?.status || 'idle'}`}>
          {statusLabel}
        </span>
      </header>

      <PipelineStatus items={pipelineItems} />

      <StatusGuidance
        status={status}
        canAsk={canAsk}
        pollingActive={pollingActive}
      />

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

function StatusGuidance({ status, canAsk, pollingActive }) {
  const guidance = buildStatusGuidance(status, { canAsk, pollingActive })
  if (!guidance) return null
  return (
    <div className={`ai-status-guidance ai-status-guidance--${guidance.tone}`}>
      <strong>{guidance.title}</strong>
      <p>{guidance.body}</p>
      {!!guidance.detail && <em>{guidance.detail}</em>}
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

function failureText(status) {
  const reason = status?.stale_reason || status?.job?.error_code || 'UNKNOWN'
  if (reason === 'CZSC_UNAVAILABLE') return 'CZSC 结构引擎未安装或不可用，暂时无法生成结构上下文。'
  if (reason === 'NO_DATA') return '当前缺少可用 K 线数据，暂时无法生成结构上下文。'
  return `结构上下文生成失败：${reason}`
}

function emptyText(status, pollingActive) {
  if (status?.status === 'failed') return failureText(status)
  if (pollingActive || status?.status === 'pending') return '结构上下文生成中，完成后就可以继续追问。'
  if (status?.missing_levels?.length) return `等待 ${formatLevels(status.missing_levels)} 的 CZSC 快照。`
  return '当前没有可用结构上下文。'
}

function buildStatusGuidance(status, flags) {
  const { canAsk, pollingActive } = flags
  const reason = statusReason(status)
  if (!status) {
    return {
      tone: 'checking',
      title: '正在检查结构数据',
      body: '先确认 K 线、CZSC 快照和 AI 上下文是否已有可用版本。',
    }
  }
  if (pollingActive || status.status === 'pending') {
    return {
      tone: 'working',
      title: '结构正在生成',
      body: '问题已经可以先排队，后台会先补 K 线和 CZSC 快照，再生成 AI 结构上下文。',
      detail: '页面请求不会同步重算结构，也不会回退旧结构入口。',
    }
  }
  if (reason === 'NO_DATA') {
    return {
      tone: 'error',
      title: '缺少 K 线数据',
      body: '当前股票没有足够 K 线，暂时不能生成 CZSC 快照。先换一只有行情的股票，或等待数据同步完成。',
    }
  }
  if (reason === 'CZSC_UNAVAILABLE') {
    return {
      tone: 'error',
      title: 'CZSC 引擎不可用',
      body: 'V5 只使用 CZSC 结构主线；引擎不可用时不会用旧 radar 或 chan 路径兜底。',
    }
  }
  if (status.status === 'failed') {
    return {
      tone: 'error',
      title: '结构生成失败',
      body: failureText(status),
      detail: '可以重新生成上下文；如果连续失败，需要检查 K 线输入和后台 worker。',
    }
  }
  if (status.missing_levels?.length) {
    return {
      tone: canAsk ? 'warn' : 'waiting',
      title: canAsk ? '有旧上下文，可继续问' : '等待多级别快照',
      body: canAsk
        ? `缺少 ${formatLevels(status.missing_levels)} 的新快照，回答会基于上一版 AI 上下文。`
        : `还缺 ${formatLevels(status.missing_levels)} 的 CZSC 快照，生成后才能回答。`,
      detail: 'V5 只展示当前回答相关的轻量证据，不做旧结构报告回退。',
    }
  }
  if (status.status === 'stale') {
    return {
      tone: 'warn',
      title: '结构待刷新',
      body: canAsk ? '可以继续提问，但这次回答会使用上一版上下文。' : '需要先生成新的 AI 结构上下文。',
    }
  }
  if (!canAsk) {
    return {
      tone: 'waiting',
      title: '还没有 AI 上下文',
      body: '可以直接提问，系统会先生成结构上下文，完成后自动回答。',
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
  if (isNoData) return '等K线'
  if (isFailed) return '失败'
  if (isWorking) return '生成中'
  if (hasMissingLevels) return `缺${formatLevels(status.missing_levels)}`
  if (status.status === 'no_snapshot') return '待生成'
  return '已就绪'
}

function contextStatusTone(status, isWorking) {
  if (!status) return 'checking'
  if (status.status === 'failed') return 'error'
  if (status.status === 'stale') return 'warn'
  if (status.context) return 'ready'
  if (isWorking) return 'working'
  return 'waiting'
}

function contextStatusDetail(status, flags) {
  const { canAsk, isFailed, isWorking } = flags
  if (!status) return '检测中'
  if (isFailed) return '失败'
  if (status.status === 'stale') return '待刷新'
  if (canAsk) return '已就绪'
  if (isWorking) return '生成中'
  return '未生成'
}

function statusReason(status) {
  return status?.stale_reason || status?.job?.error_code || ''
}

function formatLevels(levels = []) {
  return levels.map((level) => LEVEL_LABELS[level] || level).join('/')
}
