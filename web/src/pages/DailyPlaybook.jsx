import { useEffect, useMemo, useState } from 'react'
import PlaybookItemRow from '../components/PlaybookItemRow.jsx'
import PlanResponseButtons from '../components/PlanResponseButtons.jsx'
import './DailyPlaybook.css'

const API = ''
const REQUEST_TIMEOUT_MS = 12000

async function fetchJson(url, options = {}) {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const resp = await fetch(url, { ...options, signal: controller.signal })
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}))
      throw new Error(data.detail || options.errorMessage || '请求失败')
    }
    return await resp.json()
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('后端响应超时，请稍后重试')
    throw err
  } finally {
    window.clearTimeout(timer)
  }
}

function conditionSummary(item) {
  const conditions = item.trigger?.conditions || []
  if (!conditions.length) return '暂无明确触发条件，进入 Radar 复核结构。'
  return conditions
    .slice(0, 5)
    .map((condition) => `${condition.label || condition.condition_id}: ${condition.status}`)
    .join(' / ')
}

function stopReference(item) {
  const ref = item.trigger?.stop_reference
  if (!ref?.value) return '—'
  return `${ref.level || ''}${ref.field || ''} ${Number(ref.value).toFixed(2)}`
}

export default function DailyPlaybook({ onViewInChan }) {
  const [data, setData] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [responding, setResponding] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const load = async (silent = false) => {
    if (!silent) setLoading(true)
    setError(null)
    try {
      const json = await fetchJson('/api/playbook/today', { errorMessage: '今日作战加载失败' })
      setData(json.data)
      setSelectedId((current) => current || json.data?.items?.[0]?.id || null)
    } catch (err) {
      setError(err.message)
    } finally {
      if (!silent) setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const id = window.setInterval(() => load(true), 30000)
    return () => window.clearInterval(id)
  }, [])

  const selectedItem = useMemo(() => {
    const items = data?.items || []
    return items.find((item) => item.id === selectedId) || items[0] || null
  }, [data, selectedId])

  const metrics = data?.metrics || {}
  const items = data?.items || []
  const stale = data?.freshness?.is_stale

  const generate = async () => {
    setGenerating(true)
    setError(null)
    setNotice(null)
    try {
      const json = await fetchJson('/api/playbook/today/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: 1, sources: ['positions', 'scanner', 'watchlist'], max_items: 8 }),
        errorMessage: '生成今日作战失败',
      })
      setData(json.data)
      setSelectedId(json.data?.items?.[0]?.id || null)
      setNotice('今日作战计划已生成。')
    } catch (err) {
      setError(err.message)
    } finally {
      setGenerating(false)
    }
  }

  const respond = async (response) => {
    if (!selectedItem) return
    setResponding(true)
    setError(null)
    try {
      await fetchJson(`/api/playbook/items/${selectedItem.id}/response`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ response }),
        errorMessage: '记录响应失败',
      })
      await load(true)
      setNotice('响应已记录，盘后复盘会纳入这次选择。')
    } catch (err) {
      setError(err.message)
    } finally {
      setResponding(false)
    }
  }

  if (loading && !data) {
    return (
      <div className="daily-playbook-page">
        <div className="playbook-loading">正在读取今日作战...</div>
      </div>
    )
  }

  return (
    <div className="daily-playbook-page">
      <header className="playbook-header">
        <div className="playbook-title-group">
          <h2>今日作战</h2>
          <p>盘前定计划，盘中只响应计划内事件，盘后复盘纪律偏差。</p>
          <div className="playbook-risk-note">所有计划仅供参考，不构成投资建议。</div>
        </div>
        <div className="playbook-actions">
          <button type="button" onClick={() => load(false)} disabled={loading || generating}>
            刷新
          </button>
          <button type="button" onClick={generate} disabled={generating}>
            {generating ? '生成中...' : items.length ? '重新读取计划' : '生成今日作战计划'}
          </button>
        </div>
      </header>

      {error && <div className="playbook-error">{error}</div>}
      {notice && <div className="playbook-notice">{notice}</div>}
      {stale && (
        <div className="playbook-stale">
          部分标的数据过期或结构失败，系统不会把它们当作行动触发，只保留人工复核入口。
        </div>
      )}

      <section className="playbook-metrics">
        <div><span>{items.length}</span><strong>作战项</strong></div>
        <div><span>{metrics.planned_trades || 0}</span><strong>计划内交易</strong></div>
        <div><span>{metrics.unplanned_trades || 0}</span><strong>计划外交易</strong></div>
        <div><span>{metrics.executed_items || 0}</span><strong>已执行响应</strong></div>
      </section>

      {items.length === 0 ? (
        <section className="playbook-empty">
          <h3>今天还没有作战计划</h3>
          <p>先从持仓、今日机会和自选股里生成最多 8 个观察项。少一点，盯得住。</p>
          <button type="button" onClick={generate} disabled={generating}>
            {generating ? '生成中...' : '生成今日作战计划'}
          </button>
        </section>
      ) : (
        <div className="playbook-layout">
          <section className="playbook-list" aria-label="今日作战列表">
            {items.map((item) => (
              <PlaybookItemRow
                key={item.id}
                item={item}
                active={selectedItem?.id === item.id}
                onSelect={(next) => setSelectedId(next.id)}
                onViewInChan={onViewInChan}
              />
            ))}
          </section>

          <aside className="playbook-detail">
            {selectedItem ? (
              <>
                <div className="playbook-detail-head">
                  <div>
                    <span className="playbook-detail-symbol mono">{selectedItem.symbol}</span>
                    {selectedItem.name && <span className="playbook-detail-name">{selectedItem.name}</span>}
                  </div>
                  <span className={`playbook-detail-status status-${String(selectedItem.status).toLowerCase()}`}>
                    {selectedItem.status}
                  </span>
                </div>

                <div className="playbook-detail-block">
                  <h4>触发条件</h4>
                  <p>{conditionSummary(selectedItem)}</p>
                </div>

                <div className="playbook-detail-grid">
                  <div>
                    <strong>失效条件</strong>
                    <span>{selectedItem.invalidation?.invalid_if || '—'}</span>
                  </div>
                  <div>
                    <strong>止损参考</strong>
                    <span>{stopReference(selectedItem)}</span>
                  </div>
                </div>

                {selectedItem.response && (
                  <div className="playbook-response-note">
                    已记录：{selectedItem.response.response}
                  </div>
                )}

                <PlanResponseButtons disabled={responding} onRespond={respond} />

                <button
                  type="button"
                  className="playbook-view-radar"
                  onClick={() => onViewInChan?.(selectedItem.symbol, selectedItem.name)}
                >
                  去雷达复核
                </button>
              </>
            ) : (
              <div className="playbook-empty-detail">选择一个作战项查看计划。</div>
            )}
          </aside>
        </div>
      )}
    </div>
  )
}
