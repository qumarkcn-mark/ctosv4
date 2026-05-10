const PATH_LABELS = {
  UPWARD_MAJOR_WAVE: '主升延伸',
  HIGH_VOLATILITY_OSCILLATION: '高波动震荡',
  PULLBACK_IN_UPTREND: '上升回落',
  DOWNWARD_DEFENSE: '防守下跌',
  BOTTOM_REPAIR: '底部修复',
  CENTER_REBOUND: '中枢修复',
  NO_EDGE: '无优势路径',
}

const PHASE_LABELS = {
  STANDARD: '标准',
  MICRO_CONVERSION: '多空转换',
  CENTER_UPPER_CONTEST: '上沿争夺',
}

const ACTION_LABELS = {
  HOLD_OR_TRAIL: '守线跟随',
  WATCH_BREAKOUT_HOLD: '看能否站稳',
  WAIT_BREAKOUT: '等向上确认',
  WAIT_RECLAIM: '等拉回确认',
  WAIT_UPPER_BREAK: '等站上上沿',
  REDUCE_CHASING: '别追中间价',
  DEFENSIVE: '先防风险',
  WATCH_REBOUND: '看修复能否成立',
  WAIT_STRUCTURE: '等结构清楚',
  WAIT_CONFIRMATION: '等买点确认',
  WATCH: '先观察',
}

const RISK_LABELS = {
  MEDIUM: '中',
  MEDIUM_HIGH: '中高',
  HIGH: '高',
}

const CONFIRMATION_LABELS = {
  A_NOT_TRIGGERED: 'A 未触发',
  A_PARTIAL_TRIGGERED: 'A 半确认',
  A_FULL_TRIGGERED: 'A 全确认',
  B_MAINTAINED: 'B 维持',
  C_TRIGGERED: 'C 已触发',
  A_INTRADAY_PARTIAL_TRIGGERED: 'A 盘中半确认',
  A_INTRADAY_FULL_TRIGGERED: 'A 盘中确认',
  B_INTRADAY_MAINTAINED: 'B 盘中维持',
  C_INTRADAY_TRIGGERED: 'C 盘中触发',
}

const CONFIRMATION_TONES = {
  A_NOT_TRIGGERED: 'neutral',
  A_PARTIAL_TRIGGERED: 'warning',
  A_FULL_TRIGGERED: 'confirm',
  B_MAINTAINED: 'gold',
  C_TRIGGERED: 'danger',
  A_INTRADAY_PARTIAL_TRIGGERED: 'warning',
  A_INTRADAY_FULL_TRIGGERED: 'confirm',
  B_INTRADAY_MAINTAINED: 'gold',
  C_INTRADAY_TRIGGERED: 'danger',
}

const POSITION_COACH_TONES = {
  confirm: 'confirm',
  watch: 'watch',
  warning: 'warning',
  danger: 'danger',
  neutral: 'neutral',
}

const ROLE_LABELS = {
  L0: '背景',
  L1: '结构',
  L2: '触发',
}

const POSITION_LABELS = {
  CENTER_INSIDE: '中枢内',
  UP_LEAVING: '向上离开',
  DOWN_LEAVING: '向下离开',
  UP_RETEST: '向上回试',
  DOWN_PULLBACK: '向下反抽',
  UNKNOWN: '未知',
}

const LEAVE_RETURN_LABELS = {
  UP_LEAVING: '向上离开',
  UP_RETURNED_TO_CENTER: '向上拉回中枢',
  UP_RETURN_BROKEN: '向上离开失败',
  DOWN_LEAVING: '向下离开',
  DOWN_RETURNED_TO_CENTER: '向下拉回中枢',
  DOWN_RETURN_BROKEN: '向下离开失败',
  NO_LEAVE: '未离开',
  UNKNOWN: '未知',
}

const CENTER_NESTING_LABELS = {
  CHILD_ABOVE_PARENT: '小中枢在大中枢上方',
  CHILD_BELOW_PARENT: '小中枢在大中枢下方',
  CHILD_INSIDE_PARENT: '小中枢在大中枢内部',
  PARENT_INSIDE_CHILD: '大中枢在小中枢内部',
  OVERLAP: '中枢重叠',
  UNKNOWN: '关系未知',
}

