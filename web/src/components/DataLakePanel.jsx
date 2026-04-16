import { useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config.js'
import './DataLakePanel.css'

const FREQ_LABELS = { week: '周线', day: '日线', '60': '60m', '30': '30m', '15': '15m', '5': '5m' }
const FREQ_ORDER = ['week', 'day', '60', '30', '15', '5']

const fmtNum = (n) => n >= 1000 ? `${(n / 1000).toFixed(1)}K` : String(n)
const fmtSize = (mb) => mb >= 1 ? `${mb.toFixed(1)} MB` : `${(mb * 1024).toFixed(0)} KB`

// 获取前N年的YMD格式
const getYearsAgoStr = (years) => {
  const d = new Date()
  d.setFullYear(d.getFullYear() - years)
  return d.toISOString().split('T')[0]
}

export default function DataLakePanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [cleaning, setCleaning] = useState(false)
  const [deleting, setDeleting] = useState(null)

  // 主动拉取表单状态
  const [fetchSymbol, setFetchSymbol] = useState('')
  const [fetchStart, setFetchStart] = useState('')
  const [fetchEnd, setFetchEnd] = useState('')
  const [fetchFreqs, setFetchFreqs] = useState(['week', 'day', '60', '30', '15', '5'])
  const [fetching, setFetching] = useState(false)
  
  // 批量拉取UI状态
  const [batching, setBatching] = useState(false)

  // 持久化通知 (替代 alert，不会被 re-render 打断)
  const [toast, setToast] = useState(null)

  const fetchOverview = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/lake/overview`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
    } catch (e) {
      console.error('[Lake] fetch error:', e)
      // 如果后端锁表抛错，保留旧数据不让页面崩溃
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
      setToast({ type: 'success', msg: `已清理 ${json.cleaned} 个孤儿文件，释放 ${json.freed_kb.toFixed(1)} KB` })
      fetchOverview()
    } catch (e) {
      console.error('[Cleanup] error:', e)
    } finally {
      setCleaning(false)
    }
  }

  const handleDelete = async (symbol) => {
    if (!window.confirm(`确认删除 ${symbol} 的所有本地缓存数据？`)) return
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

  // 时间胶囊预设
  const handlePresetTime = (type) => {
    if (type === 'all') {
      setFetchStart('')
      setFetchEnd('')
    } else if (type === '1y') {
      setFetchStart(getYearsAgoStr(1))
      setFetchEnd('')
    } else if (type === '3y') {
      setFetchStart(getYearsAgoStr(3))
      setFetchEnd('')
    }
  }

  // 基础API拉取调用（供表单和快捷键复用）
  const sendFetchRequest = async (symbol, freqs, start, end) => {
    const payload = {
      symbol: symbol,
      freqs: freqs,
      start_date: start || null,
      end_date: end || null
    }
    await fetch(`${API_BASE}/lake/fetch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
  }

  // 表单完整拉取提交
  const handleFormFetch = async (e) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (!fetchSymbol) {
      setToast({ type: 'error', msg: '请输入股票代码' })
      return
    }
    if (fetchFreqs.length === 0) {
      setToast({ type: 'error', msg: '请至少选择一个周期' })
      return
    }
    setFetching(true)
    try {
      await sendFetchRequest(fetchSymbol, fetchFreqs, fetchStart, fetchEnd)
      setToast({ type: 'success', msg: '拉取指令已提交后台，可多次刷新概览查看入库进度。' })
      setFetchSymbol('')
      setTimeout(() => fetchOverview(), 2000)
    } catch (e) {
      console.error('[Manual Fetch Error]', e)
      setToast({ type: 'error', msg: '发送拉取请求失败' })
    } finally {
      setFetching(false)
    }
  }

  // 表格单行全量回填
  const handleInlineFetch = async (e, symbol) => {
    e?.preventDefault()
    e?.stopPropagation()
    try {
      await sendFetchRequest(symbol, ['week', 'day', '60', '30', '15', '5'], '', '')
      setToast({ type: 'success', msg: `已提交 [${symbol}] 全量历史抓取指令！` })
      setTimeout(() => fetchOverview(), 1500)
    } catch (e) {
      console.error('[Inline Fetch Error]', e)
      setToast({ type: 'error', msg: '抓取指令发送失败' })
    }
  }

  // 全局回填
  const handleBatchSyncAll = async (e) => {
    e?.preventDefault()
    e?.stopPropagation()
    if (!data || !data.stocks || data.stocks.length === 0) return

    setBatching(true)
    try {
      // 遍历并发发送，BackgroundTasks 和 ThreadPool 会自动排队
      await Promise.all(data.stocks.map(st => 
        sendFetchRequest(st.symbol, ['week', 'day', '60', '30', '15', '5'], '', '')
      ))
      setToast({ type: 'success', msg: `已成功投递 ${data.stocks.length} 个同步任务！请稍后刷新观察行数结晶。` })
    } catch (e) {
      console.error('[Batch Fetch Error]', e)
      setToast({ type: 'error', msg: '批量抓取发生异常' })
    } finally {
      setBatching(false)
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
      {/* 持久通知条 */}
      {toast && (
        <div className={`lake-toast ${toast.type}`} onClick={() => setToast(null)}>
          <span>{toast.type === 'success' ? '✅' : '❌'} {toast.msg}</span>
          <button className="lake-toast-close" onClick={() => setToast(null)}>✕</button>
        </div>
      )}
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
        <div className="lake-fetch-header">
          <h4>🧲 数据猎手引擎</h4>
          <span className="lake-hint">全新引入标的时使用，已有标的请点下方[回填]</span>
        </div>
        
        <div className="lake-fetch-inputs">
          <input 
              type="text" 
              className="lake-input lake-symbol-input"
              placeholder="代码 (e.g. sh.600519)" 
              value={fetchSymbol}
              onChange={e => setFetchSymbol(e.target.value)}
          />
          
          {/* 时间区间与其快捷方案放在一起 */}
          <div className="lake-time-group">
            <input 
                type="date"
                className="lake-input lake-date"
                value={fetchStart}
                onChange={e => setFetchStart(e.target.value)}
                title="起始日期"
            />
            <span className="lake-date-sep"></span>
            <input 
                type="date"
                className="lake-input lake-date"
                value={fetchEnd}
                onChange={e => setFetchEnd(e.target.value)}
                title="结束日期"
            />
            
            <div className="lake-capsules">
              <button className={`lake-capsule ${!fetchStart && !fetchEnd ? 'active' : ''}`} onClick={() => handlePresetTime('all')}>全量历史</button>
              <button className="lake-capsule" onClick={() => handlePresetTime('1y')}>近期1年</button>
              <button className="lake-capsule" onClick={() => handlePresetTime('3y')}>近期3年</button>
            </div>
          </div>
        </div>
        
        <div className="lake-fetch-actions">
          <div className="lake-freq-checkboxes">
             <span className="lake-freq-label">采集精度:</span>
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
          <button type="button" className="lake-fetch-submit" onClick={handleFormFetch} disabled={fetching || !fetchSymbol}>
              {fetching ? '发送请求中...' : '+ 新增截获'}
          </button>
        </div>
      </div>

      {/* 工具栏 */}
      <div className="lake-toolbar">
        <div className="lake-toolbar-left">
          <span className="lake-toolbar-label">
            LAKE INVENTORY · {data.total_stocks} STOCKS
          </span>
          <button className="lake-refresh-btn" onClick={fetchOverview}>
            [ 🔄 刷新面板 ]
          </button>
        </div>
        
        {data.stocks && data.stocks.length > 0 && (
          <button 
            className="lake-batch-btn" 
            onClick={handleBatchSyncAll} 
            disabled={batching}
          >
            {batching ? '指令洪流分发中...' : '🚀 [ 全局自动化追平/同步所有记录 ]'}
          </button>
        )}
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
              <th className="right">状态/操作</th>
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
                            {info.last?.split(' ')[0]?.slice(5) || ''}
                          </div>
                        </div>
                      ) : (
                        <span className="lake-empty-period">—</span>
                      )}
                    </td>
                  )
                })}
                <td className="right">
                  <div className="lake-row-actions">
                    <button
                      type="button"
                      className="lake-inline-fetch-btn"
                      onClick={(e) => handleInlineFetch(e, stock.symbol)}
                      title={`无脑一键全量补充 ${stock.symbol}`}
                    >
                      ⏬ 回填
                    </button>
                    <button
                      className="lake-delete-btn"
                      onClick={() => handleDelete(stock.symbol)}
                      disabled={deleting === stock.symbol}
                      title={`清除 ${stock.symbol}`}
                    >
                      {deleting === stock.symbol ? '…' : '🗑'}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.stocks.length === 0 && (
          <div className="lake-empty-msg">数据湖为空，请通过上方猎手引擎抓取标的</div>
        )}
      </div>
    </div>
  )
}
