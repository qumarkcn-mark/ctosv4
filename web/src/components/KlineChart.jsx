import { useEffect, useRef, useState, useCallback } from 'react'
import { init, dispose, registerOverlay } from 'klinecharts'
import { renderChanOverlays } from '../plugins/chanOverlay.js'
import { phantomOverlay, renderPhantomOverlays } from '../plugins/phantom_overlay.js'
import { API_BASE } from '../config.js'
import { toTimestamp } from '../utils.js'
import './KlineChart.css'

registerOverlay(phantomOverlay)

const INTERVALS = [
  { key: 'day', label: '日线', freq: 'day' },
  { key: 'm60', label: '60分', freq: '60' },
  { key: 'm30', label: '30分', freq: '30' },
  { key: 'm15', label: '15分', freq: '15' },
  { key: 'm5', label: '5分', freq: '5' },
  { key: 'm1', label: '1分', freq: '1' },
]

const MAIN_INDICATORS = ['MA', 'BOLL', 'None']
const SUB_INDICATORS = ['MACD', 'KDJ', 'RSI']

export default function KlineChart({ symbol, layerVisibility }) {
  const chartContainerRef = useRef(null)
  const chartRef = useRef(null)
  const chanDataRef = useRef(null)
  // 存储当前请求参数对应的 K 线数据，供 getBars 回调使用
  const klcDataCacheRef = useRef({ data: [], isDay: true })

  const [interval, setIntervalKey] = useState('day')
  const [mainIndicator, setMainIndicator] = useState('MA')
  const [subIndicator, setSubIndicator] = useState('MACD')
  const [loading, setLoading] = useState(false)
  const [isInferring, setIsInferring] = useState(false)
  const [stats, setStats] = useState(null)
  const [error, setError] = useState(null)

  // ─── 核心 Effect: 初始化图表 + 加载数据 (symbol/interval 变化时完全重建)
  useEffect(() => {
    if (!chartContainerRef.current || !symbol) return

    const iv = INTERVALS.find((i) => i.key === interval)
    const freq = iv?.freq ?? 'day'
    const isDay = interval === 'day'

    setLoading(true)
    setError(null)

    // 销毁旧实例
    if (chartRef.current) {
      dispose(chartContainerRef.current)
      chartRef.current = null
    }

    // 创建新实例
    const chart = init(chartContainerRef.current, {
      styles: {
        grid: {
          show: true,
          horizontal: { color: 'rgba(255,255,255,0.03)' },
          vertical: { color: 'rgba(255,255,255,0.03)' },
        },
        candle: {
          type: 'candle_solid',
          tooltip: {
            showRule: 'always',
            showType: 'standard',
            text: { color: '#D9D9D9', size: 12 },
          },
          bar: {
            upColor: '#ef5350',
            downColor: '#26a69a',
            noChangeColor: '#888888',
            upBorderColor: '#ef5350',
            downBorderColor: '#26a69a',
            upWickColor: '#ef5350',
            downWickColor: '#26a69a',
          },
        },
        // A股 MACD 颜色：红涨绿跌
        indicator: {
          bars: [{
            upColor: 'rgba(239, 83, 80, 0.8)',
            downColor: 'rgba(38, 166, 154, 0.8)',
            noChangeColor: '#888888',
          }],
          lines: [
            { color: '#ffd54f' },  // DIF — 金色
            { color: '#42a5f5' },  // DEA — 蓝色
            { color: '#ab47bc' },  // 第三线
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
            text: { color: '#0a0a0f', backgroundColor: '#f0b90b' },
          },
        },
      },
    })

    chartRef.current = chart

    // 默认指标
    chart.createIndicator('VOL', false, { id: 'pane_vol' })
    chart.createIndicator('MA', false, { id: 'candle_pane' })
    chart.createIndicator('MACD', false, { id: 'pane_sub' })

    // 注册 dataLoader — getBars 从缓存取数据
    chart.setDataLoader({
      getBars: ({ type, callback }) => {
        if (type === 'init') {
          callback(klcDataCacheRef.current.data, false)
        } else {
          callback([], false)
        }
      },
    })

    // 快捷键
    const handleKeyDown = (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return
      const c = chartRef.current
      if (!c) return
      switch (e.key) {
        case 'ArrowLeft': e.preventDefault(); c.scrollByDistance(50); break
        case 'ArrowRight': e.preventDefault(); c.scrollByDistance(-50); break
        case 'ArrowUp': e.preventDefault(); c.zoomAtCoordinate(1.1); break
        case 'ArrowDown': e.preventDefault(); c.zoomAtCoordinate(0.9); break
      }
    }
    window.addEventListener('keydown', handleKeyDown)

    // 响应式缩放
    const resizeObserver = new ResizeObserver(() => {
      chart.resize()
    })
    resizeObserver.observe(chartContainerRef.current)

    // 发起数据请求
    fetch(`${API_BASE}/chan/detail/${symbol}?freq=${freq}&count=500`)
      .then((r) => r.json())
      .then((json) => {
        if (!json?.data?.klines?.length) {
          setError('暂无数据')
          setLoading(false)
          return
        }

        const { klines, bis, segs, bi_zhongshus, bi_zhongshus_decomp, seg_zhongshus, stats: s, bsps } = json.data
        setStats(s)

        const klcData = klines.map((k) => ({
          timestamp: toTimestamp(k.time, isDay),
          open: k.open,
          high: k.high,
          low: k.low,
          close: k.close,
          volume: k.volume,
        }))

        klcDataCacheRef.current = { data: klcData, isDay }
        chanDataRef.current = { bis, segs, bi_zhongshus, bi_zhongshus_decomp: bi_zhongshus_decomp || [], seg_zhongshus, bsps, isDay }

        chart.setSymbol({ ticker: symbol })
        chart.setPeriod({ span: 1, type: isDay ? 'day' : 'minute' })

        // 渲染缠论覆盖层（移除了 500ms 固定延迟，数据到即渲染）
        requestAnimationFrame(() => {
          // ★ 初始渲染也必须尊重 layerVisibility（从 localStorage 恢复的状态）
          const vis = layerVisibility || {}
          const filteredData = {
            bis: vis.bi !== false ? bis : [],
            segs: vis.seg !== false ? segs : [],
            bi_zhongshus: vis.bi_zs !== false ? bi_zhongshus : [],
            bi_zhongshus_decomp: vis.bi_zs_decomp ? (bi_zhongshus_decomp || []) : [],
            seg_zhongshus: vis.seg_zs !== false ? seg_zhongshus : [],
            bsps: vis.bsp !== false ? bsps : [],
          }
          renderChanOverlays(chart, filteredData, isDay, false)
          setLoading(false)
        })
      })
      .catch((e) => {
        console.error(e)
        setError('数据加载失败')
        setLoading(false)
      })

    return () => {
      resizeObserver.disconnect()
      window.removeEventListener('keydown', handleKeyDown)
      if (chartContainerRef.current) {
        dispose(chartContainerRef.current)
        chartRef.current = null
      }
    }
  }, [symbol, interval])

  // ─── 主指标切换
  const handleMainIndicator = useCallback(
    (newInd) => {
      const chart = chartRef.current
      if (!chart) return
      chart.removeIndicator({ paneId: 'candle_pane', name: mainIndicator })
      if (newInd !== 'None') {
        chart.createIndicator(newInd, false, { id: 'candle_pane' })
      }
      setMainIndicator(newInd)
    },
    [mainIndicator]
  )

  // ─── 副图指标切换
  const handleSubIndicator = useCallback(
    (newInd) => {
      const chart = chartRef.current
      if (!chart) return
      chart.removeIndicator({ paneId: 'pane_sub', name: subIndicator })
      chart.createIndicator(newInd, false, { id: 'pane_sub' })
      setSubIndicator(newInd)
    },
    [subIndicator]
  )

  // ─── 图层可见性响应 — 联动缠论 + 指标
  useEffect(() => {
    if (!chartRef.current || !layerVisibility) return
    const chart = chartRef.current

    if (chanDataRef.current) {
      const { bis, segs, bi_zhongshus, bi_zhongshus_decomp, seg_zhongshus, bsps, isDay } = chanDataRef.current
      chart.removeOverlay({ groupId: 'chan_bi_group' })
      chart.removeOverlay({ groupId: 'chan_seg_group' })
      chart.removeOverlay({ groupId: 'chan_bi_zs_group' })
      chart.removeOverlay({ groupId: 'chan_bi_zs_decomp_group' })
      chart.removeOverlay({ groupId: 'chan_seg_zs_group' })
      chart.removeOverlay({ groupId: 'chan_bsp_group' })

      const filteredData = {
        bis: layerVisibility.bi !== false ? bis : [],
        segs: layerVisibility.seg !== false ? segs : [],
        bi_zhongshus: layerVisibility.bi_zs !== false ? bi_zhongshus : [],
        bi_zhongshus_decomp: layerVisibility.bi_zs_decomp ? (bi_zhongshus_decomp || []) : [],
        seg_zhongshus: layerVisibility.seg_zs !== false ? seg_zhongshus : [],
        bsps: layerVisibility.bsp !== false ? bsps : [],
      }
      renderChanOverlays(chart, filteredData, isDay, false)
    }

    // 指标联动
    if (layerVisibility.ma === false) {
      chart.removeIndicator({ paneId: 'candle_pane', name: mainIndicator })
    } else if (mainIndicator !== 'None') {
      chart.createIndicator(mainIndicator, false, { id: 'candle_pane' })
    }

    if (layerVisibility.vol === false) {
      chart.removeIndicator({ paneId: 'pane_vol', name: 'VOL' })
    } else {
      chart.createIndicator('VOL', false, { id: 'pane_vol' })
    }

    if (layerVisibility.macd === false) {
      chart.removeIndicator({ paneId: 'pane_sub', name: subIndicator })
    } else {
      chart.createIndicator(subIndicator, false, { id: 'pane_sub' })
    }
  }, [layerVisibility, mainIndicator, subIndicator])

  // ─── DeepSeek 推演
  const handleInferScenarios = async () => {
    if (!symbol || isInferring) return
    setIsInferring(true)
    setError(null)
    try {
      const chart = chartRef.current
      if (!chart) return

      // clear existing overlays if any
      chart.removeOverlay({ groupId: 'phantom_group' })

      const res = await fetch(`${API_BASE}/agent/infer_scenarios`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol })
      })
      const data = await res.json()
      
      if (!data.scenarios || data.scenarios.length === 0) {
        throw new Error('推演结果为空')
      }

      // 使用批量渲染函数，长线系统与 chanOverlay 一致
      renderPhantomOverlays(chart, data.scenarios)

    } catch (e) {
      console.error(e)
      setError('Agent 推演失败: ' + e.message)
    } finally {
      setIsInferring(false)
    }
  }

  return (
    <div className="kline-chart-wrapper">
      {/* 工具栏 */}
      <div className="kline-toolbar">
        <div className="kline-ivs">
          {INTERVALS.map((iv) => (
            <button
              key={iv.key}
              className={`kline-iv-btn ${interval === iv.key ? 'active' : ''}`}
              onClick={() => setIntervalKey(iv.key)}
            >
              {iv.label}
            </button>
          ))}
        </div>

        <div className="kline-indicators">
          {MAIN_INDICATORS.map((ind) => (
            <button
              key={ind}
              className={`kline-ind-btn ${mainIndicator === ind ? 'active' : ''}`}
              onClick={() => handleMainIndicator(ind)}
            >
              {ind === 'None' ? '无' : ind}
            </button>
          ))}
        </div>

        <div className="kline-indicators sub">
          {SUB_INDICATORS.map((ind) => (
            <button
              key={ind}
              className={`kline-ind-btn sub ${subIndicator === ind ? 'active' : ''}`}
              onClick={() => handleSubIndicator(ind)}
            >
              {ind}
            </button>
          ))}
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px', alignItems: 'center' }}>
          <button 
            className="kline-ind-btn"
            onClick={handleInferScenarios}
            disabled={isInferring}
            style={{
              background: 'rgba(239, 83, 80, 0.15)',
              borderColor: 'rgba(239, 83, 80, 0.5)',
              color: '#ef5350',
              fontWeight: 'bold',
              cursor: isInferring ? 'wait' : 'pointer'
            }}
          >
            {isInferring ? '推演中...' : 'DeepSeek 推演'}
          </button>
          
          {stats && (
            <div className="kline-stats" style={{ marginLeft: 0 }}>
              <span className="stat-pill">{stats.kline_count} 根</span>
              <span className="stat-pill accent">{stats.bi_count} 笔</span>
              <span className="stat-pill gold">{stats.seg_count || 0} 段</span>
              <span className="stat-pill">{stats.bi_zs_count || 0} 笔枢</span>
              <span className="stat-pill accent">{stats.seg_zs_count || 0} 段枢</span>
            </div>
          )}
        </div>
      </div>

      <div className="kline-main-wrapper">
        {loading && <div className="kline-loading">解析缠论结构...</div>}
        {error && <div className="kline-error">{error}</div>}
        <div ref={chartContainerRef} className="kline-container" />
      </div>
    </div>
  )
}
