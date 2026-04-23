import { useState, useEffect, useRef, useCallback } from 'react'
import { API_BASE } from '../config.js'
import MultiverseJournal from './MultiverseJournal.jsx'
import './TRadar.css'
import './TRadarV2.css'

// ═══════════════════════════════════════════════════════════════
// V2 Design Tokens — A股色彩：红涨绿跌
// ═══════════════════════════════════════════════════════════════
const T = {
  BULL:    '#e74c3c',  // A股红：涨/买入/看多
  BEAR:    '#2ecc71',  // A股绿：跌/卖出/看空
  YELLOW:  '#F59E0B',  // 观望/中性
  SURFACE: '#121212',
  PANEL:   '#1E1E1E',
  BORDER:  '#333333',
  LABEL:   '#888888',
  VALUE:   '#E5E5E5',
}

const STATE_CONFIG = {
  THIRD_BUY_CONFIRMED:  { label: '三买确立', color: T.BULL,   emoji: '🟢' },
  THIRD_SELL_CONFIRMED: { label: '三卖确立', color: T.BEAR,   emoji: '🛑' },
  WAITING_FOR_PULLBACK: { label: '等待回踩', color: '#3b82f6', emoji: '🔵' },
  IN_CENTER_OSC:        { label: '中枢震荡', color: '#3b82f6', emoji: '🔵' },
  DOWNWARD_LEAVING:     { label: '向下离开', color: T.BEAR,   emoji: '🔴' },
  UPWARD_LEAVING:       { label: '向上离开', color: T.BULL,   emoji: '🟢' },
  TREND_EXTENDING:      { label: '构建中',   color: T.YELLOW, emoji: '🟡' },
  LIMBO:                { label: '中阴阶段', color: '#f97316', emoji: '🟠' },
  FAKE_BREAK:           { label: '假突破',   color: T.YELLOW, emoji: '⚠️' },
  SMALL_TO_BIG:         { label: '小转大',   color: '#a855f7', emoji: '🔮' },
  CONFIRMED_BREAK:      { label: '三买确立', color: '#06b6d4', emoji: '🚀' },
  UNKNOWN:              { label: '数据不足', color: '#666',    emoji: '⚪' },
}

const LEVEL_NAMES = {
  day: '日线', week: '周线', m60: '60分', m30: '30分', m15: '15分', m5: '5分',
}

const PLAN_LABEL = ['甲', '乙', '丙']
const PLAN_COLOR = [T.BULL, T.YELLOW, T.BEAR]

// ═══════════════════════════════════════════════════════════════
// 读盘引擎 V4.5 — 保持原有核心逻辑，忠于缠论原文
// ═══════════════════════════════════════════════════════════════
function readBoard(matrix, week, nestingData, forwardAnalysis) {
  if (!matrix || matrix.length < 2) return null
  const [l1, l2, l3] = matrix

  const describeZoushi = (item) => {
    const name = LEVEL_NAMES[item.level] || item.level
    const zt = item.zoushi_type
    if (!zt || zt.type === '数据不足') return `${name}：数据不足`
    let desc = `${name}：${zt.type}`
    if (zt.zs_count > 0) desc += `(${zt.zs_count}个中枢)`
    if (item.zd > 0 && item.zg > 0) desc += ` 中枢${item.zd.toFixed(2)}-${item.zg.toFixed(2)}`
    return desc
  }

  let weekContext = ''
  let weekBearish = false
  if (week) {
    const wp = (week.patterns || []).join(' ')
    if (week.is_near_historical_high) weekContext = '周线历史新高区域'
    else if (week.has_top_fractal) weekContext = '周线顶分型'
    else if (week.has_bottom_fractal) weekContext = '周线底分型'
    else { const wzt = week.zoushi_type?.type || ''; weekContext = `周线${wzt || '—'}` }
    if (wp.includes('顶背驰') || wp.includes('1卖')) weekBearish = true
  }

  const structure = {
    weekContext, weekBearish,
    levels: matrix.map(item => ({
      name: LEVEL_NAMES[item.level] || item.level,
      desc: describeZoushi(item),
      zoushiType: item.zoushi_type?.type || '数据不足',
      completion: item.zoushi_type?.completion || '',
    })),
  }

  let veto = null
  const l1Type = l1.zoushi_type?.type || ''
  const l1Patterns = (l1.patterns || []).join(' ')
  if (l1Type === '下跌趋势' && !l1Patterns.includes('底背驰')) {
    veto = '日线下跌趋势未完成（无底背驰），次级别反弹属于卖点机会，不参与做多'
  }
  if (weekBearish && l1Type !== '上涨趋势') {
    veto = (veto ? veto + '；' : '') + '周线顶背驰压制'
  }

  let classifications = (l2.classifications || []).map(c => ({ ...c }))
  const l2Price = l2.price || 0
  const l2Zg = l2.zg || 0; const l2Zd = l2.zd || 0
  const l2Type = l2.zoushi_type?.type || ''
  const l2Patterns = (l2.patterns || []).join(' ')

  classifications = classifications.map(c => {
    let highlighted = false
    if (l2Type === '盘整') {
      if (c.id === 'A' && l2Price > l2Zg) highlighted = true
      else if (c.id === 'B' && l2Price >= l2Zd && l2Price <= l2Zg) highlighted = true
      else if (c.id === 'C' && l2Price < l2Zd) highlighted = true
    } else if (l2Type === '上涨趋势') {
      const hasDiv = l2Patterns?.includes('背驰')
      if (c.id === 'A' && !hasDiv) highlighted = true
      else if (c.id === 'B' && hasDiv) highlighted = true
    } else if (l2Type === '下跌趋势') {
      const hasDiv = l2Patterns?.includes('背驰')
      if (c.id === 'A' && !hasDiv) highlighted = true
      else if (c.id === 'B' && hasDiv) highlighted = true
    } else if (l2Type === '构建中') { highlighted = c.id === 'A' }
    if (veto && (c.action?.includes('入场') || c.action?.includes('买'))) {
      return { ...c, highlighted, vetoed: true, vetoReason: veto }
    }
    return { ...c, highlighted }
  })

  const watchPrices = []
  if (l2Zg > 0 && l2Zd > 0) {
    watchPrices.push({ price: l2Zg, label: `${LEVEL_NAMES[l2.level]}ZG`, role: '突破/回踩分界' })
    watchPrices.push({ price: l2Zd, label: `${LEVEL_NAMES[l2.level]}ZD`, role: '支撑/破位分界' })
  }
  if (l1.zg > 0 && l1.zd > 0 && l1.level !== l2.level) {
    watchPrices.push({ price: l1.zg, label: `${LEVEL_NAMES[l1.level]}ZG`, role: '大级别压力' })
    watchPrices.push({ price: l1.zd, label: `${LEVEL_NAMES[l1.level]}ZD`, role: '大级别支撑' })
  }
  if (l2.ex_support > 0 && !watchPrices.find(w => Math.abs(w.price - l2.ex_support) < 0.01)) {
    watchPrices.push({ price: l2.ex_support, label: '近期支撑', role: '短期极低点' })
  }
  const seen = new Set()
  const dedupedPrices = []
  for (const wp of watchPrices) {
    const key = wp.price.toFixed(2)
    if (!seen.has(key)) { seen.add(key); dedupedPrices.push(wp) }
  }
  dedupedPrices.sort((a, b) => b.price - a.price)

  return {
    structure, classifications,
    watchPrices: dedupedPrices, veto,
    intervalNesting: nestingData || null,
    forwardAnalysis: forwardAnalysis || null,
  }
}

