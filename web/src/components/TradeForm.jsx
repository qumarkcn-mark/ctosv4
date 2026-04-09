import { useState } from 'react'
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

  const handleChange = (e) => {
    setForm({ ...form, [e.target.name]: e.target.value })
    setError('')
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')

    // 校验
    if (!form.symbol.trim()) return setError('请输入股票代码')
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

      {/* 股票信息 */}
      <div className="form-row">
        <div className="form-field">
          <label>股票代码</label>
          <input
            className="input"
            name="symbol"
            value={form.symbol}
            onChange={handleChange}
            placeholder="如 sh600519"
          />
        </div>
        <div className="form-field">
          <label>股票名称</label>
          <input
            className="input"
            name="name"
            value={form.name}
            onChange={handleChange}
            placeholder="如 贵州茅台"
          />
        </div>
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
