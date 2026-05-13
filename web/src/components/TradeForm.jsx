import { useState, useRef, useEffect, useCallback } from 'react'
import { API_BASE } from '../config.js'
import { apiFetch } from '../api/client.js'
import VoiceInput from './VoiceInput.jsx'
import './TradeForm.css'

// ── A股手续费计算（与后端保持一致）──
function calcFee(price, quantity, direction, symbol) {
  if (!price || !quantity || price <= 0 || quantity <= 0) return null
  const amount = price * quantity
  const commission = Math.max(amount * 0.0005, 5)   // 佣金万5，最低5元
  const transferFee = amount * 0.00001               // 过户费万0.1
  const stampDuty = direction === 'SELL' ? amount * 0.001 : 0  // 印花税仅卖出
  return {
    commission: commission.toFixed(2),
    transferFee: transferFee.toFixed(2),
    stampDuty: stampDuty.toFixed(2),
    total: (commission + transferFee + stampDuty).toFixed(2),
  }
}

// 判断是否科创板/北交所（最小交易单位不是100股）
function isAStockSpecial(symbol) {
  const code = symbol.toLowerCase().replace(/^(sh|sz)/, '')
  return code.startsWith('688') || (code.startsWith('8') && !code.startsWith('68'))
}

export default function TradeForm({ onSubmitted }) {
  const [form, setForm] = useState({
    symbol: '',
    name: '',
    direction: 'BUY',
    price: '',
    quantity: '',
    reason_text: '',
    reason_category: '',
    plan_relationship: 'UNKNOWN',
    discipline_tag: '',
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [t1Warning, setT1Warning] = useState(false)  // T+1 提示

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

  // 当方向切换或股票变化时，重置T+1警告并重新检测
  useEffect(() => {
    setT1Warning(false)
    if (form.direction === 'SELL' && form.symbol) {
      // 查当日是否有买入记录（前端预检）
      apiFetch(`/api/trades?symbol=${encodeURIComponent(form.symbol)}&direction=BUY&limit=5`)
        .then(r => r.json())
        .then(data => {
          const today = new Date().toISOString().split('T')[0]
          const hasTodayBuy = (data.trades || []).some(t =>
            t.traded_at?.startsWith(today)
          )
          setT1Warning(hasTodayBuy)
        })
        .catch(() => {})
    }
  }, [form.direction, form.symbol])

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

  // ── 语音填充回调 ──
  const handleVoiceFill = (parsed) => {
    setForm(prev => ({
      ...prev,
      direction: parsed.direction || prev.direction,
      name: parsed.name || prev.name,
      price: parsed.price ? String(parsed.price) : prev.price,
      quantity: parsed.quantity ? String(parsed.quantity) : prev.quantity,
      // symbol 需要用户在搜索框确认，不直接覆盖（symbol_hint 仅参考）
    }))
    // 如果有股票名称提示，填入搜索框让用户确认
    if (parsed.name && !form.symbol) {
      setSearchQuery(parsed.name)
      doSearch(parsed.name)
    }
    setError('')
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')

    // 前端校验
    if (!form.symbol.trim()) return setError('请先搜索并选择一只股票')
    if (!form.price || parseFloat(form.price) <= 0) return setError('请输入有效价格')
    if (!form.quantity || parseInt(form.quantity) <= 0) return setError('请输入有效数量')

    // A股100手校验
    const qty = parseInt(form.quantity)
    if (!isAStockSpecial(form.symbol) && qty % 100 !== 0) {
      return setError(`A股最小交易单位为100股（1手），当前数量 ${qty} 不符合规格`)
    }

    setSubmitting(true)
    try {
      const resp = await apiFetch('/api/trades', {
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
          plan_relationship: form.plan_relationship || 'UNKNOWN',
          discipline_tag: form.discipline_tag.trim() || null,
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
        plan_relationship: 'UNKNOWN', discipline_tag: '',
      })
      setSearchQuery('')
      setT1Warning(false)
      onSubmitted?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const price = parseFloat(form.price) || 0
  const quantity = parseInt(form.quantity) || 0
  const amount = price * quantity
  const fee = calcFee(price, quantity, form.direction, form.symbol || '')

  return (
    <form className="trade-form" onSubmit={handleSubmit}>
      <div className="form-title-row">
        <h3 className="form-title">录入交易</h3>
        {/* 语音录入 */}
        <VoiceInput onFill={handleVoiceFill} />
      </div>

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

      {/* T+1 警告横幅 */}
      {t1Warning && (
        <div className="t1-warning-banner">
          ⚠ <strong>T+1 提示</strong>：今日已买入 <strong>{form.name || form.symbol}</strong>，
          按A股规则当日买入不可当日卖出，请注意风险。
        </div>
      )}

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
          <label>
            成交数量
            {form.symbol && isAStockSpecial(form.symbol)
              ? <span className="field-hint">（最小1股）</span>
              : <span className="field-hint">（最小100股）</span>
            }
          </label>
          <input
            className="input mono"
            name="quantity"
            type="number"
            step={form.symbol && isAStockSpecial(form.symbol) ? 1 : 100}
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

      {/* 手续费预估 */}
      {fee && (
        <div className="fee-estimate">
          <span className="fee-label">预计费用</span>
          <span className="fee-item">佣金 ¥{fee.commission}</span>
          <span className="fee-sep">+</span>
          <span className="fee-item">过户费 ¥{fee.transferFee}</span>
          {parseFloat(fee.stampDuty) > 0 && (
            <>
              <span className="fee-sep">+</span>
              <span className="fee-item fee-stamp">印花税 ¥{fee.stampDuty}</span>
            </>
          )}
          <span className="fee-total">= 共 ¥{fee.total}</span>
        </div>
      )}

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

      {/* 计划关系 */}
      <div className="form-row">
        <div className="form-field">
          <label>计划关系</label>
          <select
            className="input"
            name="plan_relationship"
            value={form.plan_relationship}
            onChange={handleChange}
          >
            <option value="UNKNOWN">未确认</option>
            <option value="PLANNED">计划内</option>
            <option value="UNPLANNED">计划外</option>
            <option value="EMOTIONAL">情绪交易</option>
            <option value="AFTER_ALERT">被提醒后执行</option>
            <option value="IGNORED_ALERT">忽略提醒后交易</option>
          </select>
        </div>
        <div className="form-field" style={{ flex: 2 }}>
          <label>纪律标签</label>
          <input
            className="input"
            name="discipline_tag"
            value={form.discipline_tag}
            onChange={handleChange}
            placeholder="例如：追高、怕回撤、按计划减仓"
          />
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
