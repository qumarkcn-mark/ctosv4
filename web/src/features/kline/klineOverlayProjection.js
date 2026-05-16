export function buildKlineTimeIndex(bars = []) {
  const index = new Map()
  bars.forEach((bar, dataIndex) => {
    const timestamp = normalizeTimestamp(bar?.timestamp ?? bar?.time ?? bar?.date ?? bar?.datetime)
    if (timestamp) {
      index.set(timestamp, dataIndex)
    }
  })
  return index
}

export function resolveOverlayIndex(timeIndex, timestamp, timeText, fallbackIndex) {
  const exact = normalizeTimestamp(timestamp) || normalizeTimestamp(timeText)
  if (exact && timeIndex?.has(exact)) {
    return timeIndex.get(exact)
  }
  const fallback = Number(fallbackIndex)
  return Number.isFinite(fallback) ? fallback : null
}

export function normalizeTimestamp(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value < 10_000_000_000 ? value * 1000 : value
  }
  const text = String(value || '').trim()
  if (!text) return 0
  if (/^\d+$/.test(text)) {
    const raw = Number(text)
    return Number.isFinite(raw) ? normalizeTimestamp(raw) : 0
  }
  const match = text.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?/)
  if (match) {
    const [, year, month, day, hour = '0', minute = '0', second = '0'] = match
    return new Date(
      Number(year),
      Number(month) - 1,
      Number(day),
      Number(hour),
      Number(minute),
      Number(second)
    ).getTime()
  }
  const parsed = Date.parse(text)
  return Number.isFinite(parsed) ? parsed : 0
}
