import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE } from '../../config.js'
import { adaptRadarContract } from './radarAdapter.js'

const POLL_INTERVAL_MS = 120_000
const REQUEST_TIMEOUT_MS = 75_000
const radarRequests = new Map()

async function loadRadar(symbol, refreshToken = 0, profile = 'auto') {
  const safeProfile = ['auto', 'fast', 'full'].includes(profile) ? profile : 'auto'
  const requestKey = `${symbol}:${refreshToken}:${safeProfile}`
  const cached = radarRequests.get(requestKey)
  if (cached) return cached

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  const request = fetch(`${API_BASE}/radar/${symbol}?user_id=1&profile=${safeProfile}`, {
    signal: controller.signal,
  })
    .then(async (res) => {
      const json = await readJsonResponse(res)
      const message = json?.data?.error?.message || json?.detail || json?.message || `Radar 请求失败 ${res.status}`
      if (!res.ok || (json.status !== 'success' && !json.data)) {
        throw new Error(message)
      }
      const adapted = adaptRadarContract(json.data)
      if (json.status !== 'success') {
        adapted.loadWarning = message
      }
      return adapted
    })
    .finally(() => {
      clearTimeout(timeout)
      radarRequests.delete(requestKey)
    })

  radarRequests.set(requestKey, request)
  return request
}

async function readJsonResponse(res) {
  const text = await res.text()
  if (!text) return {}
  try {
    return JSON.parse(text)
  } catch {
    return {
      status: 'error',
      message: text.slice(0, 160) || `请求失败 ${res.status}`,
    }
  }
}

export function useRadarData(symbol, refreshToken = 0) {
  const [radar, setRadar] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [profile, setProfile] = useState('auto')
  const pollRef = useRef(null)
  const mountedRef = useRef(true)
  const requestSeqRef = useRef(0)
  const baseKeyRef = useRef('')

  const fetchRadar = useCallback(async ({ silent = false } = {}) => {
    if (!symbol) return
    const requestSeq = requestSeqRef.current + 1
    requestSeqRef.current = requestSeq
    if (!silent) setLoading(true)
    setError('')
    try {
      const nextRadar = await loadRadar(symbol, refreshToken, profile)
      if (mountedRef.current && requestSeqRef.current === requestSeq) {
        setRadar(nextRadar)
        setError(nextRadar.loadWarning || '')
      }
    } catch (err) {
      const message = err?.name === 'AbortError'
        ? '结构计算超时，请稍后刷新'
        : (err?.message || 'Radar 请求失败')
      if (mountedRef.current && requestSeqRef.current === requestSeq) {
        setError(message)
      }
    } finally {
      if (mountedRef.current && requestSeqRef.current === requestSeq && !silent) {
        setLoading(false)
      }
    }
  }, [symbol, refreshToken, profile])

  useEffect(() => {
    mountedRef.current = true
    const nextBaseKey = `${symbol || ''}:${refreshToken}`
    if (baseKeyRef.current !== nextBaseKey) {
      baseKeyRef.current = nextBaseKey
      setRadar(null)
    }
    setError('')
    fetchRadar({ silent: false })
    pollRef.current = setInterval(() => {
      fetchRadar({ silent: true })
    }, POLL_INTERVAL_MS)
    return () => {
      mountedRef.current = false
      clearInterval(pollRef.current)
    }
  }, [fetchRadar])

  return {
    radar,
    loading,
    error,
    profile,
    setProfile,
    refresh: () => fetchRadar({ silent: false }),
  }
}
