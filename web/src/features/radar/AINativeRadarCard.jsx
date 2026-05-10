import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { API_BASE } from '../../config.js'

const latestReportRequests = new Map()

export default function AINativeRadarCard({
  symbol,
  mode,
  signalCode = '',
  structureFingerprint = '',
  disabled = false,
  disabledReason = '',
  onReportChange,
}) {
  const cacheKey = `ct_ai_native_commander:v8_structure:${symbol || 'unknown'}:${mode || 'UNKNOWN'}:${signalCode || 'no_signal'}:${structureFingerprint || 'no_structure'}`
  const cachedReport = readCachedReport(cacheKey)
  const [loading, setLoading] = useState(false)
  const [report, setReport] = useState(cachedReport)
  const [error, setError] = useState('')
  const visibleReport = disabled ? null : report
  const canShowRaw = import.meta.env.DEV && visibleReport?.raw_reasoning_md

  useEffect(() => {
    let cancelled = false
    if (disabled) {
      setReport(null)
      setError('')
      return () => {
        cancelled = true
      }
    }
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
        if (signalCode) params.set('signal_code', signalCode)
        if (structureFingerprint) params.set('structure_fingerprint', structureFingerprint)
        const json = await loadLatestReportOnce(params.toString())
        if (cancelled || json?.status !== 'success' || !json.data) return
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
  }, [cacheKey, symbol, mode, signalCode, structureFingerprint, disabled])

  useEffect(() => {
    onReportChange?.(visibleReport || null)
  }, [onReportChange, visibleReport])

  const runDeduction = async () => {
    if (!symbol || loading || disabled) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/agent/ai-native-radar`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol,
          mode,
          user_id: 1,
          signal_code: signalCode || undefined,
          structure_fingerprint: structureFingerprint || undefined,
        }),
      })
      const json = await readJsonResponse(res)
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

  const markdown = visibleReport?.coach_filtered_md || visibleReport?.raw_reasoning_md || ''
  const sandTable = parseCommanderMarkdown(markdown)
  return (
    <section className="radar-commander-panel">
      <div className="radar-commander-head">
        <div>
          <span>AI 推演</span>
          <strong>{commanderHeadline(visibleReport, loading, disabled)}</strong>
        </div>
        <div className="radar-commander-actions">
          <button type="button" onClick={runDeduction} disabled={loading || !symbol || disabled}>
            {deductionButtonLabel({ loading, disabled, report: visibleReport })}
          </button>
        </div>
      </div>

      <div className="radar-ai-disclaimer">以下内容仅供参考，不构成投资建议。</div>
      {error && <div className="radar-ai-error">{error}</div>}

      {disabled && !markdown && !loading && (
        <div className="radar-commander-empty">
          <strong>等待正式结构数据</strong>
          <span>{disabledReason || '正式结构数据补齐后，再生成教练推演。'}</span>
        </div>
      )}
      {!disabled && !markdown && !loading && (
        <div className="radar-commander-empty">
          <strong>等待统帅推演</strong>
          <span>读取结构事实、中枢边界和背驰断言后生成作战沙盘。</span>
        </div>
      )}
      {loading && !markdown && <div className="radar-ai-loading">正在生成 Free Reasoning 并执行语义过滤...</div>}
      {markdown && (
        <CommanderSandTable sections={sandTable} fallbackMarkdown={markdown} />
      )}

      {visibleReport && (
        <div className="radar-commander-meta">
          <span>{visibleReport.gate_status || 'UNKNOWN'}</span>
          <span>RUN {visibleReport.run_id || '--'}</span>
          <span>{formatRunTime(visibleReport.generated_at)}</span>
          {visibleReport.fallback_reason && <em>{visibleReport.fallback_reason}</em>}
        </div>
      )}

      {canShowRaw && (
        <details className="radar-commander-raw">
          <summary>Raw Free Reasoning</summary>
          <ReactMarkdown>{visibleReport.raw_reasoning_md}</ReactMarkdown>
        </details>
      )}
    </section>
  )
}

async function readJsonResponse(res) {
  const text = await res.text()
  if (!text) return {}
  try {
    return JSON.parse(text)
  } catch {
    return {
      status: 'error',
      message: text.slice(0, 160) || `请求失败 ${res.status}`,
    }
  }
}

async function loadLatestReportOnce(query) {
  const cached = latestReportRequests.get(query)
  if (cached) return cached
  const request = fetch(`${API_BASE}/agent/ai-native-radar/latest?${query}`)
    .then(async (res) => {
      if (!res.ok) return null
      return await res.json()
    })
    .finally(() => {
      setTimeout(() => latestReportRequests.delete(query), 1500)
    })
  latestReportRequests.set(query, request)
  return request
}

function CommanderSandTable({ sections, fallbackMarkdown }) {
  if (!sections.position && !sections.scenarios.length && !sections.discipline) {
    return (
      <div className="radar-commander-markdown">
        <ReactMarkdown>{fallbackMarkdown}</ReactMarkdown>
      </div>
    )
  }

  return (
    <div className="radar-commander-sandtable">
      {sections.position && (
        <section className="radar-commander-brief">
          <div className="radar-commander-section-head">
            <span>01</span>
            <strong>当前定位</strong>
          </div>
          <div className="radar-commander-markdown">
            <ReactMarkdown>{sections.position}</ReactMarkdown>
          </div>
        </section>
      )}

      {sections.discipline && (
        <section className="radar-commander-discipline">
          <div className="radar-commander-section-head">
            <span>02</span>
            <strong>纪律</strong>
          </div>
          <p>{sections.discipline}</p>
        </section>
      )}

      {(sections.scenarioIntro || sections.scenarios.length > 0) && (
        <section className="radar-commander-scenarios">
          <div className="radar-commander-section-head">
            <span>{sections.discipline ? '03' : '02'}</span>
            <strong>{sections.scenarioTitle || '实时路径'}</strong>
          </div>
          {sections.scenarioIntro && !sections.scenarioIntroHidden && (
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
                <ScenarioBody scenario={scenario} />
              </article>
            ))}
          </div>
        </section>
      )}

      {(sections.hiddenEvidence.length > 0 || sections.corrections.length > 0 || sections.uncertainty.length > 0) && (
        <details className="radar-commander-evidence">
          <summary>证据、修正与不确定因素</summary>
          {sections.hiddenEvidence.length > 0 && (
            <div>
              <span>结构证据</span>
              {sections.hiddenEvidence.map((item, index) => <p key={`evidence-${index}`}>{item}</p>)}
            </div>
          )}
          {sections.corrections.length > 0 && (
            <div>
              <span>AI 修正</span>
              {sections.corrections.map((item, index) => <p key={`correction-${index}`}>{item}</p>)}
            </div>
          )}
          {sections.uncertainty.length > 0 && (
            <div>
              <span>不确定因素</span>
              {sections.uncertainty.map((item, index) => <p key={`uncertainty-${index}`}>{item}</p>)}
            </div>
          )}
        </details>
      )}
    </div>
  )
}

function parseCommanderMarkdown(markdown) {
  const clean = String(markdown || '').replace(/\r\n/g, '\n')
  const position = extractFirstSection(clean, ['当前定位', '全局语境定性'], ['AI 完全分类', '完全分类', '三种剧本', '推演与应对沙盘', '防守看门狗', '纪律'])
  const scenarioBlock = extractFirstSection(clean, ['AI 完全分类', '完全分类', '三种剧本', '推演与应对沙盘'], ['纪律', 'AI 修正', '不确定因素', '防守更新'])
  const { intro, scenarios } = parseScenarios(scenarioBlock)
  const introIsEvidence = isTechnicalEvidenceBlock(intro)
  return {
    position,
    discipline: extractLabelBlock(clean, '纪律'),
    scenarioTitle: clean.includes('AI 完全分类') ? '实时路径' : '三种剧本',
    scenarioIntro: introIsEvidence ? '' : intro,
    scenarioIntroHidden: introIsEvidence,
    hiddenEvidence: introIsEvidence ? compactEvidenceLines(intro) : [],
    scenarios: scenarios.map(normalizeScenario),
    corrections: extractLooseLabelItems(clean, 'AI 修正'),
    uncertainty: extractLooseLabelItems(clean, '不确定因素'),
  }
}

function ScenarioBody({ scenario }) {
  const rows = [
    ['当前', scenario.currentState],
    ['边界', scenario.nextBoundary],
    ['触发', scenario.triggerCondition],
    ['失效', scenario.invalidation],
    ['动作', scenario.action],
  ].filter(([, value]) => value)

  return (
    <div className="radar-commander-scenario-body">
      {scenario.summary && <p className="radar-commander-scenario-summary">{scenario.summary}</p>}
      {rows.length > 0 && (
        <dl>
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      )}
      {scenario.details.length > 0 && (
        <details className="radar-commander-evidence radar-commander-evidence--inline">
          <summary>证据详情</summary>
          {scenario.details.map((item, index) => <p key={`${item}-${index}`}>{item}</p>)}
        </details>
      )}
    </div>
  )
}

function extractFirstSection(markdown, titles, nextTitles) {
  for (const title of titles) {
    const section = extractSection(markdown, title, nextTitles)
    if (section) return section
  }
  return ''
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
  const pattern = new RegExp(`(^|\\n)#{0,6}\\s*\\*{0,2}[^\\n]*【[^】]*${escapeRegExp(title)}[^】]*】[^\\n]*`, 'm')
  const sliced = markdown.slice(fromIndex)
  const match = sliced.match(pattern)
  return match ? fromIndex + (match.index || 0) + (match[1] ? 1 : 0) : -1
}

function parseScenarios(block) {
  if (!block) return { intro: '', scenarios: [] }
  const matches = findScenarioHeadings(block)
  if (!matches.length) return { intro: stripNoise(block), scenarios: [] }

  const intro = stripNoise(block.slice(0, matches[0].index))
  const scenarios = matches.map((match, index) => {
    const bodyStart = match.index + match.raw.length
    const bodyEnd = index + 1 < matches.length ? matches[index + 1].index : block.length
    return {
      title: stripScenarioTitle(match.title),
      body: stripNoise(block.slice(bodyStart, bodyEnd)),
    }
  }).filter((item) => item.title || item.body)
  return { intro, scenarios }
}

function normalizeScenario(scenario) {
  const source = String(scenario.body || '')
  const lineItems = extractBulletItems(source)
  const details = []
  const visibleItems = {}

  for (const item of lineItems) {
    const label = normalizeItemLabel(item.label)
    if (label === 'evidence' || label === 'technicalEvidence' || looksInternal(item.value)) {
      details.push(`${item.label}：${item.value}`)
      continue
    }
    if (label) {
      visibleItems[label] = item.value
    }
  }

  const summary = stripScenarioVisibleSummary(source)
  return {
    ...scenario,
    summary,
    currentState: visibleItems.currentState || '',
    nextBoundary: visibleItems.nextBoundary || '',
    triggerCondition: visibleItems.triggerCondition || '',
    invalidation: visibleItems.invalidation || '',
    action: visibleItems.action || '',
    details,
  }
}

function extractBulletItems(markdown) {
  const items = []
  const pattern = /^\s*[-*•]\s*([^：:]{2,16})[：:]\s*(.+)$/gm
  for (const match of markdown.matchAll(pattern)) {
    items.push({
      label: String(match[1] || '').trim(),
      value: String(match[2] || '').trim(),
    })
  }
  return items
}

function normalizeItemLabel(label) {
  const text = String(label || '').trim()
  if (text.includes('当前状态')) return 'currentState'
  if (text.includes('下一边界')) return 'nextBoundary'
  if (text.includes('触发条件')) return 'triggerCondition'
  if (text.includes('失效条件')) return 'invalidation'
  if (text.includes('操作指令')) return 'action'
  if (text.includes('证据')) return 'evidence'
  if (text.includes('当前信号') || text.includes('结构依据')) return 'technicalEvidence'
  return ''
}

function stripScenarioVisibleSummary(markdown) {
  return String(markdown || '')
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => {
      if (!line) return false
      if (/^[-*•]\s*[^：:]{2,16}[：:]/.test(line)) return false
      if (/^(AI 修正|不确定因素|证据|当前信号|结构依据)[：:]/.test(line)) return false
      return !looksInternal(line)
    })
    .join('\n')
    .trim()
}

