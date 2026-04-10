import { useEffect, useRef, useState } from 'react'
import { createChart } from 'lightweight-charts'
import './KlineChart.css'

const API_BASE = 'http://localhost:8000/api'

const INTERVALS = [
  { key: 'day', label: '日线' },
  { key: 'm60', label: '60分' },
  { key: 'm30', label: '30分' },
  { key: 'm15', label: '15分' },
  { key: 'm5', label: '5分' },
]

export default function KlineChart({ symbol, zhongshu }) {
  const chartRef = useRef(null)
  const chartInstance = useRef(null)
  const [interval, setInterval] = useState('day')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!chartRef.current) return

    // 销毁旧图表
    if (chartInstance.current) {
      chartInstance.current.remove()
      chartInstance.current = null
    }

    const chart = createChart(chartRef.current, {
      width: chartRef.current.clientWidth,
      height: 420,
      layout: {
        background: { color: '#0a0e1a' },
        textColor: '#888',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.03)' },
        horzLines: { color: 'rgba(255,255,255,0.03)' },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: 'rgba(234,179,8,0.3)', width: 1, style: 2 },
        horzLine: { color: 'rgba(234,179,8,0.3)', width: 1, style: 2 },
      },
      timeScale: {
        borderColor: 'rgba(255,255,255,0.06)',
        timeVisible: interval !== 'day',
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: 'rgba(255,255,255,0.06)',
      },
    })

    chartInstance.current = chart

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#EF4444',
      downColor: '#22C55E',
      borderUpColor: '#EF4444',
      borderDownColor: '#22C55E',
      wickUpColor: '#EF4444',
      wickDownColor: '#22C55E',
    })

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    })
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    })

    // 拉取 K 线数据
    setLoading(true)
    fetch(`${API_BASE}/data/klines/${symbol}?interval=${interval}&count=200`)
      .then(r => r.json())
      .then(json => {
        if (!json.klines) return

        const candles = json.klines.map(k => {
          let time
          if (interval === 'day') {
            time = k.date
          } else {
            time = Math.floor(new Date(k.date.replace(' ', 'T') + ':00+08:00').getTime() / 1000)
          }
          return { time, open: k.open, high: k.high, low: k.low, close: k.close }
        })

        const volumes = json.klines.map((k, i) => ({
          time: candles[i].time,
          value: k.volume,
          color: k.close >= k.open ? 'rgba(239,68,68,0.3)' : 'rgba(34,197,94,0.3)'
        }))

        candleSeries.setData(candles)
        volumeSeries.setData(volumes)

        // 绘制中枢区间
        if (zhongshu && zhongshu.zd > 0 && zhongshu.zg > 0) {
          candleSeries.createPriceLine({
            price: zhongshu.zg,
            color: 'rgba(234,179,8,0.5)',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'ZG',
          })
          candleSeries.createPriceLine({
            price: zhongshu.zd,
            color: 'rgba(234,179,8,0.5)',
            lineWidth: 1,
            lineStyle: 2,
            axisLabelVisible: true,
            title: 'ZD',
          })
        }

        chart.timeScale().fitContent()
        setLoading(false)
      })
      .catch(() => setLoading(false))

    const ro = new ResizeObserver(() => {
      if (chartRef.current && chartInstance.current) {
        chartInstance.current.applyOptions({ width: chartRef.current.clientWidth })
      }
    })
    ro.observe(chartRef.current)

    return () => {
      ro.disconnect()
      if (chartInstance.current) {
        chartInstance.current.remove()
        chartInstance.current = null
      }
    }
  }, [symbol, interval, zhongshu])

  return (
    <div className="kline-chart-wrapper">
      <div className="kline-toolbar">
        {INTERVALS.map(iv => (
          <button
            key={iv.key}
            className={`kline-iv-btn ${interval === iv.key ? 'active' : ''}`}
            onClick={() => setInterval(iv.key)}
          >
            {iv.label}
          </button>
        ))}
      </div>
      {loading && <div className="kline-loading">加载 K 线...</div>}
      <div ref={chartRef} className="kline-container" />
    </div>
  )
}
