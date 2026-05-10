import { API_BASE } from '../config.js'

export const PREVIEW_TIMEOUT_MS = 12000
export const STRUCTURE_TIMEOUT_MS = 45000

const klinePreviewRequests = new Map()
const chanDetailRequests = new Map()
const lightweightJsonRequests = new Map()

export async function loadDisplayOnlyKlines(symbol, count) {
  const qmtUrl = `${API_BASE}/data/qmt/klines/${symbol}?period=1m&count=${count}&cache_closed=false`
  try {
    const qmtRes = await fetch(qmtUrl)
    if (qmtRes.ok) {
      const qmtJson = await qmtRes.json()
      if (Array.isArray(qmtJson?.klines) && qmtJson.klines.length) {
        return { source: 'qmt_realtime_1m', usage: 'display_preview', klines: qmtJson.klines }
      }
    }
  } catch {}

  const today = formatLocalDate(new Date())
  const todayUrl = `${API_BASE}/data/tdx/minute/${symbol}?count=${count}&start_date=${encodeURIComponent(`${today} 00:00:00`)}`
  const tdxRes = await fetch(todayUrl)
  if (tdxRes.ok) {
    return await tdxRes.json()
  }

  const fallbackRes = await fetch(`${API_BASE}/data/tdx/minute/${symbol}?count=${count}`)
  if (!fallbackRes.ok) {
    throw new Error('1分钟数据不可用')
  }
  return await fallbackRes.json()
}

export async function loadKlinePreview(symbol, interval, count, options = {}) {
  const apiInterval = interval === 'day' ? 'day' : interval
  const previewCount = Math.min(Number(count) || 500, 2000)
  const key = `${symbol}:${apiInterval}:${previewCount}`
  const useCache = !options.signal
  const cached = klinePreviewRequests.get(key)
  if (useCache && cached) return cached

  const request = fetchJsonWithTimeout(
    `${API_BASE}/data/klines/${symbol}?interval=${apiInterval}&count=${previewCount}`,
    PREVIEW_TIMEOUT_MS,
    options,
  )
    .then(async (res) => {
      if (!res.ok) {
        throw new Error(`K线预览请求失败 ${res.status}`)
      }
      return res.json
    })
    .finally(() => {
      klinePreviewRequests.delete(key)
    })
  if (useCache) {
    klinePreviewRequests.set(key, request)
  }
  return request
}

export async function loadChanDetail(symbol, freq, count, cchanPreset, options = {}) {
  const safeCount = Number(count) || 500
  const preset = cchanPreset || 'live_tolerant'
  const computeProfile = options.computeProfile || 'chart_standard_v1'
  const key = `${symbol}:${freq}:${safeCount}:${preset}:${computeProfile}`
  const useCache = !options.signal
  const cached = chanDetailRequests.get(key)
  if (useCache && cached) return cached

  const params = new URLSearchParams({
    freq,
    count: String(safeCount),
    display_count: String(safeCount),
    cchan_preset: preset,
    compute_profile: computeProfile,
  })
  const request = fetchJsonWithTimeout(
    `${API_BASE}/chan/detail/${symbol}?${params.toString()}`,
    STRUCTURE_TIMEOUT_MS,
    options,
  )
    .then(async (res) => {
      if (!res.ok) {
        throw new Error(`结构请求失败 ${res.status}`)
      }
      return res.json
    })
    .finally(() => {
      chanDetailRequests.delete(key)
    })
  if (useCache) {
    chanDetailRequests.set(key, request)
  }
  return request
}

export async function fetchJsonWithTimeout(url, timeoutMs, options = {}) {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  const abortFromCaller = () => controller.abort()
  if (options.signal?.aborted) {
    controller.abort()
  } else if (options.signal) {
    options.signal.addEventListener('abort', abortFromCaller, { once: true })
  }
  try {
    const res = await fetch(url, { signal: controller.signal })
    const json = await res.json().catch(() => null)
    return { ok: res.ok, status: res.status, json }
  } catch (e) {
    if (e?.name === 'AbortError') {
      const reason = options.signal?.aborted ? '请求取消' : `请求超时 ${Math.round(timeoutMs / 1000)}s`
      const error = new Error(reason)
      error.name = 'AbortError'
      throw error
    }
    throw e
  } finally {
    window.clearTimeout(timer)
    if (options.signal) {
      options.signal.removeEventListener('abort', abortFromCaller)
    }
  }
}

export function isAbortLikeError(e) {
  return e?.name === 'AbortError' || String(e?.message || '').includes('请求取消')
}

