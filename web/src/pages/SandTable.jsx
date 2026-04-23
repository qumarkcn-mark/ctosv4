import { useState, useCallback, useRef, useEffect } from 'react'
import { init, dispose, registerIndicator } from 'klinecharts'
import { renderChanOverlays } from '../plugins/chanOverlay.js'
import '../components/KlineChart.css'
import './SandTable.css'
import { API_BASE } from '../config.js'

function toTimestamp(timeStr) {
  if (!timeStr) return 0
  return new Date(timeStr.trim().replace(' ', 'T')).getTime()
}

const FREQ_CONFIG = {
  week: { label: '周线', span: 1, type: 'day' },
  day:  { label: '日线', span: 1, type: 'day' },
  '60': { label: '60分', span: 60, type: 'minute' },
  '30': { label: '30分', span: 30, type: 'minute' },
  '15': { label: '15分', span: 15, type: 'minute' },
}

const SPEEDS = [
  { label: '0.5x', ms: 2000 },
  { label: '1x',   ms: 1000 },
  { label: '2x',   ms: 500  },
  { label: '3x',   ms: 333  },
]

// ── 模块级买卖点存储 ────────────────────────────────────────────
const _BS = {}  // { timestamp_ms: { type:'BUY'|'SELL', price } }
let _bsRegistered = false

function ensureBSIndicator() {
  if (_bsRegistered) return
  _bsRegistered = true
  try {
    registerIndicator({
      name: 'BSMarkers', shortName: '', figures: [
        { key: 'b', title: '', type: 'circle', styles: () => ({ style: 'fill', color: '#e1293d', size: 5 }) },
        { key: 's', title: '', type: 'circle', styles: () => ({ style: 'fill', color: '#2baf73', size: 5 }) },
      ],
      calc: (list) => list.map(item => {
        const m = _BS[item.timestamp]
        return { b: m?.type === 'BUY' ? m.price : null, s: m?.type === 'SELL' ? m.price : null }
      }),
    })
  } catch (e) { console.warn('BSMarkers register failed', e) }
}

// ── 图表样式（A股红涨绿跌，隐藏日期轴）──────────────────────────
function makeStyles() {
  return {
    grid: { show: true, horizontal: { color: 'rgba(255,255,255,0.03)' }, vertical: { color: 'rgba(255,255,255,0.03)' } },
    candle: {
      type: 'candle_solid',
      bar: { upColor:'#ef5350', downColor:'#26a69a', noChangeColor:'#888', upBorderColor:'#ef5350', downBorderColor:'#26a69a', upWickColor:'#ef5350', downWickColor:'#26a69a' },
      tooltip: { title: { template: ' ' }, legend: { template: [
        { title: 'O ', value: '{open}' }, { title: 'H ', value: '{high}' },
        { title: 'L ', value: '{low}'  }, { title: 'C ', value: '{close}' },
        { title: 'V ', value: '{volume}' },
      ]}},
    },
    indicator: {
      bars:  [{ upColor: 'rgba(239,83,80,0.8)', downColor: 'rgba(38,166,154,0.8)', noChangeColor: '#888' }],
      lines: [{ color: '#ffd54f' }, { color: '#42a5f5' }, { color: '#ab47bc' }],
    },
    crosshair: {
      show: true,
      horizontal: { line: { color: 'rgba(234,179,8,0.35)', style: 'dash' }, text: { color: '#0a0a0f', backgroundColor: '#f0b90b' } },
      vertical:   { line: { color: 'rgba(234,179,8,0.35)', style: 'dash' }, text: { show: false } },
    },
    xAxis: { show: false },
  }
}

// ── 初始化一个图表实例 ───────────────────────────────────────────
function initChart(el, klines, period, indicators = ['MA', 'VOL', 'MACD']) {
  const chart = init(el, { styles: makeStyles() })
  if (indicators.includes('MA'))   chart.createIndicator('MA',   false, { id: 'candle_pane' })
  if (indicators.includes('VOL'))  chart.createIndicator('VOL',  false, { id: 'pane_vol'    })
  if (indicators.includes('MACD')) chart.createIndicator('MACD', false, { id: 'pane_sub'    })
  try { if (indicators.includes('BS')) chart.createIndicator('BSMarkers', false, { id: 'candle_pane' }) } catch(e){}
  return chart
}

