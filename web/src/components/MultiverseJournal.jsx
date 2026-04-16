import { useState, useEffect, useCallback } from 'react'
import './MultiverseJournal.css'

const API_BASE = 'http://localhost:8000/api/multiverse'

// ── 颜色映射 ──
const CLS_COLORS = { A: '#00c087', B: '#f0b90b', C: '#f6465d' }
const ZOUSHI_BADGE = {
  '上涨趋势': { bg: 'rgba(0,192,135,0.15)', color: '#00c087' },
  '下跌趋势': { bg: 'rgba(246,70,93,0.15)', color: '#f6465d' },
  '盘整': { bg: 'rgba(240,185,11,0.15)', color: '#f0b90b' },
  '构建中': { bg: 'rgba(255,255,255,0.08)', color: '#9aa0a6' },
}

export default function MultiverseJournal({ symbol }) {
  const [tab, setTab] = useState('timeline')
  const [timeline, setTimeline] = useState([])
  const [scorecard, setScorecard] = useState(null)
  const [loading, setLoading] = useState(false)
  const [snapshotting, setSnapshotting] = useState(false)

  // 加载数据
  const fetchData = useCallback(async () => {
    if (!symbol) return
    setLoading(true)
    try {
      const [tlRes, scRes] = await Promise.all([
        fetch(`${API_BASE}/timeline/${symbol}?days=30`),
        fetch(`${API_BASE}/scorecard/${symbol}?days=30`),
      ])
      const tlData = await tlRes.json()
      const scData = await scRes.json()
      setTimeline(tlData.data || [])
      setScorecard(scData.data || null)
    } catch (e) {
      console.error('Multiverse fetch error:', e)
    }
    setLoading(false)
  }, [symbol])

  useEffect(() => { fetchData() }, [fetchData])

  // 手动拍快照
  const handleSnapshot = async () => {
    setSnapshotting(true)
    try {
      await fetch(`${API_BASE}/snapshot/${symbol}`, { method: 'POST' })
      await fetch(`${API_BASE}/settle/${symbol}`, { method: 'POST' })
      await fetchData()
    } catch (e) {
      console.error('Snapshot error:', e)
    }
    setSnapshotting(false)
  }

  return (
    <div className="mv-journal">
      {/* Tab 切换 */}
      <div className="mv-tabs">
        <button className={`mv-tab ${tab === 'timeline' ? 'active' : ''}`}
                onClick={() => setTab('timeline')}>📅 时间线</button>
        <button className={`mv-tab ${tab === 'tree' ? 'active' : ''}`}
                onClick={() => setTab('tree')}>🌳 分支树</button>
        <button className={`mv-tab ${tab === 'score' ? 'active' : ''}`}
                onClick={() => setTab('score')}>📊 记分卡</button>
        <button className="mv-snapshot-btn" onClick={handleSnapshot}
                disabled={snapshotting}>
          {snapshotting ? '📸...' : '📸 拍快照'}
        </button>
      </div>

      {loading ? (
        <div className="mv-loading">加载中...</div>
      ) : (
        <>
          {tab === 'timeline' && <TimelineView data={timeline} />}
          {tab === 'tree' && <TreeView data={timeline} />}
          {tab === 'score' && <ScorecardView data={scorecard} />}
        </>
      )}
    </div>
  )
}


// ═══ 时间线视图 ═══
function TimelineView({ data }) {
  if (!data || data.length === 0) {
    return <div className="mv-empty">暂无数据，请先点击"📸 拍快照"记录今天的分类</div>
  }

  return (
    <div className="mv-timeline">
      {data.map((snap, i) => (
        <TimelineCard key={snap.id} snap={snap} isToday={i === 0} />
      ))}
    </div>
  )
}

