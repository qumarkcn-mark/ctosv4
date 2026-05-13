import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { API_BASE } from '../../config.js'
import { apiJson } from '../../api/client.js'
import './AIStructureCoachPanel.css'

const QUICK_QUESTIONS = [
  '我现在能买吗？',
  '跌破哪里就不看了？',
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
  const mountedRef = useRef(true)

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

  useEffect(() => {
    mountedRef.current = true
    setMessages([])
    setInput('')
    setError('')
    setStatus(null)
    setPollUntil(0)
    setPendingQuestion('')
    onEvidenceContext?.(null)
    loadStatus()
    return () => {
      mountedRef.current = false
    }
  }, [symbol, loadStatus, onEvidenceContext])

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

  const loadChartEvidence = useCallback(async (answer) => {
    const focus = answer?.chart_focus
    if (!focus?.context_id || !focus?.level) return
    const params = new URLSearchParams({
      context_id: focus.context_id,
      level: focus.level,
      evidence_ids: (focus.evidence_ids || []).join(','),
    })
    const json = await apiJson(`${API_BASE}/ai-structure/chart-context/${encodeURIComponent(symbol)}?${params}`)
    onEvidenceContext?.(json.data)
  }, [symbol, onEvidenceContext])

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
        body: JSON.stringify({ symbol, question }),
      })
      const answer = json.data
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
  }, [input, loading, symbol, canAsk, prewarm, loadStatus, loadChartEvidence])

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
    } catch (err) {
      setError(err?.message || '提醒创建失败')
    }
  }, [])

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