function looksInternal(text) {
  return /semantic_signal|raw_bi_context|algorithm_reference|is_sure|ZG=|ZD=|_zs_|_ss|d1_|m30_|m5_|code|primary|fallback_reason/.test(String(text || ''))
}

function isTechnicalEvidenceBlock(text) {
  const value = String(text || '')
  if (!value.trim()) return false
  return /当前信号|结构依据|semantic_signal|raw_bi_context|algorithm_reference|is_sure|ZG=|ZD=|d1_|m30_|m5_/.test(value)
}

function compactEvidenceLines(text) {
  return String(text || '')
    .split('\n')
    .map((line) => line.trim().replace(/^[-*•]\s*/, ''))
    .filter(Boolean)
    .slice(0, 6)
}

function extractLabelBlock(markdown, label) {
  const text = String(markdown || '')
  const escaped = escapeRegExp(label)
  const patterns = [
    new RegExp(`(?:^|\\n)\\*\\*${escaped}[：:]\\*\\*\\s*([\\s\\S]*?)(?=\\n\\*\\*(?:纪律|AI 修正|不确定因素|防守更新)[：:]\\*\\*|\\n(?:AI 修正|不确定因素)[：:]|$)`),
    new RegExp(`(?:^|\\n)${escaped}[：:]\\s*([^\\n]+)`),
  ]
  for (const pattern of patterns) {
    const match = text.match(pattern)
    if (match?.[1]) return stripNoise(match[1])
  }
  return ''
}