function TimelineCard({ snap, isToday }) {
  const isSettled = snap.status === 'SETTLED'
  const isPending = snap.status === 'PENDING'

  return (
    <div className={`mv-card ${isToday ? 'today' : ''} ${isSettled ? 'settled' : ''}`}>
      {/* 日期头 */}
      <div className="mv-card-header">
        <span className="mv-date">
          📅 {snap.date} {isToday ? '(今天)' : ''}
        </span>
        <span className={`mv-status ${snap.status?.toLowerCase()}`}>
          {isSettled ? '✅ 已结算' : isPending ? '⏳ 等待结算' : snap.status}
        </span>
      </div>

      {/* 各级别结构 */}
      <div className="mv-structure-grid">
        {Object.entries(snap.structure || {}).map(([level, s]) => {
          const badge = ZOUSHI_BADGE[s.zoushi_type] || ZOUSHI_BADGE['构建中']
          return (
            <div key={level} className="mv-structure-row">
              <span className="mv-level-label">{levelName(level)}</span>
              <span className="mv-zoushi-badge"
                    style={{ background: badge.bg, color: badge.color }}>
                {s.zoushi_type}
              </span>
              {snap.highlighted?.[level] && (
                <span className="mv-highlighted-tag">
                  ← {snap.highlighted[level]}
                </span>
              )}
              {s.zg && (
                <span className="mv-zg-zd">
                  ZG:{s.zg?.toFixed(2)} ZD:{s.zd?.toFixed(2)}
                </span>
              )}
            </div>
          )
        })}
      </div>

      {/* 分类卡片（30m 级别优先展示） */}
      {renderClassifications(snap)}

      {/* 结算结果 */}
      {isSettled && snap.outcome && (
        <div className="mv-outcome">
          {Object.entries(snap.outcome).map(([level, taken]) => {
            const predicted = snap.highlighted?.[level]
            const correct = predicted === taken
            return (
              <div key={level} className="mv-outcome-row">
                <span className="mv-level-label">{levelName(level)}</span>
                <span className={`mv-outcome-badge ${correct ? 'correct' : 'wrong'}`}>
                  {predicted}→{taken} {correct ? '✓' : '✗'}
                </span>
              </div>
            )
          })}
        </div>
      )}

      {/* AI 复盘 */}
      {snap.ai_review && (
        <div className="mv-ai-review">
          🧠 {snap.ai_review}
        </div>
      )}
    </div>
  )
}

function renderClassifications(snap) {
  // 优先显示30分钟级别的分类
  const level = snap.classifications?.m30 ? 'm30' :
                snap.classifications?.day ? 'day' :
                Object.keys(snap.classifications || {})[0]
  if (!level) return null

  const cls = snap.classifications[level]
  if (!cls || cls.length === 0) return null
  const highlighted = snap.highlighted?.[level]
  const outcome = snap.outcome?.[level]

  return (
    <div className="mv-cls-row">
      {cls.map(c => {
        const isHighlighted = c.id === highlighted
        const isTaken = c.id === outcome
        const isWrong = outcome && !isTaken && isHighlighted
        return (
          <div key={c.id}
               className={`mv-cls-chip ${isHighlighted ? 'current' : ''} 
                           ${isTaken ? 'taken' : ''} ${isWrong ? 'wrong' : ''}`}
               style={{ borderColor: CLS_COLORS[c.id] }}>
            <span className="mv-cls-id" style={{ color: CLS_COLORS[c.id] }}>
              {c.id}
            </span>
            <span className="mv-cls-name">{c.name}</span>
            {isTaken && <span className="mv-taken-mark">✓</span>}
            {isWrong && <span className="mv-wrong-mark">✗</span>}
          </div>
        )
      })}
    </div>
  )
}


