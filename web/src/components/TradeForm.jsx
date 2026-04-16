import { useState, useRef, useEffect, useCallback } from 'react'
import { API_BASE } from '../config.js'
import './TradeForm.css'

export default function TradeForm({ onSubmitted }) {
  const [form, setForm] = useState({
    symbol: '',
    name: '',
    direction: 'BUY',
    price: '',
    quantity: '',
    reason_text: '',
    reason_category: '',
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  // ── 股票搜索相关 ──
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [searchOpen, setSearchOpen] = useState(false)
  const [searchLoading, setSearchLoading] = useState(false)
  const [activeIdx, setActiveIdx] = useState(-1)
  const searchTimerRef = useRef(null)
  const searchWrapperRef = useRef(null)
  const listRef = useRef(null)

  // 点击外部关闭下拉
  useEffect(() => {
    const handleClick = (e) => {
      if (searchWrapperRef.current && !searchWrapperRef.current.contains(e.target)) {
        setSearchOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
    }
  }, [])

  // 防抖搜索
  const doSearch = useCallback((q) => {
    if (!q || q.length < 1) {
      setSearchResults([])
      setSearchOpen(false)
      return
    }
    setSearchLoading(true)
    fetch(`${API_BASE}/data/search?q=${encodeURIComponent(q)}`)
      .then((r) => r.json())
      .then((data) => {
        setSearchResults(data.results || [])
        setActiveIdx(-1)
        setSearchOpen(true)
      })
      .catch(() => {})
      .finally(() => setSearchLoading(false))
  }, [])

  const handleSearchChange = (e) => {
    const val = e.target.value
    setSearchQuery(val)
    setActiveIdx(-1)
    // 清除已选中的股票（用户重新输入）
    if (form.symbol) {
      setForm({ ...form, symbol: '', name: '' })
    }
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
    searchTimerRef.current = setTimeout(() => doSearch(val), 300)
  }

  const handleSelectStock = (item) => {
    setForm({ ...form, symbol: item.symbol, name: item.name })
    setSearchQuery('')
    setSearchOpen(false)
    setSearchResults([])
    setActiveIdx(-1)
  }

  const handleClearStock = () => {
    setForm({ ...form, symbol: '', name: '' })
    setSearchQuery('')
  }

  // 键盘导航
  const handleSearchKeyDown = (e) => {
    if (!searchOpen || searchResults.length === 0) {
      if (e.key === 'Enter') {
        e.preventDefault()
        if (searchQuery.trim()) doSearch(searchQuery.trim())
      }
      return
    }
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setActiveIdx((prev) => {
          const next = prev < searchResults.length - 1 ? prev + 1 : 0
          scrollIntoView(next)
          return next
        })
        break
      case 'ArrowUp':
        e.preventDefault()
        setActiveIdx((prev) => {
          const next = prev > 0 ? prev - 1 : searchResults.length - 1
          scrollIntoView(next)
          return next
        })
        break
      case 'Enter':
        e.preventDefault()
        if (activeIdx >= 0 && activeIdx < searchResults.length) {
          handleSelectStock(searchResults[activeIdx])
        } else if (searchResults.length > 0) {
          handleSelectStock(searchResults[0])
        }
        break
      case 'Escape':
        setSearchOpen(false)
        setActiveIdx(-1)
        break
    }
  }

  const scrollIntoView = (idx) => {
    if (!listRef.current) return
    const items = listRef.current.children
    if (items[idx]) items[idx].scrollIntoView({ block: 'nearest' })
  }

  const handleChange = (e) => {
    setForm({ ...form, [e.target.name]: e.target.value })
    setError('')
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')

    // 校验
    if (!form.symbol.trim()) return setError('请先搜索并选择一只股票')
    if (!form.price || parseFloat(form.price) <= 0) return setError('请输入有效价格')
    if (!form.quantity || parseInt(form.quantity) <= 0) return setError('请输入有效数量')

    setSubmitting(true)
    try {
      const resp = await fetch('/api/trades', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: form.symbol.trim(),
          name: form.name.trim() || null,
          direction: form.direction,
          price: parseFloat(form.price),
          quantity: parseInt(form.quantity),
          reason_text: form.reason_text.trim() || null,
          reason_category: form.reason_category || null,
        }),
      })

      if (!resp.ok) {
        const data = await resp.json()
        throw new Error(data.detail || '提交失败')
      }

      // 重置表单
      setForm({
        symbol: '', name: '', direction: 'BUY',
        price: '', quantity: '', reason_text: '', reason_category: '',
      })
      setSearchQuery('')
      onSubmitted?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const amount = (parseFloat(form.price) || 0) * (parseInt(form.quantity) || 0)

  return (
    <form className="trade-form" onSubmit={handleSubmit}>
      <h3 className="form-title">录入交易</h3>

      {/* 买卖方向 */}
      <div className="direction-toggle">
        <button
          type="button"
          className={`direction-btn buy ${form.direction === 'BUY' ? 'active' : ''}`}
          onClick={() => setForm({ ...form, direction: 'BUY' })}
        >
          买入
        </button>
        <button
          type="button"
          className={`direction-btn sell ${form.direction === 'SELL' ? 'active' : ''}`}
          onClick={() => setForm({ ...form, direction: 'SELL' })}
        >
          卖出
        </button>
      </div>

      {/* 股票搜索 — 代码+名称合一 */}
      <div className="form-field">
        <label>股票</label>
        {form.symbol ? (
          /* 已选中状态：显示标签 */
          <div className="stock-selected-tag">
            <span className="selected-symbol mono">{form.symbol}</span>
            <span className="selected-name">{form.name}</span>
            <button type="button" className="selected-clear" onClick={handleClearStock} title="重新选择">✕</button>
          </div>
        ) : (
          /* 未选中状态：搜索输入框 */
          <div className="stock-search-inline" ref={searchWrapperRef}>
            <div className="search-input-wrapper">
              <span className="search-icon">🔍</span>
              <input
                type="text"
                className="search-input"
                value={searchQuery}
                onChange={handleSearchChange}
                onKeyDown={handleSearchKeyDown}
                onFocus={() => searchQuery && setSearchOpen(true)}
                placeholder="输入代码、名称或拼音搜索..."
                autoComplete="off"
              />
              {searchLoading && <span className="search-spinner" />}
            </div>

            {searchOpen && searchResults.length > 0 && (
              <div className="search-dropdown" ref={listRef}>
                {searchResults.map((item, idx) => (
                  <div
                    key={item.symbol}
                    className={`search-result-item${idx === activeIdx ? ' active' : ''}`}
                    onClick={() => handleSelectStock(item)}
                    onMouseEnter={() => setActiveIdx(idx)}
                  >
                    <span className="result-symbol mono">{item.symbol}</span>
                    <span className="result-name">{item.name}</span>
                    <span className="result-market">{item.market || ''}</span>
                  </div>
                ))}
              </div>
            )}

            {searchOpen && searchResults.length === 0 && searchQuery && !searchLoading && (
              <div className="search-dropdown">
                <div className="search-empty">未找到匹配的股票</div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* 价格和数量 */}
      <div className="form-row">
        <div className="form-field">
          <label>成交价格</label>
          <input
            className="input mono"
            name="price"
            type="number"
            step="0.01"
            value={form.price}
            onChange={handleChange}
            placeholder="0.00"
          />
        </div>
        <div className="form-field">
          <label>成交数量</label>
          <input
            className="input mono"
            name="quantity"
            type="number"
            step="100"
            value={form.quantity}
            onChange={handleChange}
            placeholder="100"
          />
        </div>
        <div className="form-field">
          <label>金额</label>
          <div className="amount-display mono">
            ¥{amount > 0 ? amount.toLocaleString() : '—'}
          </div>
        </div>
      </div>

      {/* 交易原因 */}
      <div className="form-row">
        <div className="form-field" style={{ flex: 2 }}>
          <label>交易原因</label>
          <input
            className="input"
            name="reason_text"
            value={form.reason_text}
            onChange={handleChange}
            placeholder="为什么做这笔交易？"
          />
        </div>
        <div className="form-field">
          <label>原因分类</label>
          <select
            className="input"
            name="reason_category"
            value={form.reason_category}
            onChange={handleChange}
          >
            <option value="">选择...</option>
            <option value="CHAN_SIGNAL">缠论信号</option>
            <option value="FRIEND_TIP">朋友推荐</option>
            <option value="FEELING">感觉/直觉</option>
            <option value="OTHER">其他</option>
          </select>
        </div>
      </div>

      {/* 提交 */}
      {error && <div className="form-error">⚠ {error}</div>}

      <div className="form-actions">
        <button
          type="submit"
          className={`btn btn-primary ${form.direction === 'SELL' ? 'btn-sell' : ''}`}
          disabled={submitting}
        >
          {submitting ? '提交中...' : `确认${form.direction === 'BUY' ? '买入' : '卖出'}`}
        </button>
      </div>
    </form>
  )
}
