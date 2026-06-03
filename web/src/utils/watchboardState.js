export function formatWatchPrice(value, options = {}) {
  const num = Number(value || 0)
  if (!num) return '--'
  if (options.fixed) return num.toFixed(2)
  const fixed = Math.abs(num) >= 100 ? num.toFixed(2) : num.toFixed(3)
  return fixed.replace(/(\.\d*?[1-9])0+$/, '$1').replace(/\.0+$/, '')
}

export function extractWatchStateMachine(item) {
  const summary = item?.reasoning_summary || {}
  const direct = summary.watch_state_machine || {}
  const nested = summary.watch_plan?.watch_state_machine || {}
  const machine = direct?.version ? direct : nested
  if (!machine || typeof machine !== 'object') return null
  const transitions = Array.isArray(machine.transitions) ? machine.transitions : []
  if (!transitions.length && !machine.current_state?.display && !machine.current_state?.name) return null
  return machine
}

function collectStateMachineTransitions(machine) {
  return (Array.isArray(machine?.transitions) ? machine.transitions : [])
    .map((transition) => {
      const trigger = transition?.trigger || {}
      const type = String(trigger.type || '')
      const level = Number(trigger.level || 0)
      if (!level || !['price_above', 'price_below'].includes(type)) return null
      return {
        ...transition,
        trigger: { type, level },
        direction: type === 'price_above' ? 'above' : 'below',
      }
    })
    .filter(Boolean)
}

function crossedStateMachineTransition(machine, price, previousPrice) {
  const transitions = collectStateMachineTransitions(machine)
  const prev = Number(previousPrice || 0)
  if (!price || !prev || !transitions.length) return null
  const crossed = transitions
    .filter((transition) => (
      transition.trigger.type === 'price_above'
        ? prev < transition.trigger.level && price >= transition.trigger.level
        : prev > transition.trigger.level && price <= transition.trigger.level
    ))
    .sort((a, b) => Math.abs(price - a.trigger.level) - Math.abs(price - b.trigger.level))
  return crossed[0] || null
}

function nearestStateMachineTransition(machine, price) {
  const transitions = collectStateMachineTransitions(machine)
  if (!price || !transitions.length) return null
  return transitions
    .map((transition) => ({ ...transition, distancePct: Math.abs(price - transition.trigger.level) / price }))
    .sort((a, b) => a.distancePct - b.distancePct)[0] || null
}

function stateMachineBaseLine(machine) {
  const current = machine?.current_state || {}
  return String(current.display || current.name || '').trim()
}

function stateMachineRange(machine) {
  const range = machine?.current_state?.range
  if (!Array.isArray(range) || range.length < 2) return null
  const low = Number(range[0] || 0)
  const high = Number(range[1] || 0)
  if (!low || !high || low === high) return null
  return { low: Math.min(low, high), high: Math.max(low, high) }
}

function stateMachineTransitionLine(transition, fallback = '') {
  if (!transition) return fallback
  return String(transition.observe || transition.next_state || transition.next_watch || fallback || '').trim()
}

function stateMachineTriggerLabel(transition) {
  const trigger = transition?.trigger || {}
  if (!trigger?.level) return ''
  const verb = trigger.type === 'price_below' ? '跌破' : '站上'
  return `${verb}${formatWatchPrice(trigger.level)}`
}

function stateMachineKind(transition) {
  const id = String(transition?.id || '')
  const state = String(transition?.next_state || '')
  if (/down|下|跌|破|支撑|转弱/.test(`${id}${state}`)) return 'down'
  if (/pressure|冲|上|突破|离开|增强/.test(`${id}${state}`)) return 'up'
  return transition?.trigger?.type === 'price_below' ? 'down' : 'up'
}

export function computeStateMachineState(item, currentPrice, previousPrice) {
  const price = Number(currentPrice || item?.price || 0)
  const prevPrice = Number(previousPrice || item?.previous_price || 0)
  const machine = extractWatchStateMachine(item)
  if (!machine || !price) return { available: false }

  const current = machine.current_state || {}
  const crossed = crossedStateMachineTransition(machine, price, prevPrice)
  const active = crossed || null
  const nearest = nearestStateMachineTransition(machine, price)
  const range = stateMachineRange(machine)
  let state = 'idle'
  let priority = 0
  let activeTransition = active
  let displayLine = stateMachineBaseLine(machine) || item?.reasoning_summary?.one_liner || '等待关键位'
  let nextWatchLine = ''
  let nextWatchLabel = ''
  let actionLabel = item?.reasoning_summary?.action || '观察'
  let isFreshTrigger = false

  if (active) {
    isFreshTrigger = true
    const kind = stateMachineKind(active)
    state = kind === 'down' ? 'alert' : 'confirmed'
    priority = kind === 'down' ? 2 : 1
    displayLine = stateMachineTransitionLine(active, displayLine)
    nextWatchLabel = '触发'
    nextWatchLine = active.success || active.failure || active.next_watch || active.observe || ''
  }

  return {
    available: true,
    state,
    priority,
    activeTransition,
    nearestTransition: nearest,
    distancePct: nearest?.distancePct ?? null,
    displayLine,
    nextWatchLine,
    nextWatchLabel,
    actionLabel,
    range,
    currentState: current,
    previousPrice: prevPrice || null,
    isFreshTrigger,
  }
}

