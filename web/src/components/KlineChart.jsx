import { useEffect, useRef, useState, useCallback } from 'react'
import { init, dispose, registerOverlay } from 'klinecharts'
import { renderChanOverlays } from '../plugins/chanOverlay.js'
import { phantomOverlay, renderPhantomOverlays } from '../plugins/phantom_overlay.js'
import { API_BASE } from '../config.js'
import { toTimestamp } from '../utils.js'
import './KlineChart.css'

// ─── 工具栏状态持久化 key
const TOOLBAR_KEY = 'ct_kline_toolbar_v4'

// ─── 区间套投影：当前级别 → 上级别映射
// 投影色带来自上级别的笔中枢，给小级别提供宏观支撑压力参考
const PARENT_FREQ_MAP = {
  week: null,                          // 周线无上级别
  day:  { freq: 'week', count: 200 },  // 日线 ← 周线中枢
  m60:  { freq: 'day',  count: 500 },  // 60分 ← 日线中枢
  m30:  { freq: 'day',  count: 500 },  // 30分 ← 日线中枢
  m15:  { freq: '30',   count: 800 },  // 15分 ← 30分中枢
  m5:   { freq: '30',   count: 1000 }, // 5分  ← 30分中枢
}

const INTERVALS = [
  { key: 'week', label: '周线', freq: 'week', count: 2500 },
  { key: 'day', label: '日线', freq: 'day', count: 2500 },
  { key: 'm60', label: '60分', freq: '60', count: 2500 },
  { key: 'm30', label: '30分', freq: '30', count: 2500 },
  { key: 'm15', label: '15分', freq: '15', count: 2500 },
  { key: 'm5', label: '5分', freq: '5', count: 2500 },
]

function loadToolbar() {
  try {
    const s = localStorage.getItem(TOOLBAR_KEY)
    if (s) {
      const parsed = JSON.parse(s)
      // 回退不受支持的级别（如 m1）到 m5
      if (!INTERVALS.find(i => i.key === parsed.interval)) {
        parsed.interval = 'm5'
      }
      return parsed
    }
  } catch {}
  return { interval: 'day', mainIndicator: 'MA', subIndicator: 'MACD' }
}

function saveToolbar(state) {
  localStorage.setItem(TOOLBAR_KEY, JSON.stringify(state))
}

registerOverlay(phantomOverlay)

const MAIN_INDICATORS = ['MA', 'BOLL', 'None']
const SUB_INDICATORS = ['MACD', 'KDJ', 'RSI']