export async function loadJsonOnce(url) {
  const cached = lightweightJsonRequests.get(url)
  if (cached) return cached
  const request = fetch(url)
    .then(async (res) => {
      if (!res.ok) return null
      return await res.json()
    })
    .finally(() => {
      window.setTimeout(() => lightweightJsonRequests.delete(url), 1500)
    })
  lightweightJsonRequests.set(url, request)
  return request
}

export function normalizeChartPayload(json, { isDisplayOnly, previewOnly = false }) {
  if (isDisplayOnly) {
    const source = json?.source || 'unknown'
    const label = source === 'qmt_realtime_1m' ? '1分 · QMT实时' : '1分 · TDX本地历史'
    const detail = source === 'qmt_realtime_1m' ? '预览级别' : '仅展示/回放'
    return {
      klines: (json?.klines || []).map((k) => ({ ...k, time: k.time || k.date })),
      bis: [],
      segs: [],
      bi_zhongshus: [],
      bi_zhongshus_decomp: [],
      seg_zhongshus: [],
      bsps: [],
      stats: { kline_count: json?.count || json?.klines?.length || 0 },
      config: null,
      dataBadge: { label, detail, tone: source === 'qmt_realtime_1m' ? 'live' : 'history' },
    }
  }
  if (previewOnly) {
    return {
      klines: (json?.klines || []).map((k) => ({ ...k, time: k.time || k.date })),
      bis: [],
      segs: [],
      bi_zhongshus: [],
      bi_zhongshus_decomp: [],
      seg_zhongshus: [],
      bsps: [],
      stats: { kline_count: json?.count || json?.klines?.length || 0 },
      config: null,
      dataBadge: { label: `${json?.interval || 'K'} · K线预览`, detail: `${json?.count || json?.klines?.length || 0} 根 · 结构计算中`, tone: 'history' },
    }
  }
  return {
    ...(json?.data || {}),
    klines: json?.data?.klines || [],
    structureBadge: structureBadgeFromMeta(json?.data),
  }
}

export function structureBadgeFromMeta(data) {
  if (data?.snapshot_status === 'pending' || data?.snapshot_status === 'missing') {
    return {
      label: '结构 · 排队',
      detail: data?.job?.status || 'pending',
      tone: 'live',
      title: `结构任务已入队 · ${data?.job?.job_id || '--'}`,
    }
  }
  if (data?.snapshot_status === 'stale') {
    return {
      label: '结构 · 旧快照',
      detail: data?.freshness?.last_bar_at || '待刷新',
      tone: 'history',
      title: `结构待刷新 · job ${data?.job?.job_id || '--'}`,
    }
  }
  if (data?.snapshot_status === 'failed') {
    return {
      label: '结构 · 失败',
      detail: data?.job?.error_code || 'failed',
      tone: 'history',
      title: data?.job?.error_message || '结构计算失败',
    }
  }
  if (data?.snapshot_status === 'fresh') {
    return {
      label: '结构 · 快照',
      detail: data?.freshness?.last_bar_at || 'fresh',
      tone: 'history',
      title: `结构快照命中 · fp ${(data?.structure_fingerprint || '').slice(0, 12)}`,
    }
  }
  const cache = data?.cache || {}
  const snapshot = data?.snapshot || {}
  if (cache.tier === 'incremental_tail' || snapshot.source === 'incremental_tail') {
    return {
      label: '结构 · 增量',
      detail: `${cache.tail_bars || snapshot.tail_bars || '--'} 尾部`,
      tone: 'live',
      title: `尾部增量重算 · last ${snapshot.last_kline_time || '--'}`,
    }
  }
  if (cache.tier === 'persistent_snapshot' || snapshot.source === 'persistent') {
    return {
      label: '结构 · 快照',
      detail: snapshot.last_kline_time || '命中',
      tone: 'history',
      title: `持久快照命中 · fp ${(snapshot.structure_fingerprint || '').slice(0, 12)}`,
    }
  }
  if (snapshot.source === 'generated') {
    return {
      label: '结构 · 全量',
      detail: `${cache.compute_ms || '--'}ms`,
      tone: 'history',
      title: `全量计算后已保存快照 · fp ${(snapshot.structure_fingerprint || '').slice(0, 12)}`,
    }
  }
  if (cache.hit) {
    return {
      label: '结构 · 内存',
      detail: `${cache.ttl_seconds || '--'}s`,
      tone: 'history',
      title: '短期内存缓存命中',
    }
  }
  if (cache.compute_ms != null) {
    return {
      label: '结构 · 全量',
      detail: `${cache.compute_ms}ms`,
      tone: 'history',
      title: '本次全量计算',
    }
  }
  return null
}

function formatLocalDate(date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}
