import { API_BASE } from '../../config.js'
import { apiJson } from '../../api/client.js'

export const KLINE_PERIODS = [
  { value: 'week', apiInterval: 'week', chartType: 'week', chartSpan: 1, label: '周' },
  { value: 'day', apiInterval: 'day', chartType: 'day', chartSpan: 1, label: '日' },
  { value: 'm60', apiInterval: 'm60', chartType: 'minute', chartSpan: 60, label: '60' },
  { value: 'm30', apiInterval: 'm30', chartType: 'minute', chartSpan: 30, label: '30' },
  { value: 'm15', apiInterval: 'm15', chartType: 'minute', chartSpan: 15, label: '15' },
  { value: 'm5', apiInterval: 'm5', chartType: 'minute', chartSpan: 5, label: '5' },
  { value: 'm1', apiInterval: 'm1', chartType: 'minute', chartSpan: 1, label: '1', supportsStructure: false },
]

const PERIOD_BY_VALUE = new Map(KLINE_PERIODS.map((item) => [item.value, item]))

export function normalizeKlinePeriod(value) {
  if (PERIOD_BY_VALUE.has(value)) return value
  if (value === '60') return 'm60'
  if (value === '30') return 'm30'
  if (value === '15') return 'm15'
  if (value === '5') return 'm5'
  if (value === '1' || value === '1m') return 'm1'
  return 'day'
}

export function getKlinePeriod(value) {
  return PERIOD_BY_VALUE.get(normalizeKlinePeriod(value)) || PERIOD_BY_VALUE.get('day')
}

export async function fetchKlines(symbol, periodValue, count = 1200) {
  const period = getKlinePeriod(periodValue)
  const json = await apiJson(`${API_BASE}/data/klines/${encodeURIComponent(symbol)}?interval=${period.apiInterval}&count=${count}`)
  const klines = Array.isArray(json.klines) ? json.klines : []
  return {
    ...json,
    period,
    klines: klines.map(normalizeKlineBar).filter(Boolean),
  }
}

export async function fetchCurrentPrice(symbol) {
  return apiJson(`${API_BASE}/data/price/${encodeURIComponent(symbol)}`)
}

export async function syncKlines(symbol, periodValue) {
  const period = periodValue ? getKlinePeriod(periodValue) : null
  const query = period ? `?interval=${encodeURIComponent(period.apiInterval)}` : ''
  return apiJson(`${API_BASE}/data/sync-klines/${encodeURIComponent(symbol)}${query}`, {
    method: 'POST',
  })
}

export async function fetchStructurePreview(symbol, periodValue, count = 1200) {
  const period = getKlinePeriod(periodValue)
  return apiJson(
    `${API_BASE}/ai-structure/structure-preview/${encodeURIComponent(symbol)}?level=${period.apiInterval}&count=${count}`
  ).then((json) => json.data)
}

export async function fetchStructureOverlay(symbol, periodValue, count = 1200) {
  return fetchStructurePreview(symbol, periodValue, count)
}

export async function fetchMomentumContext(symbol, periodValue, count = 1200) {
  const period = getKlinePeriod(periodValue)
  return apiJson(
    `${API_BASE}/ai-structure/momentum-context/${encodeURIComponent(symbol)}?level=${period.apiInterval}&count=${count}`
  ).then((json) => json.data)
}

function normalizeKlineBar(item) {
  const close = positiveNumber(item.close ?? item.price)
  const open = positiveNumber(item.open ?? close)
  const high = positiveNumber(item.high ?? Math.max(open, close))
  const low = positiveNumber(item.low ?? Math.min(open, close))
  const timestamp = parseKlineTime(item.timestamp ?? item.datetime ?? item.time ?? item.date)
  if (!timestamp || !close || !open || !high || !low) return null
  return {
    ...item,
    timestamp,
    open,
    high,
    low,
    close,
    volume: numberOrZero(item.volume ?? item.vol),
    turnover: numberOrZero(item.amount ?? item.turnover),
  }
}

function positiveNumber(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

function numberOrZero(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0
}

function parseKlineTime(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value < 10_000_000_000 ? value * 1000 : value
  }
  const text = String(value || '').trim()
  if (!text) return 0
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
