/**
 * CT-OS V4.0 — 缠论 Overlay 插件体系
 * 
 * 移植自 V3 chan_overlay.js，适配 V4 后端 chan_detail_service.py 输出格式。
 * 
 * 包含：
 *   Figure (8 种): 笔 / 线段 / 中枢 / 分型 / 背驰 / 推演 / 支撑线 / 涨跌停
 *   Overlay (8 种): 对应 Figure 的 KLineCharts overlay 配置
 *   renderChanOverlays(): 批量渲染函数
 */

import { registerFigure, registerOverlay } from 'klinecharts'
import { toTimestamp } from '../utils.js'

// ═══════════════════════════════════════════════════════════════
// Figure 定义 — 最小渲染单元
// ═══════════════════════════════════════════════════════════════

// 笔：虚线折线
registerFigure({
  name: 'chan_bi_figure',
  draw: (ctx, attrs) => {
    const { coordinates } = attrs
    if (!coordinates || coordinates.length < 2) return
    const [{ x: x1, y: y1 }, { x: x2, y: y2 }] = coordinates

    ctx.save()
    ctx.beginPath()
    ctx.setLineDash([4, 4])
    ctx.lineWidth = 1
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.7)'
    ctx.moveTo(x1, y1)
    ctx.lineTo(x2, y2)
    ctx.stroke()
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 线段：金黄色实线（已确认）
registerFigure({
  name: 'chan_seg_figure',
  draw: (ctx, attrs) => {
    const { coordinates } = attrs
    if (!coordinates || coordinates.length < 2) return
    const [{ x: x1, y: y1 }, { x: x2, y: y2 }] = coordinates

    ctx.save()
    ctx.beginPath()
    ctx.lineWidth = 2
    ctx.strokeStyle = '#ffd54f'
    ctx.moveTo(x1, y1)
    ctx.lineTo(x2, y2)
    ctx.stroke()
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 线段：金黄色虚线（未确认 — 最后一段未被新段破坏）
registerFigure({
  name: 'chan_seg_unsure_figure',
  draw: (ctx, attrs) => {
    const { coordinates } = attrs
    if (!coordinates || coordinates.length < 2) return
    const [{ x: x1, y: y1 }, { x: x2, y: y2 }] = coordinates

    ctx.save()
    ctx.beginPath()
    ctx.setLineDash([8, 6])
    ctx.lineWidth = 2
    ctx.strokeStyle = 'rgba(255, 213, 79, 0.7)'
    ctx.moveTo(x1, y1)
    ctx.lineTo(x2, y2)
    ctx.stroke()
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 中枢矩形：上升红 / 下降绿
registerFigure({
  name: 'chan_zs_figure',
  draw: (ctx, attrs) => {
    const { coordinates, type } = attrs
    if (!coordinates || coordinates.length < 2) return
    const [{ x: x1, y: y1 }, { x: x2, y: y2 }] = coordinates

    const width = x2 - x1
    const height = Math.abs(y2 - y1)
    const topY = Math.min(y1, y2)

    ctx.save()
    ctx.beginPath()
    if (type === 'UP_ZS') {
      ctx.fillStyle = 'rgba(239, 83, 80, 0.15)'
      ctx.strokeStyle = '#ef5350'
    } else {
      ctx.fillStyle = 'rgba(38, 166, 154, 0.15)'
      ctx.strokeStyle = '#26a69a'
    }
    ctx.lineWidth = 1
    ctx.rect(x1, topY, width, height)
    ctx.fill()
    ctx.stroke()
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 中枢矩形：同级别分解版 — 紫色虚线边框，区别于全局扫描
registerFigure({
  name: 'chan_zs_decomp_figure',
  draw: (ctx, attrs) => {
    const { coordinates, type } = attrs
    if (!coordinates || coordinates.length < 2) return
    const [{ x: x1, y: y1 }, { x: x2, y: y2 }] = coordinates

    const width = x2 - x1
    const height = Math.abs(y2 - y1)
    const topY = Math.min(y1, y2)

    ctx.save()
    ctx.beginPath()
    ctx.setLineDash([6, 4])
    if (type === 'UP_ZS') {
      ctx.fillStyle = 'rgba(171, 71, 188, 0.12)'
      ctx.strokeStyle = '#ab47bc'
    } else {
      ctx.fillStyle = 'rgba(66, 165, 245, 0.12)'
      ctx.strokeStyle = '#42a5f5'
    }
    ctx.lineWidth = 1.5
    ctx.rect(x1, topY, width, height)
    ctx.fill()
    ctx.stroke()
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 分型三角
registerFigure({
  name: 'chan_fractal_figure',
  draw: (ctx, attrs) => {
    const { coordinates, type } = attrs
    if (!coordinates || coordinates.length < 1) return
    const { x, y } = coordinates[0]

    ctx.save()
    ctx.beginPath()
    const size = 6
    if (type === 'ding') {
      ctx.fillStyle = '#ef5350'
      ctx.moveTo(x, y - size * 0.5)
      ctx.lineTo(x - size, y - size * 2)
      ctx.lineTo(x + size, y - size * 2)
    } else {
      ctx.fillStyle = '#26a69a'
      ctx.moveTo(x, y + size * 0.5)
      ctx.lineTo(x - size, y + size * 2)
      ctx.lineTo(x + size, y + size * 2)
    }
    ctx.closePath()
    ctx.fill()
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 背驰 / 买卖点标记
registerFigure({
  name: 'chan_divergence_figure',
  draw: (ctx, attrs) => {
    const { coordinates, text, isBuy } = attrs
    if (!coordinates || coordinates.length < 1) return
    const { x, y } = coordinates[0]

    ctx.save()
    ctx.font = 'bold 10px Arial'
    const padding = 4
    const textWidth = ctx.measureText(text).width
    const width = textWidth + padding * 2
    const height = 16

    ctx.fillStyle = isBuy !== false ? '#ef5350' : '#26a69a'
    const bgX = x - width / 2
    const bgY = isBuy !== false ? y + 10 : y - height - 10

    ctx.beginPath()
    if (ctx.roundRect) {
      ctx.roundRect(bgX, bgY, width, height, 3)
    } else {
      ctx.rect(bgX, bgY, width, height)
    }
    ctx.fill()

    ctx.fillStyle = '#ffffff'
    ctx.textBaseline = 'middle'
    ctx.textAlign = 'center'
    ctx.fillText(text, x, bgY + height / 2)
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 推演虚线 + 箭头
registerFigure({
  name: 'chan_projection_figure',
  draw: (ctx, attrs) => {
    const { coordinates } = attrs
    if (!coordinates || coordinates.length < 2) return
    const [{ x: x1, y: y1 }, { x: x2, y: y2 }] = coordinates

    ctx.save()
    ctx.beginPath()
    ctx.setLineDash([8, 6])
    ctx.lineWidth = 2
    ctx.strokeStyle = '#c62828'
    ctx.moveTo(x1, y1)
    ctx.lineTo(x2, y2)
    ctx.stroke()

    const angle = Math.atan2(y2 - y1, x2 - x1)
    const headLen = 10
    ctx.moveTo(x2, y2)
    ctx.lineTo(x2 - headLen * Math.cos(angle - Math.PI / 6), y2 - headLen * Math.sin(angle - Math.PI / 6))
    ctx.moveTo(x2, y2)
    ctx.lineTo(x2 - headLen * Math.cos(angle + Math.PI / 6), y2 - headLen * Math.sin(angle + Math.PI / 6))
    ctx.stroke()
    ctx.restore()
  },
  checkEventOn: () => false,
})

// ─────────────────────────────────────────────────────────────────
// 高级分析 Figure（透明度严格 ≤ 0.10，绝不比主体 K 线更亮）
// ─────────────────────────────────────────────────────────────────

// 区间套投影：全宽色带，标示上级别中枢的价格区间
// alpha 限定在 0.06 填充 + 0.18 边线，保证极度不干扰主图
registerFigure({
  name: 'chan_projection_band_figure',
  draw: (ctx, attrs) => {
    const { coordinates, type, renderOptions } = attrs
    if (!coordinates || coordinates.length < 2) return
    const [{ y: y1 }, { y: y2 }] = coordinates
    const canvasWidth = renderOptions?.width || 4000
    const topY   = Math.min(y1, y2)
    const height = Math.abs(y2 - y1)
    if (height < 1) return  // 高度为零时静默跳过

    ctx.save()
    ctx.beginPath()
    if (type === 'UP_ZS') {
      ctx.fillStyle   = 'rgba(239, 83, 80, 0.06)'
      ctx.strokeStyle = 'rgba(239, 83, 80, 0.18)'
    } else {
      ctx.fillStyle   = 'rgba(38, 166, 154, 0.06)'
      ctx.strokeStyle = 'rgba(38, 166, 154, 0.18)'
    }
    ctx.lineWidth = 1
    ctx.setLineDash([3, 5])
    ctx.rect(0, topY, canvasWidth, height)
    ctx.fill()
    // 仅画上下边线，不画左右，更简洁
    ctx.beginPath()
    ctx.moveTo(0, topY)
    ctx.lineTo(canvasWidth, topY)
    ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(0, topY + height)
    ctx.lineTo(canvasWidth, topY + height)
    ctx.stroke()
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 背驰辅助：在疑似背驰的笔端点处标注 MACD 面积衰减百分比气泡
registerFigure({
  name: 'chan_momentum_compare_figure',
  draw: (ctx, attrs) => {
    const { coordinates, area, prevArea, isDiverge } = attrs
    if (!coordinates || coordinates.length < 1) return
    const { x, y } = coordinates[0]
    if (area == null || prevArea == null) return

    const ratio = prevArea > 0 ? Math.round(area / prevArea * 100) : 0
    const label = `${ratio}%`

    ctx.save()
    ctx.font = 'bold 9px Arial'
    const textW = ctx.measureText(label).width
    const pad = 3
    const w = textW + pad * 2
    const h = 13
    const bx = x - w / 2
    // 买点（下方笔端）标签向下，卖点（上方笔端）标签向上
    const by = isDiverge ? y - h - 5 : y + 5

    // 背驰用橙黄色，普通用低调灰
    ctx.fillStyle = isDiverge
      ? 'rgba(255, 152, 0, 0.82)'
      : 'rgba(90, 90, 110, 0.65)'
    ctx.beginPath()
    if (ctx.roundRect) {
      ctx.roundRect(bx, by, w, h, 2)
    } else {
      ctx.rect(bx, by, w, h)
    }
    ctx.fill()

    ctx.fillStyle = '#ffffff'
    ctx.textBaseline = 'middle'
    ctx.textAlign = 'center'
    ctx.fillText(label, x, by + h / 2)
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 走势切分：全高垂直虚线，划分线段走势区域
// alpha 严格 ≤ 0.12，线极细，不干扰 K 线主体
registerFigure({
  name: 'chan_decomposition_grid_figure',
  draw: (ctx, attrs) => {
    const { coordinates, label, renderOptions } = attrs
    if (!coordinates || coordinates.length < 1) return
    const { x } = coordinates[0]
    const canvasHeight = renderOptions?.height || 3000

    ctx.save()
    ctx.beginPath()
    ctx.setLineDash([3, 5])
    ctx.lineWidth = 1
    ctx.strokeStyle = 'rgba(255, 213, 79, 0.12)'
    ctx.moveTo(x, 0)
    ctx.lineTo(x, canvasHeight)
    ctx.stroke()

    // 顶部走势方向小标签（↑向上趋势结束 / ↓向下趋势结束）
    if (label) {
      ctx.setLineDash([])
      ctx.fillStyle = 'rgba(255, 213, 79, 0.40)'
      ctx.font = '10px Arial'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillText(label, x, 4)
    }
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 水平支撑/压力线
registerFigure({
  name: 'chan_support_line_figure',
  draw: (ctx, attrs) => {
    const { coordinates, text, renderOptions } = attrs
    if (!coordinates || coordinates.length < 1) return
    const [{ y: y1 }] = coordinates
    const width = renderOptions?.width || 2000

    ctx.save()
    ctx.beginPath()
    ctx.setLineDash([5, 5])
    ctx.lineWidth = 1
    ctx.strokeStyle = '#64b5f6'
    ctx.moveTo(0, y1)
    ctx.lineTo(width, y1)
    ctx.stroke()

    if (text) {
      ctx.fillStyle = '#64b5f6'
      ctx.font = '10px Arial'
      ctx.fillText(text, 10, y1 - 5)
    }
    ctx.restore()
  },
  checkEventOn: () => false,
})

// ═══════════════════════════════════════════════════════════════
// Overlay 定义 — 将 Figure 注册为可标注对象
// ═══════════════════════════════════════════════════════════════

const overlayConfigs = [
  {
    name: 'chan_bi',
    totalStep: 3,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates }) => [
      { type: 'chan_bi_figure', attrs: { coordinates } },
    ],
  },
  {
    name: 'chan_seg',
    totalStep: 3,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates }) => [
      { type: 'chan_seg_figure', attrs: { coordinates } },
    ],
  },
  {
    name: 'chan_seg_unsure',
    totalStep: 3,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates }) => [
      { type: 'chan_seg_unsure_figure', attrs: { coordinates } },
    ],
  },
  {
    name: 'chan_zs',
    totalStep: 3,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay }) => {
      const type = overlay.extendData?.type || 'UP_ZS'
      return [{ type: 'chan_zs_figure', attrs: { coordinates, type } }]
    },
  },
  {
    name: 'chan_zs_decomp',
    totalStep: 3,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay }) => {
      const type = overlay.extendData?.type || 'UP_ZS'
      return [{ type: 'chan_zs_decomp_figure', attrs: { coordinates, type } }]
    },
  },
  {
    name: 'chan_fractal',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay }) => {
      const type = overlay.extendData?.type || 'ding'
      return [{ type: 'chan_fractal_figure', attrs: { coordinates, type } }]
    },
  },
  {
    name: 'chan_divergence',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay }) => {
      const text = overlay.extendData?.text || '背'
      const isBuy = overlay.extendData?.isBuy
      return [{ type: 'chan_divergence_figure', attrs: { coordinates, text, isBuy } }]
    },
  },
  {
    name: 'chan_projection',
    totalStep: 3,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates }) => [
      { type: 'chan_projection_figure', attrs: { coordinates } },
    ],
  },
  {
    name: 'chan_support_line',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, bounding }) => {
      const text = overlay.extendData?.text || ''
      return [
        { type: 'chan_support_line_figure', attrs: { coordinates, text, renderOptions: { width: bounding.width } } },
      ]
    },
  },
  // ── 高级分析 Overlay ──
  {
    name: 'chan_projection_band',
    totalStep: 3,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, bounding }) => {
      const type = overlay.extendData?.type || 'UP_ZS'
      return [{ type: 'chan_projection_band_figure', attrs: { coordinates, type, renderOptions: { width: bounding.width } } }]
    },
  },
  {
    name: 'chan_momentum_compare',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay }) => {
      const { area, prevArea, isDiverge } = overlay.extendData || {}
      return [{ type: 'chan_momentum_compare_figure', attrs: { coordinates, area, prevArea, isDiverge } }]
    },
  },
  {
    name: 'chan_decomposition_grid',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, bounding }) => {
      const label = overlay.extendData?.label || ''
      return [{ type: 'chan_decomposition_grid_figure', attrs: { coordinates, label, renderOptions: { height: bounding.height } } }]
    },
  },
]

// 批量注册所有 Overlay
overlayConfigs.forEach((config) => registerOverlay(config))

// ═══════════════════════════════════════════════════════════════
// 批量渲染函数 — 适配 V4 chan_detail_service.py 输出
// ═══════════════════════════════════════════════════════════════

/**
 * 将后端 chan_detail_service 返回的结构数据批量渲染到图表上。
 *
 * V4 后端格式 (chan_detail_service.py):
 *   bis:             [{ x0, y0, x1, y1, is_up, is_sure, momentum }, ...]
 *   segs:            [{ x0, y0, x1, y1, is_up, is_sure, momentum }, ...]
 *   bi_zhongshus:    [{ begin_date, end_date, zg, zd, gg, dd }, ...]
 *   bsps:            [{ time, price, type, is_buy }, ...]
 *   higher_zhongshus:[{ begin_date, end_date, zg, zd }, ...]  ← 上级别中枢（区间套投影）
 *   vis:             { projection, momentum_compare, support_wall, decomp_grid, ... }
 *
 * @param {object}  chart      - KLineCharts 实例
 * @param {object}  data       - 后端返回的数据 + 高级分析可选字段
 * @param {boolean} isDay      - 是否日线（影响时间戳格式）
 * @param {boolean} clearFirst - 是否先清除旧标注（默认 true，外部已清时传 false）
 */
export function renderChanOverlays(chart, data, isDay = true, clearFirst = true) {
  if (!chart || !data) return

  const vis = data.vis || {}

  // 清除所有缠论图层（含高级图层）
  if (clearFirst) {
    chart.removeOverlay({ groupId: 'chan_bi_group' })
    chart.removeOverlay({ groupId: 'chan_seg_group' })
    chart.removeOverlay({ groupId: 'chan_bi_zs_group' })        // 笔中枢（全局扫描）
    chart.removeOverlay({ groupId: 'chan_bi_zs_decomp_group' }) // 笔中枢（同级别分解）
    chart.removeOverlay({ groupId: 'chan_seg_zs_group' })       // 段中枢
    chart.removeOverlay({ groupId: 'chan_bsp_group' })
    chart.removeOverlay({ groupId: 'chan_projection_group' })
    chart.removeOverlay({ groupId: 'chan_momentum_group' })
    chart.removeOverlay({ groupId: 'chan_decomp_group' })
    chart.removeOverlay({ groupId: 'chan_support_wall_group' })
  }

  const overlays = []

  // P2-FIX #7: 使用共享的 toTimestamp
  const toTs = (dateStr) => toTimestamp(dateStr, isDay)

  // 1. 笔
  if (data.bis?.length > 0) {
    for (const bi of data.bis) {
      overlays.push({
        groupId: 'chan_bi_group',
        name: 'chan_bi',
        lock: true,
        points: [
          { timestamp: toTs(bi.x0), value: bi.y0 },
          { timestamp: toTs(bi.x1), value: bi.y1 },
        ],
      })
    }
  }

  // 1.5 线段（已确认 = 实线，未确认 = 虚线）
  if (data.segs?.length > 0) {
    for (const seg of data.segs) {
      overlays.push({
        groupId: 'chan_seg_group',
        name: seg.is_sure === false ? 'chan_seg_unsure' : 'chan_seg',
        lock: true,
        points: [
          { timestamp: toTs(seg.x0), value: seg.y0 },
          { timestamp: toTs(seg.x1), value: seg.y1 },
        ],
      })
    }
  }

  // 中枢方向辅助：找最接近 begin_date 的笔（x0 >= begin_date 的第一笔）
  // 上升中枢：第一笔通常是向下笔（从中枢上方回调进入），is_up=false → UP_ZS
  // 下降中枢：第一笔通常是向上笔（从中枢下方反弹进入），is_up=true  → DOWN_ZS
  const getZsType = (strokeList, beginDate) => {
    const first = strokeList?.find(s => s.x0 >= beginDate)
    if (!first) return 'UP_ZS'
    return first.is_up ? 'DOWN_ZS' : 'UP_ZS'
  }

  // 2. 笔中枢（全局扫描，小级别，虚线矩形）
  if (data.bi_zhongshus?.length > 0) {
    for (const zs of data.bi_zhongshus) {
      const zsType = getZsType(data.bis, zs.begin_date)
      overlays.push({
        groupId: 'chan_bi_zs_group',
        name: 'chan_zs',
        lock: true,
        extendData: { type: zsType },
        points: [
          { timestamp: toTs(zs.begin_date), value: zs.zg },
          { timestamp: toTs(zs.end_date), value: zs.zd },
        ],
      })
    }
  }

  // 2.1 笔中枢（同级别分解，紫色虚线矩形）
  if (data.bi_zhongshus_decomp?.length > 0) {
    for (const zs of data.bi_zhongshus_decomp) {
      const zsType = getZsType(data.bis, zs.begin_date)
      overlays.push({
        groupId: 'chan_bi_zs_decomp_group',
        name: 'chan_zs_decomp',
        lock: true,
        extendData: { type: zsType },
        points: [
          { timestamp: toTs(zs.begin_date), value: zs.zg },
          { timestamp: toTs(zs.end_date), value: zs.zd },
        ],
      })
    }
  }

  // 2.5 线段中枢（大级别，实线矩形，更醒目）
  if (data.seg_zhongshus?.length > 0) {
    for (const zs of data.seg_zhongshus) {
      const zsType = getZsType(data.segs, zs.begin_date)
      overlays.push({
        groupId: 'chan_seg_zs_group',
        name: 'chan_zs',
        lock: true,
        extendData: { type: zsType },
        points: [
          { timestamp: toTs(zs.begin_date), value: zs.zg },
          { timestamp: toTs(zs.end_date), value: zs.zd },
        ],
      })
    }
  }

  // 3. 买卖点 (待后端新增 bsps 字段)
  if (data.bsps?.length > 0) {
    const enabledTypes = Array.isArray(vis.bsp_types) ? new Set(vis.bsp_types) : null
    for (const bsp of data.bsps) {
      const typeStr = (bsp.type || '1').toString().toUpperCase()
      const rawType = (bsp.type || '1').toString()
      if (bsp.is_buy && vis.bsp_buy === false) continue
      if (!bsp.is_buy && vis.bsp_sell === false) continue
      if (enabledTypes && !enabledTypes.has(rawType)) continue
      const badgeText = bsp.is_buy ? `B${typeStr}` : `S${typeStr}`
      overlays.push({
        groupId: 'chan_bsp_group',
        name: 'chan_divergence',
        lock: true,
        extendData: { text: badgeText, isBuy: bsp.is_buy },
        points: [{ timestamp: toTs(bsp.time), value: bsp.price }],
      })
    }
  }

  // ──────────────────────────────────────────────────────────────
  // 高级分析图层（alpha ≤ 0.10，空数据静默跳过）
  // ──────────────────────────────────────────────────────────────

  // 4. 区间套投影 — 上级别中枢横向色带
  // 互斥约束已在 LayerPanel 状态机处理，此处无需重复检查
  if (vis.projection && data.higher_zhongshus?.length > 0) {
    for (const zs of data.higher_zhongshus) {
      // 推断中枢方向：用 ZG 相对于 ZD 的位置简单判断（实际方向由首根进入笔决定）
      const zsType = 'UP_ZS'  // 色带颜色由外部传 type 覆盖，这里用默认
      overlays.push({
        groupId:    'chan_projection_group',
        name:       'chan_projection_band',
        lock:       true,
        extendData: { type: zsType },
        points: [
          { timestamp: toTs(zs.begin_date), value: zs.zg },
          { timestamp: toTs(zs.end_date),   value: zs.zd },
        ],
      })
    }
  }

  // 5. 背驰辅助 — 同向相邻笔 MACD 面积衰减比标注
  // 原理：bis[i] 与 bis[i+2] 方向相同（隔一根反向笔），比较 momentum.area
  //       衰减超过 25% 视为疑似背驰（isDiverge=true），用橙色气泡标出
  if (vis.momentum_compare && data.bis?.length > 3) {
    const momentumPairs = []
    for (let i = 0; i < data.bis.length - 2; i++) {
      const b1 = data.bis[i]
      const b2 = data.bis[i + 2]
      if (b1.is_up !== b2.is_up) continue
      const a1 = b1.momentum?.area ?? 0
      const a2 = b2.momentum?.area ?? 0
      if (a1 < 0.0005) continue  // 忽略近零面积（合并K等情况）
      const isDiverge = a2 < a1 * 0.75
      momentumPairs.push({ bi: b2, area: a2, prevArea: a1, isDiverge })
    }
    for (const p of momentumPairs) {
      overlays.push({
        groupId:    'chan_momentum_group',
        name:       'chan_momentum_compare',
        lock:       true,
        extendData: { area: p.area, prevArea: p.prevArea, isDiverge: p.isDiverge },
        points:     [{ timestamp: toTs(p.bi.x1), value: p.bi.y1 }],
      })
    }
  }

  // 6. 走势切分 — 线段边界垂直虚线
  // 在每根已确认线段结束处画全高竖线，区分向上/向下/盘整走势区块
  if (vis.decomp_grid && data.segs?.length > 1) {
    // 不画最后一根线段的结束点（当前走势尚未结束，防止最右侧出现孤立竖线）
    for (let i = 0; i < data.segs.length - 1; i++) {
      const seg = data.segs[i]
      if (!seg.is_sure) continue  // 未确认线段不画切分线
      overlays.push({
        groupId:    'chan_decomp_group',
        name:       'chan_decomposition_grid',
        lock:       true,
        extendData: { label: seg.is_up ? '↓' : '↑' },
        points:     [{ timestamp: toTs(seg.x1), value: seg.y1 }],
      })
    }
  }

  // 7. 防线预警 — 顶/底分型密集价位聚类
  // 算法：收集所有笔端点（顶/底分型），在 ±1.5% 价格带内若有 3+ 个点则画一条防线
  if (vis.support_wall && data.bis?.length > 5) {
    const tops    = data.bis.filter(b =>  b.is_up).map(b => b.y1).sort((a, b) => a - b)
    const bottoms = data.bis.filter(b => !b.is_up).map(b => b.y1).sort((a, b) => a - b)

    const walls = []
    const clusterPrices = (prices, isBullish) => {
      if (prices.length < 3) return
      let start = 0
      for (let i = 1; i <= prices.length; i++) {
        // 超出 1.5% 带宽或到达末尾时，结算当前聚类
        if (i === prices.length || prices[i] > prices[start] * 1.015) {
          const cluster = prices.slice(start, i)
          if (cluster.length >= 3) {
            const med = cluster[Math.floor(cluster.length / 2)]
            walls.push({ price: med, strength: cluster.length, isBullish })
          }
          start = i
        }
      }
    }
    clusterPrices(tops,    false)  // 顶分型 → 压力位
    clusterPrices(bottoms, true)   // 底分型 → 支撑位

    // 按强度降序，最多渲染 8 条防线（控制视觉密度）
    walls.sort((a, b) => b.strength - a.strength)
    const anchorTs = data.bis.length > 0
      ? toTs(data.bis[data.bis.length - 1].x1)
      : 0
    for (const wall of walls.slice(0, 8)) {
      const text = `${wall.isBullish ? '支撑' : '压力'} ×${wall.strength}`
      overlays.push({
        groupId:    'chan_support_wall_group',
        name:       'chan_support_line',
        lock:       true,
        extendData: { text },
        points:     [{ timestamp: anchorTs, value: wall.price }],
      })
    }
  }

  if (overlays.length > 0) {
    chart.createOverlay(overlays)
  }

  console.log(
    `[ChanOverlay] 渲染: 笔=${data.bis?.length || 0}, ` +
    `线段=${data.segs?.length || 0}, ` +
    `笔中枢=${data.bi_zhongshus?.length || 0}, ` +
    `段中枢=${data.seg_zhongshus?.length || 0}, ` +
    `买卖点=${data.bsps?.length || 0}`
  )
}
