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
  unknown: '来源待定',
}

function eventType(item) {
  if (item.status === 'STALE' || item.status === 'ENGINE_ERROR') return '数据复核'
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
  return item.trigger?.plan_title || item.plan_id || '结构计划'
}

function invalidText(item) {
  const aiNative = item.trigger?.ai_native
  if (aiNative?.next_focus) return aiNative.next_focus
  return item.invalidation?.invalid_if || '等待 Radar 给出失效条件'
}

function aiBadge(item) {
  const aiNative = item.trigger?.ai_native
  if (!aiNative?.primary_path) return null
  return `${aiNative.primary_name || aiNative.primary_path} ${Number(aiNative.primary_score || 0)}`
}

export default function PlaybookItemRow({ item, active, onSelect, onViewInChan }) {
  const stale = item.status === 'STALE' || item.status === 'ENGINE_ERROR'
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
        <span className={`playbook-event playbook-event--${String(item.status).toLowerCase()}`}>
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
