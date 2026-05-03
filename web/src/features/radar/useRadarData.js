import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE } from '../../config.js'
import { adaptRadarContract } from './radarAdapter.js'

const POLL_INTERVAL_MS = 120_000
const REQUEST_TIMEOUT_MS = 75_000
const radarRequests = new Map()

async function loadRadar(symbol, refreshToken = 0) {
  const requestKey = `${symbol}:${refreshToken}`
  const cached = radarRequests.get(requestKey)
  if (cached) return cached

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  const request = fetch(`${API_BASE}/radar/${symbol}?user_id=1`, {
    signal: controller.signal,
  })
    .then(async (res) => {
      const json = await res.json()
      if (!res.ok || json.status !== 'success') {
        const message = json?.data?.error?.message || `Radar 请求失败 ${res.status}`
        throw new Error(message)
      }
      return adaptRadarContract(json.data)
    })
    .finally(() => {
      clearTimeout(timeout)
      radarRequests.delete(requestKey)
    })

  radarRequests.set(requestKey, request)
  return request
}

export function useRadarData(symbol, refreshToken = 0) {
  const [radar, setRadar] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const pollRef = useRef(null)
  const mountedRef = useRef(true)
  const requestSeqRef = useRef(0)

  const fetchRadar = useCallback(async ({ silent = false } = {}) => {
    if (!symbol) return
    const requestSeq = requestSeqRef.current + 1
    requestSeqRef.current = requestSeq
    if (!silent) setLoading(true)
    setError('')
    try {
      const nextRadar = await loadRadar(symbol, refreshToken)
      if (mountedRef.current && requestSeqRef.current === requestSeq) {
        setRadar(nextRadar)
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
  }, [symbol, refreshToken])

  useEffect(() => {
    mountedRef.current = true
    setRadar(null)
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
    refresh: () => fetchRadar({ silent: false }),
  }
}
