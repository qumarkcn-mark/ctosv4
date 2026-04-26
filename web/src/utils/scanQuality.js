export function getScanQualityFlags(item) {
  const flags = []
  const rr = Number(item.rr_ratio || 0)
  const atrPct = Number(item.atr_pct || 0)
  const volumeRatio = Number(item.volume_ratio || 0)

  if ((item.llm_red_flags || []).length > 0) {
    flags.push({ level: 'danger', label: '红旗风险' })
  }
  if (item.llm_verdict === '回避') {
    flags.push({ level: 'danger', label: '调研回避' })
  }
  if (rr > 0 && rr < 1.8) {
    flags.push({ level: 'warn', label: '赔率不足' })
  }
  if (atrPct >= 0.08) {
    flags.push({ level: 'warn', label: 'ATR过宽' })
  }
  if (volumeRatio > 0 && volumeRatio < 0.6) {
    flags.push({ level: 'warn', label: '量能偏弱' })
  }

  return flags
}

export function getScanQualityLevel(item) {
  const flags = getScanQualityFlags(item)
  if (flags.some((flag) => flag.level === 'danger')) return 'danger'
  if (flags.length > 0) return 'warn'
  return 'clean'
}