// ═══ 分支树视图 ═══
function TreeView({ data }) {
  if (!data || data.length < 2) {
    return <div className="mv-empty">至少需要2天数据才能显示分支树</div>
  }

  // 反转为时间正序
  const sorted = [...data].reverse()

  return (
    <div className="mv-tree">
      <svg className="mv-tree-svg" viewBox={`0 0 ${sorted.length * 200} 300`}
           preserveAspectRatio="xMinYMid meet">
        {sorted.map((snap, i) => {
          const x = i * 200 + 100
          const y = 150
          const cls = snap.classifications?.m30 || snap.classifications?.day || []
          const outcome = snap.outcome?.m30 || snap.outcome?.day
          const isLast = i === sorted.length - 1

          return (
            <g key={snap.id}>
              {/* 连接线 */}
              {i > 0 && (
                <line x1={(i - 1) * 200 + 100} y1={y}
                      x2={x} y2={y}
                      stroke="#f0b90b" strokeWidth="2" opacity="0.6" />
              )}

              {/* 主节点 */}
              <circle cx={x} cy={y} r={isLast ? 10 : 8}
                      fill={isLast ? '#f0b90b' : '#2a2d3e'}
                      stroke={isLast ? '#f0b90b' : '#3a3d4e'}
                      strokeWidth="2" />

              {/* 日期 */}
              <text x={x} y={y - 20} textAnchor="middle"
                    fill="#9aa0a6" fontSize="11">
                {snap.date?.slice(5)}
              </text>

              {/* 走势类型 */}
              <text x={x} y={y + 30} textAnchor="middle"
                    fill="#e8e9ed" fontSize="10">
                {snap.structure?.m30?.zoushi_type || snap.structure?.day?.zoushi_type || ''}
              </text>

              {/* 分支 */}
              {cls.map((c, ci) => {
                const by = y - 60 + ci * 40
                const bx = x + 60
                const isTaken = c.id === outcome
                const opacity = outcome ? (isTaken ? 1 : 0.3) : 0.7
                return (
                  <g key={c.id} opacity={opacity}>
                    <line x1={x + 10} y1={y} x2={bx - 5} y2={by}
                          stroke={CLS_COLORS[c.id]} strokeWidth="1.5"
                          strokeDasharray={isTaken ? 'none' : '4,3'} />
                    <text x={bx} y={by + 4} fill={CLS_COLORS[c.id]}
                          fontSize="9">
                      {c.id}:{c.name?.slice(0, 4)}
                      {isTaken ? ' ✓' : ''}
                    </text>
                  </g>
                )
              })}
            </g>
          )
        })}
      </svg>
    </div>
  )
}


// ═══ 记分卡视图 ═══
function ScorecardView({ data }) {
  if (!data || data.total === 0) {
    return <div className="mv-empty">暂无结算数据，需要至少2天的快照</div>
  }

  return (
    <div className="mv-scorecard">
      <div className="mv-score-header">
        近 {data.total} 天分类命中统计
      </div>

      <div className="mv-score-grid">
        <ScoreBar label="日线" value={data.day_accuracy} total={data.day_total} />
        <ScoreBar label="30分钟" value={data.m30_accuracy} total={data.m30_total} />
        <ScoreBar label="5分钟" value={data.m5_accuracy} total={data.m5_total} />
      </div>

      <div className="mv-score-stats">
        <div className="mv-stat">
          <span className="mv-stat-label">连续正确</span>
          <span className="mv-stat-value streak">{data.streak}天 {data.streak >= 5 ? '🔥' : ''}</span>
        </div>
      </div>
    </div>
  )
}

function ScoreBar({ label, value, total }) {
  const color = value >= 70 ? '#00c087' : value >= 50 ? '#f0b90b' : '#f6465d'
  return (
    <div className="mv-score-row">
      <span className="mv-score-label">{label}</span>
      <div className="mv-score-bar-bg">
        <div className="mv-score-bar-fill"
             style={{ width: `${value}%`, background: color }} />
      </div>
      <span className="mv-score-pct" style={{ color }}>
        {value}% <span className="mv-score-total">({total})</span>
      </span>
    </div>
  )
}


// ── 辅助 ──
function levelName(key) {
  const map = { day: '日线', m30: '30分', m5: '5分', m60: '60分', m15: '15分' }
  return map[key] || key
}
