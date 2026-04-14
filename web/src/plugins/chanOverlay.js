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
 *   bis:       [{ x0, y0, x1, y1, is_up, is_sure }, ...]
 *   zhongshus: [{ begin_date, end_date, zg, zd, gg, dd }, ...]
 *   bsps:      [{ time, price, type, is_buy }, ...]  (TODO: 待后端实现)
 *
 * @param {object} chart - KLineCharts 实例
 * @param {object} data  - 后端返回的 { bis, zhongshus, bsps }
 * @param {boolean} isDay - 是否日线（影响时间戳格式）
 * @param {boolean} clearFirst - 是否先清除旧标注（默认 true，外部已清时传 false避免重复）
 */
export function renderChanOverlays(chart, data, isDay = true, clearFirst = true) {
  if (!chart || !data) return

  // P2-FIX #6: 只在需要时清除，避免双重清除
  if (clearFirst) {
    chart.removeOverlay({ groupId: 'chan_bi_group' })
    chart.removeOverlay({ groupId: 'chan_seg_group' })
    chart.removeOverlay({ groupId: 'chan_zs_group' })
    chart.removeOverlay({ groupId: 'chan_bsp_group' })
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

  // 2. 笔中枢（全局扫描，小级别，虚线矩形）
  if (data.bi_zhongshus?.length > 0) {
    for (const zs of data.bi_zhongshus) {
      let zsType = 'UP_ZS'
      if (data.bis?.length > 0) {
        const firstBi = data.bis.find(bi => bi.x0 >= zs.begin_date)
        if (firstBi) zsType = firstBi.is_up ? 'UP_ZS' : 'DOWN_ZS'
      }
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
      let zsType = 'UP_ZS'
      if (data.bis?.length > 0) {
        const firstBi = data.bis.find(bi => bi.x0 >= zs.begin_date)
        if (firstBi) zsType = firstBi.is_up ? 'UP_ZS' : 'DOWN_ZS'
      }
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
      let zsType = 'UP_ZS'
      if (data.segs?.length > 0) {
        const firstSeg = data.segs.find(s => s.x0 >= zs.begin_date)
        if (firstSeg) zsType = firstSeg.is_up ? 'UP_ZS' : 'DOWN_ZS'
      }
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
    for (const bsp of data.bsps) {
      const typeStr = (bsp.type || '1').toString().toUpperCase()
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

  if (overlays.length > 0) {
    chart.createOverlay(overlays)
  }

  console.log(
    `[ChanOverlay] 渲染: 笔=${data.bis?.length || 0}, ` +
    `线段=${data.segs?.length || 0}, ` +
    `笔中枢=${data.bi_zhongshus?.length || 0}, ` +
    `段中枢=${data.seg_zhongshus?.length || 0}`
  )
}
