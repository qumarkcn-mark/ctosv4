import { useState, useRef, useEffect, useCallback } from 'react'
import { API_BASE } from '../config.js'
import './StockSearch.css'

export default function StockSearch({ onSelect }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const wrapperRef = useRef(null)
  const timerRef = useRef(null)
  const listRef = useRef(null)

  // 点击外部关闭
  useEffect(() => {
    const handleClick = (e) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      // P2-FIX #8: 清除搜索 timer
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  // 防抖搜索
  const doSearch = useCallback((q) => {
    if (!q || q.length < 1) {
      setResults([])
      setOpen(false)
      return
    }
    setLoading(true)
    fetch(`${API_BASE}/data/search?q=${encodeURIComponent(q)}`)
      .then((r) => r.json())
      .then((data) => {
        setResults(data.results || [])
        setActiveIdx(-1)
        setOpen(true)
        setLoading(false)
      })
      .catch(() => {
        setLoading(false)
      })
  }, [])

  const handleChange = (e) => {
    const val = e.target.value
    setQuery(val)
    setActiveIdx(-1)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => doSearch(val), 300)
  }

  const handleSelect = (item) => {
    setQuery('')
    setOpen(false)
    setResults([])
    setActiveIdx(-1)

    // 保存搜索历史
    const history = JSON.parse(localStorage.getItem('ct_search_history') || '[]')
    const filtered = history.filter((h) => h.symbol !== item.symbol)
    filtered.unshift(item)
    localStorage.setItem('ct_search_history', JSON.stringify(filtered.slice(0, 10)))

    onSelect?.(item)
  }

  // 键盘导航：↑↓ 选择，Enter 确认，Esc 关闭
  const handleKeyDown = (e) => {
    if (!open || results.length === 0) {
      if (e.key === 'Enter') {
        e.preventDefault()
        if (query.trim()) doSearch(query.trim())
      }
      return
    }

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setActiveIdx((prev) => {
          const next = prev < results.length - 1 ? prev + 1 : 0
          scrollItemIntoView(next)
          return next
        })
        break
      case 'ArrowUp':
        e.preventDefault()
        setActiveIdx((prev) => {
          const next = prev > 0 ? prev - 1 : results.length - 1
          scrollItemIntoView(next)
          return next
        })
        break
      case 'Enter':
        e.preventDefault()
        if (activeIdx >= 0 && activeIdx < results.length) {
          handleSelect(results[activeIdx])
        } else if (results.length > 0) {
          handleSelect(results[0])
        }
        break
      case 'Escape':
        setOpen(false)
        setActiveIdx(-1)
        break
    }
  }

  const scrollItemIntoView = (idx) => {
    if (!listRef.current) return
    const items = listRef.current.children
    if (items[idx]) {
      items[idx].scrollIntoView({ block: 'nearest' })
    }
  }

  return (
    <div className="stock-search" ref={wrapperRef}>
      <div className="search-input-wrapper">
        <span className="search-icon">🔍</span>
        <input
          type="text"
          className="search-input"
          value={query}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onFocus={() => query && setOpen(true)}
          placeholder="搜索代码/名称/拼音"
        />
        {loading && <span className="search-spinner" />}
      </div>

      {open && results.length > 0 && (
        <div className="search-dropdown" ref={listRef}>
          {results.map((item, idx) => (
            <div
              key={item.symbol}
              className={`search-result-item${idx === activeIdx ? ' active' : ''}`}
              onClick={() => handleSelect(item)}
              onMouseEnter={() => setActiveIdx(idx)}
            >
              <span className="result-symbol mono">{item.symbol}</span>
              <span className="result-name">{item.name}</span>
              <span className="result-type">{item.market || ''}{item.type === 'ZS' ? ' 指数' : ''}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