// ═══════════════════════════════════════════════════════════════
// 子组件
// ═══════════════════════════════════════════════════════════════

// ── Skeleton ──
function Skeleton({ width = '100%', height = 14, style = {} }) {
  return <div className="tv2-skeleton" style={{ width, height, ...style }} />
}

// ── Toast ──
function Toast({ message, onDismiss }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4500)
    return () => clearTimeout(t)
  }, [onDismiss])
  return (
    <div className="tv2-toast">
      <span className="tv2-toast-icon">⚡</span>
      <span>{message}</span>
      <button className="tv2-toast-close" onClick={onDismiss}>×</button>
    </div>
  )
}

// ── Key-Value Row ──
function KVRow({ label, value, valueColor, mono, highlight }) {
  return (
    <div className="tv2-kv-row" style={highlight ? { background: `${highlight}12`, borderRadius: 3, padding: '2px 4px' } : {}}>
      <span className="tv2-kv-label">{label}</span>
      <span className="tv2-kv-value" style={{
        color: valueColor || T.VALUE,
        fontFamily: mono ? '"Roboto Mono","Courier New",monospace' : 'inherit',
        fontWeight: mono ? 600 : 400,
      }}>{value}</span>
    </div>
  )
}

// ── Action Card (甲/乙/丙) — 与 AI 推演预案统一风格 ──
function ActionCard({ fc, index, isHighlighted, isVetoed, isEmpty }) {
  const color = PLAN_COLOR[index] ?? T.VALUE
  const label = PLAN_LABEL[index] ?? fc?.id ?? '—'

  if (isEmpty) {
    return (
      <div className="classification-card" style={{ opacity: 0.25 }}>
        <div className="cls-header">
          <span className="cls-id">{label}</span>
          <span className="cls-name" style={{ color: '#3a3a3a' }}>暂无预案</span>
        </div>
      </div>
    )
  }

  const isDown = PLAN_COLOR[index] === T.BEAR
  const isUp   = PLAN_COLOR[index] === T.BULL
  const actionBg = isDown
    ? 'rgba(46,204,113,0.10)'
    : isUp
    ? 'rgba(231,76,60,0.08)'
    : 'rgba(245,158,11,0.08)'
  const actionTextColor = isDown ? '#6ee7b7' : isUp ? '#fca5a5' : '#fcd34d'

  return (
    <div
      className={`classification-card ${isHighlighted ? 'highlighted' : ''} ${isVetoed ? 'vetoed' : ''}`}
      style={{ borderLeftColor: color, ...(isHighlighted ? { background: `${color}10` } : {}) }}
    >
      <div className="cls-header">
        <span
          className="cls-id"
          style={isHighlighted ? { background: `${color}25`, color } : { color }}
        >
          {label}
        </span>
        {isHighlighted && <span className="cls-current">当前</span>}
        {isVetoed && <span className="cls-current" style={{ color: T.BEAR }}>大级别否决</span>}
      </div>

      {/* 触发条件 */}
      {fc.condition && (
        <div className="cls-condition">
          <span className="cls-label">触发</span>
          <span className="cls-text">{fc.condition}</span>
        </div>
      )}

      {/* 推演逻辑 */}
      {(fc.meaning || fc.deduction) && (
        <div className="cls-condition">
          <span className="cls-label">推演</span>
          <span className="cls-text">{fc.meaning || fc.deduction}</span>
        </div>
      )}

      {/* 指令行 */}
      {fc.action && (
        <div className="cls-action-row" style={{ background: actionBg, padding: '6px 8px', borderRadius: 4, marginTop: 4 }}>
          <div className="cls-action">
            <span className="cls-label" style={{ color: actionTextColor }}>指令</span>
            <span className="cls-text" style={{ color: actionTextColor, fontWeight: 700 }}>{fc.action}</span>
          </div>
          {(fc.stop_loss || fc.stopLoss) && (
            <span className="cls-sl-price">止损 {fc.stop_loss ?? fc.stopLoss}</span>
          )}
        </div>
      )}

      {isVetoed && fc.vetoReason && (
        <div className="tv2-card-veto">⛔ {fc.vetoReason}</div>
      )}
    </div>
  )
}

// ── Entry Checklist (空仓视角) — 进度条 + 分级颜色版 ──
function EntryChecklist({ checklist }) {
  if (!checklist) return null
  const items = [
    { key: 'day_buy_node',         label: '日线买点节点',   value: checklist.day_buy_node },
    { key: 'day_not_top_diverge',  label: '日线无顶背驰',   value: checklist.day_not_top_diverge },
    { key: 'thirty_min_structure', label: '30分中枢已形成', value: checklist.thirty_min_structure },
    { key: 'thirty_min_buy_node',  label: '30分买点节点',   value: checklist.thirty_min_buy_node },
    { key: 'five_min_entry_bar',   label: '5分入场K线',     value: checklist.five_min_entry_bar },
  ]
  const passCount = items.filter(i => i.value).length
  const pct = (passCount / items.length) * 100
  const barColor = passCount === 5 ? T.BULL : passCount >= 3 ? T.YELLOW : '#555'

  return (
    <div className="tv2-panel">
      <div className="tv2-panel-title">
        入场五条件
        <span className="tv2-panel-badge" style={{ color: passCount === 5 ? T.BULL : passCount >= 3 ? T.YELLOW : '#666' }}>
          {passCount}/5
        </span>
      </div>
      {/* 进度条 */}
      <div className="tv2-checklist-progress-wrap">
        <div className="tv2-checklist-progress-bar" style={{ width: `${pct}%`, background: barColor }} />
      </div>
      {/* 条件列表 */}
      {items.map(item => (
        <div key={item.key} className={`tv2-checklist-item ${item.value ? 'tv2-checklist-item--pass' : ''}`}>
          <span style={{
            color: item.value ? T.VALUE : '#888',
            fontSize: 11,
            fontFamily: '-apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif',
          }}>{item.label}</span>
          <span
            style={{ color: item.value ? T.BULL : '#555', fontWeight: 700, fontSize: 13 }}
            aria-label={item.value ? '满足' : '未满足'}
          >
            {item.value ? '✓' : '—'}
          </span>
        </div>
      ))}
      {checklist.all_passed && (
        <div className="tv2-all-passed">✅ 五条件全满足，关注入场时机</div>
      )}
    </div>
  )
}

