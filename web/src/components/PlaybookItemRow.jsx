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

function planTitle(item) {
  return item.trigger?.plan_title || item.plan_id || '结构计划'
}

function invalidText(item) {
  return item.invalidation?.invalid_if || '等待 Radar 给出失效条件'
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
          {STATUS_LABEL[item.status] || item.status}
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
          去看盘
        </button>
      </div>
    </div>
  )
}
