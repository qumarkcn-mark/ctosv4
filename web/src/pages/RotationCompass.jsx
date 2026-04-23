import { useState, useEffect } from 'react'
import { API_BASE } from '../config.js'
import './RotationCompass.css'

const STATE_COLORS = {
  '🟢': 'state-green',
  '🚀': 'state-fire',
  '✅': 'state-green-soft',
  '⚖️': 'state-neutral',
  '📈': 'state-neutral-up',
  '⚠️': 'state-warn',
  '🔴': 'state-danger',
  '🛑': 'state-danger-strong',
  '⚪': 'state-muted',
}

// sort_score 驱动背景色深度（不显示数字，只影响行底色）
function scoreDepthStyle(score) {
  if (score >= 75) return { background: 'rgba(34,197,94,0.18)' }   // 强势绿
  if (score >= 55) return { background: 'rgba(251,191,36,0.10)' }  // 偏多黄
  if (score >= 35) return { background: 'rgba(148,163,184,0.06)' } // 中性
  return { background: 'rgba(239,68,68,0.14)' }                    // 弱势红
}

function Row({ r, showPnL, onViewInChan }) {
  const colorClass = STATE_COLORS[r.state_emoji] || 'state-muted'
  const depthStyle = scoreDepthStyle(r.sort_score || 0)
  return (
    <tr className={`rc-row ${colorClass}`} style={depthStyle}>
      <td className="rc-sym">
        <div className="sym-line">
          <span className="sym-code">{r.symbol}</span>
          {r.name && <span className="sym-name">{r.name}</span>}
          {onViewInChan && (
            <button
              className="rc-view-btn"
              title="缠论看盘"
              onClick={() => onViewInChan(r.symbol, r.name)}
            >
              🔮
            </button>
          )}
        </div>
        {r.quantity ? (
          <div className="sym-sub">
            {r.quantity}股 · 成本 {r.avg_cost?.toFixed(2)}
          </div>
        ) : (
          r.price && <div className="sym-sub">现价 {r.price.toFixed(2)}</div>
        )}
      </td>
      <td className="rc-state">
        <span className="state-emoji">{r.state_emoji}</span>
        <span className="state-label">{r.state_label}</span>
        {r.zoushi_type && <div className="zoushi-tag">{r.zoushi_type}</div>}
      </td>
      <td className="rc-node">
        {r.lifecycle_node
          ? <span className="node-tag">{r.lifecycle_node}</span>
          : <span className="dist-na">—</span>}
      </td>
      <td className="rc-dist">
        {r.distance_pct !== null && r.distance_pct !== undefined ? (
          <span
            className={
              r.distance_pct <= 0
                ? 'dist-broken'
                : r.distance_pct < 3
                ? 'dist-tight'
                : 'dist-safe'
            }
          >
            {r.distance_pct > 0 ? '+' : ''}
            {r.distance_pct}%
          </span>
        ) : (
          <span className="dist-na">—</span>
        )}
        {r.stop_loss ? (
          <div className="dist-stop">防线 {r.stop_loss.toFixed(2)}</div>
        ) : null}
      </td>
      <td className="rc-action">
        {r.main_action
          ? <div className="main-action-primary" title={r.main_action}>{r.main_action}</div>
          : <span className="dist-na">—</span>}
        {r.error && <div className="action-error">⚠ {r.error}</div>}
      </td>
      {showPnL && (
        <td className={`rc-pnl ${r.pnl_pct >= 0 ? 'up' : 'down'}`}>
          {r.pnl_pct > 0 ? '+' : ''}
          {r.pnl_pct || 0}%
        </td>
      )}
    </tr>
  )
}