const BOUNDARY_LABELS = {
  confirm: '确认',
  maintain: '维持',
  invalidate: '失效',
  pressure: '压力',
  support: '支撑',
}

const PRACTICAL_BOUNDARY_LABELS = {
  short_execution: '短线执行线',
  upside_confirm: '上方确认线',
  mid_defense: '中级别防线',
  invalidation: '失效线',
}

export function adaptRadarContract(contract) {
  const algorithm = contract?.algorithm_v2 || null
  if (!algorithm) {
    return {
      status: 'empty',
      symbol: contract?.symbol || '',
      mode: contract?.mode || 'EMPTY',
      summary: '等待雷达算法输出',
      nextWatch: [],
      triggerPlaybook: [],
      scenarios: [],
      boundaries: emptyBoundaries(),
      boundaryGroups: [],
      atoms: [],
      signal: adaptSignalV2(contract?.signals_v2 || {}),
      labels: {},
      raw: { currentScenarioId: 'B' },
      positionContext: adaptPositionContext(contract?.position_context || {}),
      coachAction: adaptCoachAction(contract?.coach_action || {}),
      dataNotes: contract?.freshness || {},
      dataHealth: adaptDataHealth((contract?.freshness || {}).levels || {}),
      freshness: contract?.freshness || {},
      structureConfig: contract?.structure_config || {},
      structureKernel: contract?.structure_kernel || {},
      diagnostics: contract?.diagnostics || {},
      disclaimer: contract?.disclaimer || '仅供参考，不构成投资建议',
    }
  }

  const confirmation = adaptConfirmation(algorithm.confirmation || {})
  return {
    status: 'ready',
    symbol: contract?.symbol || '',
    mode: contract?.mode || 'EMPTY',
    asOf: contract?.as_of || '',
    summary: algorithm.summary || '等待结构生成推演',
    nextWatch: algorithm.next_watch || [],
    triggerPlaybook: adaptTriggerPlaybook(algorithm.trigger_playbook || []),
    keyObservations: adaptKeyObservations(algorithm.boundaries || {}, algorithm.atoms || {}),
    scenarios: (algorithm.scenarios || []).map(adaptScenario),
    boundaries: adaptBoundaries(algorithm.boundaries || {}),
    boundaryGroups: adaptBoundaryGroups(algorithm.boundary_groups || []),
    atoms: adaptAtoms(algorithm.atoms || {}),
    signal: adaptSignalV2(contract.signals_v2 || {}),
    patterns: (algorithm.patterns || []).map(adaptPattern),
    transition: adaptTransition(algorithm.transition || {}),
    confirmation,
    positionContext: adaptPositionContext(contract.position_context || {}),
    coachAction: adaptCoachAction(contract.coach_action || {}),
    centerNesting: adaptCenterNesting(algorithm.center_nesting || {}),
    labels: {
      path: PATH_LABELS[algorithm.path] || algorithm.path || '未知路径',
      phase: PHASE_LABELS[algorithm.phase] || algorithm.phase || '标准',
      action: ACTION_LABELS[algorithm.action_bias] || algorithm.action_bias || '观察',
      risk: RISK_LABELS[algorithm.risk_level] || algorithm.risk_level || '中',
      confidence: algorithm.confidence || 'UNKNOWN',
      relation: algorithm.relation || '',
    },
    raw: {
      path: algorithm.path,
      phase: algorithm.phase,
      actionBias: algorithm.action_bias,
      riskLevel: algorithm.risk_level,
      currentScenarioId: scenarioIdFromConfirmation(confirmation.state) || algorithm.current_scenario_id || 'B',
      aState: algorithm.a_state || '',
      intradayOverlay: algorithm.intraday_overlay || null,
    },
    dataNotes: algorithm.data_notes || {},
    dataHealth: adaptDataHealth((algorithm.data_notes || {}).levels || contract?.freshness?.levels || {}),
    freshness: contract?.freshness || {},
    structureConfig: contract?.structure_config || {},
    structureKernel: contract?.structure_kernel || {},
    diagnostics: contract?.diagnostics || {},
    disclaimer: algorithm.disclaimer || contract?.disclaimer || '仅供参考，不构成投资建议',
  }
}