// ── Status Bar — 摘要行（替换 position-bar）──
function StatusBar({ board, isHolding, holdingStatus, holdingCost }) {
  const l2 = board?.structure?.levels?.[1]  // 30分
  const zg = board?.watchPrices?.find(w => w.label?.includes('ZG') && w.label?.includes('30'))?.price
  const zd = board?.watchPrices?.find(w => w.label?.includes('ZD') && w.label?.includes('30'))?.price

  // 持仓模式
  if (isHolding && holdingStatus && holdingCost > 0) {
    const pnlPct = holdingStatus.locked_profit_pct
    const pnlColor = pnlPct >= 0 ? T.BULL : T.BEAR
    return (
      <div className="tv2-status-bar">
        <div className="tv2-status-bar-left">
          <span className="tv2-status-mode tv2-status-mode--holding">持仓</span>
          <span className="tv2-status-stage">{holdingStatus.label || `Stage ${holdingStatus.stage}`}</span>
        </div>
        <div className="tv2-status-bar-right">
          {holdingStatus.stair_stop_price > 0 && (
            <span className="tv2-status-price">
              <span className="tv2-status-price-label">台阶止损</span>
              <span className="tv2-status-price-value" style={{ color: T.BEAR }}>
                {holdingStatus.stair_stop_price.toFixed(2)}
              </span>
            </span>
          )}
          <span className="tv2-status-price">
            <span className="tv2-status-price-label">浮盈</span>
            <span className="tv2-status-price-value" style={{ color: pnlColor }}>
              {pnlPct >= 0 ? '+' : ''}{pnlPct?.toFixed(1)}%
            </span>
          </span>
        </div>
      </div>
    )
  }

  // 空仓模式
  return (
    <div className="tv2-status-bar">
      <div className="tv2-status-bar-left">
        <span className="tv2-status-mode tv2-status-mode--empty">空仓</span>
        {board?.forwardAnalysis?.current_position && (
          <span className="tv2-status-desc" title={board.forwardAnalysis.current_position}>
            {board.forwardAnalysis.current_position}
          </span>
        )}
      </div>
      <div className="tv2-status-bar-right">
        {zg > 0 && (
          <span className="tv2-status-price">
            <span className="tv2-status-price-label">ZG</span>
            <span className="tv2-status-price-value">{zg.toFixed(2)}</span>
          </span>
        )}
        {zd > 0 && (
          <span className="tv2-status-price">
            <span className="tv2-status-price-label">ZD</span>
            <span className="tv2-status-price-value">{zd.toFixed(2)}</span>
          </span>
        )}
      </div>
    </div>
  )
}

// ── Strategy Badge — 战法标签（顶栏） ──
// 颜色规范：战法一=#10B981（绿），战法二=#F59E0B（黄），观察中=#888
const STRATEGY_BADGE_CONFIG = {
  '战法一': { color: '#10B981', icon: '↗', label: '战法一·共振' },
  '战法二': { color: '#F59E0B', icon: '⬆', label: '战法二·突破' },
  '双战法': { color: '#10B981', icon: '✦', label: '双战法' },
  '观察中': { color: '#888888', icon: '○', label: '观察中' },
}

function StrategyBadge({ sc }) {
  if (!sc) return null
  const stype = sc.strategy_type || '观察中'
  const cfg   = STRATEGY_BADGE_CONFIG[stype] || STRATEGY_BADGE_CONFIG['观察中']
  return (
    <span
      className="tv2-strategy-badge"
      style={{ color: cfg.color, borderColor: cfg.color + '50', background: cfg.color + '18' }}
      title={`战法识别：${stype}`}
    >
      <span style={{ fontSize: 9, marginRight: 3 }}>{cfg.icon}</span>
      {cfg.label}
    </span>
  )
}

// ── Strategy Entry Panel — 空仓模式：战法入场五条件 ──
function StrategyEntryPanel({ sc, legacyChecklist, rewardRatio }) {
  if (!sc) return <EntryChecklist checklist={legacyChecklist} />

  const stype  = sc.strategy_type || '观察中'
  const cfg    = STRATEGY_BADGE_CONFIG[stype] || STRATEGY_BADGE_CONFIG['观察中']
  // 优先用对应战法的 checklist，fallback 到旧格式
  const s1 = sc.strategy1 || {}
  const s2 = sc.strategy2 || {}
  const isS1Active = stype === '战法一' || stype === '双战法'
  const isS2Active = stype === '战法二' || stype === '双战法'

  const renderItems = (items) => items.map(({ key, label, value }) => (
    <div key={key} className={`tv2-checklist-item ${value ? 'tv2-checklist-item--pass' : ''}`}>
      <span style={{ color: value ? T.VALUE : '#888', fontSize: 11 }}>{label}</span>
      <span style={{ color: value ? T.BULL : '#555', fontWeight: 700, fontSize: 13 }}>{value ? '✓' : '—'}</span>
    </div>
  ))

  return (
    <div className="tv2-panel">
      <div className="tv2-panel-title">
        战法扫描
        <span className="tv2-panel-badge" style={{ color: cfg.color }}>
          {cfg.icon} {stype}
        </span>
      </div>

      {/* 不符合任何战法 */}
      {stype === '观察中' && (
        <div className="tv2-observe-note">
          <span style={{ color: '#888', fontSize: 12 }}>
            当前不建议入场。结构未满足战法一或战法二的触发条件。
          </span>
        </div>
      )}

      {/* 战法一检测 */}
      {(isS1Active || stype === '观察中') && s1.conditions && (
        <>
          {stype !== '观察中' && (
            <div className="tv2-strategy-sub-title" style={{ color: '#10B981' }}>
              战法一：三级别共振
            </div>
          )}
          {renderItems([
            { key: 's1_day', label: '日线二买结构', value: s1.conditions?.day_2buy_confirmed },
            { key: 's1_m30', label: '30分二买+共振', value: s1.conditions?.m30_2buy_resonance },
            { key: 's1_m5',  label: '5分底背驰', value: s1.conditions?.m5_bottom_beichi },
            { key: 's1_win', label: '窗口有效(<10%)', value: s1.conditions?.entry_window_valid },
            { key: 's1_wk',  label: '周线无压制', value: s1.conditions?.week_no_suppression },
          ])}
        </>
      )}

      {/* 战法二检测 */}
      {(isS2Active || stype === '观察中') && s2.conditions && (
        <>
          <div className="tv2-strategy-sub-title" style={{ color: '#F59E0B', marginTop: isS1Active && s1.conditions ? 10 : 0 }}>
            战法二：中枢上沿突破
          </div>
          {renderItems([
            { key: 's2_zs',   label: '日线明确中枢(≥3震荡)', value: s2.conditions?.day_zhongshu_exists },
            { key: 's2_near', label: '价格在中枢上沿附近', value: s2.conditions?.price_near_zg },
            { key: 's2_m30',  label: '30分中枢在ZG附近', value: s2.conditions?.m30_zhongshu_near_zg },
            { key: 's2_m5',   label: '5分三类买点', value: s2.conditions?.m5_third_buy },
            { key: 's2_wk',   label: '周线无压制', value: s2.conditions?.week_no_suppression },
          ])}
        </>
      )}

      {/* 如果两个战法的详细数据都没有，fallback 到旧格式 */}
      {!s1.conditions && !s2.conditions && (
        <EntryChecklist checklist={legacyChecklist} />
      )}

      {/* ── 赔率门控（第六条件）── */}
      {rewardRatio && stype !== '观察中' && (
        <div
          className={`tv2-checklist-item ${rewardRatio.ok ? 'tv2-checklist-item--pass' : ''}`}
          style={{ marginTop: 8, borderTop: '1px solid #222', paddingTop: 6 }}
        >
          <span style={{ color: rewardRatio.ok ? T.VALUE : '#888', fontSize: 11 }}>
            {rewardRatio.is_open ? '赔率（开放目标）' : `赔率（${rewardRatio.ratio ? '1:' + rewardRatio.ratio : '—'}）`}
          </span>
          <span style={{ color: rewardRatio.ok ? T.BULL : T.YELLOW, fontWeight: 700, fontSize: 13 }}>
            {rewardRatio.ok ? '✓' : '✗'}
          </span>
        </div>
      )}
      {rewardRatio && rewardRatio.verdict && stype !== '观察中' && (
        <div style={{ color: rewardRatio.ok ? '#888' : T.YELLOW, fontSize: 11, marginTop: 2, paddingLeft: 2 }}>
          {rewardRatio.verdict}
        </div>
      )}
    </div>
  )
}