export default function KlineChart({ symbol, layerVisibility }) {
  const chartContainerRef = useRef(null)
  const chartRef = useRef(null)
  const chanDataRef = useRef(null)
  // 存储当前请求参数对应的 K 线数据，供 getBars 回调使用
  const klcDataCacheRef = useRef({ data: [], isDay: true })
  // ★ 始终保存最新 layerVisibility，供初始化 effect 读取（不能放 deps 否则会触发完全重建）
  const layerVisibilityRef = useRef(layerVisibility)
  // ★ 区间套投影：缓存上级别中枢数据，避免每次切换图层都重新请求
  const higherLevelRef = useRef(null)

  const [interval, setIntervalKey] = useState(() => loadToolbar().interval)
  const [mainIndicator, setMainIndicator] = useState(() => loadToolbar().mainIndicator)
  const [subIndicator, setSubIndicator] = useState(() => loadToolbar().subIndicator)
  const [loading, setLoading] = useState(false)
  const [isInferring, setIsInferring] = useState(false)
  const [stats, setStats] = useState(null)
  const [error, setError] = useState(null)

  // 同步 layerVisibility 到 ref（每次渲染都更新，不触发 effect）
  useEffect(() => {
    layerVisibilityRef.current = layerVisibility
  })

  // ─── 核心 Effect: 初始化图表 + 加载数据 (symbol/interval 变化时完全重建)
  useEffect(() => {
    if (!chartContainerRef.current || !symbol) return

    const iv = INTERVALS.find((i) => i.key === interval)
    const freq = iv?.freq ?? 'day'
    const isDay = interval === 'day' || interval === 'week'

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

    // ★ 按涂层状态决定初始指标 — 不再无条件创建
    const initVis = layerVisibilityRef.current || {}
    if (initVis.vol !== false) {
      chart.createIndicator('VOL', false, { id: 'pane_vol' })
    }
    const initMainInd = loadToolbar().mainIndicator
    if (initVis.ma !== false && initMainInd !== 'None') {
      chart.createIndicator(initMainInd, false, { id: 'candle_pane' })
    }
    const initSubInd = loadToolbar().subIndicator
    if (initVis.macd !== false) {
      chart.createIndicator(initSubInd, false, { id: 'pane_sub' })
    }

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
    fetch(`${API_BASE}/chan/detail/${symbol}?freq=${freq}&count=${iv?.count ?? 500}`)
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
            bis:                 vis.bi      !== false ? bis              : [],
            segs:                vis.seg     !== false ? segs             : [],
            bi_zhongshus:        vis.bi_zs   !== false ? bi_zhongshus     : [],
            bi_zhongshus_decomp: vis.bi_zs_decomp     ? (bi_zhongshus_decomp || []) : [],
            seg_zhongshus:       vis.seg_zs  !== false ? seg_zhongshus    : [],
            bsps:                vis.bsp     !== false ? bsps             : [],
            // 高级分析：使用已缓存的上级别中枢（projection 可能在加载前就已打开）
            higher_zhongshus:    vis.projection && higherLevelRef.current?.bi_zhongshus
                                   ? higherLevelRef.current.bi_zhongshus
                                   : [],
            vis,
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
      saveToolbar({ ...loadToolbar(), mainIndicator: newInd })
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
      saveToolbar({ ...loadToolbar(), subIndicator: newInd })
    },
    [subIndicator]
  )

  // ─── 图层可见性响应 — 联动缠论 + 指标
  useEffect(() => {
    if (!chartRef.current || !layerVisibility) return
    const chart = chartRef.current

    if (chanDataRef.current) {
      const { bis, segs, bi_zhongshus, bi_zhongshus_decomp, seg_zhongshus, bsps, isDay } = chanDataRef.current
      // 清除所有缠论图层（含高级图层）
      chart.removeOverlay({ groupId: 'chan_bi_group' })
      chart.removeOverlay({ groupId: 'chan_seg_group' })
      chart.removeOverlay({ groupId: 'chan_bi_zs_group' })
      chart.removeOverlay({ groupId: 'chan_bi_zs_decomp_group' })
      chart.removeOverlay({ groupId: 'chan_seg_zs_group' })
      chart.removeOverlay({ groupId: 'chan_bsp_group' })
      chart.removeOverlay({ groupId: 'chan_projection_group' })
      chart.removeOverlay({ groupId: 'chan_momentum_group' })
      chart.removeOverlay({ groupId: 'chan_decomp_group' })
      chart.removeOverlay({ groupId: 'chan_support_wall_group' })

      const vis = layerVisibility
      const filteredData = {
        bis:                 vis.bi      !== false ? bis              : [],
        segs:                vis.seg     !== false ? segs             : [],
        bi_zhongshus:        vis.bi_zs   !== false ? bi_zhongshus     : [],
        bi_zhongshus_decomp: vis.bi_zs_decomp     ? (bi_zhongshus_decomp || []) : [],
        seg_zhongshus:       vis.seg_zs  !== false ? seg_zhongshus    : [],
        bsps:                vis.bsp     !== false ? bsps             : [],
        // 高级分析
        higher_zhongshus:    vis.projection && higherLevelRef.current?.bi_zhongshus
                               ? higherLevelRef.current.bi_zhongshus
                               : [],
        vis,
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

  // ─── 区间套投影：当 projection 图层开启时，异步拉取上级别中枢并叠加色带
  // 独立 effect，不影响主图初始化流程；symbol/interval 变化时清缓存
  useEffect(() => {
    higherLevelRef.current = null

    if (!layerVisibility?.projection || !symbol) return

    const parentConfig = PARENT_FREQ_MAP[interval]
    if (!parentConfig) return  // 周线无上级别，静默退出

    fetch(`${API_BASE}/chan/detail/${symbol}?freq=${parentConfig.freq}&count=${parentConfig.count}`)
      .then((r) => r.json())
      .then((json) => {
        if (!json?.data?.bi_zhongshus) return
        higherLevelRef.current = json.data

        // 若图表实例和原始数据均就绪，仅叠加投影图层（不重清其他图层）
        if (chartRef.current && chanDataRef.current) {
          chartRef.current.removeOverlay({ groupId: 'chan_projection_group' })
          const { isDay } = chanDataRef.current
          renderChanOverlays(
            chartRef.current,
            {
              // 其他字段置空，此次调用仅追加投影色带
              bis: [], segs: [], bi_zhongshus: [], bi_zhongshus_decomp: [],
              seg_zhongshus: [], bsps: [],
              higher_zhongshus: json.data.bi_zhongshus,
              vis: { projection: true },
            },
            isDay,
            false,  // clearFirst=false 保留主图已有标注
          )
        }
      })
      .catch((e) => console.warn('[ChanOverlay] 上级别数据拉取失败:', e))
  }, [layerVisibility?.projection, symbol, interval])

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
              onClick={() => {
                setIntervalKey(iv.key)
                saveToolbar({ ...loadToolbar(), interval: iv.key })
              }}
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
