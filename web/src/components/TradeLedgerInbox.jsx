import { useEffect, useRef, useState } from 'react'
import { API_BASE } from '../config.js'
import './TradeLedgerInbox.css'

const STATUS_LABEL = {
  DRAFT: '有效',
  BLOCKED: '待处理',
  POSSIBLE_DUPLICATE: '疑似重复',
  CONFIRMED: '已入账',
}

export default function TradeLedgerInbox({ onConfirmed }) {
  const fileRef = useRef(null)
  const [tradeDate, setTradeDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [uploading, setUploading] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState('')
  const [batch, setBatch] = useState(null)
  const [drafts, setDrafts] = useState([])
  const [summary, setSummary] = useState([])
  const [previewUrl, setPreviewUrl] = useState('')

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl)
    }
  }, [previewUrl])

  const handleFile = async (file) => {
    if (!file) return
    setError('')
    setUploading(true)
    setPreviewUrl((current) => {
      if (current) URL.revokeObjectURL(current)
      return URL.createObjectURL(file)
    })
    try {
      const body = new FormData()
      body.append('file', file)
      const resp = await fetch(apiUrl(`/trade-imports/ths-summary?trade_date=${encodeURIComponent(tradeDate)}`), {
        method: 'POST',
        body,
      })
      const data = await resp.json()
      if (!resp.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : '截图识别失败')
      }
      setBatch(data.batch)
      setDrafts(data.drafts || [])
      setSummary(data.summary || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const handleDrop = (event) => {
    event.preventDefault()
    handleFile(event.dataTransfer.files?.[0])
  }

  const patchDraft = async (draftId, patch) => {
    setError('')
    const resp = await fetch(apiUrl(`/trade-imports/drafts/${draftId}`), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
    const data = await resp.json()
    if (!resp.ok) {
      throw new Error(typeof data.detail === 'string' ? data.detail : '更新草稿失败')
    }
    setDrafts(prev => prev.map(row => row.id === draftId ? data.draft : row))
  }

  const ignoreDraft = async (draft) => {
    setError('')
    try {
      const resp = await fetch(apiUrl(`/trade-imports/drafts/${draft.id}`), {
        method: 'DELETE',
      })
      const data = await resp.json()
      if (!resp.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : '忽略草稿失败')
      }
      setBatch(data.batch)
      setDrafts(data.drafts || [])
      setSummary(data.summary || [])
    } catch (err) {
      setError(err.message)
    }
  }

  const updateLocal = (draftId, patch) => {
    setDrafts(prev => prev.map(row => row.id === draftId ? { ...row, ...patch } : row))
  }

  const handleBlur = async (draft, field) => {
    try {
      const value = draft[field]
      const patchValue = field === 'quantity'
        ? parseInt(value || 0)
        : ['price', 'amount'].includes(field)
          ? (value === '' || value == null ? null : parseFloat(value))
          : value
      await patchDraft(draft.id, {
        [field]: patchValue,
      })
    } catch (err) {
      setError(err.message)
    }
  }

  const acknowledgeDuplicate = async (draft) => {
    updateLocal(draft.id, { duplicate_ack: !draft.duplicate_ack })
    try {
      await patchDraft(draft.id, { duplicate_ack: !draft.duplicate_ack })
    } catch (err) {
      setError(err.message)
    }
  }

  const blockingRows = drafts.filter(row =>
    row.status === 'BLOCKED' || (row.status === 'POSSIBLE_DUPLICATE' && !row.duplicate_ack)
  )
  const confirmableRows = drafts.filter(row => row.status !== 'CONFIRMED')
  const canConfirm = batch && confirmableRows.length > 0 && blockingRows.length === 0 && !confirming

  const confirmBatch = async () => {
    if (!batch) return
    setError('')
    setConfirming(true)
    try {
      const resp = await fetch(apiUrl(`/trade-imports/${batch.batch_id}/confirm`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ draft_ids: confirmableRows.map(row => row.id) }),
      })
      const data = await resp.json()
      if (!resp.ok) {
        const detail = data.detail
        throw new Error(typeof detail === 'string' ? detail : detail?.message || '确认入账失败')
      }
      setBatch(data.batch.batch)
      setDrafts(data.batch.drafts || [])
      setSummary(data.batch.summary || [])
      onConfirmed?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setConfirming(false)
    }
  }

  return (
    <div className="ledger-inbox">
      <div className="ledger-inbox-notice">
        <span className="ledger-status-dot" />
        汇总成交，缺少成交时间，仅用于持仓和日级行为统计。
      </div>

      <div className="ledger-inbox-grid">
        <aside className="ledger-upload-panel">
          <div className="ledger-date-row">
            <label>交易日期</label>
            <input type="date" value={tradeDate} onChange={e => setTradeDate(e.target.value)} />
          </div>

          <button
            type="button"
            className={`ledger-dropzone ${uploading ? 'is-loading' : ''}`}
            onClick={() => fileRef.current?.click()}
            onDrop={handleDrop}
            onDragOver={event => event.preventDefault()}
            disabled={uploading}
          >
            <span>{uploading ? '识别中' : '上传同花顺当日成交截图'}</span>
            <small>支持拖拽、点击选择 JPG/PNG/WebP</small>
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/webp"
            hidden
            onChange={e => handleFile(e.target.files?.[0])}
          />

          {batch && (
            <div className="ledger-batch-meta">
              <div><span>批次</span><strong>{batch.batch_id.slice(0, 8)}</strong></div>
              <div><span>状态</span><strong>{batch.status}</strong></div>
              <div><span>草稿</span><strong>{drafts.length} 行</strong></div>
            </div>
          )}
        </aside>

        <section className="ledger-draft-panel">
          <div className="ledger-table-header">
            <h3>待确认交易</h3>
            <span>{blockingRows.length > 0 ? `${blockingRows.length} 行需处理` : '可确认'}</span>
          </div>

          {error && <div className="ledger-error">{error}</div>}

          {drafts.length === 0 && !uploading ? (
            <div className="ledger-empty">
              <strong>今天的成交还没有进入 CT-OS</strong>
              <span>上传同花顺“当日成交”汇总截图，系统会先生成草稿，确认后才入账。</span>
            </div>
          ) : (
            <div className="ledger-table-wrap">
              <table className="ledger-table">
                <thead>
                  <tr>
                    <th>状态</th>
                    <th>股票</th>
                    <th>买卖</th>
                    <th>均价</th>
                    <th>数量</th>
                    <th>金额</th>
                    <th>置信</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {drafts.map(draft => (
                    <tr key={draft.id} className={`ledger-row ledger-row-${draft.status.toLowerCase()}`}>
                      <td><span className="ledger-pill">{STATUS_LABEL[draft.status] || draft.status}</span></td>
                      <td>
                        {draft.matched_candidates?.length > 0 && !draft.symbol ? (
                          <select
                            value=""
                            onChange={e => patchDraft(draft.id, { symbol: e.target.value })}
                          >
                            <option value="">选择代码</option>
                            {draft.matched_candidates.map(item => (
                              <option key={item.symbol} value={item.symbol}>{item.name} {item.symbol}</option>
                            ))}
                          </select>
                        ) : (
                          <input
                            value={draft.symbol || draft.name || ''}
                            onChange={e => updateLocal(draft.id, { symbol: e.target.value })}
                            onBlur={() => handleBlur(draft, 'symbol')}
                          />
                        )}
                        <small>{draft.name}</small>
                      </td>
                      <td>
                        <select
                          className={draft.direction === 'BUY' ? 'buy' : 'sell'}
                          value={draft.direction}
                          onChange={e => patchDraft(draft.id, { direction: e.target.value })}
                        >
                          <option value="BUY">买入</option>
                          <option value="SELL">卖出</option>
                        </select>
                      </td>
                      <td><input className="mono" value={draft.price} onChange={e => updateLocal(draft.id, { price: e.target.value })} onBlur={() => handleBlur(draft, 'price')} /></td>
                      <td><input className="mono" value={draft.quantity} onChange={e => updateLocal(draft.id, { quantity: e.target.value })} onBlur={() => handleBlur(draft, 'quantity')} /></td>
                      <td><input className="mono" value={draft.amount || ''} onChange={e => updateLocal(draft.id, { amount: e.target.value })} onBlur={() => handleBlur(draft, 'amount')} /></td>
                      <td className="mono">{Math.round((draft.confidence || 0) * 100)}%</td>
                      <td>
                        <div className="ledger-actions">
                          {draft.status === 'POSSIBLE_DUPLICATE' ? (
                            <label className="ledger-check">
                              <input type="checkbox" checked={draft.duplicate_ack} onChange={() => acknowledgeDuplicate(draft)} />
                              确认
                            </label>
                          ) : (
                            <span className="ledger-warnings">{draft.warnings?.[0] || '-'}</span>
                          )}
                          {draft.status !== 'CONFIRMED' && (
                            <button
                              type="button"
                              className="ledger-ignore-btn"
                              onClick={() => ignoreDraft(draft)}
                              title="从本次导入中忽略这一行"
                            >
                              忽略
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="ledger-footer">
            <div className="ledger-summary">
              {summary.slice(0, 4).map(item => (
                <span key={`${item.name}-${item.direction}`}>
                  {item.name} {item.direction === 'BUY' ? '买' : '卖'} {item.count} 笔 / {item.quantity} 股
                </span>
              ))}
            </div>
            <button className="btn btn-primary" type="button" disabled={!canConfirm} onClick={confirmBatch}>
              {confirming ? '入账中' : `确认入账 ${confirmableRows.length || 0} 行`}
            </button>
          </div>
        </section>

        <aside className="ledger-preview-panel">
          {previewUrl ? (
            <img src={previewUrl} alt="同花顺成交截图预览" />
          ) : (
            <div className="ledger-preview-empty">截图预览</div>
          )}
        </aside>
      </div>
    </div>
  )
}

function apiUrl(path) {
  const value = String(path || '')
  if (/^https?:\/\//.test(value)) return value
  if (value.startsWith('/api/')) {
    return `${API_BASE}${value.slice(4)}`
  }
  if (value.startsWith('/')) return `${API_BASE}${value}`
  return `${API_BASE}/${value}`
}
