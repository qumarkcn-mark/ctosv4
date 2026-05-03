import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { API_BASE } from '../../config.js'

export default function AINativeRadarCard({ symbol, mode }) {
  const cacheKey = `ct_ai_native_commander:v2:${symbol || 'unknown'}:${mode || 'UNKNOWN'}`
  const cachedReport = readCachedReport(cacheKey)
  const [loading, setLoading] = useState(false)
  const [report, setReport] = useState(cachedReport)
  const [error, setError] = useState('')
  const canShowRaw = import.meta.env.DEV && report?.raw_reasoning_md

  useEffect(() => {
    let cancelled = false
    const nextCachedReport = readCachedReport(cacheKey)
    if (nextCachedReport) {
      setReport(nextCachedReport)
    } else {
      setReport(null)
    }
    loadLatestReport()
    setError('')

    async function loadLatestReport() {
      if (!symbol) return
      try {
        const params = new URLSearchParams({ symbol, user_id: '1' })
        if (mode) params.set('mode', mode)
        const res = await fetch(`${API_BASE}/agent/ai-native-radar/latest?${params.toString()}`)
        const json = await res.json()
        if (cancelled || !res.ok || json.status !== 'success' || !json.data) return
        const latestReport = {
          ...json.data,
          generated_at: json.data?.generated_at || new Date().toISOString(),
        }
        setReport(latestReport)
        cacheReport(cacheKey, latestReport)
      } catch {
        // 最近一次推演回填失败时，不影响用户手动点击“推演”。
      }
    }

    return () => {
      cancelled = true
    }
  }, [cacheKey, symbol, mode])

  const runDeduction = async () => {
    if (!symbol || loading) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/agent/ai-native-radar`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, mode, user_id: 1 }),
      })
      const json = await res.json()
      if (!res.ok || json.status !== 'success') {
        throw new Error(json?.detail || json?.message || 'AI Native 推演失败')
      }
      const nextReport = {
        ...json.data,
        generated_at: json.data?.generated_at || new Date().toISOString(),
      }
      setReport(nextReport)
      cacheReport(cacheKey, nextReport)
    } catch (err) {
      setError(err?.message || 'AI Native 推演失败')
    } finally {
      setLoading(false)
    }
  }

  const markdown = report?.raw_reasoning_md || report?.coach_filtered_md || ''
  const sandTable = parseCommanderMarkdown(markdown)
  return (
    <section className="radar-commander-panel">
      <div className="radar-commander-head">
        <div>
          <span>AI 推演</span>
          <strong>{commanderHeadline(report, loading)}</strong>
        </div>
        <div className="radar-commander-actions">
          <button type="button" onClick={runDeduction} disabled={loading || !symbol}>
            {loading ? '推演中' : report ? '重新推演' : '开始推演'}
          </button>
        </div>
      </div>

      <div className="radar-ai-disclaimer">以下内容仅供参考，不构成投资建议。</div>
      {error && <div className="radar-ai-error">{error}</div>}

      {!markdown && !loading && (
        <div className="radar-commander-empty">
          <strong>等待统帅推演</strong>
          <span>读取结构事实、中枢边界和背驰断言后生成作战沙盘。</span>
        </div>
      )}
      {loading && !markdown && <div className="radar-ai-loading">正在生成 Free Reasoning 并执行语义过滤...</div>}
      {markdown && (
        <CommanderSandTable sections={sandTable} fallbackMarkdown={markdown} />
      )}

      {report && (
        <div className="radar-commander-meta">
          <span>{report.gate_status || 'UNKNOWN'}</span>
          <span>RUN {report.run_id || '--'}</span>
          <span>{formatRunTime(report.generated_at)}</span>
          {report.fallback_reason && <em>{report.fallback_reason}</em>}
        </div>
      )}

      {canShowRaw && (
        <details className="radar-commander-raw">
          <summary>Raw Free Reasoning</summary>
          <ReactMarkdown>{report.raw_reasoning_md}</ReactMarkdown>
        </details>
      )}
    </section>
  )
}

function CommanderSandTable({ sections, fallbackMarkdown }) {
  if (!sections.global && !sections.defense && !sections.scenarios.length) {
    return (
      <div className="radar-commander-markdown">
        <ReactMarkdown>{fallbackMarkdown}</ReactMarkdown>
      </div>
    )
  }

  return (
    <div className="radar-commander-sandtable">
      {sections.global && (
        <section className="radar-commander-brief">
          <div className="radar-commander-section-head">
            <span>01</span>
            <strong>全局语境定性</strong>
          </div>
          <div className="radar-commander-markdown">
            <ReactMarkdown>{sections.global}</ReactMarkdown>
          </div>
        </section>
      )}

      {sections.defense && (
        <section className="radar-commander-watchdog">
          <div className="radar-commander-section-head">
            <span>02</span>
            <strong>防守看门狗</strong>
          </div>
          <div className="radar-commander-markdown">
            <ReactMarkdown>{sections.defense}</ReactMarkdown>
          </div>
        </section>
      )}

      {(sections.scenarioIntro || sections.scenarios.length > 0) && (
        <section className="radar-commander-scenarios">
          <div className="radar-commander-section-head">
            <span>03</span>
            <strong>推演与应对沙盘</strong>
          </div>
          {sections.scenarioIntro && (
            <div className="radar-commander-scenario-note">
              <ReactMarkdown>{sections.scenarioIntro}</ReactMarkdown>
            </div>
          )}
          <div className="radar-commander-scenario-grid">
            {sections.scenarios.map((scenario, index) => (
              <article key={`${scenario.title}-${index}`} className={`radar-commander-scenario radar-commander-scenario--${index + 1}`}>
                <div className="radar-commander-scenario-title">
                  <span>{scenarioLabel(index)}</span>
                  <strong>{scenario.title}</strong>
                </div>
                <div className="radar-commander-markdown">
                  <ReactMarkdown>{scenario.body}</ReactMarkdown>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

function parseCommanderMarkdown(markdown) {
  const clean = String(markdown || '').replace(/\r\n/g, '\n')
  const global = extractSection(clean, '全局语境定性', ['防守看门狗'])
  const defense = extractSection(clean, '防守看门狗', ['推演与应对沙盘'])
  const scenarioBlock = extractSection(clean, '推演与应对沙盘', [])
  const { intro, scenarios } = parseScenarios(scenarioBlock)
  return {
    global,
    defense,
    scenarioIntro: intro,
    scenarios,
  }
}

function extractSection(markdown, title, nextTitles) {
  const start = findHeadingIndex(markdown, title)
  if (start < 0) return ''
  const afterHeading = markdown.indexOf('\n', start)
  const contentStart = afterHeading >= 0 ? afterHeading + 1 : start
  const nextIndexes = nextTitles
    .map((item) => findHeadingIndex(markdown, item, contentStart))
    .filter((index) => index >= 0)
  const contentEnd = nextIndexes.length ? Math.min(...nextIndexes) : markdown.length
  return stripNoise(markdown.slice(contentStart, contentEnd))
}

function findHeadingIndex(markdown, title, fromIndex = 0) {
  const pattern = new RegExp(`(^|\\n)#{0,6}\\s*\\*{0,2}[^\\n]*【${escapeRegExp(title)}】[^\\n]*`, 'm')
  const sliced = markdown.slice(fromIndex)
  const match = sliced.match(pattern)
  return match ? fromIndex + (match.index || 0) + (match[1] ? 1 : 0) : -1
}

function parseScenarios(block) {
  if (!block) return { intro: '', scenarios: [] }
  const scenarioPattern = /(?:^|\n)\s*(?:#{1,6}\s*)?\*{0,3}\s*((?:剧本|预案)\s*[A-ZＡ-Ｚ一二三四五六七八九十0-9０-９]+[：:][^\n*]+?)\s*\*{0,3}\s*(?=\n|$)/g
  const matches = [...block.matchAll(scenarioPattern)]
  if (!matches.length) return { intro: stripNoise(block), scenarios: [] }

  const intro = stripNoise(block.slice(0, matches[0].index || 0))
  const scenarios = matches.map((match, index) => {
    const headingStart = match.index || 0
    const bodyStart = headingStart + match[0].length
    const bodyEnd = index + 1 < matches.length ? matches[index + 1].index || block.length : block.length
    return {
      title: stripScenarioTitle(match[1]),
      body: stripNoise(block.slice(bodyStart, bodyEnd)),
    }
  }).filter((item) => item.title || item.body)
  return { intro, scenarios }
}

function stripNoise(value) {
  return String(value || '')
    .replace(/^[-*_]{3,}\s*$/gm, '')
    .replace(/^\s*#{0,6}\s*\*{0,3}\s*(?:\d+[.、]\s*)?【(?:全局语境定性|防守看门狗|推演与应对沙盘)】\s*\*{0,3}\s*$/gm, '')
    .trim()
}

function stripScenarioTitle(value) {
  return String(value || '').replace(/\*+/g, '').trim()
}

function scenarioLabel(index) {
  return ['A', 'B', 'C', 'D'][index] || String(index + 1)
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function readCachedReport(cacheKey) {
  try {
    const cached = window.sessionStorage.getItem(cacheKey)
    return cached ? JSON.parse(cached) : null
  } catch {
    return null
  }
}

function cacheReport(cacheKey, report) {
  try {
    window.sessionStorage.setItem(cacheKey, JSON.stringify(report))
  } catch {
    // 缓存失败不影响推演主流程。
  }
}

function commanderHeadline(report, loading) {
  if (loading) return '统帅推演生成中'
  if (!report) return 'Free Reasoning 沙盘'
  if (report.gate_status === 'FALLBACK') return '结构事实保护模式'
  return '交易纪律沙盘已生成'
}

function formatRunTime(value) {
  if (!value) return '--'
  try {
    return new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return '--'
  }
}
