import { useState, useCallback, useRef, useEffect } from 'react'
import { init, dispose } from 'klinecharts'
import '../components/KlineChart.css'
import './SandTable.css'

import { API_BASE } from '../config.js'

function toTimestamp(timeStr) {
  if (!timeStr) return 0
  const cleanStr = timeStr.trim().replace(' ', 'T')
  return new Date(cleanStr).getTime()
}

export default function SandTable() {
  const [taskState, setTaskState] = useState('IDLE') // IDLE, LOADING, TRAINING, FINISHED
  const [sessionData, setSessionData] = useState(null)
  
  // 资金账户状态
  const [cash, setCash] = useState(1000000)
  const [shares, setShares] = useState(0)
  const [trades, setTrades] = useState([])
  const [currentKline, setCurrentKline] = useState(null)

  // ★ C2 Fix: 用 ref 追踪交易可变状态，避免 Space 键闭包捕获旧值
  const cashRef = useRef(cash)
  const sharesRef = useRef(shares)
  const tradesRef = useRef(trades)
  const currentKlineRef = useRef(currentKline)
  const sessionDataRef = useRef(sessionData)

  useEffect(() => { cashRef.current = cash }, [cash])
  useEffect(() => { sharesRef.current = shares }, [shares])
  useEffect(() => { tradesRef.current = trades }, [trades])
  useEffect(() => { currentKlineRef.current = currentKline }, [currentKline])
  useEffect(() => { sessionDataRef.current = sessionData }, [sessionData])

  const chartContainerRef = useRef(null)
  const chartRef = useRef(null)

  // ★ I3 Fix: 防抖锁，防止 Space 键连按导致并发请求
  const isAdvancingRef = useRef(false)

  const [scores, setScores] = useState(null)

  // K线数据缓存（用于 v10 DataLoader 回调）
  const klcDataRef = useRef([])
  // subscribeBar 回调引用（用于推进时增量推送新 K 线）
  const subscribeBarCallbackRef = useRef(null)

  // 初始化图表实例
  useEffect(() => {
    if (taskState === 'TRAINING' && !!chartContainerRef.current) {
      // ★ 使用完整 A 股配色初始化（红涨绿跌）
      const chart = init(chartContainerRef.current, {
        styles: {
          grid: {
            show: true,
            horizontal: { color: 'rgba(255,255,255,0.03)' },
            vertical: { color: 'rgba(255,255,255,0.03)' },
          },
          candle: {
            type: 'candle_solid',
            bar: {
              upColor: '#ef5350',        // 红涨
              downColor: '#26a69a',       // 绿跌
              noChangeColor: '#888888',
              upBorderColor: '#ef5350',
              downBorderColor: '#26a69a',
              upWickColor: '#ef5350',
              downWickColor: '#26a69a',
            },
          },
          indicator: {
            bars: [{
              upColor: 'rgba(239, 83, 80, 0.8)',
              downColor: 'rgba(38, 166, 154, 0.8)',
              noChangeColor: '#888888',
            }],
            lines: [
              { color: '#ffd54f' },  // DIF — 金色
              { color: '#42a5f5' },  // DEA — 蓝色
              { color: '#ab47bc' },
            ],
          },
          crosshair: {
            show: true,
            horizontal: {
              line: { color: 'rgba(234,179,8,0.35)', style: 'dash' },
              text: { color: '#0a0a0f', backgroundColor: '#f0b90b' },
            },
            vertical: {
              line: { color: 'rgba(234,179,8,0.35)', style: 'dash' },
              text: { show: false },  // ★ 盲测：隐藏十字线日期
            },
          },
          xAxis: {
            show: false,  // ★ 盲测：隐藏 X 轴日期刻度
          },
        },
      })
      chartRef.current = chart

      // ★ 添加指标：VOL + MA + MACD
      chart.createIndicator('VOL', false, { id: 'pane_vol' })
      chart.createIndicator('MA', false, { id: 'candle_pane' })
      chart.createIndicator('MACD', false, { id: 'pane_sub' })

      // ★ 盲测模式：init 后强制覆盖 tooltip，隐藏所有日期/时间信息
      chart.setStyles({
        candle: {
          tooltip: {
            title: { template: ' ' },  // 替换默认的 {symbol} · {period}
            legend: {
              template: [
                { title: 'O ', value: '{open}' },
                { title: 'H ', value: '{high}' },
                { title: 'L ', value: '{low}' },
                { title: 'C ', value: '{close}' },
                { title: 'V ', value: '{volume}' },
              ]
            },
          },
        },
      })

      // ★ v10 API: 先准备数据缓存
      const sd = sessionDataRef.current
      if (sd) {
        const klcData = sd.klines.map((k) => ({
          timestamp: toTimestamp(k.date),
          open: k.open,
          high: k.high,
          low: k.low,
          close: k.close,
          volume: k.volume,
        }))
        klcDataRef.current = klcData

        // ★ v10 API: 注册 DataLoader（同 KlineChart.jsx 方式）
        chart.setDataLoader({
          getBars: ({ type, callback }) => {
            if (type === 'init') {
              callback(klcDataRef.current, false)
            } else {
              callback([], false)
            }
          },
          subscribeBar: ({ callback }) => {
            // 保存实时推送回调引用，advance 时调用
            subscribeBarCallbackRef.current = callback
          },
          unsubscribeBar: () => {
            subscribeBarCallbackRef.current = null
          }
        })

        // ★ v10 API: 触发数据加载
        chart.setSymbol({ ticker: sd.symbol })
        chart.setPeriod({ span: 1, type: 'day' })
      }

      return () => {
        subscribeBarCallbackRef.current = null
        if (chartContainerRef.current) {
          dispose(chartContainerRef.current)
          chartRef.current = null
        }
      }
    }
  }, [taskState]) // eslint-disable-line

  // ★ C2 Fix: handleFinish 从 ref 读取最新状态
  const handleFinish = useCallback(() => {
    const sd = sessionDataRef.current
    const kline = currentKlineRef.current
    if (!sd || !kline) return

    let finalCash = cashRef.current
    let finalShares = sharesRef.current
    let finalTrades = [...tradesRef.current]
    
    const endPrice = kline.close
    const startPrice = sd.initialClose

    // ★ C3 Fix: 自动平仓未关闭的持仓
    if (finalShares > 0) {
      const revenue = finalShares * endPrice
      finalCash += revenue
      finalTrades.push({
        type: 'SELL',
        price: endPrice,
        shares: finalShares,
        date: kline.date + ' (自动平仓)'
      })
      finalShares = 0
    }

    const finalValue = finalCash
    const totalReturn = (finalValue - 1000000) / 1000000
    const benchmarkReturn = (endPrice - startPrice) / startPrice
    const excessReturn = totalReturn - benchmarkReturn

    // 胜率计算（包含自动平仓的卖出）
    let winCount = 0
    let lastBuyPrice = 0
    finalTrades.forEach(t => {
      if (t.type === 'BUY') {
        lastBuyPrice = t.price
      } else if (t.type === 'SELL') {
        if (t.price > lastBuyPrice) winCount++
      }
    })
    const sellCount = finalTrades.filter(t => t.type === 'SELL').length
    const winRate = sellCount > 0 ? (winCount / sellCount) : 0

    // 更新 React 状态（用于 UI 展示）
    setCash(finalCash)
    setShares(finalShares)
    setTrades(finalTrades)

    setScores({
      totalReturn,
      benchmarkReturn,
      excessReturn,
      winRate,
      tradeCount: finalTrades.length,
      finalValue
    })

    setTaskState('FINISHED')
  }, [])

  // ★ C2 Fix + I3 Fix: handleAdvance 从 ref 读 sessionData，自带防抖
  const handleAdvance = useCallback(async () => {
    const sd = sessionDataRef.current
    if (!sd || isAdvancingRef.current) return
    isAdvancingRef.current = true

    try {
      const res = await fetch(`${API_BASE}/sand-table/advance`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task_id: sd.task_id })
      })
      const data = await res.json()
      if (res.ok) {
        // ★ I4 Fix: 先渲染最后一根 K 线，再判断是否结束
        if (data.next_kline) {
          const k = data.next_kline
          const klcPoint = {
            timestamp: toTimestamp(k.date),
            open: k.open, high: k.high, low: k.low, close: k.close, volume: k.volume
          }
          setCurrentKline(k)
          // ★ v10 API: 用 subscribeBar 回调推送增量 K 线
          klcDataRef.current = [...klcDataRef.current, klcPoint]
          if (subscribeBarCallbackRef.current) {
            subscribeBarCallbackRef.current(klcPoint)
          }
        }

        if (data.is_end) {
          // 延迟一帧让 currentKline ref 更新
          setTimeout(() => handleFinish(), 50)
          return
        }
      }
    } catch (e) {
      console.error(e)
    } finally {
      isAdvancingRef.current = false
    }
  }, [handleFinish])

  // 绑定快捷键进行 advance
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.code === 'Space' && !e.repeat) {
        e.preventDefault()
        handleAdvance()
      }
    }
    if (taskState === 'TRAINING') {
      window.addEventListener('keydown', handleKeyDown)
      return () => window.removeEventListener('keydown', handleKeyDown)
    }
  }, [taskState, handleAdvance])

  // 开始任务
  const handleStartTask = async (poolConfig) => {
    setTaskState('LOADING')
    
    // 获取缓存的自选股
    let watchlistSymbols = []
    try {
      const watchlistData = JSON.parse(localStorage.getItem('ct_watchlist_v4') || '[]')
      watchlistSymbols = watchlistData.flatMap(g => g.stocks.map(s => s.symbol))
    } catch(e) {
      console.warn("Watchlist parse failed")
    }

    try {
      const res = await fetch(`${API_BASE}/sand-table/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pool_symbols: watchlistSymbols,
          pool: poolConfig,
          freq: 'day',
          window_count: 80
        })
      })
      const data = await res.json()
      if (res.ok) {
        setSessionData({
          ...data,
          isDay: true,
          initialClose: data.klines[data.klines.length - 1].close
        })
        setCurrentKline(data.klines[data.klines.length - 1])
        setTaskState('TRAINING')
        setCash(1000000)
        setShares(0)
        setTrades([])
      } else {
        alert("启动失败：" + data.detail)
        setTaskState('IDLE')
      }
    } catch (e) {
      console.error(e)
      alert("网络错误")
      setTaskState('IDLE')
    }
  }

  // 买入操作：全仓买入（100股整倍）
  const handleBuy = () => {
    if (cash <= 0 || !currentKline) return
    const price = currentKline.close
    
    const possibleShares = Math.floor(cash / price / 100) * 100
    if (possibleShares === 0) return

    const cost = possibleShares * price
    setCash(prev => prev - cost)
    setShares(prev => prev + possibleShares)

    setTrades(prev => [...prev, {
      type: 'BUY',
      price: price,
      shares: possibleShares,
      date: currentKline.date
    }])
  }

  // 卖出操作：全仓清仓
  const handleSell = () => {
    if (shares <= 0 || !currentKline) return
    const price = currentKline.close
    
    const revenue = shares * price
    setCash(prev => prev + revenue)
    setTrades(prev => [...prev, {
      type: 'SELL',
      price: price,
      shares: shares,
      date: currentKline.date
    }])
    setShares(0)
  }

  return (
    <div className="sand-table">
      {taskState === 'IDLE' && (
        <div className="st-idle-panel animate-fade-in">
          <h2>推演沙盘 — 交易纪律训练</h2>
          <p>系统将随机选择一只股票和历史时间点，你将在裸K图中进行推演训练。</p>
          <div className="st-pool-selection">
            <button className="btn btn-primary" onClick={() => handleStartTask('custom')}>使用自选股池</button>
            <button className="btn" onClick={() => handleStartTask('all')}>使用全部股池</button>
          </div>
        </div>
      )}

      {taskState === 'LOADING' && (
        <div className="st-loading-panel">
          <span className="animate-pulse">部署沙盘中...</span>
        </div>
      )}

      {(taskState === 'TRAINING' || taskState === 'FINISHED') && (
        <div className="st-main-view">
          <div className="st-topbar">
            <h3>{sessionData?.symbol} (盲测中)</h3>
            {taskState === 'TRAINING' && <div className="text-secondary">按 Space 空格键步进，也可点击下方面板</div>}
            {taskState === 'FINISHED' && <button className="btn" onClick={() => setTaskState('IDLE')}>再来一局</button>}
          </div>

          <div className="kline-main-wrapper">
            <div ref={chartContainerRef} className="kline-container" />
          </div>

          <div className="st-action-panel">
            <div className="st-stats">
              <div className="stat-group">
                <span>总资产:</span>
                <span className="strong">¥{(cash + shares * (currentKline?.close || 0)).toFixed(2)}</span>
              </div>
              <div className="stat-group">
                <span>可用资金:</span>
                <span>¥{cash.toFixed(2)}</span>
              </div>
              <div className="stat-group">
                <span>持仓:</span>
                <span>{shares} 股</span>
              </div>
              <div className="stat-group">
                <span>最新价:</span>
                <span className={currentKline?.close > currentKline?.open ? 'text-up' : 'text-down'}>{currentKline?.close}</span>
              </div>
            </div>

            {taskState === 'TRAINING' && (
              <div className="st-controls">
                <button className="btn st-btn-buy" onClick={handleBuy} disabled={cash < (currentKline?.close * 100)}>买入 (100股整倍)</button>
                <button className="btn st-btn-sell" onClick={handleSell} disabled={shares <= 0}>全部卖出</button>
                <button className="btn st-btn-advance" onClick={handleAdvance}>下一根K线 (Space)</button>
              </div>
            )}
          </div>
        </div>
      )}

      {taskState === 'FINISHED' && scores && (
        <div className="st-result-modal animate-fade-in">
          <div className="st-result-card">
            <h2>训练结束报告</h2>
            <div className="score-board">
              <div className="score-item">
                <label>总收益率</label>
                <span className={(scores.totalReturn >= 0 ? "text-up" : "text-down")}>{(scores.totalReturn * 100).toFixed(2)}%</span>
              </div>
              <div className="score-item">
                <label>基准收益(买入持有)</label>
                <span>{(scores.benchmarkReturn * 100).toFixed(2)}%</span>
              </div>
              <div className="score-item">
                <label>超额收益</label>
                <span className={(scores.excessReturn >= 0 ? "text-up" : "text-down")}>{(scores.excessReturn * 100).toFixed(2)}%</span>
              </div>
              <div className="score-item">
                <label>胜率</label>
                <span>{(scores.winRate * 100).toFixed(1)}%</span>
              </div>
            </div>
            
            <div className="st-trades-log">
              <h4>交易记录</h4>
              <ul>
                {trades.map((t, idx) => (
                  <li key={idx}>
                    <span className={t.type === 'BUY' ? 'text-up' : 'text-down'}>{t.type === 'BUY' ? '买入' : '卖出'}</span>
                    <span> - {t.date} </span>
                    <span> - {t.shares}股 @ ¥{t.price.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            </div>
            
            <button className="btn btn-primary" onClick={() => setTaskState('IDLE')}>重新开始</button>
          </div>
        </div>
      )}
    </div>
  )
}