// ── ATR Check Row — 止损 ATR 合理性 ──
function AtrCheckRow({ atrCheck }) {
  if (!atrCheck || !atrCheck.atr) return null
  const valid   = atrCheck.valid
  const color   = valid ? T.BULL : T.YELLOW
  return (
    <div className="tv2-panel tv2-panel--compact" style={{ padding: '8px 12px' }}>
      <div className="tv2-panel-title" style={{ marginBottom: 4 }}>
        止损合理性
        <span className="tv2-panel-badge" style={{ color }}>
          {atrCheck.atr_multiple?.toFixed(1)}×ATR {valid ? '✓' : '⚠'}
        </span>
      </div>
      {atrCheck.note && (
        <div style={{ color: valid ? '#888' : T.YELLOW, fontSize: 11 }}>{atrCheck.note}</div>
      )}
    </div>
  )
}

// ── StructureStatusCard — 持仓模式：结构完整/失效状态卡 ──
function StructureStatusCard({ holdingStatus, holdingStageV2 }) {
  // 读取 stage_0_extended 字段（来自 _validate_entry_thesis）
  const ext = holdingStatus?.stage_0_extended || {}
  const structStatus    = ext.structure_status || ''
  const holdingRationale = ext.holding_rationale || ''
  const m5Intact        = ext.m5_zhongshu_intact
  const m5Zg            = ext.m5_entry_zg || 0

  // 读取 holding_stage_v2 的 action
  const stageAction  = holdingStageV2?.action || ''
  const stageLabel   = holdingStageV2?.label  || ''
  const trailingStop = holdingStageV2?.trailing_stop || holdingStatus?.stair_stop_price || 0

  const isIntact = m5Intact === true || structStatus === '结构完整'
  const isFailed = m5Intact === false || structStatus === '结构失效'

  if (!structStatus && !holdingStageV2) return null

  return (
    <div className={`tv2-panel ${isFailed ? 'tv2-panel--danger' : ''}`}
      style={{ borderLeft: `3px solid ${isFailed ? T.BEAR : T.BULL}` }}>
      <div className="tv2-panel-title">
        持仓结构判断
        {structStatus && (
          <span className="tv2-panel-badge" style={{ color: isFailed ? T.BEAR : T.BULL }}>
            {isFailed ? '⚠ 结构失效' : '✓ 结构完整'}
          </span>
        )}
      </div>

      {/* 结构失效 → 强醒目警示 */}
      {isFailed && (
        <div className="tv2-action-suggest tv2-action-suggest--danger" role="alert">
          🔴 5分入场中枢已被跌破，入场假设失效。建议出场，不再等待。
        </div>
      )}

      {/* 结构完整，正在等待 */}
      {isIntact && !isFailed && (
        <div className="tv2-action-suggest tv2-action-suggest--intact" role="status">
          ✅ 结构仍然有效，价格在消化，继续持有。时间不是止损理由。
        </div>
      )}

      {/* 止损价格信息 */}
      <div style={{ marginTop: 8 }}>
        {m5Zg > 0 && (
          <KVRow
            label="原始止损（5分中枢ZG）"
            value={m5Zg.toFixed(2)}
            valueColor={T.BEAR}
            mono
            highlight={T.BEAR}
          />
        )}
        {trailingStop > 0 && (
          <KVRow
            label="台阶止损"
            value={trailingStop.toFixed(2)}
            valueColor={T.BEAR}
            mono
          />
        )}
      </div>

      {/* 当前阶段行动建议 */}
      {stageAction && (
        <div style={{ marginTop: 8, fontSize: 12, color: '#9ca3af' }}>
          {stageLabel && <span style={{ color: T.YELLOW, marginRight: 4 }}>[{stageLabel}]</span>}
          {stageAction}
        </div>
      )}

      {/* 持仓逻辑说明 */}
      {holdingRationale && !isFailed && (
        <div style={{ marginTop: 6, fontSize: 11, color: '#666' }}>{holdingRationale}</div>
      )}
    </div>
  )
}

// ── Stage Indicator — 6圆点进度条 ──
const STAGE_DOTS = [
  { n: 0, label: '验证',  color: '#F59E0B' },
  { n: 1, label: '确认',  color: '#F59E0B' },
  { n: 2, label: '保本',  color: '#10B981' },
  { n: 3, label: '护利',  color: '#10B981' },
  { n: 4, label: '预警',  color: '#F59E0B' },
  { n: 5, label: '终结',  color: '#EF4444' },
]

