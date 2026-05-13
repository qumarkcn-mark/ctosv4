import { API_BASE } from '../config.js'

const TOKEN_KEYS = ['ctos_token', 'token']

export function getAuthToken() {
  for (const key of TOKEN_KEYS) {
    const token = localStorage.getItem(key)
    if (token) return token
  }
  return ''
}

export function apiUrl(path) {
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  if (path === '/api' || path.startsWith('/api/')) {
    if (API_BASE.startsWith('http://') || API_BASE.startsWith('https://')) {
      return `${API_BASE.replace(/\/$/, '')}${path.slice('/api'.length)}`
    }
    return path
  }
  if (path.startsWith('/')) return `${API_BASE}${path}`
  return `${API_BASE}/${path}`
}

export async function apiFetch(path, options = {}) {
  const token = getAuthToken()
  const headers = new Headers(options.headers || {})
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  return fetch(apiUrl(path), {
    ...options,
    headers,
  })
}

export async function apiJson(path, options = {}) {
  const response = await apiFetch(path, options)
  const text = await response.text()
  const json = text ? JSON.parse(text) : {}
  if (!response.ok || json.status === 'error') {
    throw new Error(json.detail || json.message || `请求失败 ${response.status}`)
  }
  return json
}