export default function SandTable() {
  const [taskState,     setTaskState]     = useState('IDLE')
  const [sessionData,   setSessionData]   = useState(null)
  const [selectedFreq,  setSelectedFreq]  = useState('day')
  const [cash,          setCash]          = useState(1000000)
  const [shares,        setShares]        = useState(0)
  const [avgCost,       setAvgCost]       = useState(0)
  const [trades,        setTrades]        = useState([])
  const [currentKline,  setCurrentKline]  = useState(null)
  const [scores,        setScores]        = useState(null)
  const [isPlaying,     setIsPlaying]     = useState(false)
  const [speedIdx,      setSpeedIdx]      = useState(1)       // 默认 1x
  const [indics,        setIndics]        = useState({ ma: true, vol: true, macd: true, chan: true })
  const [isLoadingData, setIsLoadingData] = useState(false)

  // Refs
  const cashRef        = useRef(1000000)
  const sharesRef      = useRef(0)
  const avgCostRef     = useRef(0)
  const tradesRef      = useRef([])
  const klineRef       = useRef(null)
  const sdRef          = useRef(null)
  const advancingRef   = useRef(false)
  const playTimerRef   = useRef(null)
  const freqRef        = useRef('day')

  useEffect(() => { cashRef.current    = cash        }, [cash])
  useEffect(() => { sharesRef.current  = shares      }, [shares])
  useEffect(() => { avgCostRef.current = avgCost     }, [avgCost])
  useEffect(() => { tradesRef.current  = trades      }, [trades])
  useEffect(() => { klineRef.current   = currentKline}, [currentKline])
  useEffect(() => { sdRef.current      = sessionData }, [sessionData])
  useEffect(() => { freqRef.current    = selectedFreq}, [selectedFreq])

  const chartElRef     = useRef(null)
  const chartRef       = useRef(null)
  const klcRef         = useRef([])

  // ── 获取最新数据与缠论结构 ───────────────────────────────────────
  const fetchChanData = useCallback(async (isSwitchingFreq = false) => {
    const sd = sdRef.current
    const freq = freqRef.current
    if (!sd) return

    try {
      const res = await fetch(`${API_BASE}/sand-table/chan-detail?task_id=${sd.task_id}&freq=${freq}`)
      const json = await res.json()
      if (!res.ok) return
      
      const { klines, bis, segs, bi_zhongshus, bi_zhongshus_decomp, seg_zhongshus, bsps } = json.data
      if (!klines || !klines.length) return

      const isDay = freq === 'day' || freq === 'week'
      const klcData = klines.map((k) => ({
        timestamp: toTimestamp(k.time), open: k.open, high: k.high, low: k.low, close: k.close, volume: k.volume,
      }))
      
      setCurrentKline(klines[klines.length - 1])
      klcRef.current = klcData

      const chart = chartRef.current
      if (chart) {
        if (isSwitchingFreq) {
          // 如果是切换级别，直接全量替换数据，这样缩放会重置
          chart.applyNewData(klcData)
          chart.setPeriod({ span: FREQ_CONFIG[freq].span, type: FREQ_CONFIG[freq].type })
          chart.resize() // 强制图表重绘尺寸，防止初始大小为0
        } else {
          // 如果是步进，仅更新最新一根 K 线（保持用户的缩放/平移不变）
          chart.updateData(klcData[klcData.length - 1])
        }

        // 更新缠论 Overlay
        if (indics.chan) {
          chart.removeOverlay({ groupId: 'chan_bi_group' })
          chart.removeOverlay({ groupId: 'chan_seg_group' })
          chart.removeOverlay({ groupId: 'chan_bi_zs_group' })
          chart.removeOverlay({ groupId: 'chan_seg_zs_group' })
          
          const filteredData = { bis, segs, bi_zhongshus, bi_zhongshus_decomp: bi_zhongshus_decomp || [], seg_zhongshus, bsps: bsps || [] }
          renderChanOverlays(chart, filteredData, isDay, false)
        }
      }
    } catch(e) { console.error("fetchChanData failed", e) }
  }, [indics.chan])

  // ── 监听 selectedFreq 切换 ───────────────────────────────────────
  useEffect(() => {
    // 等图表创建完毕并且是在 TRAINING 状态下，再拉取数据
    if (taskState === 'TRAINING' && chartRef.current) {
      setIsLoadingData(true)
      fetchChanData(true).finally(() => setIsLoadingData(false))
    }
  }, [selectedFreq, fetchChanData, taskState])

  // ── 初始化图表 ──────────────────────────────────────────────────
  useEffect(() => {
    if (taskState !== 'TRAINING') return
    ensureBSIndicator()

    if (!chartElRef.current) return
    if (!chartRef.current) {
      const freqCfg = FREQ_CONFIG[selectedFreq] || FREQ_CONFIG.day
      const chart = initChart(chartElRef.current, [], { span: freqCfg.span, type: freqCfg.type }, ['MA','VOL','MACD','BS'])
      chartRef.current = chart
      // 如果 sessionData 还没有准备好 symbol，使用默认值
      chart.setSymbol({ ticker: sessionData?.symbol || 'Unknown' })
      
      // 初始化图表后，触发第一次数据拉取
      setIsLoadingData(true)
      fetchChanData(true).finally(() => setIsLoadingData(false))
    }

    return () => {
      if (chartElRef.current && chartRef.current) { 
        dispose(chartElRef.current); 
        chartRef.current = null 
      }
    }
  }, [taskState]) // 仅依赖 taskState，保证只初始化一次

  // ── 指标切换 ────────────────────────────────────────────────────
  const toggleIndic = useCallback((key) => {
    const chart = chartRef.current
    if (!chart) return
    
    setIndics(prev => {
      const next = { ...prev, [key]: !prev[key] }
      
      if (key === 'chan') {
        if (!next[key]) {
          chart.removeOverlay({ groupId: 'chan_bi_group' })
          chart.removeOverlay({ groupId: 'chan_seg_group' })
          chart.removeOverlay({ groupId: 'chan_bi_zs_group' })
          chart.removeOverlay({ groupId: 'chan_seg_zs_group' })
        } else {
          fetchChanData(false) // 重新拉取并绘制
        }
      } else {
        const map = { ma: ['MA','candle_pane'], vol: ['VOL','pane_vol'], macd: ['MACD','pane_sub'] }
        const [name, pane] = map[key]
        if (next[key]) chart.createIndicator(name, false, { id: pane })
        else           chart.removeIndicator(pane, name)
      }
      return next
    })
  }, [fetchChanData])

  const refreshBS = useCallback(() => {
    const chart = chartRef.current
    if (!chart) return
    try { chart.removeIndicator('candle_pane', 'BSMarkers') } catch(e){}
    try { chart.createIndicator('BSMarkers', false, { id: 'candle_pane' }) } catch(e){}
  }, [])

  // ── handleFinish ────────────────────────────────────────────────
  const handleFinish = useCallback(() => {
    const sd    = sdRef.current
    const kline = klineRef.current
    if (!sd || !kline) return
    let fCash = cashRef.current, fShares = sharesRef.current
    let fTrades = [...tradesRef.current]
    const endP = kline.close, startP = sd.initialClose
    if (fShares > 0) {
      fCash += fShares * endP
      fTrades.push({ type:'SELL', price:endP, shares:fShares, date:kline.time+' (自动清盘)' })
      fShares = 0
    }
    const totalReturn = (fCash - 1000000) / 1000000
    const benchReturn = (endP - startP) / startP
    let wins=0, lastBuy=0
    fTrades.forEach(t => { if(t.type==='BUY') lastBuy=t.price; else if(t.price>lastBuy) wins++ })
    const sells = fTrades.filter(t=>t.type==='SELL').length
    setCash(fCash); setShares(fShares); setTrades(fTrades)
    setScores({ totalReturn, benchReturn, excessReturn: totalReturn-benchReturn, winRate: sells>0?wins/sells:0, tradeCount: fTrades.length })
    setTaskState('FINISHED')
    setIsPlaying(false)
  }, [])

  // ── handleAdvance ───────────────────────────────────────────────
  const handleAdvance = useCallback(async () => {
    const sd = sdRef.current
    const freq = freqRef.current
    if (!sd || advancingRef.current) return
    advancingRef.current = true
    try {
      const res  = await fetch(`${API_BASE}/sand-table/advance`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ task_id: sd.task_id, freq }) })
      const data = await res.json()
      if (!res.ok) return
      
      if (data.is_end) {
        setTimeout(() => handleFinish(), 50); 
        return
      }

      // 更新虚拟时间后，重新拉取当前级别的 500 根数据 + 缠论结构
      await fetchChanData(false)
      
    } catch(e) { console.error(e) }
    finally { advancingRef.current = false }
  }, [handleFinish, fetchChanData])

  // ── 自动播放 ────────────────────────────────────────────────────
  useEffect(() => {
    if (isPlaying && taskState === 'TRAINING') {
      const ms = SPEEDS[speedIdx].ms
      playTimerRef.current = setInterval(() => handleAdvance(), ms)
      return () => clearInterval(playTimerRef.current)
    }
    clearInterval(playTimerRef.current)
  }, [isPlaying, speedIdx, taskState, handleAdvance])

  // ── 键盘快捷键 ──────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e) => {
      if (taskState !== 'TRAINING') return
      if (e.code === 'Space' && !e.repeat) { e.preventDefault(); if (!isPlaying) handleAdvance() }
      if (e.code === 'KeyP' && !e.repeat)  { e.preventDefault(); setIsPlaying(p => !p) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [taskState, isPlaying, handleAdvance])

  // ── handleStartTask ─────────────────────────────────────────────
  const handleStartTask = async (poolConfig) => {
    setTaskState('LOADING')
    setIsPlaying(false)
    setSelectedFreq('day') // 每次开始强制重置为日线
    freqRef.current = 'day'
    Object.keys(_BS).forEach(k => delete _BS[k])

    let watchlist = []
    try { watchlist = JSON.parse(localStorage.getItem('ct_watchlist_v4')||'[]').flatMap(g=>g.stocks.map(s=>s.symbol)) } catch(e){}

    try {
      const res  = await fetch(`${API_BASE}/sand-table/start`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ pool_symbols: watchlist, pool: poolConfig }),
      })
      const data = await res.json()
      if (res.ok) {
        setSessionData({ ...data, freq: selectedFreq, initialClose: 1.0 }) // initialClose 会在首次 fetchChanData 后覆盖
        setCash(1000000); setShares(0); setAvgCost(0); setTrades([])
        setTaskState('TRAINING')
      } else { alert('启动失败：'+data.detail); setTaskState('IDLE') }
    } catch(e) { alert('网络错误'); setTaskState('IDLE') }
  }

  // 获取了第一根 K 线后修正 initialClose
  useEffect(() => {
    if (taskState === 'TRAINING' && currentKline && sessionData?.initialClose === 1.0) {
      setSessionData(prev => ({ ...prev, initialClose: currentKline.close }))
    }
  }, [currentKline, taskState, sessionData])

  // ── 买卖操作 ────────────────────────────────────────────────────────
  // 分仓买入
  const handleBuy = (pct) => {
    if (cash <= 0 || !currentKline) return
    const price = currentKline.close
    const lots  = Math.floor((cash * pct) / price / 100) * 100
    if (lots === 0) return
    const cost = lots * price
    const newS  = shares + lots
    const newA  = (avgCost * shares + cost) / newS
    setCash(c => c - cost); setShares(newS); setAvgCost(newA)
    const ts = toTimestamp(currentKline.time)
    _BS[ts] = { type: 'BUY', price }
    refreshBS()
    setTrades(t => [...t, { type:'BUY', price, shares:lots, pct:Math.round(pct*100), date:currentKline.time, ts }])
  }

  // 分仓卖出 (先进先出，由于是统一均价，按比例或全部卖)
  const handleSell = (pct) => {
    if (shares <= 0 || !currentKline) return
    const price = currentKline.close
    const sellShares = Math.floor(shares * pct / 100) * 100
    if (sellShares === 0) return
    const newS = shares - sellShares
    setCash(c => c + sellShares * price); 
    setShares(newS); 
    if (newS === 0) setAvgCost(0)
    
    const ts = toTimestamp(currentKline.time)
    _BS[ts] = { type: 'SELL', price }
    refreshBS()
    setTrades(t => [...t, { type:'SELL', price, shares:sellShares, date:currentKline.time, ts }])
  }

  // ── 派生数据 ──────────────────────────────────────────────────────
  const freqCfg   = FREQ_CONFIG[selectedFreq] || FREQ_CONFIG.day
  const total     = cash + shares * (currentKline?.close || 0)
  const floatPnl  = shares > 0 && avgCost > 0
    ? ((currentKline?.close - avgCost) / avgCost * 100).toFixed(2) : null

  return (
    <div className="sand-table">
      {/* IDLE */}
      {taskState === 'IDLE' && (
        <div className="st-idle-panel animate-fade-in">
          <h2>推演沙盘 V2</h2>
          <p>盲测环境 · 虚拟时光机 · 缠论原构重现</p>
          <div className="st-pool-selection">
            <button className="btn btn-primary" onClick={() => handleStartTask('custom')}>使用自选股池</button>
            <button className="btn"             onClick={() => handleStartTask('all')}>使用全部股池</button>
          </div>
        </div>
      )}

      {/* LOADING */}
      {taskState === 'LOADING' && (
        <div className="st-loading-panel">
          <span className="animate-pulse">部署时光机中...</span>
        </div>
      )}

      {/* TRAINING / FINISHED */}
      {(taskState === 'TRAINING' || taskState === 'FINISHED') && (
        <div className="st-main-view">

          {/* 顶栏 */}
          <div className="st-topbar">
            <div className="st-topbar-left" style={{gap: '12px'}}>
              <h3>盲测中</h3>
              <div className="st-freq-tabs">
                {Object.entries(FREQ_CONFIG).map(([f, cfg]) => (
                  <button 
                    key={f} 
                    className={`st-freq-tab ${selectedFreq===f?'active':''}`} 
                    onClick={() => setSelectedFreq(f)}
                    disabled={taskState === 'FINISHED' || isLoadingData}
                  >
                    {cfg.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="st-topbar-center">
              {taskState === 'TRAINING' && (
                <>
                  <button className={`btn st-play-btn ${isPlaying?'active':''}`} onClick={() => setIsPlaying(p=>!p)}>
                    {isPlaying ? '⏸ 暂停' : '▶ 自动'} (P)
                  </button>
                  {SPEEDS.map((s,i) => (
                    <button key={i} className={`btn st-speed-btn ${speedIdx===i?'active':''}`} onClick={() => setSpeedIdx(i)}>{s.label}</button>
                  ))}
                </>
              )}
            </div>
            <div className="st-topbar-right">
              {taskState === 'TRAINING' && (
                <div className="st-indic-toggles">
                  {['chan', 'ma', 'vol', 'macd'].map(k => (
                    <button key={k} className={`btn st-indic-btn ${indics[k]?'active':''}`} onClick={()=>toggleIndic(k)}>
                      {k === 'chan' ? '缠论' : k.toUpperCase()}
                    </button>
                  ))}
                  <button className="btn st-indic-btn" style={{marginLeft: '12px', background: 'rgba(239, 68, 68, 0.15)', color: '#ef4444'}} onClick={handleFinish}>
                    结束并复盘
                  </button>
                </div>
              )}
              {taskState === 'FINISHED' && <button className="btn" onClick={() => setTaskState('IDLE')}>再来一局</button>}
            </div>
          </div>

          {/* 图表区 */}
          <div className="st-chart-area">
            <div className="st-chart-full-panel">
              {isLoadingData && <div className="st-data-loading">载入结构中...</div>}
              <div ref={chartElRef} className="kline-container" style={{opacity: isLoadingData ? 0.6 : 1}} />
            </div>
          </div>

          {/* 操作面板 */}
          <div className="st-action-panel">
            <div className="st-stats">
              <div className="stat-group"><span>总资产</span><span className="strong">¥{total.toFixed(0)}</span></div>
              <div className="stat-group"><span>可用</span><span>¥{cash.toFixed(0)}</span></div>
              <div className="stat-group">
                <span>持仓/均价</span>
                <span>{shares > 0 ? `${shares}股 @ ¥${avgCost.toFixed(2)}` : '空仓'}</span>
              </div>
              {floatPnl !== null && (
                <div className="stat-group">
                  <span>浮盈</span>
                  <span className={parseFloat(floatPnl)>=0?'text-up':'text-down'}>{parseFloat(floatPnl)>0?'+':''}{floatPnl}%</span>
                </div>
              )}
              <div className="stat-group">
                <span>现价</span>
                <span className={currentKline?.close>currentKline?.open?'text-up':'text-down'}>{currentKline?.close?.toFixed(2)}</span>
              </div>
            </div>

            {taskState === 'TRAINING' && (
              <div className="st-controls">
                <div className="st-buy-group">
                  <span className="st-label">买入</span>
                  <button className="btn st-btn-buy" onClick={()=>handleBuy(0.1)} disabled={cash<(currentKline?.close*100)}>一成</button>
                  <button className="btn st-btn-buy" onClick={()=>handleBuy(0.25)} disabled={cash<(currentKline?.close*100)}>25%</button>
                  <button className="btn st-btn-buy" onClick={()=>handleBuy(0.5)} disabled={cash<(currentKline?.close*100)}>半仓</button>
                  <button className="btn st-btn-buy st-btn-buy-full" onClick={()=>handleBuy(1.0)} disabled={cash<(currentKline?.close*100)}>满仓</button>
                </div>
                <div className="st-buy-group">
                  <span className="st-label">卖出</span>
                  <button className="btn st-btn-sell" onClick={()=>handleSell(0.25)} disabled={shares<=0}>25%</button>
                  <button className="btn st-btn-sell" onClick={()=>handleSell(0.5)} disabled={shares<=0}>半仓</button>
                  <button className="btn st-btn-sell" onClick={()=>handleSell(1.0)} disabled={shares<=0}>清仓</button>
                </div>
                <button className="btn st-btn-advance" onClick={handleAdvance} disabled={isPlaying || isLoadingData}>+1根 (Space)</button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 结束报告 */}
      {taskState === 'FINISHED' && scores && (
        <div className="st-result-modal animate-fade-in">
          <div className="st-result-card">
            <h2>训练结束报告</h2>
            <div className="score-board">
              <div className="score-item"><label>总收益率</label><span className={scores.totalReturn>=0?'text-up':'text-down'}>{(scores.totalReturn*100).toFixed(2)}%</span></div>
              <div className="score-item"><label>基准(持有)</label><span>{(scores.benchReturn*100).toFixed(2)}%</span></div>
              <div className="score-item"><label>超额收益</label><span className={scores.excessReturn>=0?'text-up':'text-down'}>{(scores.excessReturn*100).toFixed(2)}%</span></div>
              <div className="score-item"><label>胜率</label><span>{(scores.winRate*100).toFixed(1)}%</span></div>
            </div>
            <div className="st-trades-log">
              <h4>交易记录</h4>
              <ul>
                {trades.map((t,i) => (
                  <li key={i}>
                    <span className={t.type==='BUY'?'text-up':'text-down'}>{t.type==='BUY'?`▲买(${t.pct||100}%)`:'▼卖'}</span>
                    <span> · {t.shares}股 @ ¥{t.price.toFixed(2)} · {t.date}</span>
                  </li>
                ))}
              </ul>
            </div>
            <button className="btn btn-primary" onClick={()=>setTaskState('IDLE')}>重新开始</button>
          </div>
        </div>
      )}
    </div>
  )
}