function adaptSignalV2(signal) {
  const primary = signal?.primary || {}
  const context = signal?.context || {}
  const kronosTimeline = adaptKronosTimeline(context.kronos_timeline)
  const kronosEnvelope = adaptKronosEnvelope(context.kronos_envelope)
  return {
    version: signal?.version || '',
    state: signal?.state || 'empty',
    code: primary.code || '',
    labelExpert: primary.label_expert || '',
    labelPlain: primary.label_plain || '结构未给出优势信号，继续观察边界',
    action: primary.action || '继续观察',
    level: primary.level || context.level || '',
    pattern: primary.pattern || '',
    strength: primary.strength || '',
    keyPrice: Number(context.key_price || 0),
    stopLossPrice: Number(context.stop_loss_price || 0),
    riskRewardRatio: Number(context.risk_reward_ratio || 0),
    boundaryState: context.boundary_state || '',
    macdAreaRatio: Number(context.macd_area_ratio || 0),
    resonance: (signal?.resonance || []).map(item => ({
      level: item.level || '',
      code: item.code || '',
      labelPlain: item.label_plain || '',
      labelExpert: item.label_expert || '',
    })),
    kronosTimeline,
    kronosEnvelope,
    error: signal?.error || '',
    disclaimer: signal?.disclaimer || context.disclaimer || '仅供参考，不构成投资建议',
  }
}

function adaptKronosTimeline(timeline) {
  if (!timeline || typeof timeline !== 'object') return null
  const bars = Number(timeline.estimated_confirmation_bars)
  const fenxing = timeline.predicted_fenxing && typeof timeline.predicted_fenxing === 'object'
    ? timeline.predicted_fenxing
    : null
  const fenxingPrice = Number(fenxing?.price || 0)
  const hasBars = Number.isFinite(bars) && bars > 0
  const hasFenxing = Boolean(fenxing?.type || (Number.isFinite(fenxingPrice) && fenxingPrice > 0))
  if (!hasBars && !hasFenxing) return null

  return {
    level: timeline.level || '',
    estimatedConfirmationBars: hasBars ? bars : null,
    estimatedConfirmationDate: timeline.estimated_confirmation_date || '',
    predictedFenxing: hasFenxing
      ? {
          type: fenxing?.type || '',
          step: Number(fenxing?.step || 0),
          price: Number.isFinite(fenxingPrice) && fenxingPrice > 0 ? fenxingPrice : null,
          confidenceNote: fenxing?.confidence_note || '',
        }
      : null,
    predictedTrendSummary: timeline.predicted_trend_summary || '',
  }
}

function adaptKronosEnvelope(envelope) {
  if (!envelope || typeof envelope !== 'object') return null
  const low = Number(envelope.envelope_low || 0)
  const high = Number(envelope.envelope_high || 0)
  if (!Number.isFinite(low) || !Number.isFinite(high) || low <= 0 || high <= 0) return null

  const validation = envelope.validation ? String(envelope.validation) : ''
  return {
    targetDay: envelope.target_day || '',
    low,
    high,
    barDirection: envelope.bar_direction || '',
    aiBuyPoint: envelope.ai_buy_point === null || envelope.ai_buy_point === undefined ? null : Number(envelope.ai_buy_point),
    validation,
    validationTone: kronosValidationTone(validation),
    parentLevel: envelope.parent_level || '',
    childLevel: envelope.child_level || '',
    alignment: envelope.alignment || '',
    confidenceNote: envelope.confidence_note || '',
  }
}

function kronosValidationTone(validation) {
  if (!validation) return 'neutral'
  if (validation.startsWith('CONFLICT')) return 'danger'
  if (validation.startsWith('WARNING')) return 'warning'
  return 'neutral'
}

