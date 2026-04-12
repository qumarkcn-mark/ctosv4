import { useEffect, useRef, useState, useCallback } from 'react'
import { createChart } from 'lightweight-charts'
import './KlineChart.css'

const API_BASE = 'http://localhost:8000/api'

const INTERVALS = [
  { key: 'day', label: '日线', freq: 'day' },
  { key: 'm60', label: '60分', freq: '60' },
  { key: 'm30', label: '30分', freq: '30' },
  { key: 'm15', label: '15分', freq: '15' },
  { key: 'm5',  label: '5分',  freq: '5'  },
]

// ─── 从 lightweight-charts 的时间格式转成日期字符串索引（用于查 MACD）
function toTimeKey(t) {
  if (typeof t === 'number') return String(t)
  return t
}

// ─── 把后端日期转成 lightweight-charts 需要的时间格式
function parseTime(date, isDay) {
  if (isDay) return date  // 日线直接用 "YYYY-MM-DD"
  // 分钟线转 Unix timestamp (秒)，并处理 CST +08:00
  return Math.floor(new Date(date.replace(' ', 'T') + '+08:00').getTime() / 1000)
}

// ─── 画中枢矩形覆盖层（Canvas 叠加方案）
function drawZhongshuBoxes(canvas, chart, candleSeries, zhongshus) {
  if (!canvas || !zhongshus?.length) return

  const ctx = canvas.getContext('2d')
  const dpr = window.devicePixelRatio || 1
  const rect = canvas.getBoundingClientRect()
  canvas.width  = rect.width  * dpr
  canvas.height = rect.height * dpr
  ctx.scale(dpr, dpr)
  ctx.clearRect(0, 0, rect.width, rect.height)

  for (const zs of zhongshus) {
    try {
      const x0 = chart.timeScale().timeToCoordinate(zs.begin_date)
      const x1 = chart.timeScale().timeToCoordinate(zs.end_date)
      const yZG = candleSeries.priceToCoordinate(zs.zg)
      const yZD = candleSeries.priceToCoordinate(zs.zd)
      const yGG = candleSeries.priceToCoordinate(zs.gg)
      const yDD = candleSeries.priceToCoordinate(zs.dd)

      if (x0 == null || x1 == null || yZG == null || yZD == null) continue

      const left   = Math.min(x0, x1)
      const width  = Math.abs(x1 - x0) + 24  // 右边稍微延伸一点

      // 外层：GG ~ DD 极值区（更浅）
      ctx.fillStyle = 'rgba(234,179,8,0.04)'
      ctx.fillRect(left, Math.min(yGG, yDD), width, Math.abs(yGG - yDD))

      // 核心区：ZG ~ ZD（标准中枢）
      ctx.fillStyle = 'rgba(234,179,8,0.12)'
      ctx.fillRect(left, Math.min(yZG, yZD), width, Math.abs(yZG - yZD))

      // 边框
      ctx.strokeStyle = 'rgba(234,179,8,0.5)'
      ctx.lineWidth = 1
      ctx.setLineDash([4, 3])
      ctx.strokeRect(left, Math.min(yZG, yZD), width, Math.abs(yZG - yZD))
      ctx.setLineDash([])

      // ZG / ZD 标签
      ctx.fillStyle = 'rgba(234,179,8,0.9)'
      ctx.font = '10px monospace'
      ctx.fillText(`ZG ${zs.zg.toFixed(2)}`, left + 4, Math.min(yZG, yZD) - 4)
      ctx.fillText(`ZD ${zs.zd.toFixed(2)}`, left + 4, Math.max(yZG, yZD) + 12)
    } catch {}
  }
}

