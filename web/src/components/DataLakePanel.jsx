import { useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config.js'
import './DataLakePanel.css'

const FREQ_LABELS = { day: '日线', '60': '60m', '30': '30m', '15': '15m', '5': '5m' }
const FREQ_ORDER = ['day', '60', '30', '15', '5']

const fmtNum = (n) => n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n)
const fmtSize = (mb) => mb >= 1 ? `${mb.toFixed(1)} MB` : `${(mb * 1024).toFixed(0)} KB`

export default function DataLakePanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [cleaning, setCleaning] = useState(false)
  const [deleting, setDeleting] = useState(null)

  // 主动拉取表单状态
  const [fetchSymbol, setFetchSymbol] = useState('')
  const [fetchStart, setFetchStart] = useState('')
  const [fetchEnd, setFetchEnd] = useState('')
  const [fetchFreqs, setFetchFreqs] = useState(['day', '60', '30', '15', '5'])
  const [fetching, setFetching] = useState(false)

  const fetchOverview = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/lake/overview`)
      const json = await res.json()
      setData(json)
    } catch (e) {
      console.error('[Lake] fetch error:', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchOverview() }, [fetchOverview])

  const handleCleanup = async () => {
    setCleaning(true)
    try {
      const res = await fetch(`${API_BASE}/lake/cleanup`, { method: 'POST' })
      const json = await res.json()
      alert(`已清理 ${json.cleaned} 个孤儿文件，释放 ${json.freed_kb.toFixed(1)} KB`)
      fetchOverview()
    } catch (e) {
      console.error('[Cleanup] error:', e)
    } finally {
      setCleaning(false)
    }
  }

  const handleDelete = async (symbol) => {
    if (!confirm(`确认删除 ${symbol} 的所有本地缓存数据？`)) return
    setDeleting(symbol)
    try {
      const res = await fetch(`${API_BASE}/lake/${symbol}`, { method: 'DELETE' })
      const json = await res.json()
      if (json.deleted) fetchOverview()
    } catch (e) {
      console.error('[Delete] error:', e)
    } finally {
      setDeleting(null)
    }
  }

  const toggleFreq = (f) => {
    setFetchFreqs(prev => 
      prev.includes(f) ? prev.filter(x => x !== f) : [...prev, f]
    )
  }

  const handleFetch = async () => {
    if (!fetchSymbol) {
      alert("请输入股票代码")
      return
    }
    if (fetchFreqs.length === 0) {
      alert("请至少选择一个周期")
      return
    }
    setFetching(true)
    try {
      const payload = {
        symbol: fetchSymbol,
        freqs: fetchFreqs,
        start_date: fetchStart || null,
        end_date: fetchEnd || null
      }
      await fetch(`${API_BASE}/lake/fetch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      // 不需要 await 等待太久，后台会慢慢去下
      alert("拉取指令已提交后台，可多次刷新概览查看入库进度。")
      setFetchSymbol('')
      setTimeout(() => fetchOverview(), 2000)
    } catch (e) {
      console.error('[Manual Fetch Error]', e)
      alert("发送拉取请求失败")
    } finally {
      setFetching(false)
    }
  }

  if (loading && !data) {
    return (
      <div className="lake-loading">
        <div className="lake-spinner"></div>
        <span>扫描数据湖...</span>
      </div>
    )
  }

  if (!data) {
    return <div className="lake-empty-msg">加载失败，请重试</div>
  }

  return (
    <div className="lake-panel">
      {/* 统计卡片 */}
      <div className="lake-stats">
        <div className="lake-stat-card">
          <div className="lake-stat-value blue">{data.total_stocks}</div>
          <div className="lake-stat-label">已缓存股票</div>
        </div>
        <div className="lake-stat-card">
          <div className="lake-stat-value green">{fmtNum(data.total_bars)}</div>
          <div className="lake-stat-label">K线总条数</div>
        </div>
        <div className="lake-stat-card">
          <div className="lake-stat-value purple">{fmtSize(data.disk_mb)}</div>
          <div className="lake-stat-label">磁盘占用</div>
        </div>
        <div className="lake-stat-card">
          <div className={`lake-stat-value ${data.orphan_files > 0 ? 'amber' : 'muted'}`}>
            {data.orphan_files}
          </div>
          <div className="lake-stat-label">孤儿文件</div>
          {data.orphan_files > 0 && (
            <button className="lake-cleanup-btn" onClick={handleCleanup} disabled={cleaning}>
              {cleaning ? '清理中…' : '[ 一键清理 ]'}
            </button>
          )}
        </div>
      </div>

      {/* 初始化主动拉取工具 */}
      <div className="lake-fetch-form">
        <h4>主动回填数据中心</h4>
        <div className="lake-fetch-inputs">
          <input 
              type="text" 
              className="lake-input"
              placeholder="股票代码 (如 sh.600519)" 
              value={fetchSymbol}
              onChange={e => setFetchSymbol(e.target.value)}
          />
          <input 
              type="date"
              className="lake-input"
              value={fetchStart}
              onChange={e => setFetchStart(e.target.value)}
              title="可选: 起始日期"
          />
          <span className="lake-date-sep">至</span>
          <input 
              type="date"
              className="lake-input"
              value={fetchEnd}
              onChange={e => setFetchEnd(e.target.value)}
              title="可选: 结束日期"
          />
        </div>
        
        <div className="lake-fetch-actions">
          <div className="lake-freq-checkboxes">
             {FREQ_ORDER.map(f => (
                 <label key={f} className="lake-checkbox-label">
                     <input 
                       type="checkbox" 
                       checked={fetchFreqs.includes(f)} 
                       onChange={() => toggleFreq(f)} 
                     />
                     {FREQ_LABELS[f]}
                 </label>
             ))}
          </div>
          <button className="lake-fetch-submit" onClick={handleFetch} disabled={fetching || !fetchSymbol}>
              {fetching ? '发送请求中...' : '一键拉取'}
          </button>
        </div>
      </div>

      {/* 工具栏 */}
      <div className="lake-toolbar">
        <span className="lake-toolbar-label">
          LAKE · {data.total_stocks} symbols · {FREQ_ORDER.length} periods
        </span>
        <button className="lake-refresh-btn" onClick={fetchOverview}>
          [ 🔄 刷新面板 ]
        </button>
      </div>

      {/* 详情表格 */}
      <div className="lake-table-wrap">
        <table className="lake-table">
          <thead>
            <tr>
              <th className="left">股票</th>
              {FREQ_ORDER.map(f => (
                <th key={f} className="center">{FREQ_LABELS[f]}</th>
              ))}
              <th className="right">操作</th>
            </tr>
          </thead>
          <tbody>
            {data.stocks.map(stock => (
              <tr key={stock.symbol}>
                <td className="symbol">{stock.symbol}</td>
                {FREQ_ORDER.map(freq => {
                  const info = stock.periods?.[freq]
                  const count = info?.count || 0
                  return (
                    <td key={freq} className="center">
                      {count > 0 ? (
                        <div>
                          <div className="lake-bar-count">{fmtNum(count)}</div>
                          <div className="lake-bar-date">
                            ~{info.last?.split(' ')[0]?.slice(5) || ''}
                          </div>
                        </div>
                      ) : (
                        <span className="lake-empty-period">—</span>
                      )}
                    </td>
                  )
                })}
                <td className="right">
                  <button
                    className="lake-delete-btn"
                    onClick={() => handleDelete(stock.symbol)}
                    disabled={deleting === stock.symbol}
                    title={`删除 ${stock.symbol} 的所有缓存数据`}
                  >
                    {deleting === stock.symbol ? '…' : '🗑'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.stocks.length === 0 && (
          <div className="lake-empty-msg">数据湖为空，请先添加自选股或主动拉取数据</div>
        )}
      </div>
    </div>
  )
}