function adaptPositionContext(context) {
  return {
    state: context.state || 'EMPTY',
    label: context.label || '空仓',
    isHolding: Boolean(context.is_holding),
    quantity: Number(context.quantity || 0),
    avgCost: Number(context.avg_cost || 0),
    currentPrice: Number(context.current_price || 0),
    structurePrice: Number(context.structure_price || 0),
    quotePrice: Number(context.quote_price || 0),
    priceSource: context.price_source || '',
    quoteTime: context.quote_time || '',
    isRealtimeDesynced: Boolean(context.is_realtime_desynced),
    realtimeGapPct: context.realtime_gap_pct === null || context.realtime_gap_pct === undefined ? null : Number(context.realtime_gap_pct),
    realtimeNote: context.realtime_note || '',
    pnlPct: context.pnl_pct === null || context.pnl_pct === undefined ? null : Number(context.pnl_pct),
    positionValue: Number(context.position_value || 0),
    weightPct: context.weight_pct === null || context.weight_pct === undefined ? null : Number(context.weight_pct),
    riskFlags: context.risk_flags || [],
    strategyType: context.strategy_type || '',
    entryDate: context.entry_date || '',
  }
}

function adaptCoachAction(action) {
  return {
    version: action.version || '',
    positionState: action.position_state || '',
    positionLabel: action.position_label || '',
    radarState: action.radar_state || '',
    radarLabel: action.radar_label || '',
    action: action.action || '',
    label: action.label || '',
    tone: POSITION_COACH_TONES[action.tone] || 'neutral',
    priority: action.priority || '',
    summary: action.summary || '',
    reason: action.reason || '',
    focus: action.focus || '',
    boundaries: action.boundaries || [],
    riskLines: action.risk_lines || [],
    nearestRiskLine: action.nearest_risk_line || null,
    nextIf: action.next_if || [],
    disclaimer: action.disclaimer || '',
  }
}

export function formatPrice(value) {
  const num = Number(value)
  if (!Number.isFinite(num) || num <= 0) return '--'
  if (num >= 100) return num.toFixed(2)
  return num.toFixed(3).replace(/0$/, '').replace(/0$/, '')
}

export function boundaryGroupLabel(group) {
  return PRACTICAL_BOUNDARY_LABELS[group] || BOUNDARY_LABELS[group] || group
}

function adaptScenario(scenario) {
  return {
    id: scenario.id,
    name: scenario.name,
    role: scenario.role,
    state: scenario.state,
    triggerIf: scenario.trigger_if || [],
    meaning: scenario.meaning || '',
    sourceBoundaries: scenario.source_boundaries || [],
  }
}

function adaptBoundaries(boundaries) {
  return {
    confirm: boundaries.confirm || [],
    maintain: boundaries.maintain || [],
    invalidate: boundaries.invalidate || [],
    pressure: boundaries.pressure || [],
    support: boundaries.support || [],
  }
}

function adaptBoundaryGroups(groups) {
  return (groups || []).map(group => ({
    id: group.id || '',
    label: group.label || boundaryGroupLabel(group.id),
    purpose: group.purpose || '',
    items: group.items || [],
  })).filter(group => group.items.length > 0)
}

function adaptTriggerPlaybook(items) {
  return (items || []).map((item, index) => ({
    id: `${item.path || 'X'}-${index}`,
    path: item.path || '',
    title: item.title || '',
    tone: item.tone || 'neutral',
    condition: item.condition || '',
    then: item.then || '',
    boundary: item.boundary || {},
  }))
}

function adaptKeyObservations(boundaries, atoms) {
  const quote = currentPriceFromAtoms(atoms)
  return (boundaries.pressure || [])
    .filter(item => shouldShowObservation(item, quote))
    .map((item, index) => ({
      id: `observation-${index}`,
      label: observationLabel(item),
      value: formatPrice(item.value),
      meaning: item.meaning || '',
      tone: item.field === 'ATH' ? 'ath' : 'pressure',
      source: item.source_label || item.level || '',
      time: item.time || '',
      distancePct: Number.isFinite(Number(item.distance_pct)) ? Number(item.distance_pct) : null,
    }))
}

function adaptDataHealth(levels) {
  const preferred = ['day', '30', '5']
  const entries = Object.entries(levels || {})
  const ordered = [
    ...preferred
      .filter(level => levels?.[level])
      .map(level => [level, levels[level]]),
    ...entries.filter(([level]) => !preferred.includes(level)),
  ]
  return ordered.map(([level, item]) => ({
    level,
    lastBarAt: item?.last_bar_at || '',
    isStale: Boolean(item?.is_stale),
    staleReason: item?.stale_reason || '',
  }))
}