export default function KlineChart({ symbol }) {
  const wrapperRef   = useRef(null)
  const chartRef     = useRef(null)   // 主图 DOM
  const macdRef      = useRef(null)   // MACD 副图 DOM
  const canvasRef    = useRef(null)   // 中枢覆盖 Canvas

  const chartInst    = useRef(null)
  const macdInst     = useRef(null)
  const candleRef    = useRef(null)
  const biLinesRef   = useRef([])     // 当前所有笔折线 series 引用

  const [interval, setIntervalKey] = useState('day')
  const [loading,  setLoading]     = useState(false)
  const [stats,    setStats]       = useState(null)
  const [error,    setError]       = useState(null)

  // ─── 初始化：只创建 DOM 和 chart 实例，数据在下一个 effect 中加载
  useEffect(() => {
    if (!chartRef.current || !macdRef.current) return

    // 销毁旧实例
    if (chartInst.current)  { chartInst.current.remove();  chartInst.current  = null }
    if (macdInst.current)   { macdInst.current.remove();   macdInst.current   = null }

    const isDark = true
    const baseOpts = {
      layout: { background: { color: '#0a0e1a' }, textColor: '#666', fontSize: 11 },
      grid:   { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
      crosshair: {
        mode: 0,
        vertLine: { color: 'rgba(234,179,8,0.35)', width: 1, style: 2 },
        horzLine: { color: 'rgba(234,179,8,0.35)', width: 1, style: 2 },
      },
      timeScale:      { borderColor: 'rgba(255,255,255,0.06)', timeVisible: interval !== 'day', secondsVisible: false },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.06)' },
    }

    // 主图（K 线 + 笔 + 成交量）
    const main = createChart(chartRef.current, {
      ...baseOpts,
      width:  chartRef.current.clientWidth,
      height: 420,
    })
    chartInst.current = main

    // MACD 副图
    const macd = createChart(macdRef.current, {
      ...baseOpts,
      width:  macdRef.current.clientWidth,
      height: 130,
      timeScale: { ...baseOpts.timeScale, visible: false },  // MACD 复用时间轴但不显示
    })
    macdInst.current = macd

    // ─── 同步主图↔MACD 时间轴
    main.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (range) macd.timeScale().setVisibleLogicalRange(range)
    })
    macd.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (range) main.timeScale().setVisibleLogicalRange(range)
    })

    // ResizeObserver
    const ro = new ResizeObserver(() => {
      if (chartRef.current && chartInst.current)
        chartInst.current.applyOptions({ width: chartRef.current.clientWidth })
      if (macdRef.current && macdInst.current)
        macdInst.current.applyOptions({ width: macdRef.current.clientWidth })
    })
    if (wrapperRef.current) ro.observe(wrapperRef.current)

    return () => {
      ro.disconnect()
      if (chartInst.current) { chartInst.current.remove(); chartInst.current = null }
      if (macdInst.current)  { macdInst.current.remove();  macdInst.current  = null }
    }
  }, [])  // 只在 mount 时创建图实例

  // ─── 数据加载：每当 symbol / interval 变化时重新拉取并渲染
  useEffect(() => {
    if (!chartInst.current || !macdInst.current || !symbol) return

    const iv    = INTERVALS.find(i => i.key === interval)
    const freq  = iv?.freq ?? 'day'
    const isDay = interval === 'day'

    setLoading(true)
    setError(null)

    // 清理旧笔折线
    biLinesRef.current.forEach(s => { try { chartInst.current.removeSeries(s) } catch {} })
    biLinesRef.current = []

    fetch(`${API_BASE}/chan/detail/${symbol}?freq=${freq}&count=500`)
      .then(r => r.json())
      .then(json => {
        if (!json?.data?.klines?.length) {
          setError('暂无数据')
          setLoading(false)
          return
        }

        const { klines, bis, zhongshus, macd: macdData, stats: s } = json.data
        setStats(s)

        // ── 1. 烛台 K 线
        const candleSeries = chartInst.current.addCandlestickSeries({
          upColor: '#EF4444', downColor: '#22C55E',
          borderUpColor: '#EF4444', borderDownColor: '#22C55E',
          wickUpColor: '#EF4444',   wickDownColor: '#22C55E',
        })
        candleRef.current = candleSeries

        const candles = klines.map(k => ({
          time:  parseTime(k.time, isDay),
          open:  k.open, high: k.high, low: k.low, close: k.close,
        }))
        candleSeries.setData(candles)

        // ── 2. 成交量
        const volSeries = chartInst.current.addHistogramSeries({
          priceFormat: { type: 'volume' }, priceScaleId: '',
        })
        volSeries.priceScale().applyOptions({ scaleMargins: { top: 0.88, bottom: 0 } })
        volSeries.setData(klines.map((k, i) => ({
          time:  candles[i].time,
          value: k.volume,
          color: k.close >= k.open ? 'rgba(239,68,68,0.25)' : 'rgba(34,197,94,0.25)',
        })))

        // ── 3. 笔折线（每笔一个 LineSeries，两点折线）
        for (const bi of (bis ?? [])) {
          const t0 = parseTime(bi.x0, isDay)
          const t1 = parseTime(bi.x1, isDay)
          const series = chartInst.current.addLineSeries({
            color:     bi.is_up ? 'rgba(248,113,113,0.85)' : 'rgba(74,222,128,0.85)',
            lineWidth: 1,
            lineStyle: bi.is_sure ? 0 : 2,  // 0=实线, 2=虚线
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
          })
          series.setData([
            { time: t0, value: bi.y0 },
            { time: t1, value: bi.y1 },
          ])
          biLinesRef.current.push(series)
        }

        // ── 4. MACD 副图
        const macdChart = macdInst.current

        const difSeries = macdChart.addLineSeries({
          color: '#60a5fa', lineWidth: 1,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        })
        const deaSeries = macdChart.addLineSeries({
          color: '#f59e0b', lineWidth: 1,
          priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
        })
        const histSeries = macdChart.addHistogramSeries({
          priceLineVisible: false, lastValueVisible: false,
        })

        const macdTimes = (macdData?.dates ?? klines.map(k => k.time)).map(d => parseTime(d, isDay))
        const dif  = macdData?.dif  ?? []
        const dea  = macdData?.dea  ?? []
        const hist = macdData?.hist ?? []

        difSeries.setData(macdTimes.map((t, i) => ({ time: t, value: dif[i] ?? 0 })))
        deaSeries.setData(macdTimes.map((t, i) => ({ time: t, value: dea[i] ?? 0 })))
        histSeries.setData(macdTimes.map((t, i) => ({
          time: t, value: hist[i] ?? 0,
          color: (hist[i] ?? 0) >= 0 ? 'rgba(239,68,68,0.7)' : 'rgba(34,197,94,0.7)',
        })))

        chartInst.current.timeScale().fitContent()

        // ── 5. 中枢覆盖 Canvas（在 fitContent 后立刻绘制）
        requestAnimationFrame(() => {
          drawZhongshuBoxes(canvasRef.current, chartInst.current, candleSeries, zhongshus)
        })

        // 当时间轴拖拽时重绘中枢
        chartInst.current.timeScale().subscribeVisibleTimeRangeChange(() => {
          requestAnimationFrame(() => {
            drawZhongshuBoxes(canvasRef.current, chartInst.current, candleSeries, zhongshus)
          })
        })

        setLoading(false)
      })
      .catch(e => {
        console.error(e)
        setError('数据加载失败')
        setLoading(false)
      })
  }, [symbol, interval])

  return (
    <div className="kline-chart-wrapper" ref={wrapperRef}>
      {/* 工具栏 */}
      <div className="kline-toolbar">
        <div className="kline-ivs">
          {INTERVALS.map(iv => (
            <button
              key={iv.key}
              className={`kline-iv-btn ${interval === iv.key ? 'active' : ''}`}
              onClick={() => setIntervalKey(iv.key)}
            >
              {iv.label}
            </button>
          ))}
        </div>
        {stats && (
          <div className="kline-stats">
            <span className="stat-pill">{stats.kline_count} 根</span>
            <span className="stat-pill accent">{stats.bi_count} 笔</span>
            <span className="stat-pill gold">{stats.zhongshu_count} 枢</span>
          </div>
        )}
      </div>

      {/* 主图容器（相对定位，canvas 绝对叠加其上）*/}
      <div className="kline-main-wrapper">
        {loading && <div className="kline-loading">解析缠论结构...</div>}
        {error   && <div className="kline-error">{error}</div>}
        <div ref={chartRef} className="kline-container" />
        {/* 中枢方块透明画布，与主图完全重叠 */}
        <canvas ref={canvasRef} className="kline-overlay-canvas" />
      </div>

      {/* MACD 副图 */}
      <div className="kline-macd-label">MACD (12,26,9)</div>
      <div ref={macdRef} className="kline-macd-container" />
    </div>
  )
}