function extractLooseLabelItems(markdown, label) {
  const text = String(markdown || '')
  const escaped = escapeRegExp(label)
  const pattern = new RegExp(`(?:^|\\n)(?:\\*\\*)?${escaped}[：:](?:\\*\\*)?\\s*([^\\n]+)`, 'g')
  const items = []
  for (const match of text.matchAll(pattern)) {
    const value = String(match[1] || '').trim()
    if (!value) continue
    items.push(...value.split(/[；;]/).map((item) => item.trim()).filter(Boolean))
  }
  return items.slice(0, 4)
}

function findScenarioHeadings(block) {
  const explicitPattern = /(?:^|\n)\s*(?:#{1,6}\s*)?\*{0,3}\s*((?:剧本|预案|路径)\s*[A-ZＡ-Ｚ一二三四五六七八九十0-9０-９]+[：:][^\n*]+?)\s*\*{0,3}\s*(?=\n|$)/g
  const explicit = [...block.matchAll(explicitPattern)].map((match) => ({
    index: match.index || 0,
    raw: match[0],
    title: match[1],
  }))
  if (explicit.length) return explicit

  const numberedPattern = /(?:^|\n)\s*(?:#{1,6}\s*)?\*{0,3}\s*((?:[1-9][0-9]*|[一二三四五六七八九十]+)[.、]\s*[^\n*：:]{3,80})\s*\*{0,3}\s*(?=\n|$)/g
  return [...block.matchAll(numberedPattern)]
    .map((match) => ({
      index: match.index || 0,
      raw: match[0],
      title: match[1],
    }))
    .filter((match) => /走势推演|成立条件|失效条件|纪律含义/.test(block.slice(match.index + match.raw.length, match.index + match.raw.length + 260)))
}

function stripNoise(value) {
  return String(value || '')
    .replace(/^[-*_]{3,}\s*$/gm, '')
    .replace(/^\s*#{0,6}\s*\*{0,3}\s*(?:\d+[.、]\s*)?【(?:当前定位|全局语境定性|防守看门狗|AI 完全分类|完全分类|三种剧本|推演与应对沙盘)[^】]*】\s*\*{0,3}\s*$/gm, '')
    .trim()
}

function stripScenarioTitle(value) {
  return String(value || '')
    .replace(/\*+/g, '')
    .replace(/^(?:[1-9][0-9]*|[一二三四五六七八九十]+)[.、]\s*/, '')
    .trim()
}

function scenarioLabel(index) {
  return ['A', 'B', 'C', 'D'][index] || String(index + 1)
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function readCachedReport(cacheKey) {
  try {
    clearLegacyCommanderCache()
    const cached = window.sessionStorage.getItem(cacheKey)
    const report = cached ? JSON.parse(cached) : null
    return isUsableCommanderCache(report) ? report : null
  } catch {
    return null
  }
}

function clearLegacyCommanderCache() {
  const keys = Object.keys(window.sessionStorage)
  for (const key of keys) {
    if (
      key.startsWith('ct_ai_native_commander:v1:') ||
      key.startsWith('ct_ai_native_commander:v2:') ||
      key.startsWith('ct_ai_native_commander:v3:') ||
      key.startsWith('ct_ai_native_commander:v4:') ||
      key.startsWith('ct_ai_native_commander:v5:') ||
      key.startsWith('ct_ai_native_commander:v6:') ||
      key.startsWith('ct_ai_native_commander:v7:')
    ) {
      window.sessionStorage.removeItem(key)
    }
  }
}

function cacheReport(cacheKey, report) {
  try {
    if (!isUsableCommanderCache(report)) return
    window.sessionStorage.setItem(cacheKey, JSON.stringify(report))
  } catch {
    // 缓存失败不影响推演主流程。
  }
}

function isUsableCommanderCache(report) {
  if (!report) return false
  if (String(report.gate_status || '').toUpperCase() === 'FALLBACK') return false
  if (report.fallback_reason) return false
  const markdown = String(report.coach_filtered_md || report.raw_reasoning_md || '').trim()
  return Boolean(markdown)
}

function commanderHeadline(report, loading, disabled = false) {
  if (loading) return '统帅推演生成中'
  if (disabled && !report) return '结构数据待补齐'
  if (!report) return 'Free Reasoning 沙盘'
  if (report.gate_status === 'FALLBACK') return '结构事实保护模式'
  return '交易纪律沙盘已生成'
}

function deductionButtonLabel({ loading, disabled, report }) {
  if (loading) return '推演中'
  if (disabled) return '待补齐'
  return report ? '重新推演' : '开始推演'
}

function formatRunTime(value) {
  if (!value) return '--'
  try {
    return new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return '--'
  }
}