export function derivePositionPathState(item, currentPrice, previousPrice) {
  const base = item?.position_path || {}
  if (!base || base.data_status !== 'ready') {
    return {
      version: 'position_path_state.v1',
      data_status: base.data_status || 'missing',
      lifecycle_stage: base.lifecycle_stage || (item?.position ? 'entry_validation' : 'watching'),
      major_task: base.major_task || '',
      current_phase: base.current_phase || '暂无路径状态',
      success_path: base.success_path || '',
      failure_path: base.failure_path || '',
      next_focus: base.next_focus || '等待统一推演提取状态机',
      draft_action: base.draft_action || 'WAIT',
      range: base.range || null,
      ui_state: 'missing',
    }
  }
  const active = base.active_transition || null
  const draftAction = String(base.draft_action || 'WAIT')
  const transitionText = `${active?.id || ''}${active?.next_state || ''}${base.current_phase || ''}`
  const uiState = draftAction === 'LOCKDOWN' || /down|下|跌|破|支撑|转弱|失败|风险/.test(transitionText)
    ? 'alert'
    : draftAction === 'REVIEW_TRIGGER' || draftAction === 'T0_PAPER_ACTION'
      ? 'confirmed'
      : 'idle'
  return {
    version: 'position_path_state.v1',
    data_status: 'ready',
    lifecycle_stage: base.lifecycle_stage || (item?.position ? 'entry_validation' : 'watching'),
    major_task: base.major_task || '',
    current_phase: base.current_phase || base.major_task || '等待路径确认',
    success_path: base.success_path || '',
    failure_path: base.failure_path || '',
    next_focus: base.next_focus || '等待下一次结构触发',
    draft_action: draftAction,
    range: base.range || null,
    active_transition: active,
    nearest_transition: base.nearest_transition || null,
    ui_state: uiState,
  }
}

export function computeCardState(item, currentPrice, previousPrice) {
  const tactical = computeStateMachineState(item, currentPrice, previousPrice)
  const map = {
    idle: 'normal',
    alert: 'alert',
    near: 'alert',
    invalid: 'danger',
    confirmed: 'confirm',
  }
  return map[tactical.state] || 'normal'
}

export function buildIntradayReviewQuestion(item, currentPrice, previousPrice) {
  const price = Number(currentPrice || item?.price || 0)
  const machine = computeStateMachineState(item, price, previousPrice)
  if (!machine.available) {
    const summary = item?.reasoning_summary || {}
    const currentLine = String(summary.one_liner || '').trim()
    const parts = [
      `请做盘中1分钟复核：当前价${formatWatchPrice(price, { fixed: true })}`,
      currentLine ? `上一版卡片主线：${currentLine}` : '',
      '这只票当前没有卡片关键分支数据，不要硬套触发分支；请直接结合上一版完整推演、当前价、盘中1分钟/5分钟/30分钟事实，判断当下走势是在承接、反抽、背驰、震荡延续，还是已经发生结构变化；最后给出接下来最关键的一两个观察点。',
    ]
    return parts.filter(Boolean).join('；')
  }
  const transition = machine.activeTransition || machine.nearestTransition || null
  const trigger = transition.trigger || {}
  const triggerText = trigger.type === 'price_below' ? '跌破' : '站上'
  const level = trigger.level ? formatWatchPrice(trigger.level) : ''
  const rangeText = machine.range
    ? `当前状态区间${formatWatchPrice(machine.range.low)}-${formatWatchPrice(machine.range.high)}`
    : ''
  const modeText = machine.activeTransition
    ? machine.isFreshTrigger
      ? `当前价${formatWatchPrice(price, { fixed: true })}刚刚${triggerText}${level}`
      : `当前价${formatWatchPrice(price, { fixed: true })}已经在${level}${trigger.type === 'price_below' ? '下方' : '上方'}，这是越过关键位后的状态复核`
    : `当前价${formatWatchPrice(price, { fixed: true })}尚未触发关键分支${level ? `，最近观察位是${triggerText}${level}` : ''}`
  const parts = [
    `请做盘中1分钟复核：${modeText}`,
    rangeText,
    machine.displayLine ? `当前状态：${machine.displayLine}` : '',
    transition?.observe ? `接下来观察：${transition.observe}` : '',
    transition?.success ? `如果确认成功，下一步看：${transition.success}` : '',
    transition?.failure ? `如果确认失败，下一步看：${transition.failure}` : '',
    transition?.next_watch ? `后续观察：${transition.next_watch}` : '',
    '请优先结合当前价、5分钟/30分钟结构和上一版完整推演判断现在是承接、反抽、背驰、震荡延续，还是已经确认/失败；如果盘中1分钟K线、1分钟MACD和日内路径已经有足够数据，再作为细节复核；最后给出接下来最关键的观察点。',
  ]
  return parts.filter(Boolean).join('；')
}

export function intradayReviewLabel(item, currentPrice, previousPrice) {
  const machine = computeStateMachineState(item, currentPrice, previousPrice)
  if (!machine.available) return '1m盘中复核'
  if (!machine.activeTransition) return '1m区间复核'
  return machine.isFreshTrigger ? '1m复核触发' : '1m状态复核'
}
