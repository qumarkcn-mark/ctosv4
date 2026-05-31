import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { dispose, init } from 'klinecharts'
import {
  KLINE_PERIODS,
  fetchCurrentPrice,
  fetchKlines,
  fetchMomentumContext,
  fetchStructureOverlay,
  getKlinePeriod,
  normalizeKlinePeriod,
  syncKlines,
} from './klineClient.js'
import {
  readKlinePreferences,
  writeKlinePreference,
} from './klinePreferences.js'
import {
  buildKlineTimeIndex,
  resolveOverlayIndex,
} from './klineOverlayProjection.js'
import './PriceEvidenceView.css'

const KLINE_COUNT = 1200
const CANDLE_PANE_ID = 'candle_pane'
const SUB_PANE_ID = 'ct-sub-indicator-pane'
const VOLUME_HEIGHT = 112
const X_AXIS_HEIGHT = 28
const MAIN_INDICATORS = [
  { value: 'MA', label: 'MA' },
  { value: 'BOLL', label: 'BOLL' },
  { value: 'NONE', label: '裸K' },
]
const SUB_INDICATORS = [
  { value: 'VOL', label: 'VOL' },
  { value: 'MACD', label: 'MACD' },
  { value: 'RSI', label: 'RSI' },
  { value: 'NONE', label: '无' },
]

