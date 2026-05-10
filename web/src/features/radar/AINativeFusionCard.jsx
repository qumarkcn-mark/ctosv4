import { useEffect, useMemo, useState } from 'react'
import { API_BASE } from '../../config.js'

export default function AINativeFusionCard({ symbol, mode, signalCode = '', structureFingerprint = '', disabled = false, disabledReason = '' }) {
  const cacheKey = `ct_ai_native_fusion:v11_structure:${symbol || 'unknown'}:${mode || 'UNKNOWN'}:${signalCode || 'no_signal'}:${structureFingerprint || 'no_structure'}`
  const [loading, setLoading] = useState(false)
  const [report, setReport] = useState(() => readCachedReport(cacheKey))
  const [error, setError] = useState('')
  const visibleReport = disabled ? null : report
  const fusion = visibleReport?.fusion
  const firstStage = visibleReport?.first_stage_reasoning
  const aiChan = visibleReport?.ai_chan_inference
  const kronos = visibleReport?.kronos_forecast
  const alignment = visibleReport?.data_alignment || fusion?.diagnostics?.data_alignment
  const primary = useMemo(() => primaryPath(fusion), [fusion])
  const state = fusionState(fusion, loading, disabled)

  useEffect(() => {
    if (disabled) {
      setReport(null)
      setError('')
      return
    }
    setReport(readCachedReport(cacheKey))
    setError('')
  }, [cacheKey, disabled])

  const runFusion = async () => {
    if (!symbol || loading || disabled) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/agent/ai-native-fusion`, {
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
        throw new Error(json?.detail || json?.message || 'V7.0 Fusion 推演失败')
      }
      const nextReport = {
        ...json.data,
        generated_at: json.data?.fusion?.generated_at || new Date().toISOString(),
      }
      setReport(nextReport)
      cacheReport(cacheKey, nextReport)
    } catch (err) {
      setError(err?.message || 'V7.0 Fusion 推演失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className={`radar-fusion-panel radar-fusion-panel--${state.tone}`}>
      <div className="radar-fusion-head">
        <div>
          <span>统一走势推演</span>
          <strong>{state.headline}</strong>
        </div>
        <em className={`radar-fusion-status radar-fusion-status--${state.tone}`}>{state.label}</em>
        <button type="button" onClick={runFusion} disabled={loading || !symbol || disabled}>
          {loading ? '生成中' : fusion ? '重新生成' : '生成综合判断'}
        </button>
      </div>

      <div className="radar-ai-disclaimer">基于当前结构、持仓状态和预测参考生成交易教练判断。仅供参考，不构成投资建议。</div>
      {error && <div className="radar-ai-error">{error}</div>}
      {(alignment || aiChan || kronos) && <DataAlignmentStrip alignment={alignment} aiChan={aiChan} kronos={kronos} />}
      {firstStage ? <FirstStageSummary report={firstStage} /> : aiChan && <AIChanSummary inference={aiChan} />}
      {fusion?.fallback_reason && (
        <div className="radar-fusion-fallback">
          <span>结构兜底</span>
          <strong>{fusion.fallback_reason}</strong>
        </div>
      )}

      {disabled && !fusion && !loading && (
        <div className="radar-commander-empty">
          <strong>等待正式结构数据</strong>
          <span>{disabledReason || '正式结构数据补齐后，再生成综合判断。'}</span>
        </div>
      )}
      {!disabled && !fusion && !loading && (
        <div className="radar-commander-empty">
          <strong>等待综合判断</strong>
          <span>先看结构是否有效，再补充观察条件和失效边界。</span>
        </div>
      )}
      {loading && !fusion && <div className="radar-ai-loading">正在生成结构判断、观察条件和失效边界...</div>}

      {fusion && (
        <>
          <div className="radar-fusion-judgement">
            <span>统一结论</span>
            <strong>{fusion.current_judgement}</strong>
          </div>

          {primary && (
            <div className="radar-fusion-primary">
              <div className="radar-fusion-primary-top">
                <span>{primary.name}</span>
                <strong>{state.tone === 'fallback' ? '结构' : formatPercent(primary.probability)}</strong>
              </div>
              <div className="radar-fusion-path-track">
                <i style={{ width: `${clampPercent(primary.probability)}%` }} />
              </div>
              <dl>
                <div>
                  <dt>缠论</dt>
                  <dd>{primary.chan_basis}</dd>
                </div>
                <div>
                  <dt>Kronos</dt>
                  <dd>{primary.kronos_basis}</dd>
                </div>
              </dl>
            </div>
          )}

          <PathProbabilityList paths={fusion.path_inferences || []} primaryId={fusion.primary_path_id} isFallback={state.tone === 'fallback'} />

          <div className="radar-fusion-grid">
            <MiniList title="等待" items={fusion.wait_for} />
            <MiniList title="失效" items={fusion.invalidation} />
          </div>

          <ActionPlaybookPanel playbook={fusion.action_playbook} />

          {fusion.coach_message && (
            <div className="radar-fusion-coach">
              <span>教练话术</span>
              <strong>{fusion.coach_message}</strong>
            </div>
          )}

          <div className="radar-commander-meta">
            <span>{kronos?.model_scope ? `预测参考 ${kronos.model_scope}` : '预测参考'}</span>
            <span>{diagnosticSummary(fusion) || formatRunTime(fusion.generated_at || visibleReport.generated_at)}</span>
          </div>

          {fusion.diagnostics && (
            <details className="radar-fusion-diagnostics">
              <summary>推演详情</summary>
              <div>
                <span>{firstStageSourceLabel(fusion.diagnostics.first_stage_source)}</span>
                <span>Radar {formatMs(fusion.diagnostics.radar_ms)}</span>
                <span>AI Chan {formatMs(fusion.diagnostics.ai_chan_ms)}</span>
                <span>Total {formatMs(fusion.diagnostics.total_ms)}</span>
                <span>LLM {formatMs(fusion.diagnostics.llm_ms)}</span>
                <span>预测 {formatMs(fusion.diagnostics.kronos_ms)}</span>
                <span>{formatPromptSize(fusion.diagnostics.prompt_chars)}</span>
                {fusion.diagnostics.structure_profile && <span>{String(fusion.diagnostics.structure_profile).toUpperCase()}</span>}
                {fusion.diagnostics.structure_cache_hit && <span>结构缓存</span>}
              </div>
            </details>
          )}

          {kronos?.warnings?.length > 0 && (
            <div className="radar-ai-warning-note">
              {kronos.warnings.join(' / ')}
            </div>
          )}
        </>
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

function AIChanSummary({ inference }) {
  const confidence = Number(inference.structure_confidence)
  const confidenceText = Number.isFinite(confidence) ? `${Math.round(confidence * 100)}%` : '--'
  const primary = (inference.paths || []).find((path) => path.id === inference.primary_path_id) || inference.paths?.[0]
  const corrections = Array.isArray(inference.corrections) ? inference.corrections.filter(Boolean).slice(0, 2) : []
  const uncertainty = Array.isArray(inference.uncertainty) ? inference.uncertainty.filter(Boolean).slice(0, 2) : []

  return (
    <div className="radar-ai-chan-summary">
      <div className="radar-ai-chan-summary-head">
        <div>
          <span>AI Chan</span>
          <strong>{inference.current_position || '缠论推演等待补齐'}</strong>
        </div>
        <em>{confidenceText}</em>
      </div>
      {primary && (
        <div className="radar-ai-chan-path">
          <span>{primary.name || primary.id}</span>
          <strong>{primary.entry_condition || '等待结构条件补齐'}</strong>
        </div>
      )}
      {(corrections.length > 0 || uncertainty.length > 0) && (
        <div className="radar-ai-chan-notes">
          {corrections.map((item, index) => (
            <span key={`correction-${index}`}>修正 · {item}</span>
          ))}
          {uncertainty.map((item, index) => (
            <span key={`uncertainty-${index}`}>不确定 · {item}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function FirstStageSummary({ report }) {
  const text = String(report?.coach_filtered_md || report?.raw_reasoning_md || '')
  const position = extractMarkdownSection(text, ['当前定位', '全局语境定性']) || '第一步 AI 推演已生成。'
  const scripts = extractMarkdownSection(text, ['三种剧本', '推演与应对沙盘'])
  const scriptTitles = extractScriptTitles(scripts).slice(0, 3)
  return (
    <div className="radar-first-stage-summary">
      <div className="radar-first-stage-summary-head">
        <div>
          <span>第一步 AI 推演</span>
          <strong>{compactText(position, 110)}</strong>
        </div>
        <em>{report?.gate_status || 'READY'}</em>
      </div>
      {scriptTitles.length > 0 && (
        <div className="radar-first-stage-scripts">
          {scriptTitles.map((title, index) => (
            <span key={`${title}-${index}`}>{title}</span>
          ))}
        </div>
      )}
      <p>综合判断会优先承接这份“当前定位 + 三种剧本”，预测参考只用于复查时间和价格区间。</p>
    </div>
  )
}

function DataAlignmentStrip({ alignment, aiChan, kronos }) {
  const status = String(alignment?.status || 'UNKNOWN').toUpperCase()
  const tone = status === 'ALIGNED' ? 'ready' : status.includes('STALE') ? 'warning' : 'neutral'
  return (
    <div className={`radar-data-alignment radar-data-alignment--${tone}`}>
      <div>
        <span>数据切片</span>
        <strong>{alignmentLabel(status)}</strong>
      </div>
      <dl>
        <div>
          <dt>AI切片</dt>
          <dd>{formatDateTime(alignment?.analysis_data_time || alignment?.ai_chan_generated_at || aiChan?.generated_at)}</dd>
        </div>
        <div>
          <dt>Kronos</dt>
          <dd>{formatDateTime(alignment?.primary_data_time || alignment?.kronos_generated_at || kronos?.generated_at)}</dd>
        </div>
        {alignment?.max_delta_minutes != null && (
          <div>
            <dt>偏差</dt>
            <dd>{formatDelta(alignment.max_delta_minutes)}</dd>
          </div>
        )}
      </dl>
      {alignment?.note && <p>{alignment.note}</p>}
    </div>
  )
}

function extractMarkdownSection(markdown, titles) {
  const text = String(markdown || '').replace(/\r\n/g, '\n')
  const headingPattern = /^#{0,6}\s*\*{0,3}\s*(?:\d+[.、]\s*)?【([^】]+)】[^\n]*$/gm
  const matches = [...text.matchAll(headingPattern)]
  for (let i = 0; i < matches.length; i += 1) {
    const title = matches[i][1]
    if (!titles.some((item) => title.includes(item))) continue
    const start = (matches[i].index || 0) + matches[i][0].length
    const end = i + 1 < matches.length ? matches[i + 1].index || text.length : text.length
    return text.slice(start, end).replace(/^[-*_]{3,}\s*$/gm, '').trim()
  }
  return ''
}

function extractScriptTitles(markdown) {
  return [...String(markdown || '').matchAll(/(?:剧本|预案)[一二三四五六七八九十A-Z0-9]+[：:：\s]+([^\n（(]+)/g)]
    .map((match, index) => `${['A', 'B', 'C', 'D'][index] || index + 1} ${String(match[1] || '').replace(/\*+/g, '').trim()}`)
    .filter(Boolean)
}

function compactText(value, maxLength) {
  const text = String(value || '').replace(/\s+/g, ' ').replace(/\*+/g, '').trim()
  if (text.length <= maxLength) return text
  return `${text.slice(0, maxLength)}...`
}

function ActionPlaybookPanel({ playbook }) {
  if (!playbook) return null
  const action = String(playbook.action || 'OBSERVE').toUpperCase()
  const groups = [
    { key: 'test', label: '试仓', items: playbook.test_conditions },
    { key: 'add', label: '加仓', items: playbook.add_conditions },
    { key: 'reduce', label: '减仓', items: playbook.reduce_conditions },
    { key: 'exit', label: '清仓', items: playbook.exit_conditions },
    { key: 'hold', label: '持有', items: playbook.hold_conditions },
  ].filter((group) => Array.isArray(group.items) && group.items.some(Boolean))

  return (
    <div className={`radar-fusion-playbook radar-fusion-playbook--${actionTone(action)}`}>
      <div className="radar-fusion-playbook-head">
        <div>
          <span>动作条件</span>
          <strong>{playbook.action_label || actionLabel(action)}</strong>
        </div>
        <em>{action}</em>
      </div>

      <div className="radar-fusion-playbook-meta">
        <span>复核 {recheckLabel(playbook.recheck_trigger)}</span>
        <span>上限 {formatWeight(playbook.max_position_weight_pct)}</span>
      </div>

      {playbook.primary_reason && (
        <p className="radar-fusion-playbook-reason">{playbook.primary_reason}</p>
      )}

      {groups.length > 0 ? (
        <div className="radar-fusion-playbook-groups">
          {groups.map((group) => (
            <ConditionGroup key={group.key} title={group.label} items={group.items} tone={group.key} />
          ))}
        </div>
      ) : (
        <div className="radar-fusion-playbook-empty">当前没有触发条件，保持观察。</div>
      )}

      {playbook.risk_note && (
        <div className="radar-fusion-playbook-risk">{playbook.risk_note}</div>
      )}
    </div>
  )
}

function ConditionGroup({ title, items, tone }) {
  const list = Array.isArray(items) ? items.filter(Boolean).slice(0, 3) : []
  if (!list.length) return null
  return (
    <div className={`radar-fusion-condition radar-fusion-condition--${tone}`}>
      <span>{title}</span>
      {list.map((item, index) => (
        <strong key={`${title}-${index}`}>{item}</strong>
      ))}
    </div>
  )
}

function PathProbabilityList({ paths, primaryId, isFallback }) {
  if (!paths.length) return null
  return (
    <div className="radar-fusion-paths">
      {paths.slice(0, 4).map((path) => (
        <div key={path.id} className={`radar-fusion-path ${path.id === primaryId ? 'is-primary' : ''} ${isFallback ? 'is-fallback' : ''}`}>
          <span>{path.name || path.chan_path_id}</span>
          <div className="radar-fusion-path-track">
            <i style={{ width: `${clampPercent(path.probability)}%` }} />
          </div>
          <strong>{isFallback ? '结构' : formatPercent(path.probability)}</strong>
        </div>
      ))}
    </div>
  )
}

function MiniList({ title, items }) {
  const list = Array.isArray(items) ? items.filter(Boolean).slice(0, 3) : []
  return (
    <div className="radar-fusion-mini-list">
      <span>{title}</span>
      {list.length ? list.map((item, index) => <strong key={`${title}-${index}`}>{item}</strong>) : <strong>等待补齐</strong>}
    </div>
  )
}

function actionTone(action) {
  if (action === 'EXIT') return 'danger'
  if (action === 'REDUCE') return 'warning'
  if (action === 'TEST' || action === 'ADD') return 'active'
  if (action === 'HOLD') return 'hold'
  return 'observe'
}

function actionLabel(action) {
  return {
    EXIT: '退出或降到极小观察仓',
    REDUCE: '降低风险暴露',
    HOLD: '持有但守防线',
    OBSERVE: '观察等待确认',
    TEST: '满足条件后试仓',
    ADD: '确认后再加仓',
    NO_ACTION: '无动作',
  }[action] || '观察等待确认'
}

function recheckLabel(value) {
  return {
    NEXT_5M_CLOSE: '5分收盘',
    NEXT_30M_CLOSE: '30分收盘',
    NEXT_DAILY_CLOSE: '日线收盘',
    PRICE_TOUCH: '触价',
    MANUAL_REFRESH: '手动',
  }[value] || '30分收盘'
}

function formatWeight(value) {
  const num = Number(value)
  if (!Number.isFinite(num) || num <= 0) return '--'
  return `${num.toFixed(num >= 10 ? 0 : 1)}%`
}

function primaryPath(fusion) {
  const paths = fusion?.path_inferences || []
  if (!paths.length) return null
  return paths.find((path) => path.id === fusion.primary_path_id) || paths[0]
}

function fusionHeadline(fusion, loading) {
  if (loading) return '正在生成综合判断'
  if (!fusion) return '等待综合判断'
  return fusion.primary_path_id ? `主路径 ${fusion.primary_path_id}` : '推演已生成'
}

function fusionState(fusion, loading, disabled = false) {
  if (loading) {
    return { tone: 'running', label: '生成中', headline: '正在生成综合判断' }
  }
  if (disabled && !fusion) {
    return { tone: 'idle', label: '待补齐', headline: '结构数据待补齐' }
  }
  if (!fusion) {
    return { tone: 'idle', label: '等待', headline: '等待综合判断' }
  }
  if (fusion.fallback_reason || String(fusion.primary_path_id || '').startsWith('fallback-')) {
    return { tone: 'fallback', label: '结构兜底', headline: '结构事实兜底' }
  }
  return { tone: 'ready', label: '已生成', headline: fusionHeadline(fusion, false) }
}

function formatPercent(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '--'
  return `${Math.round(num * 100)}%`
}

function clampPercent(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return 0
  return Math.max(2, Math.min(100, Math.round(num * 100)))
}

function formatRunTime(value) {
  if (!value) return '--'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16)
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function formatDateTime(value) {
  if (!value) return '--'
  try {
    const text = String(value).replace(' ', 'T')
    const parsed = new Date(text)
    if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 16)
    return parsed.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    })
  } catch {
    return String(value).slice(0, 16)
  }
}

function alignmentLabel(status) {
  if (status === 'ALIGNED') return '同段数据'
  if (status === 'STALE_KRONOS') return 'Kronos 偏旧'
  if (status === 'STALE_CHAN') return '缠论偏旧'
  return '待确认'
}

function formatDelta(minutes) {
  const value = Number(minutes)
  if (!Number.isFinite(value)) return '--'
  if (value < 1) return '<1m'
  return `${Math.round(value)}m`
}

function formatMs(value) {
  const num = Number(value)
  if (!Number.isFinite(num)) return '--'
  if (num >= 1000) return `${(num / 1000).toFixed(num >= 10000 ? 0 : 1)}s`
  return `${Math.round(num)}ms`
}

function formatPromptSize(value) {
  const num = Number(value)
  if (!Number.isFinite(num) || num <= 0) return 'Prompt --'
  if (num >= 1000) return `Prompt ${(num / 1000).toFixed(1)}k`
  return `Prompt ${Math.round(num)}`
}

function firstStageSourceLabel(value) {
  if (value === 'latest_ai_reasoning') return 'Step1 缓存'
  if (value === 'generated_ai_chan') return 'Step1 新生成'
  return 'Step1 --'
}

function diagnosticSummary(fusion) {
  const diagnostics = fusion?.diagnostics
  if (!diagnostics) return ''
  const total = formatMs(diagnostics.total_ms)
  const llm = formatMs(diagnostics.llm_ms)
  if (fusion?.fallback_reason) return `FALLBACK · ${llm}`
  return `AI READY · ${total}`
}

function readCachedReport(key) {
  try {
    clearLegacyFusionCache()
    const raw = localStorage.getItem(key)
    const report = raw ? JSON.parse(raw) : null
    if (!isUsableFusionCache(report)) return null
    return report
  } catch {
    return null
  }
}

function cacheReport(key, report) {
  try {
    localStorage.setItem(key, JSON.stringify(report))
  } catch {
    // 缓存失败不影响推演展示。
  }
}

function clearLegacyFusionCache() {
  const keys = Object.keys(localStorage)
  for (const key of keys) {
    if (key.startsWith('ct_ai_native_fusion:v1:') || key.startsWith('ct_ai_native_fusion:v2:') || key.startsWith('ct_ai_native_fusion:v3:') || key.startsWith('ct_ai_native_fusion:v4:') || key.startsWith('ct_ai_native_fusion:v5:') || key.startsWith('ct_ai_native_fusion:v6:') || key.startsWith('ct_ai_native_fusion:v7:') || key.startsWith('ct_ai_native_fusion:v8:')) {
      localStorage.removeItem(key)
    }
  }
}

function isUsableFusionCache(report) {
  if (!report?.fusion) return false
  const fusion = report.fusion
  if (fusion.fallback_reason || String(fusion.primary_path_id || '').startsWith('fallback-')) return false
  const kronos = report.kronos_forecast || {}
  const warnings = Array.isArray(kronos.warnings) ? kronos.warnings.join(' ') : ''
  const text = [
    fusion.current_judgement,
    fusion.coach_message,
    fusion.path_inferences?.map((item) => `${item.name || ''} ${item.kronos_basis || ''}`).join(' '),
    warnings,
  ].join(' ')
  if (text.includes('path_probability_proxy_from_force_score') || text.includes('force_score代理') || text.includes('force_score 代理')) {
    return false
  }
  return Boolean((report.first_stage_reasoning || report.ai_chan_inference) && fusion.action_playbook)
}
