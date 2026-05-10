import { useEffect, useRef, useState, useCallback } from 'react'
import { init, dispose } from 'klinecharts'
import {
  buildChanOverlayBatches,
  clearChanOverlays,
  renderChanOverlayBatchesProgressively,
  renderChanOverlays,
} from '../plugins/chanOverlay.js'
import { API_BASE } from '../config.js'
import { toTimestamp } from '../utils.js'
import {
  isAbortLikeError,
  loadChanDetail,
  loadDisplayOnlyKlines,
  loadJsonOnce,
  loadKlinePreview,
  normalizeChartPayload,
  structureBadgeFromMeta,
} from './klineData.js'
import './KlineChart.css'

// ─── 工具栏状态持久化 key
const TOOLBAR_KEY = 'ct_kline_toolbar_v4'

// ─── 区间套投影：当前级别 → 上级别映射
// 投影色带来自上级别的笔中枢，给小级别提供宏观支撑压力参考
const PARENT_FREQ_MAP = {
  week: null,                          // 周线无上级别
  day:  { freq: 'week', count: 1200 }, // 日线 ← 周线中枢
  m60:  { freq: 'day',  count: 1200 }, // 60分 ← 日线中枢
  m30:  { freq: 'day',  count: 1200 }, // 30分 ← 日线中枢
  m15:  { freq: '30',   count: 1200 }, // 15分 ← 30分中枢
  m5:   { freq: '30',   count: 1200 }, // 5分  ← 30分中枢
  m1:   null,                          // 1分仅展示/盯盘，不叠加正式上级别投影
}

const INTERVALS = [
  { key: 'week', label: '周线', freq: 'week', count: 1200 },
  { key: 'day', label: '日线', freq: 'day', count: 1200 },
  { key: 'm60', label: '60分', freq: '60', count: 1200 },
  { key: 'm30', label: '30分', freq: '30', count: 1200 },
  { key: 'm15', label: '15分', freq: '15', count: 1200 },
  { key: 'm5', label: '5分', freq: '5', count: 1200 },
  { key: 'm1', label: '1分', freq: '1', count: 240, displayOnly: true },
]

