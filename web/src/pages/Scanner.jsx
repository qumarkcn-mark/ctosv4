import { useEffect, useMemo, useState } from 'react'
import ScanCard from '../components/ScanCard.jsx'
import { getScanQualityFlags, getScanQualityLevel } from '../utils/scanQuality.js'
import './Scanner.css'

const API = ''
const REQUEST_TIMEOUT_MS = 8000
const STRATEGY_LABEL = {
  war1: '战法一·日线三买',
  war2: '战法二·趋势台阶',
}
const FILTERS = [
  { value: 'ready', label: '已完成' },
  { value: 'all', label: '全部' },
  { value: 'pending', label: '待分析' },
  { value: 'analyzing', label: '分析中' },
  { value: 'failed', label: '失败' },
]
const QUALITY_FILTERS = [
  { value: 'all', label: '全部质量' },
  { value: 'clean', label: '无警示' },
  { value: 'warn', label: '需复核' },
  { value: 'danger', label: '高风险' },
]
const SORTS = [
  { value: 'score', label: '评分优先' },
  { value: 'rr', label: '赔率优先' },
  { value: 'risk', label: '风险优先' },
]
const RISK_ORDER = { danger: 3, warn: 2, clean: 1 }
const EMPTY_TEXT = {
  ready: ['暂无 ready 候选', '盘后扫描完成后，技术面通过且调研层处理完的股票会出现在这里。'],
  all: ['暂无扫描候选', '手动扫描或盘后任务完成后，候选会按状态出现在这里。'],
  pending: ['暂无待分析候选', '技术扫描入库后、调研层开始前，会短暂出现在这里。'],
  analyzing: ['暂无分析中候选', 'LLM 调研运行时，候选会短暂出现在这里。'],
  failed: ['暂无失败候选', '调研失败或写回失败的候选会出现在这里，方便排查。'],
}

function strategyLabel(item) {
  return item.strategy_name || STRATEGY_LABEL[item.strategy] || item.strategy_id || item.strategy
}

async function fetchJson(url, options = {}) {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const resp = await fetch(url, { ...options, signal: controller.signal })
    if (!resp.ok) throw new Error(options.errorMessage || '请求失败')
    return await resp.json()
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('后端响应超时，请确认服务已启动')
    throw err
  } finally {
    window.clearTimeout(timer)
  }
}

