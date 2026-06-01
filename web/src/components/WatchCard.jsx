import './WatchCard.css'
import { computeStateMachineState, formatWatchPrice } from '../utils/watchboardState.js'

const formatPrice = (value) => formatWatchPrice(value, { fixed: true })
const formatLevel = formatWatchPrice

function formatPct(value) {
  const num = Number(value || 0)
  if (!Number.isFinite(num)) return '--'
  return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`
}

function actionClass(action) {
  const normalized = String(action || '').replace(/^考虑/, '')
  if (normalized.includes('止损')) return 'stop'
  if (normalized.includes('减仓') || normalized.includes('锁利')) return 'reduce'
  if (normalized.includes('加仓') || normalized.includes('建仓')) return 'add'
  if (normalized.includes('持有')) return 'hold'
  if (normalized.includes('关注')) return 'watch'
  if (normalized.includes('观望')) return 'wait'
  const map = {
    持有: 'hold',
    加仓: 'add',
    减仓: 'reduce',
    止损: 'stop',
    观望: 'wait',
    关注: 'watch',
  }
  return map[normalized] || 'wait'
}

function normalizeCurrentAction(action, hasPosition = false) {
  const text = String(action || '').trim()
  if (!text) return hasPosition ? '持仓观察' : '观望'
  if (hasPosition && /^(观望|观察|继续观望)$/.test(text)) return '持仓观察'
  if (text.includes('止损') || text.includes('减仓') || text.includes('锁利')) return '持仓观察'
  if (text.includes('加仓') || text.includes('建仓')) return '等待确认'
  return text
}

function missingStateMachineMessage(summary = {}) {
  const status = String(summary.extract_status || '').trim()
  if (!summary.one_liner && !summary.watch_plan) return '暂无推演数据'
  if (status === 'failed') return '关键分支提取失败'
  return '暂无关键分支数据'
}

function missingStateMachineMeta(summary = {}) {
  const status = String(summary.extract_status || '').trim()
  if (!summary.one_liner && !summary.watch_plan) return '无推演'
  if (status === 'failed') return '分支提取失败'
  if (status === 'missing_state_machine') return '无关键分支'
  return '无关键分支'
}

function formatMetaTime(value) {
  const text = String(value || '').trim()
  if (!text) return ''
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) return text.slice(5, 16)
  return text.slice(0, 16)
}

function reasoningFreshnessMeta(item = {}, summary = {}) {
  const freshness = item.reasoning_freshness || {}
  const status = String(freshness.status || '').trim()
  if (status === 'stale') {
    return {
      label: `旧推演${formatMetaTime(freshness.data_as_of) ? ` · ${formatMetaTime(freshness.data_as_of)}` : ''}`,
      tone: 'stale',
    }
  }
  if (status === 'ready') {
    const value = formatMetaTime(freshness.data_as_of || summary.data_as_of || summary.generated_at)
    return { label: value ? `同源 · ${value}` : '同源', tone: 'ready' }
  }
  if (status === 'missing') return { label: '无推演', tone: 'muted' }
  return { label: '', tone: 'muted' }
}

export default function WatchCard({ item, currentPrice, previousPrice, onClick }) {
  const price = Number(currentPrice || item.price || 0)
  const machine = computeStateMachineState(item, price, previousPrice)
  const state = machine.available ? machine.state : 'idle'
  const summary = item.reasoning_summary || {}
  const message = machine.available ? (machine.displayLine || summary.one_liner || '等待关键位') : missingStateMachineMessage(summary)
  const rawAction = summary.action || machine.actionLabel || '观望'
  const triggerLine = machine.available ? machine.nextWatchLine : ''
  const triggerLabel = machine.nextWatchLabel || (machine.activeTransition ? (machine.isFreshTrigger ? '触发' : '已越过') : '等待')
  const position = item.position
  const action = normalizeCurrentAction(rawAction, Boolean(position?.shares))
  const quoteTime = item.price_data?.quote_time || ''
  const pnlPct = Number(position?.pnl_pct ?? 0)
  const cost = Number(position?.cost ?? 0)
  const hasPnlPct = position?.pnl_pct !== null && position?.pnl_pct !== undefined
  const hasNegativeCost = cost < 0
  const pnlLabel = pnlPct >= 0 ? '盈' : '亏'
  const freshnessMeta = reasoningFreshnessMeta(item, summary)

  const minL = Number(machine.range?.low || 0)
  const maxL = Number(machine.range?.high || 0)
  const hasRangeLevels = Boolean(minL && maxL)
  let dotPercentage = 50
  if (minL && maxL && maxL > minL) {
    dotPercentage = Math.max(0, Math.min(100, ((price - minL) / (maxL - minL)) * 100))
  }

  return (
    <button className={`watch-card state-${state}`} onClick={onClick} type="button">
      <div className="watch-card-top">
        <div>
          <strong>{item.name || item.symbol}</strong>
          <span>{item.symbol}{quoteTime ? ` · ${quoteTime}` : ''}</span>
        </div>
        <div className="watch-card-price">
          <strong>{formatPrice(price)}</strong>
          <span className={Number(item.change_pct || 0) >= 0 ? 'is-up' : 'is-down'}>
            {formatPct(item.change_pct)}
          </span>
        </div>
      </div>

      {position && (
        <div className="watch-card-position">
          <span className="position-main">{position.shares}股 @{formatPrice(position.cost)}</span>
          {(hasPnlPct || hasNegativeCost) && (
            <span className={`position-pnl ${hasNegativeCost || Number(position.pnl_pct) >= 0 ? 'is-up' : 'is-down'}`}>
              {hasNegativeCost && !hasPnlPct ? '负成本' : `${pnlLabel} ${formatPct(position.pnl_pct)}`}
            </span>
          )}
        </div>
      )}

      <p className="watch-card-summary">{message}</p>
      {triggerLine && (
        <div className="watch-card-trigger">
          <span>{triggerLabel}</span>
          <strong>{triggerLine}</strong>
        </div>
      )}

      {hasRangeLevels && (
        <div className="watch-card-range-bar">
          <span>▼ {formatLevel(minL)}</span>
          <div className="range-bar-track">
            <div className="range-bar-fill" style={{ left: '0%', width: `${dotPercentage}%` }} />
            <div className="range-bar-dot" style={{ left: `${dotPercentage}%` }} />
          </div>
          <span>▲ {formatLevel(maxL)}</span>
        </div>
      )}

      <div className="watch-card-bottom">
        {!hasRangeLevels && (
          <div className="watch-card-levels">
            <span className="watch-card-no-level">{machine.available ? '无区间数据' : missingStateMachineMeta(summary)}</span>
          </div>
        )}
        {freshnessMeta.label && (
          <span className={`watch-card-freshness is-${freshnessMeta.tone}`}>{freshnessMeta.label}</span>
        )}
        <span className={`watch-action-badge action-${actionClass(action)}`}>当前：{action}</span>
      </div>
    </button>
  )
}