function shouldShowObservation(item, quote) {
  if (!item) return false
  if (item.field === 'ATH') return true
  const value = Number(item.value)
  if (!Number.isFinite(value) || value <= 0) return false
  return quote <= 0 || value >= quote
}

function observationLabel(item) {
  if (item.field === 'ATH') return '历史前高'
  return `${item.level || ''}${item.field || ''}`
}

function currentPriceFromAtoms(atoms) {
  for (const role of ['L2', 'L1', 'L0']) {
    const price = Number(atoms?.[role]?.price)
    if (Number.isFinite(price) && price > 0) return price
  }
  return 0
}

function adaptAtoms(atoms) {
  return ['L0', 'L1', 'L2'].map((role) => {
    const atom = atoms[role] || {}
    const center = atom.center || {}
    const leaveReturn = atom.leave_return_status || {}
    const momentum = atom.momentum_compare || {}
    return {
      role,
      roleLabel: ROLE_LABELS[role] || role,
      level: atom.public_level || atom.level || role,
      price: atom.price || 0,
      state: atom.position_state || 'UNKNOWN',
      stateLabel: POSITION_LABELS[atom.position_state] || atom.position_state || '未知',
      rawState: atom.raw_state || '',
      center,
      centerRelation: atom.center_relation || '',
      leaveReturn: {
        ...leaveReturn,
        label: LEAVE_RETURN_LABELS[leaveReturn.status] || leaveReturn.status || '未知',
      },
      momentum: {
        ...momentum,
        label: momentum.is_weaker ? '力度衰减' : '力度未衰减',
      },
      eventSequence: atom.event_sequence || [],
      centerBinding: atom.center_binding || {},
      tags: atom.tags || [],
      quality: atom.quality || 'UNKNOWN',
    }
  })
}

function adaptPattern(pattern) {
  return {
    code: pattern.code || '',
    name: pattern.name || pattern.code || '结构模板',
    confidence: pattern.confidence || 'UNKNOWN',
    pathHint: PATH_LABELS[pattern.path_hint] || pattern.path_hint || '',
    evidence: pattern.evidence || [],
    notes: pattern.notes || [],
  }
}

function adaptTransition(transition) {
  return {
    from: transition.from || '',
    to: transition.to || '',
    status: transition.status || 'UNCHANGED',
    trigger: transition.trigger || '',
    patternCode: transition.pattern_code || '',
    meaning: transition.meaning || '',
    evidence: transition.evidence || [],
  }
}

function adaptConfirmation(confirmation) {
  const state = confirmation.state || 'A_NOT_TRIGGERED'
  return {
    ...confirmation,
    state,
    label: CONFIRMATION_LABELS[state] || state,
    tone: CONFIRMATION_TONES[state] || 'neutral',
    progress: Number.isFinite(Number(confirmation.progress)) ? Number(confirmation.progress) : 0,
    matched: confirmation.matched || [],
    unmatched: confirmation.unmatched || [],
  }
}

function scenarioIdFromConfirmation(state) {
  if (state === 'C_TRIGGERED') return 'C'
  if (state === 'C_INTRADAY_TRIGGERED') return 'C'
  if (state === 'B_MAINTAINED') return 'B'
  if (state === 'B_INTRADAY_MAINTAINED') return 'B'
  if (state === 'A_PARTIAL_TRIGGERED' || state === 'A_FULL_TRIGGERED') return 'A'
  if (state === 'A_INTRADAY_PARTIAL_TRIGGERED' || state === 'A_INTRADAY_FULL_TRIGGERED') return 'A'
  return ''
}

function adaptCenterNesting(nesting) {
  return Object.entries(nesting || {}).map(([key, item]) => ({
    key,
    relation: item?.relation || 'UNKNOWN',
    label: CENTER_NESTING_LABELS[item?.relation] || item?.relation || '关系未知',
    parentLevel: item?.parent_level || '',
    childLevel: item?.child_level || '',
    parentZg: item?.parent_zg || 0,
    parentZd: item?.parent_zd || 0,
    childZg: item?.child_zg || 0,
    childZd: item?.child_zd || 0,
    gapToParentZg: item?.gap_to_parent_zg || 0,
  }))
}

function emptyBoundaries() {
  return {
    confirm: [],
    maintain: [],
    invalidate: [],
    pressure: [],
    support: [],
  }
}