export default function PriceEvidenceView({ symbol, symbolName, chartContext, onAddToWatchlist }) {
  const chartHostRef = useRef(null)
  const chartRef = useRef(null)
  const latestBarsRef = useRef([])
  const quoteRef = useRef(null)
  const subIndicatorRef = useRef(readKlinePreferences().subIndicator)
  const structureLayerRef = useRef(true)
  const structureViewRef = useRef(null)
  const momentumLayerRef = useRef(false)
  const momentumContextRef = useRef(null)
  const aiEvidenceContextRef = useRef(null)
  const paneHeightRef = useRef(0)
  const requestRef = useRef(0)
  const structureRequestRef = useRef(0)
  const mountedRef = useRef(false)
  const initialPrefs = useMemo(() => readKlinePreferences(), [])
  const [period, setPeriod] = useState(() => normalizeKlinePeriod(initialPrefs.period))
  const [mainIndicator, setMainIndicator] = useState(() => normalizeIndicator(initialPrefs.mainIndicator, MAIN_INDICATORS, 'MA'))
  const [subIndicator, setSubIndicator] = useState(() => normalizeIndicator(initialPrefs.subIndicator, SUB_INDICATORS, 'VOL'))
  const [structureLayer, setStructureLayer] = useState(() => initialPrefs.structureLayer !== 'off')
  const [momentumLayer, setMomentumLayer] = useState(() => initialPrefs.momentumLayer === 'on')
  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState('')
  const [barCount, setBarCount] = useState(0)
  const [quote, setQuote] = useState(null)
  const [structureView, setStructureView] = useState(null)
  const [structureOverlay, setStructureOverlay] = useState(null)
  const [momentumContext, setMomentumContext] = useState(null)
  const [momentumOverlay, setMomentumOverlay] = useState(null)
  const [aiEvidenceOverlay, setAiEvidenceOverlay] = useState(null)
  const [overlaySize, setOverlaySize] = useState({ width: 0, height: 0 })
  const [structureStatus, setStructureStatus] = useState('idle')
  const [momentumStatus, setMomentumStatus] = useState('idle')
  const [addingToWatchlist, setAddingToWatchlist] = useState(false)
  const [watchlistMessage, setWatchlistMessage] = useState('')
  const activePeriod = useMemo(() => getKlinePeriod(period), [period])
  const supportsStructureLayers = activePeriod.supportsStructure !== false

  const updatePaneHeights = useCallback(() => {
    const chart = chartRef.current
    const host = chartHostRef.current
    if (!chart || !host) return
    setOverlaySize({ width: host.clientWidth, height: host.clientHeight })
    const subHeight = subIndicatorRef.current === 'NONE' ? 0 : VOLUME_HEIGHT
    const candleHeight = Math.max(260, Math.floor(host.clientHeight - subHeight - X_AXIS_HEIGHT))
    if (Math.abs(candleHeight - paneHeightRef.current) < 2) return
    paneHeightRef.current = candleHeight
    chart.setPaneOptions({
      id: CANDLE_PANE_ID,
      order: 0,
      height: candleHeight,
      minHeight: 260,
      dragEnabled: false,
    })
  }, [])

  const updateStructureOverlay = useCallback(() => {
    const chart = chartRef.current
    const host = chartHostRef.current
    const view = structureViewRef.current
    const bars = latestBarsRef.current
    if (!chart || !host || !view || !structureLayerRef.current) {
      setStructureOverlay(null)
      return
    }
    const viewport = {
      width: host.clientWidth,
      height: host.clientHeight,
      range: chart.getVisibleRange(),
    }
    const timeIndex = buildKlineTimeIndex(bars)
    const bis = (view.bis || [])
      .map((item) => {
      const startIndex = resolveOverlayIndex(timeIndex, item.start_timestamp, item.start_time, item.start_index)
      const endIndex = resolveOverlayIndex(timeIndex, item.end_timestamp, item.end_time, item.end_index)
      if (!isStructureItemVisible(startIndex, endIndex, viewport.range, 2)) return null
      const start = chart.convertToPixel(
        { dataIndex: startIndex, value: item.start_price },
        { paneId: CANDLE_PANE_ID, absolute: false }
      )
      const end = chart.convertToPixel(
        { dataIndex: endIndex, value: item.end_price },
        { paneId: CANDLE_PANE_ID, absolute: false }
      )
      if (!validPoint(start) || !validPoint(end)) return null
      const clipped = clipLineToViewport(start, end, viewport)
      if (!clipped) return null
      return { ...item, start_index: startIndex, end_index: endIndex, start: clipped.start, end: clipped.end }
    }).filter(Boolean)
    const segments = (view.segments || [])
      .map((item) => {
        const startIndex = resolveOverlayIndex(timeIndex, item.start_timestamp, item.start_time, item.start_index)
        const endIndex = resolveOverlayIndex(timeIndex, item.end_timestamp, item.end_time, item.end_index)
        if (!isStructureItemVisible(startIndex, endIndex, viewport.range, 2)) return null
        const start = chart.convertToPixel(
          { dataIndex: startIndex, value: item.start_price },
          { paneId: CANDLE_PANE_ID, absolute: false }
        )
        const end = chart.convertToPixel(
          { dataIndex: endIndex, value: item.end_price },
          { paneId: CANDLE_PANE_ID, absolute: false }
        )
        if (!validPoint(start) || !validPoint(end)) return null
        const clipped = clipLineToViewport(start, end, viewport)
        if (!clipped) return null
        return { ...item, start_index: startIndex, end_index: endIndex, start: clipped.start, end: clipped.end }
      })
      .filter(Boolean)
    let unfinishedBi = null
    if (view.unfinished_bi) {
      const item = view.unfinished_bi
      const startIndex = resolveOverlayIndex(timeIndex, item.start_timestamp, item.start_time, item.start_index)
      const endIndex = resolveOverlayIndex(timeIndex, item.end_timestamp, item.end_time, item.end_index)
      if (isStructureItemVisible(startIndex, endIndex, viewport.range, 2)) {
        const start = chart.convertToPixel(
          { dataIndex: startIndex, value: item.start_price },
          { paneId: CANDLE_PANE_ID, absolute: false }
        )
        const end = chart.convertToPixel(
          { dataIndex: endIndex, value: item.end_price },
          { paneId: CANDLE_PANE_ID, absolute: false }
        )
        if (validPoint(start) && validPoint(end)) {
          const clipped = clipLineToViewport(start, end, viewport)
          if (clipped) {
            unfinishedBi = { ...item, start_index: startIndex, end_index: endIndex, start: clipped.start, end: clipped.end }
          }
        }
      }
    }
    const centers = (view.centers || [])
      .map((item) => {
      const beginIndex = resolveOverlayIndex(
        timeIndex,
        item.begin_bar_timestamp || item.begin_timestamp,
        item.begin_bar_time || item.begin_time,
        item.begin_index
      )
      const endIndex = resolveOverlayIndex(
        timeIndex,
        item.end_bar_timestamp || item.end_timestamp,
        item.end_bar_time || item.end_time,
        item.end_index
      )
      if (!isStructureItemVisible(beginIndex, endIndex, viewport.range, 2)) return null
      const leftTop = chart.convertToPixel(
        { dataIndex: beginIndex, value: item.zg },
        { paneId: CANDLE_PANE_ID, absolute: false }
      )
      const rightBottom = chart.convertToPixel(
        { dataIndex: endIndex, value: item.zd },
        { paneId: CANDLE_PANE_ID, absolute: false }
      )
      if (!validPoint(leftTop) || !validPoint(rightBottom)) return null
      const beginHalfWidth = estimateHalfBarWidth(chart, beginIndex, item.zg, bars.length)
      const endHalfWidth = estimateHalfBarWidth(chart, endIndex, item.zd, bars.length)
      const leftX = Math.min(leftTop.x, rightBottom.x) - beginHalfWidth
      const rightX = Math.max(leftTop.x, rightBottom.x) + endHalfWidth
      return {
        ...item,
        begin_index: beginIndex,
        end_index: endIndex,
        x: leftX,
        y: Math.min(leftTop.y, rightBottom.y),
        width: Math.max(2, rightX - leftX),
        height: Math.abs(rightBottom.y - leftTop.y),
      }
    }).map((item) => clipRectToViewport(item, viewport))
      .filter((item) => item && item.width > 2 && item.height > 2)
    setStructureOverlay({ bis, unfinishedBi, segments, centers })
  }, [])

  const updateMomentumOverlay = useCallback(() => {
    const chart = chartRef.current
    const host = chartHostRef.current
    const context = momentumContextRef.current
    if (!chart || !host || !context || !momentumLayerRef.current) {
      setMomentumOverlay(null)
      return
    }
    const viewport = {
      width: host.clientWidth,
      height: host.clientHeight,
      range: chart.getVisibleRange(),
    }
    const previous = momentumLegToOverlay(chart, context.previous_leg, viewport, 'previous')
    const current = momentumLegToOverlay(chart, context.current_leg, viewport, 'current')
    const legs = [previous, current].filter(Boolean)
    setMomentumOverlay(legs.length ? { legs, verdict: context.verdict || {} } : null)
  }, [])

  const updateAiEvidenceOverlay = useCallback(() => {
    const chart = chartRef.current
    const host = chartHostRef.current
    const context = aiEvidenceContextRef.current
    if (!chart || !host || !context || !chartContextMatchesPeriod(context, period)) {
      setAiEvidenceOverlay(null)
      return
    }
    const viewport = {
      width: host.clientWidth,
      height: host.clientHeight,
      range: chart.getVisibleRange(),
    }
    const overlays = context.overlays || {}
    const centerSource = overlays.active_center || null
    const center = evidenceCenterToOverlay(chart, centerSource, viewport)
    const lines = (Array.isArray(overlays.lines) ? overlays.lines : [])
      .filter((item) => !isCenterBoundaryEvidence(item, centerSource))
      .map((item) => evidenceLineToOverlay(chart, item, viewport))
      .filter(Boolean)
    setAiEvidenceOverlay(center || lines.length ? { center, lines, level: context.level } : null)
  }, [period])

  const clampScrollBoundaries = useCallback(() => {
    const chart = chartRef.current
    const bars = latestBarsRef.current
    if (!chart || !bars.length) return
    try {
      chart.setMaxOffsetLeftDistance(0)
      chart.setMaxOffsetRightDistance(Math.min(88, Math.max(36, bars.length * 0.08)))
      const range = chart.getVisibleRange()
      if (range.realFrom < 0) chart.scrollToDataIndex(0, 0)
      if (range.realTo > bars.length + 12) chart.scrollToDataIndex(bars.length - 1, 0)
    } catch {
      // klinecharts beta 的边界 API 可能变化；失败时不阻断主图渲染。
    }
  }, [])

  const handleChartViewportChange = useCallback(() => {
    clampScrollBoundaries()
    updateStructureOverlay()
    updateMomentumOverlay()
    updateAiEvidenceOverlay()
  }, [clampScrollBoundaries, updateAiEvidenceOverlay, updateMomentumOverlay, updateStructureOverlay])

  const loadQuote = useCallback(async () => {
    if (!symbol) return
    try {
      const nextQuote = await fetchCurrentPrice(symbol)
      if (mountedRef.current) setQuote(nextQuote)
    } catch {
      if (mountedRef.current) setQuote(null)
    }
  }, [symbol])

  const loadBars = useCallback(async () => {
    if (!symbol) return []
    const requestId = requestRef.current + 1
    requestRef.current = requestId
    setLoading(true)
    setError('')
    try {
      const result = await fetchKlines(symbol, period, KLINE_COUNT)
      if (requestRef.current !== requestId || !mountedRef.current) return []
      latestBarsRef.current = result.klines
      setBarCount(result.klines.length)
      setError('')
      return result.klines
    } catch (err) {
      if (requestRef.current === requestId && mountedRef.current) {
        latestBarsRef.current = []
        setBarCount(0)
        setError(err?.message || 'K 线加载失败')
      }
      return []
    } finally {
      if (requestRef.current === requestId && mountedRef.current) setLoading(false)
    }
  }, [period, symbol])

  const loadStructureOverlay = useCallback(async () => {
    const requestId = structureRequestRef.current + 1
    structureRequestRef.current = requestId
    structureViewRef.current = null
    setStructureView(null)
    setStructureOverlay(null)
    if (!symbol || !structureLayer || !supportsStructureLayers) {
      setStructureStatus('idle')
      return
    }
    setStructureStatus('loading')
    try {
      const view = await fetchStructureOverlay(symbol, period, KLINE_COUNT)
      if (structureRequestRef.current !== requestId || !mountedRef.current) return
      structureViewRef.current = view
      setStructureView(view)
      setStructureStatus('ready')
      requestAnimationFrame(updateStructureOverlay)
    } catch {
      if (structureRequestRef.current !== requestId || !mountedRef.current) return
      structureViewRef.current = null
      setStructureStatus('missing')
    }
  }, [period, structureLayer, supportsStructureLayers, symbol, updateStructureOverlay])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    quoteRef.current = quote
  }, [quote])

  useEffect(() => {
    if (!chartHostRef.current || chartRef.current) return
    dispose(chartHostRef.current)
    chartHostRef.current.replaceChildren()
    chartHostRef.current.removeAttribute('k-line-chart-id')
    const chart = init(chartHostRef.current, {
      locale: 'zh-CN',
      timezone: 'Asia/Shanghai',
      zoomAnchor: { main: 'cursor', xAxis: 'cursor' },
      styles: buildChartStyles(),
      formatter: {
        formatDate: ({ timestamp, type }) => formatChartDate(timestamp, type),
        formatBigNumber: formatBigNumber,
      },
      layout: [
        {
          type: 'candle',
          options: { id: CANDLE_PANE_ID, order: 0, minHeight: 260 },
        },
        {
          type: 'xAxis',
          options: {
            order: Number.MAX_SAFE_INTEGER,
            height: X_AXIS_HEIGHT,
            minHeight: 24,
            dragEnabled: false,
          },
        },
      ],
    })
    chartRef.current = chart
    chart?.setSymbol({ ticker: symbol || 'CT-OS', pricePrecision: 2, volumePrecision: 0 })
    applyMainIndicator(chart, mainIndicator)
    applySubIndicator(chart, subIndicator)
    updatePaneHeights()
    chart?.subscribeAction('onScroll', handleChartViewportChange)
    chart?.subscribeAction('onZoom', handleChartViewportChange)
    chart?.subscribeAction('onVisibleRangeChange', handleChartViewportChange)
    return () => {
      try {
        chart?.unsubscribeAction('onScroll', handleChartViewportChange)
        chart?.unsubscribeAction('onZoom', handleChartViewportChange)
        chart?.unsubscribeAction('onVisibleRangeChange', handleChartViewportChange)
      } finally {
        dispose(chart)
        if (chartHostRef.current) {
          chartHostRef.current.replaceChildren()
          chartHostRef.current.removeAttribute('k-line-chart-id')
        }
        chartRef.current = null
      }
    }
  }, [handleChartViewportChange, symbol, updatePaneHeights])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !symbol) return
    chart.setSymbol({ ticker: symbol, name: symbolName || symbol, pricePrecision: 2, volumePrecision: 0 })
    chart.setPeriod({ type: activePeriod.chartType, span: activePeriod.chartSpan })
    chart.setDataLoader({
      getBars: async ({ callback }) => {
        const bars = await loadBars()
        callback(bars, { backward: false, forward: false })
        requestAnimationFrame(() => {
          chart.scrollToRealTime(0)
          handleChartViewportChange()
        })
      },
    })
    chart.resetData()
    loadQuote()
  }, [activePeriod, handleChartViewportChange, loadBars, loadQuote, symbol, symbolName])

  useEffect(() => {
    loadStructureOverlay()
    return () => {
      structureRequestRef.current += 1
    }
  }, [loadStructureOverlay])

  useEffect(() => {
    let cancelled = false
    momentumContextRef.current = null
    setMomentumContext(null)
    setMomentumOverlay(null)
    if (!symbol || !momentumLayer || !supportsStructureLayers) {
      setMomentumStatus('idle')
      return () => {
        cancelled = true
      }
    }
    setMomentumStatus('loading')
    fetchMomentumContext(symbol, period, KLINE_COUNT)
      .then((context) => {
        if (cancelled) return
        momentumContextRef.current = context
        setMomentumContext(context)
        setMomentumStatus(context?.status === 'insufficient_data' ? 'insufficient' : 'ready')
        requestAnimationFrame(updateMomentumOverlay)
      })
      .catch(() => {
        if (cancelled) return
        momentumContextRef.current = null
        setMomentumStatus('missing')
      })
    return () => {
      cancelled = true
    }
  }, [momentumLayer, period, supportsStructureLayers, symbol, updateMomentumOverlay])

  useEffect(() => {
    aiEvidenceContextRef.current = chartContext || null
    updateAiEvidenceOverlay()
  }, [chartContext, updateAiEvidenceOverlay])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    applyMainIndicator(chart, mainIndicator)
    writeKlinePreference('main_indicator', mainIndicator)
  }, [mainIndicator])

  useEffect(() => {
    const chart = chartRef.current
    subIndicatorRef.current = subIndicator
    if (!chart) return
    applySubIndicator(chart, subIndicator)
    updatePaneHeights()
    writeKlinePreference('sub_indicator', subIndicator)
  }, [subIndicator, updatePaneHeights])

  useEffect(() => {
    structureLayerRef.current = structureLayer
    writeKlinePreference('structure_layer', structureLayer ? 'on' : 'off')
    updateStructureOverlay()
  }, [structureLayer, updateStructureOverlay])

  useEffect(() => {
    momentumLayerRef.current = momentumLayer
    writeKlinePreference('momentum_layer', momentumLayer ? 'on' : 'off')
    updateMomentumOverlay()
  }, [momentumLayer, updateMomentumOverlay])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const resizeObserver = new ResizeObserver(() => {
      updatePaneHeights()
      chart.resize()
      requestAnimationFrame(() => {
        handleChartViewportChange()
      })
    })
    if (chartHostRef.current) resizeObserver.observe(chartHostRef.current)
    return () => resizeObserver.disconnect()
  }, [handleChartViewportChange, updatePaneHeights])

  const handlePeriodChange = (nextPeriod) => {
    const normalized = normalizeKlinePeriod(nextPeriod)
    setPeriod(normalized)
    writeKlinePreference('period', normalized)
  }

  const handleMainIndicatorChange = (nextIndicator) => {
    setMainIndicator(normalizeIndicator(nextIndicator, MAIN_INDICATORS, 'MA'))
  }

  const handleSubIndicatorChange = (nextIndicator) => {
    setSubIndicator(normalizeIndicator(nextIndicator, SUB_INDICATORS, 'VOL'))
  }

  const handleStructureLayerToggle = () => {
    setStructureLayer((current) => !current)
  }

  const handleMomentumLayerToggle = () => {
    setMomentumLayer((current) => !current)
  }

  const handleRefresh = async () => {
    if (!symbol || syncing) return
    setSyncing(true)
    setError('')
    try {
      await syncKlines(symbol, period)
      chartRef.current?.resetData()
      await loadStructureOverlay()
      await loadQuote()
    } catch (err) {
      setError(err?.message || '同步失败')
    } finally {
      setSyncing(false)
    }
  }

  const handleAddToWatchlist = async () => {
    if (!onAddToWatchlist || addingToWatchlist) return
    setAddingToWatchlist(true)
    setWatchlistMessage('')
    try {
      const result = await onAddToWatchlist()
      setWatchlistMessage(result?.message || '已加入自选')
    } catch (err) {
      setWatchlistMessage(err?.message || '加入失败')
    } finally {
      setAddingToWatchlist(false)
    }
  }

  return (
    <section className="base-kline">
      <header className="base-kline__toolbar">
        <div className="base-kline__title">
          <span>K 线</span>
          <strong>{symbolName || symbol}</strong>
          <em>{symbol}</em>
          <button
            type="button"
            className="base-kline__watchlist-btn"
            onClick={handleAddToWatchlist}
            disabled={!symbol || addingToWatchlist || !onAddToWatchlist}
            title="加入左侧自选股"
          >
            {addingToWatchlist ? '加入中' : '+ 自选'}
          </button>
          {watchlistMessage && <small>{watchlistMessage}</small>}
        </div>

        <div className="base-kline__control-strip">
          <div className="base-kline__periods" role="tablist" aria-label="K 线周期">
            {KLINE_PERIODS.map((item) => (
              <button
                key={item.value}
                type="button"
                className={period === item.value ? 'is-active' : ''}
                onClick={() => handlePeriodChange(item.value)}
                role="tab"
                aria-selected={period === item.value}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="base-kline__indicators" aria-label="K 线指标">
            <div>
              <span>主图</span>
              {MAIN_INDICATORS.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  className={mainIndicator === item.value ? 'is-active' : ''}
                  onClick={() => handleMainIndicatorChange(item.value)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div>
              <span>副图</span>
              {SUB_INDICATORS.map((item) => (
                <button
                  key={item.value}
                  type="button"
                  className={subIndicator === item.value ? 'is-active' : ''}
                  onClick={() => handleSubIndicatorChange(item.value)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="base-kline__status">
          <div className="base-kline__structure-meta">
            <div className="base-kline__layer-controls" aria-label="K线图层">
              <button
                type="button"
                className={structureLayer ? 'is-layer-active' : ''}
                onClick={handleStructureLayerToggle}
                disabled={!supportsStructureLayers}
                title="显示/隐藏 CZSC 笔与中枢；线段仅在 CZSC 原生提供时显示"
              >
                结构
              </button>
              <button
                type="button"
                className={momentumLayer ? 'is-layer-active' : ''}
                onClick={handleMomentumLayerToggle}
                disabled={!supportsStructureLayers}
                title="显示/隐藏当前段与上一同向段力量对比"
              >
                力量
              </button>
            </div>
            {structureLayer && supportsStructureLayers && (
              <div className="base-kline__legend" aria-label="结构图例">
                <span><i className="is-bi" />笔</span>
                <span><i className="is-center" />中枢</span>
                {(structureView?.segments?.length || 0) > 0 && <span><i className="is-segment" />线段</span>}
              </div>
            )}
            <span>{loading ? '加载中' : `${barCount} 根`}</span>
            {!supportsStructureLayers && <span>TDX 1分</span>}
            {structureLayer && supportsStructureLayers && <span>{structureStatusLabel(structureStatus, structureView)}</span>}
            {momentumLayer && supportsStructureLayers && <span>{momentumStatusLabel(momentumStatus, momentumContext)}</span>}
          </div>
          <div className="base-kline__quote-actions">
            {quote?.price && (
              <strong className={Number(quote.change_pct) >= 0 ? 'is-up' : 'is-down'}>
                {formatPrice(quote.price)}
              </strong>
            )}
            <button type="button" onClick={handleRefresh} disabled={syncing || loading}>
              {syncing ? '同步中' : '刷新'}
            </button>
          </div>
        </div>
      </header>

      <div className="base-kline__canvas">
        <div ref={chartHostRef} className="base-kline__host" />
        {structureOverlay && (
          <svg
            className="base-kline__structure-layer"
            viewBox={`0 0 ${overlaySize.width || 1} ${overlaySize.height || 1}`}
            aria-hidden="true"
          >
            {structureOverlay.centers.map((item) => (
              <rect
                key={item.id}
                className={`base-kline__center ${item.active ? 'is-active' : ''}`}
                x={item.x}
                y={item.y}
                width={item.width}
                height={item.height}
              />
            ))}
            {structureOverlay.bis.map((item) => (
              <line
                key={item.id}
                className={`base-kline__bi ${item.is_up ? 'is-up' : 'is-down'} ${item.is_sure ? '' : 'is-unsure'}`}
                x1={item.start.x}
                y1={item.start.y}
                x2={item.end.x}
                y2={item.end.y}
              />
            ))}
            {structureOverlay.unfinishedBi && (
              <line
                key={structureOverlay.unfinishedBi.id}
                className={`base-kline__bi ${structureOverlay.unfinishedBi.is_up ? 'is-up' : 'is-down'} is-unsure`}
                x1={structureOverlay.unfinishedBi.start.x}
                y1={structureOverlay.unfinishedBi.start.y}
                x2={structureOverlay.unfinishedBi.end.x}
                y2={structureOverlay.unfinishedBi.end.y}
              />
            )}
            {structureOverlay.segments?.map((item) => (
              <line
                key={item.id}
                className={`base-kline__segment ${item.is_up ? 'is-up' : 'is-down'} ${item.is_sure ? '' : 'is-unsure'}`}
                x1={item.start.x}
                y1={item.start.y}
                x2={item.end.x}
                y2={item.end.y}
              />
            ))}
          </svg>
        )}
        {momentumOverlay && (
          <svg
            className="base-kline__momentum-layer"
            viewBox={`0 0 ${overlaySize.width || 1} ${overlaySize.height || 1}`}
            aria-hidden="true"
          >
            {momentumOverlay.legs.map((item) => (
              <g key={item.key} className={`base-kline__momentum-leg is-${item.kind}`}>
                <line x1={item.start.x} y1={item.start.y} x2={item.end.x} y2={item.end.y} />
                <circle cx={item.start.x} cy={item.start.y} r="3" />
                <circle cx={item.end.x} cy={item.end.y} r="3" />
                <text x={item.label.x} y={item.label.y}>{item.label.text}</text>
              </g>
            ))}
          </svg>
        )}
        {aiEvidenceOverlay && (
          <svg
            className="base-kline__ai-evidence-layer"
            viewBox={`0 0 ${overlaySize.width || 1} ${overlaySize.height || 1}`}
            aria-hidden="true"
          >
            {aiEvidenceOverlay.center && (
              <rect
                className="base-kline__ai-evidence-center"
                x={aiEvidenceOverlay.center.x}
                y={aiEvidenceOverlay.center.y}
                width={aiEvidenceOverlay.center.width}
                height={aiEvidenceOverlay.center.height}
              />
            )}
            {aiEvidenceOverlay.lines.map((item) => (
              <g key={item.key} className={`base-kline__ai-evidence-line is-${item.role}`}>
                <line x1={item.x1} y1={item.y} x2={item.x2} y2={item.y} />
                <text x={item.labelX} y={item.y - 7}>{item.label}</text>
                <text x={item.labelX} y={item.y + 12}>{formatPrice(item.price)}</text>
              </g>
            ))}
          </svg>
        )}
        {loading && <div className="base-kline__loading">加载 K 线数据</div>}
        {error && <div className="base-kline__error">{error}</div>}
        {!loading && !error && barCount === 0 && (
          <div className="base-kline__empty">
            <strong>没有 K 线数据</strong>
            <span>可以先点击刷新，后台会同步当前股票并排队生成 V5 结构。</span>
          </div>
        )}
      </div>
    </section>
  )
}

function validPoint(point) {
  return point && Number.isFinite(point.x) && Number.isFinite(point.y)
}

function estimateHalfBarWidth(chart, dataIndex, value, barCount) {
  const index = Number(dataIndex)
  if (!chart || !Number.isFinite(index)) return 3
  const baseValue = Number(value)
  const yValue = Number.isFinite(baseValue) && baseValue > 0 ? baseValue : 1
  const neighborIndex = index < barCount - 1 ? index + 1 : index - 1
  if (neighborIndex < 0 || neighborIndex >= barCount) return 3
  const point = chart.convertToPixel(
    { dataIndex: index, value: yValue },
    { paneId: CANDLE_PANE_ID, absolute: false }
  )
  const neighbor = chart.convertToPixel(
    { dataIndex: neighborIndex, value: yValue },
    { paneId: CANDLE_PANE_ID, absolute: false }
  )
  if (!validPoint(point) || !validPoint(neighbor)) return 3
  const distance = Math.abs(neighbor.x - point.x)
  if (!Number.isFinite(distance) || distance <= 0) return 3
  return Math.max(2, Math.min(16, distance / 2))
}

function isStructureItemContained(startIndex, endIndex, range, padding = 0) {
  const start = Number(startIndex)
  const end = Number(endIndex)
  if (!Number.isFinite(start) || !Number.isFinite(end) || !range) return false
  const left = Math.min(start, end)
  const right = Math.max(start, end)
  return left >= range.from - padding && right <= range.to + padding
}

function isStructureItemVisible(startIndex, endIndex, range, padding = 0) {
  const start = Number(startIndex)
  const end = Number(endIndex)
  if (!Number.isFinite(start) || !Number.isFinite(end) || !range) return false
  const left = Math.min(start, end)
  const right = Math.max(start, end)
  return right >= range.from - padding && left <= range.to + padding
}

function lineFitsViewport(start, end, viewport) {
  const padX = Math.max(24, viewport.width * 0.04)
  const padY = Math.max(32, viewport.height * 0.12)
  const startXVisible = start.x >= -padX && start.x <= viewport.width + padX
  const endXVisible = end.x >= -padX && end.x <= viewport.width + padX
  const startYVisible = start.y >= -padY && start.y <= viewport.height + padY
  const endYVisible = end.y >= -padY && end.y <= viewport.height + padY
  return startXVisible && endXVisible && startYVisible && endYVisible
}

function clipLineToViewport(start, end, viewport) {
  const bounds = viewportBounds(viewport)
  if (pointInsideBounds(start, bounds) && pointInsideBounds(end, bounds)) return { start, end }

  const dx = end.x - start.x
  const dy = end.y - start.y
  let t0 = 0
  let t1 = 1
  const tests = [
    [-dx, start.x - bounds.left],
    [dx, bounds.right - start.x],
    [-dy, start.y - bounds.top],
    [dy, bounds.bottom - start.y],
  ]
  for (const [p, q] of tests) {
    if (p === 0) {
      if (q < 0) return null
      continue
    }
    const r = q / p
    if (p < 0) {
      if (r > t1) return null
      if (r > t0) t0 = r
    } else {
      if (r < t0) return null
      if (r < t1) t1 = r
    }
  }
  if (t1 - t0 < 0.001) return null
  return {
    start: { x: start.x + dx * t0, y: start.y + dy * t0 },
    end: { x: start.x + dx * t1, y: start.y + dy * t1 },
  }
}

function clipRectToViewport(rect, viewport) {
  if (!rect) return null
  const bounds = viewportBounds(viewport)
  const left = Math.max(rect.x, bounds.left)
  const right = Math.min(rect.x + rect.width, bounds.right)
  const top = Math.max(rect.y, bounds.top)
  const bottom = Math.min(rect.y + rect.height, bounds.bottom)
  if (right <= left || bottom <= top) return null
  return {
    ...rect,
    x: left,
    y: top,
    width: right - left,
    height: bottom - top,
  }
}

function viewportBounds(viewport) {
  const padX = Math.max(24, viewport.width * 0.04)
  const padY = Math.max(32, viewport.height * 0.12)
  return {
    left: -padX,
    right: viewport.width + padX,
    top: -padY,
    bottom: viewport.height + padY,
  }
}

function pointInsideBounds(point, bounds) {
  return point.x >= bounds.left && point.x <= bounds.right && point.y >= bounds.top && point.y <= bounds.bottom
}

function momentumLegToOverlay(chart, leg, viewport, kind) {
  if (!leg || !isStructureItemContained(leg.start_index, leg.end_index, viewport.range, 1)) return null
  const start = chart.convertToPixel(
    { dataIndex: leg.start_index, value: leg.start_price },
    { paneId: CANDLE_PANE_ID, absolute: false }
  )
  const end = chart.convertToPixel(
    { dataIndex: leg.end_index, value: leg.end_price },
    { paneId: CANDLE_PANE_ID, absolute: false }
  )
  if (!validPoint(start) || !validPoint(end)) return null
  if (!lineFitsViewport(start, end, viewport)) return null
  const labelX = Math.max(8, Math.min(viewport.width - 72, (start.x + end.x) / 2))
  const labelY = Math.max(16, Math.min(viewport.height - 12, Math.min(start.y, end.y) - 10))
  return {
    key: `${kind}:${leg.id || `${leg.start_index}-${leg.end_index}`}`,
    kind,
    start,
    end,
    label: {
      x: labelX,
      y: labelY,
      text: kind === 'current' ? '当前段' : '上一同向',
    },
  }
}

function evidenceLineToOverlay(chart, item, viewport) {
  const price = Number(item?.price)
  if (!Number.isFinite(price) || price <= 0) return null
  const point = chart.convertToPixel(
    { dataIndex: Math.max(0, Math.floor((viewport.range.from + viewport.range.to) / 2)), value: price },
    { paneId: CANDLE_PANE_ID, absolute: false }
  )
  if (!validPoint(point)) return null
  const bounds = viewportBounds(viewport)
  if (point.y < bounds.top || point.y > bounds.bottom) return null
  const labelX = Math.max(12, Math.min(viewport.width - 76, viewport.width - 78))
  return {
    key: item.evidence_id || `${item.role}:${price}`,
    role: item.role || 'default',
    label: item.label || evidenceRoleLabel(item.role),
    price,
    y: point.y,
    x1: 0,
    x2: Math.max(0, viewport.width - 56),
    labelX,
  }
}

function isCenterBoundaryEvidence(item, center) {
  if (!center) return false
  const role = String(item?.role || '')
  if (!['trigger', 'invalidation'].includes(role)) return false
  const price = Number(item?.price)
  const zg = Number(center?.zg)
  const zd = Number(center?.zd)
  if (!Number.isFinite(price)) return false
  if (role === 'trigger' && Number.isFinite(zg)) return Math.abs(price - zg) < 0.0001
  if (role === 'invalidation' && Number.isFinite(zd)) return Math.abs(price - zd) < 0.0001
  return false
}

function evidenceCenterToOverlay(chart, item, viewport) {
  const zd = Number(item?.zd)
  const zg = Number(item?.zg)
  if (!Number.isFinite(zd) || !Number.isFinite(zg) || zd <= 0 || zg <= 0) return null
  const topPoint = chart.convertToPixel(
    { dataIndex: Math.max(0, Math.floor((viewport.range.from + viewport.range.to) / 2)), value: Math.max(zd, zg) },
    { paneId: CANDLE_PANE_ID, absolute: false }
  )
  const bottomPoint = chart.convertToPixel(
    { dataIndex: Math.max(0, Math.floor((viewport.range.from + viewport.range.to) / 2)), value: Math.min(zd, zg) },
    { paneId: CANDLE_PANE_ID, absolute: false }
  )
  if (!validPoint(topPoint) || !validPoint(bottomPoint)) return null
  return clipRectToViewport({
    x: 0,
    y: Math.min(topPoint.y, bottomPoint.y),
    width: Math.max(0, viewport.width - 56),
    height: Math.abs(bottomPoint.y - topPoint.y) || 3,
  }, viewport)
}

function chartContextMatchesPeriod(context, period) {
  const level = String(context?.level || '')
  const current = getKlinePeriod(period)?.apiInterval
  if (!level || !current) return false
  const aliases = {
    m5: ['m5', '5', '5m'],
    m15: ['m15', '15', '15m'],
    m30: ['m30', '30', '30m'],
    m60: ['m60', '60', '60m', '1h'],
    day: ['day', 'd', '1d'],
    week: ['week', 'w', '1w'],
  }
  return (aliases[current] || [current]).includes(level)
}

function evidenceRoleLabel(role) {
  if (role === 'trigger') return '触发线'
  if (role === 'invalidation') return '失败线'
  if (role === 'current_price') return '当前价'
  return 'AI证据'
}

function structureStatusLabel(status, view) {
  if (status === 'ready') {
    const biCount = view?.bis?.length || 0
    const segmentCount = view?.segments?.length || 0
    const sourceLabel = '实时结构'
    if (segmentCount > 0) return `${sourceLabel} · ${biCount} 笔 · ${segmentCount} 线段`
    if (view?.capabilities?.segment_status === 'unavailable') return `${sourceLabel} · ${biCount} 笔 · 线段待接入`
    return `${sourceLabel} · ${biCount} 笔`
  }
  if (status === 'loading') return '结构加载'
  if (status === 'missing') return '实时结构失败'
  return ''
}

function momentumStatusLabel(status, context) {
  if (status === 'ready') return context?.verdict?.label || '力量'
  if (status === 'loading') return '力量加载'
  if (status === 'insufficient') return '力量不足'
  if (status === 'missing') return '无力量'
  return ''
}

function applyMainIndicator(chart, indicatorName) {
  ;['MA', 'BOLL'].forEach((name) => chart.removeIndicator({ paneId: CANDLE_PANE_ID, name }))
  if (indicatorName !== 'NONE') {
    chart.createIndicator(indicatorName, true, { id: CANDLE_PANE_ID })
  }
}

function applySubIndicator(chart, indicatorName) {
  chart.getIndicators()
    .filter((item) => item.paneId !== CANDLE_PANE_ID)
    .forEach((item) => chart.removeIndicator({ id: item.id }))
  if (indicatorName === 'NONE') {
    try {
      chart.setPaneOptions({ id: SUB_PANE_ID, order: 1, height: 0, minHeight: 0, dragEnabled: false })
    } catch {
      // 副图 pane 尚未创建时无需处理。
    }
    return
  }
  chart.createIndicator(indicatorName, false, {
    id: SUB_PANE_ID,
    order: 1,
    height: VOLUME_HEIGHT,
    minHeight: 88,
    dragEnabled: false,
  })
}

function normalizeIndicator(value, options, fallback) {
  return options.some((item) => item.value === value) ? value : fallback
}

function buildChartStyles() {
  return {
    grid: {
      show: true,
      horizontal: { show: true, size: 1, color: 'rgba(255,255,255,0.06)', style: 'solid' },
      vertical: { show: false },
    },
    candle: {
      type: 'candle_solid',
      bar: {
        upColor: '#f43f5e',
        downColor: '#10b981',
        noChangeColor: '#76808f',
        upBorderColor: '#f43f5e',
        downBorderColor: '#10b981',
        noChangeBorderColor: '#76808f',
        upWickColor: '#f43f5e',
        downWickColor: '#10b981',
        noChangeWickColor: '#76808f',
      },
      tooltip: {
        showRule: 'always',
        showType: 'standard',
        text: {
          size: 11,
          family: 'JetBrains Mono, SF Mono, monospace',
          color: 'rgba(255,255,255,0.72)',
        },
      },
      priceMark: {
        show: false,
        last: {
          show: false,
          upColor: '#f43f5e',
          downColor: '#10b981',
          noChangeColor: '#76808f',
          line: { show: true, style: 'dashed', dashValue: [4, 4], size: 1 },
          text: { show: true, size: 11, color: '#08080a', family: 'JetBrains Mono, monospace' },
        },
      },
    },
    xAxis: {
      axisLine: { show: true, color: 'rgba(255,255,255,0.08)', size: 1 },
      tickText: { color: 'rgba(255,255,255,0.38)', size: 11, family: 'JetBrains Mono, monospace' },
      tickLine: { show: false },
    },
    yAxis: {
      axisLine: { show: true, color: 'rgba(255,255,255,0.08)', size: 1 },
      tickText: { color: 'rgba(255,255,255,0.45)', size: 11, family: 'JetBrains Mono, monospace' },
      tickLine: { show: false },
    },
    separator: {
      size: 1,
      color: 'rgba(255,255,255,0.08)',
      fill: false,
      activeBackgroundColor: 'rgba(6,182,212,0.08)',
    },
    crosshair: {
      show: true,
      horizontal: {
        show: true,
        line: { show: true, style: 'dashed', dashedValue: [4, 4], size: 1, color: 'rgba(255,255,255,0.22)' },
        text: { show: true, size: 11, color: '#08080a', backgroundColor: 'rgba(255,255,255,0.72)' },
      },
      vertical: {
        show: true,
        line: { show: true, style: 'dashed', dashedValue: [4, 4], size: 1, color: 'rgba(255,255,255,0.22)' },
        text: { show: true, size: 11, color: '#08080a', backgroundColor: 'rgba(255,255,255,0.72)' },
      },
    },
    indicator: {
      bars: [{
        upColor: 'rgba(244,63,94,0.42)',
        downColor: 'rgba(16,185,129,0.42)',
        noChangeColor: 'rgba(118,128,143,0.36)',
      }],
      lines: [
        { color: '#f59e0b', size: 1, style: 'solid' },
        { color: '#06b6d4', size: 1, style: 'solid' },
        { color: '#c8a832', size: 1, style: 'solid' },
      ],
      tooltip: {
        text: { size: 11, family: 'JetBrains Mono, monospace', color: 'rgba(255,255,255,0.56)' },
      },
    },
  }
}

function formatChartDate(timestamp, type) {
  const date = new Date(timestamp)
  const yyyy = date.getFullYear()
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  const hh = String(date.getHours()).padStart(2, '0')
  const mi = String(date.getMinutes()).padStart(2, '0')
  if (type === 'xAxis') return `${mm}-${dd}`
  if (hh === '00' && mi === '00') return `${yyyy}-${mm}-${dd}`
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}`
}

function formatBigNumber(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '--'
  if (Math.abs(parsed) >= 100000000) return `${(parsed / 100000000).toFixed(2)}亿`
  if (Math.abs(parsed) >= 10000) return `${(parsed / 10000).toFixed(1)}万`
  return parsed.toFixed(0)
}

function formatPrice(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '--'
  return parsed >= 100 ? parsed.toFixed(1) : parsed.toFixed(2)
}