export default function RotationCompass({ onViewInChan }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [loadedAt, setLoadedAt] = useState(null)

  const load = () => {
    setLoading(true)
    setError(null)
    fetch(`${API_BASE}/rotation/compass`)
      .then((r) => r.json())
      .then((j) => {
        if (j.status === 'success') {
          setData(j.data)
          setLoadedAt(new Date())
        } else {
          setError(j.message || '接口异常')
        }
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
  }, [])

  if (loading) {
    return (
      <div className="rc-loading">
        <div className="spinner" />
        <div>🧭 正在对照所有持仓与候选股…</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="rc-error">
        <div>加载失败：{error}</div>
        <button onClick={load}>重试</button>
      </div>
    )
  }

  if (!data) return null

  const { holdings, candidates, suggestions, summary } = data
  const hasSuggestions =
    suggestions &&
    (suggestions.cut.length || suggestions.add.length || suggestions.rotate.length)

  return (
    <div className="rotation-compass">
      <div className="rc-header">
        <div className="rc-title-group">
          <h2>🧭 调仓罗盘</h2>
          <div className="rc-subtitle">
            基于缠论结构的全仓位横向对比 · {summary.holdings_count} 只持仓 ·{' '}
            {summary.candidates_count} 只候选
            {loadedAt && (
              <span className="rc-loaded-at">
                {' '}· 更新于 {loadedAt.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
              </span>
            )}
          </div>
        </div>
        <button className="rc-refresh" onClick={load} disabled={loading}>
          🔄 重新对照
        </button>
      </div>

      {/* ── 顶部摘要条 ── */}
      <div className="rc-summary-bar">
        <div className="summary-stat">
          <div className="stat-val stat-good">{summary.top_holding_score}</div>
          <div className="stat-lbl">最强持仓分</div>
        </div>
        <div className="summary-stat">
          <div className={`stat-val ${summary.worst_holding_score < 45 ? 'stat-bad' : 'stat-mid'}`}>
            {summary.worst_holding_score}
          </div>
          <div className="stat-lbl">最弱持仓分</div>
        </div>
        <div className="summary-stat">
          <div className="stat-val">{summary.cut_count}</div>
          <div className="stat-lbl">建议砍出</div>
        </div>
        <div className="summary-stat">
          <div className="stat-val">{summary.add_count}</div>
          <div className="stat-lbl">建议加仓</div>
        </div>
        <div className="summary-stat">
          <div className="stat-val">{summary.rotate_count}</div>
          <div className="stat-lbl">候选可换</div>
        </div>
        {summary.freed_cash_estimate > 0 && (
          <div className="summary-stat stat-freed">
            <div className="stat-val">¥{summary.freed_cash_estimate.toLocaleString()}</div>
            <div className="stat-lbl">可腾挪现金</div>
          </div>
        )}
      </div>

      {/* ── 建议调仓 ── */}
      {hasSuggestions ? (
        <div className="rc-suggestions">
          <h3>🔀 今日建议调仓</h3>
          <div className="sug-grid">
            {suggestions.cut.length > 0 && (
              <div className="sug-col sug-cut">
                <h4>✂️ 砍 ({suggestions.cut.length})</h4>
                {suggestions.cut.map((s, i) => (
                  <div key={i} className="sug-item">
                    <div className="sug-sym">
                      {s.symbol} <span className="sug-name">{s.name}</span>
                    </div>
                    <div className="sug-act">{s.action}</div>
                    <div className="sug-reason">{s.reason}</div>
                    {s.freed ? (
                      <div className="sug-freed">腾 ¥{s.freed.toLocaleString()}</div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
            {suggestions.add.length > 0 && (
              <div className="sug-col sug-add">
                <h4>🔼 加 ({suggestions.add.length})</h4>
                {suggestions.add.map((s, i) => (
                  <div key={i} className="sug-item">
                    <div className="sug-sym">
                      {s.symbol} <span className="sug-name">{s.name}</span>
                    </div>
                    <div className="sug-act">{s.action}</div>
                    <div className="sug-reason">{s.reason}</div>
                  </div>
                ))}
              </div>
            )}
            {suggestions.rotate.length > 0 && (
              <div className="sug-col sug-rot">
                <h4>🔄 换 ({suggestions.rotate.length})</h4>
                {suggestions.rotate.map((s, i) => (
                  <div key={i} className="sug-item">
                    <div className="sug-sym">
                      {s.symbol} <span className="sug-name">{s.name}</span>
                    </div>
                    <div className="sug-act">{s.action}</div>
                    <div className="sug-reason">{s.reason}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="rc-no-suggestions">
          ✅ 当前无明显调仓信号，持仓整体健康
        </div>
      )}

      {/* ── 现有持仓 ── */}
      <div className="rc-section">
        <h3>📌 现有持仓 ({holdings.length})</h3>
        {holdings.length === 0 ? (
          <div className="rc-empty">当前空仓</div>
        ) : (
          <div className="rc-table-wrap">
            <table className="rc-table">
              <thead>
                <tr>
                  <th>标的</th>
                  <th>结构状态</th>
                  <th>买卖节点</th>
                  <th>距防线</th>
                  <th>结构指引</th>
                  <th>浮盈</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h) => (
                  <Row key={h.symbol} r={h} showPnL onViewInChan={onViewInChan} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── 关注候选 ── */}
      <div className="rc-section">
        <h3>👀 关注候选 ({candidates.length})</h3>
        {candidates.length === 0 ? (
          <div className="rc-empty">
            自选股为空。可在「缠论看盘」页搜索股票并加入自选列表。
          </div>
        ) : (
          <div className="rc-table-wrap">
            <table className="rc-table">
              <thead>
                <tr>
                  <th>标的</th>
                  <th>结构状态</th>
                  <th>买卖节点</th>
                  <th>距防线</th>
                  <th>结构指引</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c) => (
                  <Row key={c.symbol} r={c} showPnL={false} onViewInChan={onViewInChan} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="rc-footer-note">
        行颜色深度由结构强弱决定（绿深=强势 / 红深=弱势）。"买卖节点"为缠论客观结构节点，"结构指引"为甲情形首句操作叙述，仅供参考，不构成交易建议。
      </div>
    </div>
  )
}