function StageIndicator({ stage }) {
  // stage 为数字 0-5；非数字（如 "empty"）时不渲染
  if (typeof stage !== 'number') return null
  return (
    <div className="tv2-stage-indicator" role="progressbar" aria-label={`持仓阶段 ${stage}`}>
      {STAGE_DOTS.map(dot => {
        const isActive = dot.n === stage
        return (
          <div key={dot.n} className={`tv2-stage-dot${isActive ? ' tv2-stage-dot--active' : ''}`}>
            <div
              className="tv2-stage-dot-circle"
              style={isActive ? { background: dot.color, borderColor: dot.color } : {}}
            />
            <span
              className="tv2-stage-dot-label"
              style={isActive ? { color: dot.color } : {}}
            >
              {dot.label}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ── Holding Panel (持仓视角) ──
function HoldingPanel({ status, holdingStatus }) {
  if (!status || status.stage === 'empty') return null

  const stageNum   = typeof status.stage === 'number' ? status.stage : -1
  // Stage 5（趋势终结）→ 面板边框变红警示
  const isStopLoss = stageNum === 5
  // Stage 4（减速预警）→ 黄色警示
  const isWarn     = stageNum === 4

  // 从 holdingStatus（_compute_holding_status 输出）读取战法相关字段
  const strategyType      = holdingStatus?.strategy_type || status?.strategy_type || '战法一'
  const divergeType       = holdingStatus?.top_diverge_30min_type || ''  // 中继型 / 转折型
  const relayNote         = holdingStatus?.m30_relay_note || ''
  const targetOpen        = holdingStatus?.target_open || false
  const isS2              = strategyType === '战法二'

  const badgeColor = isStopLoss ? T.BEAR : isWarn ? T.YELLOW : T.BULL

  return (
    <div className={`tv2-panel ${isStopLoss ? 'tv2-panel--danger' : ''}`}>
      <div className="tv2-panel-title">
        持仓状态
        {status.label && (
          <span className="tv2-panel-badge" style={{ color: badgeColor }}>
            {status.label}
          </span>
        )}
      </div>

      {/* 六阶段进度条 */}
      <StageIndicator stage={stageNum} />

      {/* Stage 0：走势验证期状态 */}
      {stageNum === 0 && status.validation && (
        <div className="tv2-validation-status">
          <span className="tv2-validation-status-label">
            {status.validation.status === '验证通过' ? '✅ 验证通过' :
             status.validation.status === '预案失效' ? '❌ 预案失效' :
             status.validation.status === '时间失效' ? '⏰ 时间失效' :
             `⏳ ${status.validation.status}`}
          </span>
          <span className="tv2-validation-bars">
            {status.validation.bars_remaining > 0
              ? `剩余 ${status.validation.bars_remaining} 根K线`
              : '验证窗口已关闭'}
          </span>
        </div>
      )}

      <KVRow
        label="台阶止损"
        value={status.stair_stop_price > 0 ? status.stair_stop_price.toFixed(2) : '—'}
        valueColor={T.BEAR}
        mono
      />
      <KVRow
        label="浮动盈亏"
        value={`${status.locked_profit_pct >= 0 ? '+' : ''}${status.locked_profit_pct.toFixed(2)}%`}
        valueColor={status.locked_profit_pct >= 0 ? T.BULL : T.BEAR}
      />

      {/* 30分顶背驰警示（Stage 1~3 显示） */}
      {status.top_diverge_30min && stageNum < 4 && (
        <div className="tv2-diverge-warn" aria-label="警示">
          <span>[警示]</span>
          <span>30分顶背驰出现，关注出局信号</span>
        </div>
      )}

      {/* 战法二：中继型背驰说明（Stage 1~3，不升阶，继续持有）*/}
      {relayNote && stageNum < 4 && (
        <div className="tv2-diverge-warn" style={{ color: T.YELLOW, borderColor: '#555' }} aria-label="中继说明">
          <span>[中继]</span>
          <span>{relayNote}</span>
        </div>
      )}

      {/* Stage 4：战法差异化减仓建议 */}
      {stageNum === 4 && (() => {
        // 战法二 + 转折型：减半仓，剩余等待日线信号
        if (isS2) {
          return (
            <div className="tv2-action-suggest tv2-action-suggest--warn" role="alert">
              ⚠️ 30分转折背驰，减仓50%，剩余持仓等待日线顶背驰信号（手动操作后录入）
            </div>
          )
        }
        // 战法一：任意30分顶背驰即减仓
        return (
          <div className="tv2-action-suggest tv2-action-suggest--warn" role="alert">
            ⚠️ 30分顶背驰，减仓50%锁定利润（手动操作后录入）
          </div>
        )
      })()}

      {/* Stage 5：清仓建议（红色） */}
      {stageNum === 5 && (
        <div className="tv2-action-suggest tv2-action-suggest--danger" role="alert">
          🔴 趋势终结信号，建议全面清仓（日线顶背驰/跌破台阶止损）
        </div>
      )}

      {/* 目标价：战法二开放目标 / 战法一结构前高 */}
      {targetOpen ? (
        <KVRow
          label="目标价"
          value="趋势进行中，无固定目标价"
          valueColor="#888"
        />
      ) : !status.target_is_placeholder && (
        <>
          {status.target_1_reached && (
            <KVRow label="目标价1" value={`${status.target_price_1.toFixed(2)}  ✓到达`} valueColor={T.BULL} mono highlight={T.BULL} />
          )}
          {status.target_2_reached && (
            <KVRow label="目标价2" value={`${status.target_price_2.toFixed(2)}  ✓到达`} valueColor={T.BULL} mono highlight={T.BULL} />
          )}
          {!status.target_1_reached && status.target_price_1 > 0 && (
            <KVRow label="目标价1" value={status.target_price_1.toFixed(2)} valueColor={T.LABEL} mono />
          )}
        </>
      )}
    </div>
  )
}


// ── Structure Panel — 紧凑单行版 ──
function StructurePanel({ board }) {
  if (!board) return null
  const levels = board.structure?.levels ?? []
  const prices = board.watchPrices?.slice(0, 4) ?? []

  const zoushiColor = (type) => {
    if (type === '上涨趋势') return T.BULL
    if (type === '下跌趋势') return T.BEAR
    return T.YELLOW
  }

  return (
    <div className="tv2-panel tv2-panel--compact">
      <div className="tv2-panel-title">结构快照</div>

      {/* 走势单行 */}
      {levels.length > 0 && (
        <div className="tv2-struct-row">
          {levels.map((lv, i) => (
            <span key={i} className="tv2-struct-chip">
              <span className="tv2-struct-chip-name">{lv.name}</span>
              <span className="tv2-struct-chip-type" style={{ color: zoushiColor(lv.zoushiType) }}>
                {lv.zoushiType || '—'}
              </span>
            </span>
          ))}
        </div>
      )}

      {/* 价格单行 */}
      {prices.length > 0 && (
        <div className="tv2-struct-prices">
          {prices.map((wp, i) => (
            <span key={i} className="tv2-struct-price-item">
              <span className="tv2-struct-price-label">{wp.label}</span>
              <span className="tv2-struct-price-val">{wp.price.toFixed(2)}</span>
            </span>
          ))}
        </div>
      )}

      {board.veto && (
        <div className="tv2-veto-row">⛔ {board.veto}</div>
      )}
    </div>
  )
}


// ── Matrix Detail (折叠) ──
function MatrixDetail({ matrix }) {
  const [open, setOpen] = useState(false)
  if (!matrix?.length) return null
  return (
    <div className="tv2-matrix-wrap">
      <button className="tv2-matrix-toggle" onClick={() => setOpen(!open)}>
        {open ? '▾' : '▸'} 详细矩阵
      </button>
      {open && (
        <div className="tv2-matrix-rows">
          {matrix.map(item => {
            const cfg = STATE_CONFIG[item.state] || STATE_CONFIG.UNKNOWN
            return (
              <div key={item.level} className="tradar-level">
                <span className="level-dot">{cfg.emoji}</span>
                <span className="level-name">{LEVEL_NAMES[item.level] || item.level}</span>
                <span className="level-state" style={{ color: cfg.color }}>{cfg.label}</span>
                {item.zd > 0 && (
                  <span className="level-zs mono">ZG:{item.zg?.toFixed(2)} ZD:{item.zd?.toFixed(2)}</span>
                )}
                {item.patterns?.length > 0 && (
                  <div className="level-patterns">
                    {item.patterns.map((pt, i) => (
                      <span key={i} className={`pattern-tag ${pt.includes('危') || pt.includes('背驰') ? 'warn' : ''}`}>{pt}</span>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Stale Warning Banner ──
function StaleWarning({ freshness }) {
  if (!freshness?.is_stale) return null
  const ts = freshness.last_updated_ts
  const timeStr = ts > 0 ? new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '—'
  return (
    <div className="tv2-stale-banner" role="alert" aria-label="数据过期警告">
      ⚠️ 实时数据获取失败，展示本地缓存数据（最后更新 {timeStr}）
    </div>
  )
}

// ── Skeleton Screen ──
function SkeletonScreen() {
  return (
    <div className="tv2-skeleton-wrap">
      {/* Action cards skeleton */}
      <div className="tv2-cards-row">
        {[0, 1, 2].map(i => (
          <div key={i} className="tv2-skeleton-card">
            <Skeleton height={10} width="30%" />
            <Skeleton height={16} width="70%" style={{ marginTop: 8 }} />
            <Skeleton height={10} width="90%" style={{ marginTop: 6 }} />
          </div>
        ))}
      </div>
      {/* Panel skeleton */}
      <div className="tv2-panel" style={{ gap: 10 }}>
        {[0, 1, 2, 3].map(i => (
          <div key={i} style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Skeleton height={11} width="40%" />
            <Skeleton height={11} width="30%" />
          </div>
        ))}
      </div>
    </div>
  )
}

// ── AI Report Section ──
/**
 * 从触发条件文本里提取价格和方向
 * 返回 { price: number, type: 'above'|'below' } 或 null
 *
 * 示例：
 *   "守住ZG=125.73回踩低点"  → { price: 125.73, type: 'above' }
 *   "触发跌破ZG=125.73"      → { price: 125.73, type: 'below' }
 */
function parseTrigger(text) {
  if (!text) return null
  // 空格/中文标点容错，先判断方向关键词
  const isBear = /跌破|破位|下破|跌穿|触发跌|突破下/.test(text)
  const isBull = /守住|站上|突破(?!下)|上破|站稳|回踩.*守|守.*回/.test(text)
  // 提取第一个出现的数字（含小数）
  const numMatch = text.match(/(\d{2,}\.?\d*)/)
  if (!numMatch) return null
  const price = parseFloat(numMatch[1])
  if (isNaN(price) || price <= 0) return null
  if (isBear) return { price, type: 'below' }
  if (isBull) return { price, type: 'above' }
  return null
}

function AISection({ aiReport, deducing, onDeduce, onCollapse, activeHistoryId, historyTimestamp, onBackToCurrent, currentPrice }) {
  return (
    <div className="tv2-ai-wrap">
      {/* loading 态：footer 按钮已经触发了 API，这里只显示进度 */}
      {!aiReport && deducing && (
        <div className="tradar-ai-actions">
          <button className="tradar-ai-btn" disabled>🧠 推演中...</button>
        </div>
      )}
      {/* 兜底：API 失败后 aiReport 为 null 且 deducing=false，允许重试 */}
      {!aiReport && !deducing && (
        <div className="tradar-ai-actions">
          <button className="tradar-ai-btn" onClick={onDeduce}>🔄 重新推演</button>
        </div>
      )}
      {aiReport && (
        <div className="thinking-report">
          {activeHistoryId && (
            <div className="report-history-banner">
              <span>⚠️ 历史快照 ({historyTimestamp})</span>
              <button onClick={onBackToCurrent}>返回实时</button>
            </div>
          )}
          {aiReport.diagnosis && (
            <div className="thinking-position" style={{ marginTop: 0 }}>🎯 定调: {aiReport.diagnosis}</div>
          )}
          {aiReport.account_status?.is_holding && (
            <div style={{ background: '#1e293b', padding: '10px', borderRadius: 6, border: '1px solid #334155', fontSize: 13, color: '#cbd5e1', display: 'flex', justifyContent: 'space-between' }}>
              <span>成本: <strong style={{ color: '#fff' }}>{aiReport.account_status.cost}</strong></span>
              <span style={{ color: aiReport.account_status.pnl_percentage >= 0 ? T.BULL : T.BEAR, fontWeight: 700 }}>
                盈亏: {aiReport.account_status.pnl_percentage}%
              </span>
            </div>
          )}
          {aiReport.pre_plans?.length > 0 && (
            <div className="ai-classifications">
              <div className="classifications-title">⚔️ 机械化战斗预案</div>
              {aiReport.pre_plans.map((p, idx) => {
                // ── 判断当前价是否已触发该预案 ──
                const parsed   = parseTrigger(p.trigger)
                const triggered = parsed && currentPrice > 0
                  ? (parsed.type === 'below' ? currentPrice < parsed.price : currentPrice >= parsed.price)
                  : false
                const accentColor = p.color === '🔴' ? T.BEAR : p.color === '🟢' ? T.BULL : T.YELLOW
                return (
                <div key={idx} className={`classification-card ${triggered ? 'highlighted' : ''}`}
                  style={{
                    borderLeft: `4px solid ${accentColor}`,
                    paddingBottom: 8,
                    ...(triggered ? { background: `${accentColor}12` } : {}),
                  }}>
                  <div className="cls-header" style={{ marginBottom: 8 }}>
                    <span className="cls-name" style={{ color: '#f8fafc', fontWeight: 700, fontSize: 11 }}>{p.color} {p.plan_name}</span>
                    {triggered && <span className="cls-current">当前</span>}
                  </div>
                  <div className="cls-condition" style={{ marginBottom: 4 }}>
                    <span className="cls-label">触发</span>
                    <span className="cls-text">{p.trigger}</span>
                  </div>
                  <div className="cls-condition" style={{ marginBottom: 8 }}>
                    <span className="cls-label">推演</span>
                    <span className="cls-text" style={{ color: '#94a3b8' }}>{p.deduction}</span>
                  </div>
                  <div className="cls-action-row" style={{ background: p.color === '🔴' ? 'rgba(46,204,113,0.10)' : p.color === '🟢' ? 'rgba(231,76,60,0.08)' : 'rgba(245,158,11,0.08)', padding: 8, borderRadius: 4 }}>
                    <div className="cls-action">
                      <span className="cls-label" style={{ color: p.color === '🔴' ? '#5edb8a' : p.color === '🟢' ? '#f08070' : '#F59E0B' }}>指令</span>
                      <span className="cls-text" style={{ color: p.color === '🔴' ? '#6ee7b7' : p.color === '🟢' ? '#fca5a5' : '#fcd34d', fontWeight: 700 }}>{p.machine_action}</span>
                    </div>
                  </div>
                </div>
              )})}
            </div>
          )}
          {aiReport.core_defense && (
            <div className="red-line-banner" style={{ marginTop: 12 }}>🛡️ 核心防线: {aiReport.core_defense}</div>
          )}
          {aiReport.market_context_verdict && (
            <div className="market-verdict-banner" style={{ marginTop: 12 }}>
              <span className="market-verdict-icon">💹</span>
              <span className="market-verdict-text">{aiReport.market_context_verdict}</span>
            </div>
          )}
          <div className="tradar-v2-actions-footer">
            <button className="tradar-ai-refresh" onClick={onCollapse}>收起分析</button>
          </div>
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════════
// 主组件 TRadarV2
// ═══════════════════════════════════════════════════════════════
const POLL_INTERVAL_MS = 30_000

export default function TRadarV2({ symbol }) {
  const [mode, setMode]         = useState('A')
  const [collapsed, setCollapsed] = useState(false)
  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(false)
  const [holding, setHolding]   = useState(null)

  // V2 新字段
  const [entryChecklist, setEntryChecklist]           = useState(null)
  const [holdingStatus, setHoldingStatus]             = useState(null)
  const [holdingStageV2, setHoldingStageV2]           = useState(null)   // Task #9 六阶段状态机
  const [strategyClassification, setStrategyClassification] = useState(null) // Task #5 战法分类
  const [stopAtrCheck, setStopAtrCheck]               = useState(null)   // Task #6 ATR校验
  const [targets, setTargets]                         = useState(null)   // Task #7 目标价
  const [rewardRatio, setRewardRatio]                 = useState(null)   // Task #23 赔率门控
  const [dataFreshness, setDataFreshness]             = useState(null)

  // 持仓状态切换提示
  const [pendingRefresh, setPendingRefresh] = useState(false)
  const [toast, setToast]                   = useState(null)
  const prevStageRef                        = useRef(null)

  // AI 推演
  const [deducing, setDeducing] = useState(false)
  const [aiReport, setAiReport] = useState(null)
  const [showAI, setShowAI]     = useState(false)

  // 历史
  const [showHistory, setShowHistory]       = useState(false)
  const [historyList, setHistoryList]       = useState([])
  const [activeHistoryId, setActiveHistoryId] = useState(null)
  const [historyTimestamp, setHistoryTimestamp] = useState(null)

  // 轮询 ref
  const pollTimerRef = useRef(null)
  const holdingRef   = useRef(null)   // 始终持有最新 holding，供轮询回调读取

  // ── 数据获取（V2 API）──
  const fetchV2 = useCallback(async (holdingData, silent = false) => {
    if (!symbol) return
    if (!silent) setLoading(true)
    // 优先用传入的 holdingData，其次读 ref（轮询场景），最后 fallback 到 state
    const h = holdingData !== undefined ? holdingData : holdingRef.current
    const params = (h && h.cost > 0 && h.qty > 0) ? `?cost=${h.cost}&qty=${h.qty}` : ''
    try {
      const res  = await fetch(`${API_BASE}/chan/matrix/v2/${symbol}${params}`)
      const json = await res.json()
      if (json.status === 'success') {
        const d = json.data

        // 检测持仓阶段是否发生变化（切换空仓↔持仓，Task #19）
        const newStage = d.holding_status?.stage
        if (prevStageRef.current !== null && prevStageRef.current !== newStage) {
          const wasEmpty = prevStageRef.current === 'empty' || prevStageRef.current === null
          const nowEmpty = newStage === 'empty'
          if (wasEmpty && !nowEmpty) {
            // 空仓 → 持仓：切换到持仓分析视图
            const stageLabel = d.holding_stage_v2?.label || `Stage ${newStage}`
            setToast(`已进入持仓模式（${stageLabel}）—— 雷达切换到出场判断视图`)
          } else if (!wasEmpty && nowEmpty) {
            // 持仓 → 空仓：切换回入场扫描视图
            const sc = d.strategy_classification?.strategy_type || '观察中'
            setToast(`已清仓，切回空仓模式（当前：${sc}）`)
          } else {
            // 阶段内部变化（Stage 1→2 等）
            const stageLabel = d.holding_stage_v2?.label || `Stage ${newStage}`
            setToast(`持仓阶段更新 → ${stageLabel}`)
          }
          setPendingRefresh(true)
        }
        prevStageRef.current = newStage

        setData(d)
        setEntryChecklist(d.entry_checklist ?? null)
        setHoldingStatus(d.holding_status ?? null)
        setHoldingStageV2(d.holding_stage_v2 ?? null)
        setStrategyClassification(d.strategy_classification ?? null)
        setStopAtrCheck(d.stop_atr_check ?? null)
        setTargets(d.targets ?? null)
        setRewardRatio(d.reward_ratio ?? null)
        setDataFreshness(d.data_freshness ?? null)
      }
    } catch (_) {
      // 网络异常时保留旧数据，由骨架屏或警告条体现
    } finally {
      if (!silent) setLoading(false)
    }
  }, [symbol])


  // ── 首次加载：先查持仓，立即发起 V2 请求 ──
  useEffect(() => {
    if (!symbol) return
    setAiReport(null); setShowAI(false)
    setActiveHistoryId(null); setHistoryTimestamp(null); setShowHistory(false)
    setHolding(null); setData(null); setRewardRatio(null); prevStageRef.current = null
    setPendingRefresh(false)

    let active = true
    ;(async () => {
      let h = null
      try {
        const res  = await fetch(`${API_BASE}/positions/${symbol}`)
        if (res.ok) {
          const p = await res.json()   // positions API 直接返回对象，无 data 包装
          h = (p && p.quantity > 0) ? { cost: p.avg_cost, qty: p.quantity } : null
        }
        // 404 = 未持仓，h 保持 null
      } catch (_) {}
      if (!active) return
      holdingRef.current = h   // 同步到 ref，供后续轮询读取
      setHolding(h)
      await fetchV2(h, false)   // 立即请求，不等 tick
    })()

    // 轮询定时器
    pollTimerRef.current = setInterval(() => {
      fetchV2(undefined, true)
    }, POLL_INTERVAL_MS)

    return () => {
      active = false
      clearInterval(pollTimerRef.current)
    }
  }, [symbol])

  // ── 手动刷新（Toast 点击后）──
  const handleManualRefresh = () => {
    setPendingRefresh(false)
    setToast(null)
    fetchV2(undefined, false)
  }

  // ── AI 推演 ──
  const handleAIDeduce = async () => {
    if (!symbol) return
    setDeducing(true); setShowAI(true)
    try {
      const res    = await fetch(`${API_BASE}/agent/radar_deduce`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, mode }),
      })
      const result = await res.json()
      if (result.status === 'success') setAiReport(result.data)
    } catch (_) {} finally {
      setDeducing(false)
    }
  }

  // ── 历史 ──
  const handleToggleHistory = async () => {
    if (!showHistory) {
      try {
        const res  = await fetch(`${API_BASE}/agent/radar_history/${symbol}`)
        const json = await res.json()
        if (json.status === 'success') setHistoryList(json.data)
      } catch (_) {}
    }
    setShowHistory(!showHistory)
  }

  const loadHistorySnapshot = (h) => {
    setActiveHistoryId(h.id); setHistoryTimestamp(h.created_at)
    setData(h.matrix_data)
    const deduction = typeof h.deduction_process === 'string'
      ? JSON.parse(h.deduction_process) : h.deduction_process
    setAiReport(deduction); setShowAI(true); setShowHistory(false)
  }

  const handleBackToCurrent = () => {
    setActiveHistoryId(null); setHistoryTimestamp(null)
    setAiReport(null); setShowAI(false)
    fetchV2(undefined, false)
  }

  // ── 衍生状态 ──
  const matrix        = data ? (mode === 'A' ? data.matrix_a : data.matrix_b) : []
  const nestingData   = data ? (mode === 'A' ? data.interval_nesting_a : data.interval_nesting_b) : null
  const forwardAnalysis = data ? (mode === 'A' ? data.forward_analysis_a : data.forward_analysis_b) : null
  const board         = readBoard(matrix, data?.week, nestingData, forwardAnalysis)

  // 行动卡片数据源：优先用 forward_analysis 里的 forward_classes
  const actionCards   = board?.forwardAnalysis?.forward_classes ?? board?.classifications ?? []

  // 当前价（用于 AI 预案触发高亮）
  const currentPrice  = matrix?.[0]?.price ?? 0
  const isHolding     = holdingStatus && holdingStatus.stage !== 'empty'

  // ── 渲染 ──
  return (
    <div className={`tradar-v2 ${collapsed ? 'collapsed' : ''}`}>

      {/* ─── 顶栏（第一眼级）─── */}
      <div className="tradar-v2-header" onClick={() => setCollapsed(!collapsed)}>

        {/* 竖排标题格 + 战法标签 (Task #17) */}
        <div className="tradar-v2-vtag-wrap">
          <div className="tradar-v2-vtag" aria-label="推演雷达">推演雷达</div>
          {/* 空仓模式才显示战法标签（持仓模式已锁定，不再扫描）*/}
          {!isHolding && strategyClassification && (
            <StrategyBadge sc={strategyClassification} />
          )}
        </div>

        {/* 右侧控制区 */}
        <div className="tradar-v2-header-controls">
          {pendingRefresh && (
            <button
              className="tv2-refresh-badge"
              onClick={e => { e.stopPropagation(); handleManualRefresh() }}
              aria-label="点击刷新雷达视图"
            >刷新</button>
          )}

          {holding && (
            <span className="tv2-holding-badge">
              <span className="tv2-holding-dot" style={{ background: T.BULL }} aria-hidden />
              持仓 {holding.qty}股 · {holding.cost?.toFixed(2)}
            </span>
          )}

          <div className="tradar-v2-tabs" onClick={e => e.stopPropagation()}>
            <button className={`tv2-tab ${mode === 'A' ? 'active' : ''}`} onClick={() => setMode('A')}>短线</button>
            <button className={`tv2-tab ${mode === 'B' ? 'active' : ''}`} onClick={() => setMode('B')}>波段</button>
            <button className={`tv2-tab ${mode === 'journal' ? 'active' : ''}`} onClick={() => setMode('journal')}>🌌 日志</button>
          </div>
          <span className="tradar-collapse-icon">{collapsed ? '▶' : '▼'}</span>
        </div>
      </div>

      {!collapsed && (
        <div className="tradar-v2-body">
          {mode === 'journal' ? (
            <MultiverseJournal symbol={symbol} />
          ) : (
            <>
              {/* ─── 过期数据警告（始终显示在最上方）─── */}
              <StaleWarning freshness={dataFreshness} />

              {/* ─── Loading：骨架屏 ─── */}
              {loading && !data && <SkeletonScreen />}

              {/* ─── 区间套 Banner ─── */}
              {!loading && board?.intervalNesting?.depth >= 2 && (
                <div className={`nesting-banner nesting-depth-${board.intervalNesting.depth}`}>
                  <span className="nesting-icon">{board.intervalNesting.direction === 'bottom' ? '🟢' : '🔴'}</span>
                  <span className="nesting-label">{board.intervalNesting.label}</span>
                  <span className="nesting-levels">
                    {board.intervalNesting.levels.map(l => LEVEL_NAMES[l.level] || l.level).join(' → ')}
                  </span>
                </div>
              )}

              {/* ─── StatusBar 摘要行（替换旧 position-bar）─── */}
              {!loading && data && (
                <StatusBar
                  board={board}
                  isHolding={isHolding}
                  holdingStatus={holdingStatus}
                  holdingCost={holding?.cost ?? 0}
                />
              )}

              {/* ═══ 核心信息层（第二眼级）— 持仓/空仓双模式（Task #16 #18）═══ */}
              {!loading && data && (
                <>
                  {/* ── 空仓模式：展示甲乙丙预案 + 战法入场条件 ── */}
                  {!isHolding && (
                    <>
                      {/* 甲乙丙完全分类预案 */}
                      <div className="ai-classifications">
                        <div className="classifications-title">📋 完全分类预案</div>
                        {[0, 1, 2].map(i => {
                          const fc = actionCards[i]
                          if (!fc) return (
                            <ActionCard key={i} fc={null} index={i}
                              isHighlighted={false} isVetoed={false} isEmpty />
                          )
                          return (
                            <ActionCard
                              key={fc.id || i} fc={fc} index={i}
                              isHighlighted={fc.highlighted ?? false}
                              isVetoed={fc.vetoed ?? false}
                            />
                          )
                        })}
                      </div>

                      {/* 战法入场条件（含战法分类 + ATR校验）*/}
                      <div className="tv2-data-layer">
                        <StrategyEntryPanel
                          sc={strategyClassification}
                          legacyChecklist={entryChecklist}
                          rewardRatio={rewardRatio}
                        />
                        <AtrCheckRow atrCheck={stopAtrCheck} />
                        <MatrixDetail matrix={matrix} />
                      </div>
                    </>
                  )}

                  {/* ── 持仓模式：隐藏所有入场分析，只显示出场判断（Task #16）── */}
                  {isHolding && (
                    <div className="tv2-data-layer">
                      {/* 结构完整/失效状态卡（Task #17）*/}
                      <StructureStatusCard
                        holdingStatus={holdingStatus}
                        holdingStageV2={holdingStageV2}
                      />

                      {/* 六阶段状态机：台阶止损 + 背驰预警 + 减仓/清仓建议 */}
                      <HoldingPanel
                        status={holdingStageV2 ?? holdingStatus}
                        holdingStatus={holdingStatus}
                        holdingCost={holding?.cost}
                      />

                      <MatrixDetail matrix={matrix} />
                    </div>
                  )}
                </>
              )}



              {/* ═══ AI 解读（情绪安抚层，默认折叠）═══ */}
              {!loading && data && !showAI && (
                <div className="tv2-ai-footer">
                  <button className="tv2-ai-expand tv2-ai-expand--slim" onClick={handleAIDeduce} aria-label="展开并触发AI深度看盘">
                    🧠 AI 深度看盘
                  </button>
                  <button
                    className={`tradar-history-btn ${showHistory ? 'active' : ''}`}
                    onClick={handleToggleHistory}
                  >
                    🕰️ {showHistory ? '收起' : '复盘'}
                  </button>
                </div>
              )}

              {!loading && showAI && (
                <div className="tv2-ai-section-wrap">
                  <div className="tv2-ai-disclaimer">以下解读由算法规则生成，不构成买卖建议</div>
                  <AISection
                    aiReport={aiReport}
                    deducing={deducing}
                    onDeduce={handleAIDeduce}
                    onCollapse={() => { setShowAI(false); setAiReport(null) }}
                    activeHistoryId={activeHistoryId}
                    historyTimestamp={historyTimestamp}
                    onBackToCurrent={handleBackToCurrent}
                    currentPrice={currentPrice}
                  />
                </div>
              )}

              {/* 历史列表 */}
              {showHistory && (
                <div className="tradar-history-list">
                  {historyList.length === 0 ? (
                    <div className="history-empty">暂无历史推演</div>
                  ) : (
                    historyList.map(h => (
                      <div key={h.id} className="history-item" onClick={() => loadHistorySnapshot(h)}>
                        <span className="history-date">{h.created_at?.slice(5, 16)}</span>
                        <span className="history-summary" title={h.summary}>{h.summary}</span>
                      </div>
                    ))
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* ─── Toast（持仓状态切换提示）─── */}
      {toast && <Toast message={toast} onDismiss={() => setToast(null)} />}
    </div>
  )
}