function pct(value) {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(1)}%`
}

function price(value) {
  if (value === null || value === undefined) return '—'
  return Number(value).toFixed(2)
}

function ratio(value) {
  if (!value) return '—'
  return `1:${Number(value).toFixed(1)}`
}

export default function Scanner({ onViewInChan }) {
  const [items, setItems] = useState([])
  const [selectedItem, setSelectedItem] = useState(null)
  const [statusFilter, setStatusFilter] = useState('ready')
  const [qualityFilter, setQualityFilter] = useState('all')
  const [sortBy, setSortBy] = useState('score')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [busyId, setBusyId] = useState(null)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [loadedAt, setLoadedAt] = useState(null)

  const load = async () => {
    setError(null)
    try {
      const [resultJson, statusJson] = await Promise.all([
        fetchJson(`${API}/api/scan/results?status=${statusFilter}&limit=80`, { errorMessage: '候选列表加载失败' }),
        fetchJson(`${API}/api/scan/status`, { errorMessage: '扫描状态加载失败' }),
      ])
      setItems(resultJson.results || [])
      setSelectedItem((current) => {
        if (!current) return null
        return (resultJson.results || []).find((item) => item.id === current.id) || null
      })
      setStatus(statusJson)
      setLoadedAt(new Date())
    } catch (err) {
      setError(err.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    setLoading(true)
    load()
    const id = window.setInterval(load, 30000)
    return () => window.clearInterval(id)
  }, [statusFilter])

  const stats = useMemo(() => {
    const counts = status?.counts || {}
    return [
      { label: '已展示', value: counts.ready || 0 },
      { label: '待分析', value: counts.pending || 0 },
      { label: '分析中', value: counts.analyzing || 0 },
      { label: '失败', value: counts.failed || 0 },
    ]
  }, [status])
  const statusCounts = useMemo(() => {
    const counts = status?.counts || {}
    return {
      ready: counts.ready || 0,
      pending: counts.pending || 0,
      analyzing: counts.analyzing || 0,
      failed: counts.failed || 0,
      all: (counts.ready || 0) + (counts.pending || 0) + (counts.analyzing || 0) + (counts.failed || 0),
    }
  }, [status])

  const job = status?.job
  const emptyCopy = EMPTY_TEXT[statusFilter] || EMPTY_TEXT.ready
  const visibleItems = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    const filteredByQuality = qualityFilter === 'all'
      ? [...items]
      : items.filter((item) => getScanQualityLevel(item) === qualityFilter)
    const filtered = normalizedQuery
      ? filteredByQuality.filter((item) => {
        const haystack = [
          item.symbol,
          item.name,
          item.chan_desc,
          item.llm_summary,
          item.strategy,
          item.strategy_id,
          item.strategy_name,
          item.strategy_type,
        ].filter(Boolean).join(' ').toLowerCase()
        return haystack.includes(normalizedQuery)
      })
      : filteredByQuality

    return filtered.sort((a, b) => {
      if (sortBy === 'rr') {
        return Number(b.rr_ratio || 0) - Number(a.rr_ratio || 0)
      }
      if (sortBy === 'risk') {
        const riskDiff = RISK_ORDER[getScanQualityLevel(b)] - RISK_ORDER[getScanQualityLevel(a)]
        if (riskDiff !== 0) return riskDiff
      }
      return Number(b.score || 0) - Number(a.score || 0)
    })
  }, [items, qualityFilter, query, sortBy])
  const qualityCounts = useMemo(() => {
    const counts = { all: items.length, clean: 0, warn: 0, danger: 0 }
    items.forEach((item) => {
      counts[getScanQualityLevel(item)] += 1
    })
    return counts
  }, [items])
  const selectedQualityFlags = selectedItem ? getScanQualityFlags(selectedItem) : []
  const jobLabel = useMemo(() => {
    if (!job) return null
    if (job.running || job.last_status === 'queued') return '后台扫描中'
    if (job.last_status === 'completed') return `上次扫描完成：${job.last_candidate_count || 0} 个候选`
    if (job.last_status === 'failed') return '上次扫描失败'
    return null
  }, [job])

  const deleteCandidate = async (item) => {
    await fetchJson(`${API}/api/scan/results/${item.id}`, {
      method: 'DELETE',
      errorMessage: '删除候选失败',
    })
    setItems((prev) => prev.filter((x) => x.id !== item.id))
    await load()
  }

  const handleAdd = async (item) => {
    setBusyId(item.id)
    setError(null)
    setNotice(null)
    try {
      await fetchJson(`${API}/api/scan/results/${item.id}/observe`, {
        method: 'POST',
        errorMessage: '加入观察库失败',
      })
      setItems((prev) => prev.filter((x) => x.id !== item.id))
      await load()
    } catch (err) {
      setError(err.message || '加入观察库失败')
    } finally {
      setBusyId(null)
    }
  }

  const handleDelete = async (item) => {
    setBusyId(item.id)
    setError(null)
    setNotice(null)
    try {
      await deleteCandidate(item)
    } catch (err) {
      setError(err.message || '删除失败')
    } finally {
      setBusyId(null)
    }
  }

  const handleRun = async () => {
    setRunning(true)
    setError(null)
    setNotice(null)
    try {
      const response = await fetchJson(`${API}/api/scan/run`, {
        method: 'POST',
        errorMessage: '扫描触发失败',
      })
      setNotice(response.status === 'running' ? '扫描已经在后台运行' : '后台扫描已启动，结果会自动刷新')
      await load()
    } catch (err) {
      setError(err.message || '扫描触发失败')
    } finally {
      setRunning(false)
    }
  }

  const handleView = (item) => {
    onViewInChan?.(item.symbol, item.name || item.symbol)
  }

  const handleDetail = (item) => {
    setSelectedItem(item)
  }

  if (loading) {
    return (
      <div className="scanner-loading">
        <div className="scanner-spinner" />
        <div>正在读取今日机会...</div>
      </div>
    )
  }

  return (
    <div className="scanner-page">
      <div className="scanner-header">
        <div className="scanner-title-group">
          <h2>机会池</h2>
          <div className="scanner-subtitle">
            技术扫描入库，LLM 只做调研排雷
            {status?.scan_date && <span> · {status.scan_date}</span>}
            {loadedAt && (
              <span> · 更新于 {loadedAt.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
            )}
          </div>
          <div className="scanner-risk-note">
            扫描结果仅供参考，用于观察和复盘，不构成交易建议。
          </div>
        </div>
        <div className="scanner-actions">
          <button type="button" onClick={load} disabled={running}>刷新</button>
          <button type="button" onClick={handleRun} disabled={running || status?.job?.running}>
            {running || status?.job?.running ? '后台扫描中...' : '手动扫描'}
          </button>
        </div>
      </div>

      {error && <div className="scanner-error">{error}</div>}
      {notice && !error && <div className="scanner-notice">{notice}</div>}

      <div className="scanner-filter-bar" aria-label="候选状态筛选">
        {FILTERS.map((filter) => (
          <button
            key={filter.value}
            type="button"
            className={statusFilter === filter.value ? 'is-active' : ''}
            onClick={() => setStatusFilter(filter.value)}
          >
            <span>{filter.label}</span>
            <strong>{statusCounts[filter.value] || 0}</strong>
          </button>
        ))}
      </div>

      <div className="scanner-filter-bar scanner-filter-bar--quality" aria-label="候选质量筛选">
        {QUALITY_FILTERS.map((filter) => (
          <button
            key={filter.value}
            type="button"
            className={qualityFilter === filter.value ? 'is-active' : ''}
            onClick={() => setQualityFilter(filter.value)}
          >
            <span>{filter.label}</span>
            <strong>{qualityCounts[filter.value] || 0}</strong>
          </button>
        ))}
      </div>

      <div className="scanner-filter-bar scanner-filter-bar--sort" aria-label="候选排序">
        {SORTS.map((sort) => (
          <button
            key={sort.value}
            type="button"
            className={sortBy === sort.value ? 'is-active' : ''}
            onClick={() => setSortBy(sort.value)}
          >
            <span>{sort.label}</span>
          </button>
        ))}
      </div>

      <div className="scanner-search-row">
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索代码、名称、结构描述"
          aria-label="搜索候选"
        />
        {query && (
          <button type="button" onClick={() => setQuery('')}>
            清除
          </button>
        )}
      </div>

      <div className="scanner-stat-bar">
        {stats.map((s) => (
          <div key={s.label} className="scanner-stat">
            <span>{s.value}</span>
            <small>{s.label}</small>
          </div>
        ))}
      </div>

      {jobLabel && (
        <div className={`scanner-job scanner-job-${job.last_status}`}>
          <span>{jobLabel}</span>
          {job?.last_scan_date && <small>{job.last_scan_date}</small>}
          {job?.last_error && <small>{job.last_error}</small>}
        </div>
      )}

      {visibleItems.length === 0 ? (
        <div className="scanner-empty">
          <div className="scanner-empty-title">{items.length === 0 ? emptyCopy[0] : '当前筛选下暂无候选'}</div>
          <div className="scanner-empty-desc">
            {items.length === 0 ? emptyCopy[1] : '可以清空搜索，切回“全部质量”，或换一个状态筛选继续查看。'}
          </div>
        </div>
      ) : (
        <div className="scanner-grid">
          {visibleItems.map((item) => (
            <ScanCard
              key={item.id}
              item={item}
              busy={busyId === item.id}
              onView={handleView}
              onDetail={handleDetail}
              onAdd={handleAdd}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      {selectedItem && (
        <div className="scanner-drawer-shell" role="dialog" aria-modal="true" aria-label={`${selectedItem.symbol} 扫描详情`}>
          <button type="button" className="scanner-drawer-backdrop" onClick={() => setSelectedItem(null)} aria-label="关闭详情" />
          <aside className="scanner-drawer">
            <div className="scanner-drawer-head">
              <div>
                <div className="scanner-drawer-symbol">{selectedItem.symbol}</div>
                <div className="scanner-drawer-subtitle">{strategyLabel(selectedItem)}</div>
              </div>
              <button type="button" onClick={() => setSelectedItem(null)} aria-label="关闭详情">关闭</button>
            </div>

            <div className="scanner-detail-section">
              <h3>入选原因</h3>
              <p>{selectedItem.chan_desc || '结构信号待补充'}</p>
              <div className="scanner-detail-score">
                <span>{Math.round(selectedItem.score || 0)}</span>
                <small>综合评分</small>
              </div>
            </div>

            <div className="scanner-detail-grid">
              <div><span>现价</span><strong>{price(selectedItem.close)}</strong></div>
              <div><span>止损</span><strong>{price(selectedItem.stop_loss)}</strong></div>
              <div><span>目标</span><strong>{price(selectedItem.target)}</strong></div>
              <div><span>赔率</span><strong>{ratio(selectedItem.rr_ratio)}</strong></div>
              <div><span>ATR</span><strong>{pct(selectedItem.atr_pct)}</strong></div>
              <div><span>量比</span><strong>{selectedItem.volume_ratio ? Number(selectedItem.volume_ratio).toFixed(2) : '—'}</strong></div>
            </div>

            <div className="scanner-detail-section">
              <h3>质量检查</h3>
              {selectedQualityFlags.length === 0 ? (
                <p>当前未触发质量警示。</p>
              ) : (
                <div className="scanner-detail-quality">
                  {selectedQualityFlags.map((flag) => (
                    <span key={flag.label} className={`scanner-detail-quality-${flag.level}`}>{flag.label}</span>
                  ))}
                </div>
              )}
            </div>

            <div className="scanner-detail-section">
              <h3>调研结论</h3>
              <div className="scanner-detail-verdict">
                <span>{selectedItem.llm_verdict || '中性'}</span>
                <p>{selectedItem.llm_summary || '仅技术面通过，待基本面确认'}</p>
              </div>
            </div>

            <div className="scanner-detail-columns">
              <div className="scanner-detail-section">
                <h3>支持因素</h3>
                {(selectedItem.llm_pros || []).length === 0 ? (
                  <p>暂无支持因素。</p>
                ) : (
                  <ul>{selectedItem.llm_pros.map((item, index) => <li key={index}>{item}</li>)}</ul>
                )}
              </div>
              <div className="scanner-detail-section">
                <h3>风险因素</h3>
                {(selectedItem.llm_cons || []).length === 0 ? (
                  <p>暂无风险因素。</p>
                ) : (
                  <ul>{selectedItem.llm_cons.map((item, index) => <li key={index}>{item}</li>)}</ul>
                )}
              </div>
            </div>

            {(selectedItem.llm_red_flags || []).length > 0 && (
              <div className="scanner-detail-section scanner-detail-redflags">
                <h3>红旗风险</h3>
                <ul>{selectedItem.llm_red_flags.map((item, index) => <li key={index}>{item}</li>)}</ul>
              </div>
            )}

            <div className="scanner-detail-actions">
              <button type="button" onClick={() => handleView(selectedItem)}>看盘</button>
              <button type="button" onClick={() => handleAdd(selectedItem)} disabled={busyId === selectedItem.id}>入观察</button>
            </div>

            <div className="scanner-detail-risk">
              以上内容仅供参考，用于观察和复盘，不构成交易建议。
            </div>
          </aside>
        </div>
      )}
    </div>
  )
}
