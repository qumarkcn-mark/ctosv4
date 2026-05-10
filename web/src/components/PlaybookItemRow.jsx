const MODE_LABEL = {
  HOLDING: '持仓',
  EMPTY: '空仓',
}

const STATUS_LABEL = {
  WATCHING: '观察中',
  TRIGGERED: '已触发',
  EXECUTED: '已执行',
  IGNORED: '已忽略',
  INVALIDATED: '已失效',
  STALE: '数据过期',
  ENGINE_ERROR: '结构失败',
}

const SOURCE_LABEL = {
  positions: '持仓',
  scanner: '机会池',
  watchlist: '自选股',
  rebalance: '调仓',
  unknown: '来源待定',
}

function eventType(item) {
  if (item.status === 'STALE' || item.status === 'ENGINE_ERROR') return '数据复核'
  if (item.source === 'rebalance') return '调仓意图'
  if (item.status === 'TRIGGERED') return '条件触发'
  if (item.source === 'positions' || item.mode === 'HOLDING') return '持仓防线'
  return '观察机会'
}

function itemSource(item) {
  if (item.source) return item.source
  if (item.mode === 'HOLDING') return 'positions'
  return 'unknown'
}

function planTitle(item) {
  const rebalanceAction = item.trigger?.rebalance?.action
  if (item.source === 'rebalance' && rebalanceAction) {
    if (rebalanceAction.action === 'NO_ACTION' || fusionStatus(item)?.state === 'FALLBACK') {
      return item.trigger?.plan_title || rebalanceAction.action_label || '结构兜底复核'
    }
    return `${rebalanceAction.action || 'ACTION'} · ${rebalanceAction.action_label || '调仓意图'}`
  }
  return item.trigger?.plan_title || item.plan_id || '结构计划'
}

function invalidText(item) {
  if (item.source === 'rebalance') {
    const conditions = item.trigger?.rebalance?.conditions || {}
    return conditions.execute_if?.[0] || conditions.delay_if?.[0] || item.invalidation?.invalid_if || '等待调仓条件确认'
  }
  const aiNative = item.trigger?.ai_native
  if (aiNative?.next_focus) return aiNative.next_focus
  return item.invalidation?.invalid_if || '等待 Radar 给出失效条件'
}

function aiBadge(item) {
  if (item.source === 'rebalance') {
    const memory = item.trigger?.rebalance?.memory || {}
    const count = Number(memory.previous_intent_count || 0)
    const suffix = count ? ` · ${memory.urgency_escalated ? '升级' : `第${count + 1}次`}` : ''
    return `${item.trigger?.rebalance?.urgency || STATUS_LABEL[item.status] || item.status}${suffix}`
  }
  const aiNative = item.trigger?.ai_native
  if (!aiNative?.primary_path) return null
  return `${aiNative.primary_name || aiNative.primary_path} ${Number(aiNative.primary_score || 0)}`
}

function fusionStatus(item) {
  if (item.source !== 'rebalance') return null
  const rebalance = item.trigger?.rebalance || {}
  return rebalance.fusion_status || rebalance.evidence?.fusion_status || null
}

function fusionStatusLabel(status) {
  if (!status?.state) return null
  return status.state === 'FALLBACK' ? '结构兜底' : 'AI Ready'
}

export default function PlaybookItemRow({ item, active, onSelect, onViewInChan }) {
  const stale = item.status === 'STALE' || item.status === 'ENGINE_ERROR'
  const fusion = fusionStatus(item)
  const fusionLabel = fusionStatusLabel(fusion)
  return (
    <div
      className={`playbook-row${active ? ' is-active' : ''}${stale ? ' is-stale' : ''}`}
      onClick={() => onSelect(item)}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onSelect(item)
      }}
    >
      <div className="playbook-event-cell">
        <span className={`playbook-event playbook-event--${String(item.status).toLowerCase()}${item.source === 'rebalance' ? ' playbook-event--rebalance' : ''}`}>
          {SOURCE_LABEL[itemSource(item)] || eventType(item)}
        </span>
        <em>{eventType(item)}</em>
      </div>
      <div className="playbook-symbol-cell">
        <span className="playbook-symbol mono">{item.symbol}</span>
        {item.name && <span className="playbook-name">{item.name}</span>}
      </div>
      <div className="playbook-mode-cell">
        <span className={`playbook-mode playbook-mode--${item.mode?.toLowerCase()}`}>
          {MODE_LABEL[item.mode] || item.mode}
        </span>
      </div>
      <div className="playbook-plan-cell">
        <strong>{planTitle(item)}</strong>
        <span>{invalidText(item)}</span>
      </div>
      <div className="playbook-status-cell">
        <span className={`playbook-status playbook-status--${String(item.status).toLowerCase()}`}>
          {aiBadge(item) || STATUS_LABEL[item.status] || item.status}
        </span>
        {fusionLabel && (
          <em className={`playbook-row-fusion playbook-row-fusion--${fusion.state === 'FALLBACK' ? 'fallback' : 'ready'}`}>
            {fusionLabel}
          </em>
        )}
      </div>
      <div className="playbook-row-actions">
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            onViewInChan?.(item.symbol, item.name)
          }}
        >
          雷达
        </button>
      </div>
    </div>
  )
}