function loadToolbar() {
  try {
    const s = localStorage.getItem(TOOLBAR_KEY)
    if (s) {
      const parsed = JSON.parse(s)
      // 回退不受支持的级别到 m5
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

const MAIN_INDICATORS = ['MA', 'BOLL', 'None']
const SUB_INDICATORS = ['MACD', 'KDJ', 'RSI']
const STRUCTURE_DEBOUNCE_MS = 650
const STRUCTURE_POLL_MS = 2000
const STRUCTURE_MAX_POLLS = 20
const QMT_LOG_QUOTE_ENABLED = false
const MIN_INTERACTIVE_BAR_SPACE = 2

export default function KlineChart({ symbol, layerVisibility, refreshToken = 0 }) {
  const chartContainerRef = useRef(null)
  const chartRef = useRef(null)
  const chanDataRef = useRef(null)
  const overlayCancelRef = useRef(null)
  const renderTokenRef = useRef(0)
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
  const [stats, setStats] = useState(null)
  const [configMeta, setConfigMeta] = useState(null)
  const [dataBadge, setDataBadge] = useState(null)
  const [structureBadge, setStructureBadge] = useState(null)
  const [qmtLogQuote, setQmtLogQuote] = useState(null)
  const [oneMinuteStatus, setOneMinuteStatus] = useState({ available: false, reason: '检测中', checked: false })
  const [error, setError] = useState(null)
  const cchanPreset = layerVisibility?.cchan_preset || 'live_tolerant'
  const compactStatus = buildCompactStatus({ dataBadge, structureBadge, stats, configMeta })

  const scheduleChanOverlayRender = useCallback((chart, data, isDay, clearFirst = false, onDone = null, onError = null) => {
    if (!chart || !data) return
    if (overlayCancelRef.current) {
      overlayCancelRef.current()
      overlayCancelRef.current = null
    }
    const token = ++renderTokenRef.current
    const mark = createPerfMark(`overlay:${token}`)
    try {
      const overlays = buildChanOverlayBatches(data, isDay)
      mark('built', { overlays: overlays.length })
      clearChanOverlays(chart, clearFirst)
      if (!overlays.length) {
        mark('done-empty')
        if (onDone) onDone()
        return
      }
      overlayCancelRef.current = renderChanOverlayBatchesProgressively(chart, overlays, {
        chunkSize: 1,
        getCurrentChart: () => chartRef.current,
        onBatch: ({ rendered, total }) => mark('batch', { rendered, total }),
        onDone: () => {
          if (renderTokenRef.current !== token) return
          overlayCancelRef.current = null
          mark('done')
          if (onDone) onDone()
        },
        onError: (e) => {
          if (renderTokenRef.current !== token) return
          overlayCancelRef.current = null
          console.error('[KlineChart] 缠论图层渲染失败:', e)
          if (onError) onError(e)
          if (onDone) onDone()
        },
      })
    } catch (e) {
      console.error('[KlineChart] 缠论图层构建失败:', e)
      if (onError) onError(e)
      if (onDone) onDone()
    }
  }, [])

  const cancelChanOverlayRender = useCallback(() => {
    renderTokenRef.current += 1
    if (overlayCancelRef.current) {
      overlayCancelRef.current()
      overlayCancelRef.current = null
    }
  }, [])

  // 同步 layerVisibility 到 ref（每次渲染都更新，不触发 effect）
  useEffect(() => {
    layerVisibilityRef.current = layerVisibility
  })

  useEffect(() => {
    if (!symbol) return
    let cancelled = false
    Promise.allSettled([
      loadJsonOnce(`${API_BASE}/data/tdx/minute/health?symbol=${encodeURIComponent(symbol)}`),
    ]).then(([tdx]) => {
      if (cancelled) return
      const tdxAvailable = tdx.status === 'fulfilled' && Boolean(tdx.value?.available)
      setOneMinuteStatus({
        available: tdxAvailable,
        reason: tdx.value?.reason || '1分钟展示源不可用',
        checked: true,
      })
    }).catch(() => {
      if (!cancelled) setOneMinuteStatus({ available: false, reason: '1分钟展示源不可用', checked: true })
    })
    return () => { cancelled = true }
  }, [symbol])

  useEffect(() => {
    if (interval !== 'm1' || !oneMinuteStatus.checked || oneMinuteStatus.available) return
    setIntervalKey('m5')
    saveToolbar({ ...loadToolbar(), interval: 'm5' })
  }, [interval, oneMinuteStatus])

  useEffect(() => {
    if (!symbol) return
    if (!QMT_LOG_QUOTE_ENABLED) {
      setQmtLogQuote(null)
      return
    }
    let cancelled = false
    let timer = null

    const loadQuote = async () => {
      try {
        const json = await loadJsonOnce(`${API_BASE}/data/qmt-log/quotes?symbols=${encodeURIComponent(symbol)}`)
        const quote = Array.isArray(json?.quotes) ? json.quotes[0] : null
        if (!cancelled) setQmtLogQuote(quote?.price ? quote : null)
      } catch {
        if (!cancelled) setQmtLogQuote(null)
      }
    }

    loadQuote()
    timer = window.setInterval(loadQuote, 5000)
    return () => {
      cancelled = true
      if (timer) window.clearInterval(timer)
    }
  }, [symbol])

  // ─── 核心 Effect: 初始化图表 + 加载数据 (symbol/interval/refreshToken 变化时完全重建)
  useEffect(() => {
    const container = chartContainerRef.current
    if (!container || !symbol) return

    const iv = INTERVALS.find((i) => i.key === interval)
    const freq = iv?.freq ?? 'day'
    const isDay = interval === 'day' || interval === 'week'
    const isDisplayOnly = Boolean(iv?.displayOnly)

    setLoading(true)
    setError(null)
    setStats(null)
    setConfigMeta(null)
    setDataBadge(null)
    setStructureBadge(null)
    let cancelled = false
    let structureApplied = false
    let structureTimer = null
    let structurePollTimer = null
    let resizeTimer = null
    let resizeFrame = null
    let scrollBoundaryFrame = null
    const interactionProbe = {
      zoomAt: 0,
      scrollAt: 0,
      crosshairAt: 0,
    }
    const previewController = new AbortController()
    const structureController = new AbortController()
    let previewApplied = false

    // 销毁旧实例
    if (chartRef.current) {
      dispose(chartRef.current.getDom?.() || container)
      chartRef.current = null
    }

    // 创建新实例
    const chart = init(container, {
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
            line: { color: 'rgba(250, 204, 21, 0.85)', style: 'dashed', dashedValue: [4, 4], size: 1 },
            text: { color: '#0a0a0f', backgroundColor: '#f0b90b' },
          },
          vertical: {
            line: { color: 'rgba(250, 204, 21, 0.85)', style: 'dashed', dashedValue: [4, 4], size: 1 },
            text: { color: '#0a0a0f', backgroundColor: '#f0b90b' },
          },
        },
      },
    })

    chartRef.current = chart
    primeChartPointerEvents(chart)

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

    const scheduleScrollBoundaryClamp = () => {
      if (scrollBoundaryFrame) {
        window.cancelAnimationFrame(scrollBoundaryFrame)
      }
      scrollBoundaryFrame = window.requestAnimationFrame(() => {
        scrollBoundaryFrame = null
        if (cancelled || chartRef.current !== chart) return
        clampKlineScrollBoundaries(chart)
      })
    }

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

    const rememberZoom = () => {
      interactionProbe.zoomAt = performance.now()
      scheduleScrollBoundaryClamp()
    }
    const rememberScroll = () => {
      interactionProbe.scrollAt = performance.now()
    }
    const rememberCrosshair = () => { interactionProbe.crosshairAt = performance.now() }
    chart.subscribeAction('onZoom', rememberZoom)
    chart.subscribeAction('onScroll', rememberScroll)
    chart.subscribeAction('onCrosshairChange', rememberCrosshair)

    const settleChartLayout = () => {
      if (cancelled || chartRef.current !== chart) return
      chart.resize()
      primeChartPointerEvents(chart)

      if (resizeFrame) {
        window.cancelAnimationFrame(resizeFrame)
      }
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = null
        if (cancelled || chartRef.current !== chart) return
        chart.resize()
        primeChartPointerEvents(chart)
        scheduleScrollBoundaryClamp()
      })
    }

    const scheduleChartLayoutSettle = () => {
      if (resizeTimer) {
        window.clearTimeout(resizeTimer)
      }
      settleChartLayout()
      resizeTimer = window.setTimeout(() => {
        resizeTimer = null
        settleChartLayout()
      }, 120)
    }

    // 响应式缩放
    const resizeObserver = new ResizeObserver(scheduleChartLayoutSettle)
    resizeObserver.observe(container)
    resizeObserver.observe(chart.getDom())
    window.addEventListener('resize', scheduleChartLayoutSettle)

    const handleChartPointerMove = () => {
      primeChartPointerEvents(chart)
    }
    const handleChartWheel = (event) => {
      const before = interactionProbe.zoomAt
      primeChartPointerEvents(chart, event)
      window.setTimeout(() => {
        if (cancelled || chartRef.current !== chart) return
        if (interactionProbe.zoomAt !== before) return
        const scale = Math.sign(-(event.deltaY || 0)) * Math.min(1, Math.abs(event.deltaY || 0) / 100)
        if (!scale) return
        chart.zoomAtCoordinate(scale, { x: event.offsetX, y: event.offsetY })
        debugKlineEvent('wheel_fallback_zoom', {
          symbol,
          interval,
          scale,
          barSpace: chart.getBarSpace?.(),
          visibleRange: chart.getVisibleRange?.(),
        })
      }, 0)
    }
    const chartDom = chart.getDom()
    chartDom?.addEventListener('pointermove', handleChartPointerMove, { passive: true, capture: true })
    chartDom?.addEventListener('wheel', handleChartWheel, { passive: true, capture: true })

    const applyKlinePayload = (payload, { structureReady }) => {
      if (cancelled || chartRef.current !== chart) return false
      if (!payload.klines.length) {
        return false
      }

      const { klines, bis, segs, bi_zhongshus, bi_zhongshus_decomp, seg_zhongshus, stats: s, bsps } = payload
      setStats(structureReady ? s : { kline_count: klines.length, bi_count: 0, seg_count: 0, bi_zs_count: 0, seg_zs_count: 0 })
      setConfigMeta(structureReady ? (payload.config || null) : null)
      setDataBadge(payload.dataBadge || null)
      setStructureBadge(structureReady ? (payload.structureBadge || null) : null)

      const klcData = klines.map((k) => ({
        timestamp: toTimestamp(k.time || k.date, isDay),
        open: k.open,
        high: k.high,
        low: k.low,
        close: k.close,
        volume: k.volume,
      }))

      klcDataCacheRef.current = { data: klcData, isDay }
      chanDataRef.current = {
        bis,
        segs,
        bi_zhongshus,
        bi_zhongshus_decomp: bi_zhongshus_decomp || [],
        seg_zhongshus,
        bsps,
        isDay,
        klineIndexByTimestamp: buildKlineIndexByTimestamp(klcData),
      }

      syncChartData(chart, symbol, periodFromInterval(interval, freq))
      primeChartPointerEvents(chart)
      scheduleScrollBoundaryClamp()
      if (structureReady) {
        setError(null)
      }

      if (!structureReady) return true

      // ★ 初始渲染也必须尊重 layerVisibility（从 localStorage 恢复的状态）
      const filteredData = buildVisibleChanOverlayData(chanDataRef.current, layerVisibility || {}, higherLevelRef.current)
      scheduleChanOverlayRender(chart, filteredData, isDay, false, () => {
        if (!cancelled) setLoading(false)
      }, () => {
        if (!cancelled) setError('结构图层渲染失败，K线可继续查看')
      })
      return true
    }

    const applyPreviewKlines = () => {
      if (cancelled || previewApplied || structureApplied) return
      previewApplied = true
      loadKlinePreview(symbol, interval, iv?.count ?? 500, { signal: previewController.signal })
        .then((json) => {
          if (cancelled || structureApplied) return
          const previewPayload = normalizeChartPayload(json, { isDisplayOnly: false, previewOnly: true })
          if (applyKlinePayload(previewPayload, { structureReady: false })) {
            setLoading(false)
          }
        })
        .catch((e) => {
          if (isAbortLikeError(e)) return
          console.warn('[KlineChart] K线预览加载失败:', e)
        })
    }

    // 发起数据请求。1分只做 K 线展示，不进入正式 CChan 结构链。
    if (isDisplayOnly) {
      loadDisplayOnlyKlines(symbol, iv?.count ?? 240)
        .then((json) => {
          const payload = normalizeChartPayload(json, { isDisplayOnly: true })
          if (!applyKlinePayload(payload, { structureReady: false })) {
            if (!cancelled) setError('暂无数据')
          }
          if (!cancelled) {
            setLoading(false)
          }
        })
        .catch((e) => {
          if (isAbortLikeError(e)) return
          console.error(e)
          if (!cancelled) {
            setError('1分数据不可用')
            setLoading(false)
          }
        })
    } else {
      // 分钟线在小窗口下不走 preview -> formal 双阶段渲染。
      // KLineCharts v10 beta 在 flex 小容器、多 pane、数据重置后容易出现 overlay/crosshair 状态不同步；
      // 分钟线直接使用正式 snapshot 数据，避免同一个图表实例短时间内两次 resetData。
      if (isDay) {
        applyPreviewKlines()
      }

      const loadStructure = (pollCount = 0) => {
        if (cancelled) return
        loadChanDetail(symbol, freq, iv?.count ?? 500, cchanPreset, { signal: structureController.signal })
          .then((json) => {
            const payload = normalizeChartPayload(json, { isDisplayOnly: false })
            if (!payload.klines.length) {
              if (payload.snapshot_status && ['pending', 'missing', 'failed'].includes(payload.snapshot_status)) {
                setStructureBadge(payload.structureBadge || structureBadgeFromMeta(payload))
                if (payload.snapshot_status === 'failed') {
                  setError('结构计算失败，K线可继续查看')
                } else if (!cancelled && pollCount < STRUCTURE_MAX_POLLS) {
                  structurePollTimer = window.setTimeout(() => {
                    loadStructure(pollCount + 1)
                  }, STRUCTURE_POLL_MS)
                }
                if (!isDay) {
                  applyPreviewKlines()
                }
                if (!cancelled) setLoading(false)
                return
              }
              if (!cancelled) {
                setError('暂无数据')
                setLoading(false)
              }
              return
            }
            structureApplied = true
            applyKlinePayload(payload, { structureReady: true })
            if (
              payload.snapshot_status === 'stale' &&
              payload.job &&
              !['SUCCESS', 'SKIPPED', 'FAILED_FINAL'].includes(payload.job.status) &&
              !cancelled &&
              pollCount < STRUCTURE_MAX_POLLS
            ) {
              structurePollTimer = window.setTimeout(() => {
                loadStructure(pollCount + 1)
              }, STRUCTURE_POLL_MS)
            }
          })
          .catch((e) => {
            if (isAbortLikeError(e)) return
            console.error(e)
            if (!cancelled) {
              setError('结构加载失败，K线可继续查看')
              setLoading(false)
            }
          })
      }

      // 快速切换周期时，只为用户最终停留的级别发正式结构计算，避免过期请求挤占后台线程。
      structureTimer = window.setTimeout(() => {
        loadStructure(0)
      }, isDay ? STRUCTURE_DEBOUNCE_MS : 0)
    }

    return () => {
      cancelled = true
      cancelChanOverlayRender()
      if (structureTimer) {
        window.clearTimeout(structureTimer)
      }
      if (structurePollTimer) {
        window.clearTimeout(structurePollTimer)
      }
      if (resizeTimer) {
        window.clearTimeout(resizeTimer)
      }
      if (resizeFrame) {
        window.cancelAnimationFrame(resizeFrame)
      }
      if (scrollBoundaryFrame) {
        window.cancelAnimationFrame(scrollBoundaryFrame)
      }
      previewController.abort()
      structureController.abort()
      resizeObserver.disconnect()
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('resize', scheduleChartLayoutSettle)
      chart.unsubscribeAction('onZoom', rememberZoom)
      chart.unsubscribeAction('onScroll', rememberScroll)
      chart.unsubscribeAction('onCrosshairChange', rememberCrosshair)
      chartDom?.removeEventListener('pointermove', handleChartPointerMove, { capture: true })
      chartDom?.removeEventListener('wheel', handleChartWheel, { capture: true })
      dispose(container)
      if (chartRef.current === chart) {
        chartRef.current = null
      }
    }
  }, [symbol, interval, cchanPreset, refreshToken, scheduleChanOverlayRender, cancelChanOverlayRender])

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
      const { isDay } = chanDataRef.current
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

      const filteredData = buildVisibleChanOverlayData(chanDataRef.current, layerVisibility, higherLevelRef.current)
      scheduleChanOverlayRender(chart, filteredData, isDay, false)
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
  }, [layerVisibility, mainIndicator, subIndicator, scheduleChanOverlayRender])

  // ─── 区间套投影：当 projection 图层开启时，异步拉取上级别中枢并叠加色带
  // 独立 effect，不影响主图初始化流程；symbol/interval 变化时清缓存
  useEffect(() => {
    higherLevelRef.current = null

    if (!layerVisibility?.projection || !symbol) return

    const parentConfig = PARENT_FREQ_MAP[interval]
    if (!parentConfig) return  // 周线无上级别，静默退出

    loadChanDetail(symbol, parentConfig.freq, parentConfig.count, cchanPreset)
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
  }, [layerVisibility?.projection, symbol, interval, cchanPreset, refreshToken])

  return (
    <div className="kline-chart-wrapper">
      {/* 工具栏 */}
      <div className="kline-toolbar">
        <div className="kline-ivs">
          {INTERVALS.map((iv) => (
            <button
              key={iv.key}
              className={`kline-iv-btn ${interval === iv.key ? 'active' : ''} ${iv.displayOnly ? 'is-display-only' : ''}`}
              onClick={() => {
                if (iv.key === 'm1' && !oneMinuteStatus.available) return
                setIntervalKey(iv.key)
                saveToolbar({ ...loadToolbar(), interval: iv.key })
              }}
              disabled={iv.key === 'm1' && !oneMinuteStatus.available}
              title={
                iv.key === 'm1' && !oneMinuteStatus.available
                  ? `1分钟展示源不可用：${oneMinuteStatus.reason}`
                  : (iv.displayOnly ? '1分仅用于盘中展示/历史回放，不确认雷达主推演' : iv.label)
              }
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

        <div className="kline-status-strip">
          {qmtLogQuote && (
            <div className="kline-live-quote" title="QMT日志行情，仅用于盘中preview，不参与正式结构">
              <span>QMT日志</span>
              <strong>{formatQuotePrice(qmtLogQuote.price)}</strong>
              {qmtLogQuote.trade_time && <em>{formatQmtTradeTime(qmtLogQuote.trade_time)}</em>}
            </div>
          )}
          {compactStatus && (
            <div className={`kline-compact-status kline-compact-status--${compactStatus.tone}`} title={compactStatus.title}>
              <span>{compactStatus.source}</span>
              <em>{compactStatus.structure}</em>
              <strong>{compactStatus.stats}</strong>
            </div>
          )}
        </div>
      </div>

      <div className="kline-main-wrapper">
        {loading && <div className="kline-loading">解析缠论结构...</div>}
        {error && <div className="kline-error">{error}</div>}
        <div key={`${symbol}:${interval}`} ref={chartContainerRef} className="kline-container" />
      </div>
    </div>
  )
}

function buildCompactStatus({ dataBadge, structureBadge, stats, configMeta }) {
  if (!dataBadge && !structureBadge && !stats) return null
  const source = compactSourceLabel(dataBadge)
  const structure = compactStructureLabel(structureBadge)
  const statText = compactStatsLabel(stats)
  const title = [
    dataBadge ? `${dataBadge.label}${dataBadge.detail ? ` · ${dataBadge.detail}` : ''}` : '',
    structureBadge ? `${structureBadge.label}${structureBadge.detail ? ` · ${structureBadge.detail}` : ''}` : '',
    statText,
  ].filter(Boolean).join(' | ')
  return {
    source,
    structure,
    stats: statText,
    tone: structureBadge?.tone || dataBadge?.tone || 'history',
    title,
  }
}

function buildVisibleChanOverlayData(chanData, layerVisibility = {}, higherLevelData = null) {
  const vis = layerVisibility || {}
  return {
    klineIndexByTimestamp: chanData?.klineIndexByTimestamp || null,
    bis:                 vis.bi      !== false ? (chanData?.bis || [])              : [],
    segs:                vis.seg     !== false ? (chanData?.segs || [])             : [],
    bi_zhongshus:        vis.bi_zs   !== false ? (chanData?.bi_zhongshus || [])     : [],
    bi_zhongshus_decomp: vis.bi_zs_decomp     ? (chanData?.bi_zhongshus_decomp || []) : [],
    seg_zhongshus:       vis.seg_zs  !== false ? (chanData?.seg_zhongshus || [])    : [],
    bsps:                vis.bsp     !== false ? (chanData?.bsps || [])             : [],
    higher_zhongshus:    vis.projection && higherLevelData?.bi_zhongshus
                           ? higherLevelData.bi_zhongshus
                           : [],
    vis,
  }
}

function buildKlineIndexByTimestamp(klcData) {
  const index = new Map()
  for (let i = 0; i < klcData.length; i += 1) {
    index.set(klcData[i].timestamp, i)
  }
  return index
}

function compactSourceLabel(dataBadge) {
  if (!dataBadge) return 'K线'
  const label = String(dataBadge.label || 'K线')
    .replace(' · K线预览', '')
    .replace('BaoStock 前复权', 'BS')
    .replace('BaoStock', 'BS')
    .replace('TDX本地分钟', 'TDX')
    .replace('TDX本地', 'TDX')
  const date = compactDate(dataBadge.detail)
  return date ? `${label} · ${date}` : label
}

function compactStructureLabel(structureBadge) {
  if (!structureBadge) return '结构待载入'
  const label = String(structureBadge.label || '结构')
    .replace('结构 · ', '')
    .replace('旧快照', '待刷新')
  return label || '结构'
}

function compactStatsLabel(stats) {
  if (!stats) return ''
  return [
    `${stats.kline_count || 0}根`,
    `${stats.bi_count || 0}笔`,
    `${stats.seg_count || 0}段`,
  ].join(' ')
}

function compactDate(value) {
  const text = String(value || '')
  const match = text.match(/\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?/)
  if (!match) return ''
  return match[0].replace(/^\d{4}-/, '').replace(/:00$/, '')
}

function periodFromInterval(interval, freq) {
  if (interval === 'week' || freq === 'week') {
    return { span: 1, type: 'week' }
  }
  if (interval === 'day' || freq === 'day') {
    return { span: 1, type: 'day' }
  }
  const minuteSpan = Number(String(freq || interval || '').replace(/^m/, ''))
  return { span: Number.isFinite(minuteSpan) && minuteSpan > 0 ? minuteSpan : 1, type: 'minute' }
}

function syncChartData(chart, symbol, period) {
  const currentSymbol = chart.getSymbol()
  const currentPeriod = chart.getPeriod()
  const symbolChanged = currentSymbol?.ticker !== symbol
  const periodChanged = currentPeriod?.type !== period.type || currentPeriod?.span !== period.span

  if (symbolChanged) {
    chart.setSymbol({ ticker: symbol })
  }
  if (periodChanged) {
    chart.setPeriod(period)
  }
  if (!symbolChanged && !periodChanged) {
    chart.resetData()
  }
}

function clampKlineScrollBoundaries(chart) {
  const dataCount = chart?.getDataList?.()?.length || 0
  if (!chart || dataCount <= 0) return

  // 左侧历史边界不能露空；右侧保留未来留白，方便把最新 K 线放到画面中间。
  clampKlineMinBarSpace(chart, dataCount)
  chart.setMaxOffsetLeftDistance(0)
  chart.setMaxOffsetRightDistance(getRightPreviewOffsetDistance(chart))
  chart.scrollByDistance(0)
}

function clampKlineMinBarSpace(chart, dataCount) {
  const chartWidth = Number(chart?.getDom?.()?.clientWidth)
  const currentBar = Number(chart?.getBarSpace?.()?.bar)
  if (!Number.isFinite(chartWidth) || chartWidth <= 0 || !Number.isFinite(currentBar) || currentBar <= 0) {
    return
  }
  const minBar = Math.max(chartWidth / Math.max(dataCount, 1), MIN_INTERACTIVE_BAR_SPACE)
  if (currentBar < minBar) {
    chart.setBarSpace(minBar)
  }
}

function getRightPreviewOffsetDistance(chart) {
  const chartWidth = Number(chart?.getDom?.()?.clientWidth)
  return Number.isFinite(chartWidth) && chartWidth > 0 ? chartWidth * 0.5 : 600
}

function primeChartPointerEvents(chart, sourceEvent = null) {
  const target = chart?.getDom?.()
  if (!target || typeof window === 'undefined') return

  window.requestAnimationFrame(() => {
    if (!target.isConnected) return
    const rect = target.getBoundingClientRect()
    if (!rect.width || !rect.height) return
    const clientX = Number.isFinite(sourceEvent?.clientX) ? sourceEvent.clientX : rect.left + rect.width / 2
    const clientY = Number.isFinite(sourceEvent?.clientY) ? sourceEvent.clientY : rect.top + rect.height / 2

    // KLineCharts v10 只在 mouseenter 后绑定 mousemove/wheel。
    // React 切换股票/周期会重建 chart；若鼠标停在原位置，新容器收不到自然 mouseenter，
    // 十字星和滚轮缩放就会偶发失效。这里补发一次进入事件，只用于恢复内部事件绑定。
    target.dispatchEvent(new MouseEvent('mouseenter', {
      view: window,
      bubbles: false,
      cancelable: false,
      clientX,
      clientY,
    }))
  })
}

function debugKlineEvent(stage, extra = {}) {
  if (localStorage.getItem('ct_kline_debug') !== '1') return
  console.debug('[KlineDebug]', stage, extra)
}

function createPerfMark(label) {
  const enabled = localStorage.getItem('ct_kline_perf') === '1'
  const started = performance.now()
  let last = started
  return (stage, extra = {}) => {
    if (!enabled) return
    const now = performance.now()
    console.debug('[KlinePerf]', label, stage, {
      total_ms: Math.round(now - started),
      delta_ms: Math.round(now - last),
      ...extra,
    })
    last = now
  }
}

function formatQuotePrice(value) {
  const num = Number(value)
  if (!Number.isFinite(num) || num <= 0) return '--'
  return num >= 100 ? num.toFixed(2) : num.toFixed(3).replace(/0$/, '').replace(/0$/, '')
}

function formatQmtTradeTime(value) {
  const text = String(value || '')
  if (text.length >= 14) {
    return `${text.slice(8, 10)}:${text.slice(10, 12)}:${text.slice(12, 14)}`
  }
  return text
}
