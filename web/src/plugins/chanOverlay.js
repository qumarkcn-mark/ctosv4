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

function resetChanCanvasState(ctx) {
  // KLineCharts V10 beta 的 overlay canvas 可能继承上一段绘制的合成状态。
  // Chan 自定义 figure 每次绘制前主动复位，避免结构线被画到背景层后不可见。
  ctx.globalCompositeOperation = 'source-over'
  ctx.globalAlpha = 1
}

function getZhongshuStyle(layer = 'bi') {
  // 中枢是结构事实，不是交易动作。按 CT-OS 设计系统保持低干扰：
  // 笔中枢用冷灰，段中枢才使用行动金，分解层用雷达青区分分析模式。
  if (layer === 'seg') {
    return {
      fillStyle: 'rgba(200, 168, 50, 0.045)',
      strokeStyle: 'rgba(200, 168, 50, 0.72)',
      lineWidth: 1.5,
      lineDash: [],
    }
  }
  if (layer === 'bi_decomp') {
    return {
      fillStyle: 'rgba(6, 182, 212, 0.035)',
      strokeStyle: 'rgba(6, 182, 212, 0.42)',
      lineWidth: 1,
      lineDash: [5, 5],
    }
  }
  return {
    fillStyle: 'rgba(255, 255, 255, 0.022)',
    strokeStyle: 'rgba(255, 255, 255, 0.28)',
    lineWidth: 1,
    lineDash: [4, 4],
  }
}

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
    resetChanCanvasState(ctx)
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
    resetChanCanvasState(ctx)
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
    resetChanCanvasState(ctx)
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

// 中枢矩形：低干扰结构框
registerFigure({
  name: 'chan_zs_figure',
  draw: (ctx, attrs) => {
    const { coordinates } = attrs
    if (!coordinates || coordinates.length < 2) return
    const [{ x: x1, y: y1 }, { x: x2, y: y2 }] = coordinates

    const x = Math.min(x1, x2)
    const width = Math.abs(x2 - x1)
    const height = Math.abs(y2 - y1)
    const topY = Math.min(y1, y2)
    if (width < 1 || height < 1) return
    const style = getZhongshuStyle('bi')

    ctx.save()
    resetChanCanvasState(ctx)
    ctx.beginPath()
    ctx.setLineDash(style.lineDash)
    ctx.fillStyle = style.fillStyle
    ctx.strokeStyle = style.strokeStyle
    ctx.lineWidth = style.lineWidth
    ctx.rect(x, topY, width, height)
    ctx.fill()
    ctx.stroke()
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 中枢矩形：同级别分解版
registerFigure({
  name: 'chan_zs_decomp_figure',
  draw: (ctx, attrs) => {
    const { coordinates } = attrs
    if (!coordinates || coordinates.length < 2) return
    const [{ x: x1, y: y1 }, { x: x2, y: y2 }] = coordinates

    const x = Math.min(x1, x2)
    const width = Math.abs(x2 - x1)
    const height = Math.abs(y2 - y1)
    const topY = Math.min(y1, y2)
    if (width < 1 || height < 1) return
    const style = getZhongshuStyle('bi_decomp')

    ctx.save()
    resetChanCanvasState(ctx)
    ctx.beginPath()
    ctx.setLineDash(style.lineDash)
    ctx.fillStyle = style.fillStyle
    ctx.strokeStyle = style.strokeStyle
    ctx.lineWidth = style.lineWidth
    ctx.rect(x, topY, width, height)
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
    resetChanCanvasState(ctx)
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
    resetChanCanvasState(ctx)
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
    resetChanCanvasState(ctx)
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
    resetChanCanvasState(ctx)
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
    resetChanCanvasState(ctx)
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
    resetChanCanvasState(ctx)
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
    resetChanCanvasState(ctx)
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

// 批量笔：单个 figure 内绘制全部笔，避免 KLineCharts 管理数百个 overlay 对象。
registerFigure({
  name: 'chan_bi_batch_figure',
  draw: (ctx, attrs) => {
    const { coordinates, items } = attrs
    if (!coordinates?.length || !items?.length) return

    ctx.save()
    resetChanCanvasState(ctx)
    ctx.beginPath()
    ctx.setLineDash([4, 4])
    ctx.lineWidth = 1
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.7)'
    for (const item of items) {
      const p0 = coordinates[item.p0]
      const p1 = coordinates[item.p1]
      if (!p0 || !p1) continue
      ctx.moveTo(p0.x, p0.y)
      ctx.lineTo(p1.x, p1.y)
    }
    ctx.stroke()
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 批量线段：确认段和未确认段在同一 overlay 中按样式分批绘制。
registerFigure({
  name: 'chan_seg_batch_figure',
  draw: (ctx, attrs) => {
    const { coordinates, items } = attrs
    if (!coordinates?.length || !items?.length) return

    const drawGroup = (predicate, dash, strokeStyle) => {
      ctx.beginPath()
      ctx.setLineDash(dash)
      ctx.lineWidth = 2
      ctx.strokeStyle = strokeStyle
      for (const item of items) {
        if (!predicate(item)) continue
        const p0 = coordinates[item.p0]
        const p1 = coordinates[item.p1]
        if (!p0 || !p1) continue
        ctx.moveTo(p0.x, p0.y)
        ctx.lineTo(p1.x, p1.y)
      }
      ctx.stroke()
    }

    ctx.save()
    resetChanCanvasState(ctx)
    drawGroup((item) => item.isSure !== false, [], '#ffd54f')
    drawGroup((item) => item.isSure === false, [8, 6], 'rgba(255, 213, 79, 0.7)')
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 批量中枢：低干扰结构框，只由一个 overlay 触发绘制。
registerFigure({
  name: 'chan_zs_batch_figure',
  draw: (ctx, attrs) => {
    const { coordinates, items, decomp, layer } = attrs
    if (!coordinates?.length || !items?.length) return

    ctx.save()
    resetChanCanvasState(ctx)
    for (const item of items) {
      const p0 = coordinates[item.p0]
      const p1 = coordinates[item.p1]
      if (!p0 || !p1) continue
      const x = Math.min(p0.x, p1.x)
      const y = Math.min(p0.y, p1.y)
      const width = Math.abs(p1.x - p0.x)
      const height = Math.abs(p1.y - p0.y)
      if (width < 1 || height < 1) continue

      ctx.beginPath()
      const itemLayer = item.layer || layer || (decomp ? 'bi_decomp' : 'bi')
      const style = getZhongshuStyle(itemLayer)
      ctx.setLineDash(style.lineDash)
      ctx.fillStyle = style.fillStyle
      ctx.strokeStyle = style.strokeStyle
      ctx.lineWidth = style.lineWidth
      ctx.rect(x, y, width, height)
      ctx.fill()
      ctx.stroke()
    }
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 批量买卖点：文本标记数量也可能较多，合并到一个 overlay。
registerFigure({
  name: 'chan_bsp_batch_figure',
  draw: (ctx, attrs) => {
    const { coordinates, items } = attrs
    if (!coordinates?.length || !items?.length) return

    ctx.save()
    resetChanCanvasState(ctx)
    ctx.font = 'bold 10px Arial'
    ctx.textBaseline = 'middle'
    ctx.textAlign = 'center'
    for (const item of items) {
      const point = coordinates[item.p0]
      if (!point) continue
      const text = item.text || 'B'
      const padding = 4
      const width = ctx.measureText(text).width + padding * 2
      const height = 16
      const bgX = point.x - width / 2
      const bgY = item.isBuy !== false ? point.y + 10 : point.y - height - 10

      ctx.fillStyle = item.isBuy !== false ? '#ef5350' : '#26a69a'
      ctx.beginPath()
      if (ctx.roundRect) {
        ctx.roundRect(bgX, bgY, width, height, 3)
      } else {
        ctx.rect(bgX, bgY, width, height)
      }
      ctx.fill()

      ctx.fillStyle = '#ffffff'
      ctx.fillText(text, point.x, bgY + height / 2)
    }
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 批量区间套投影：一个 overlay 绘制全部上级别中枢色带。
registerFigure({
  name: 'chan_projection_band_batch_figure',
  draw: (ctx, attrs) => {
    const { coordinates, items, renderOptions } = attrs
    if (!coordinates?.length || !items?.length) return
    const canvasWidth = renderOptions?.width || 4000

    ctx.save()
    resetChanCanvasState(ctx)
    ctx.setLineDash([3, 5])
    ctx.lineWidth = 1
    for (const item of items) {
      const p0 = coordinates[item.p0]
      const p1 = coordinates[item.p1]
      if (!p0 || !p1) continue
      const topY = Math.min(p0.y, p1.y)
      const height = Math.abs(p1.y - p0.y)
      if (height < 1) continue

      if (item.type === 'UP_ZS') {
        ctx.fillStyle = 'rgba(239, 83, 80, 0.06)'
        ctx.strokeStyle = 'rgba(239, 83, 80, 0.18)'
      } else {
        ctx.fillStyle = 'rgba(38, 166, 154, 0.06)'
        ctx.strokeStyle = 'rgba(38, 166, 154, 0.18)'
      }
      ctx.beginPath()
      ctx.rect(0, topY, canvasWidth, height)
      ctx.fill()
      ctx.beginPath()
      ctx.moveTo(0, topY)
      ctx.lineTo(canvasWidth, topY)
      ctx.moveTo(0, topY + height)
      ctx.lineTo(canvasWidth, topY + height)
      ctx.stroke()
    }
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 批量背驰辅助：避免每个面积比气泡都成为独立 overlay。
registerFigure({
  name: 'chan_momentum_compare_batch_figure',
  draw: (ctx, attrs) => {
    const { coordinates, items } = attrs
    if (!coordinates?.length || !items?.length) return

    ctx.save()
    resetChanCanvasState(ctx)
    ctx.font = 'bold 9px Arial'
    ctx.textBaseline = 'middle'
    ctx.textAlign = 'center'
    for (const item of items) {
      const point = coordinates[item.p0]
      if (!point || item.area == null || item.prevArea == null) continue
      const ratio = item.prevArea > 0 ? Math.round(item.area / item.prevArea * 100) : 0
      const label = `${ratio}%`
      const width = ctx.measureText(label).width + 6
      const height = 13
      const bgX = point.x - width / 2
      const bgY = item.isDiverge ? point.y - height - 5 : point.y + 5

      ctx.fillStyle = item.isDiverge
        ? 'rgba(255, 152, 0, 0.82)'
        : 'rgba(90, 90, 110, 0.65)'
      ctx.beginPath()
      if (ctx.roundRect) {
        ctx.roundRect(bgX, bgY, width, height, 2)
      } else {
        ctx.rect(bgX, bgY, width, height)
      }
      ctx.fill()
      ctx.fillStyle = '#ffffff'
      ctx.fillText(label, point.x, bgY + height / 2)
    }
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 批量走势切分：一个 overlay 绘制全部垂直切分线。
registerFigure({
  name: 'chan_decomposition_grid_batch_figure',
  draw: (ctx, attrs) => {
    const { coordinates, items, renderOptions } = attrs
    if (!coordinates?.length || !items?.length) return
    const canvasHeight = renderOptions?.height || 3000

    ctx.save()
    resetChanCanvasState(ctx)
    ctx.setLineDash([3, 5])
    ctx.lineWidth = 1
    ctx.strokeStyle = 'rgba(255, 213, 79, 0.12)'
    ctx.beginPath()
    for (const item of items) {
      const point = coordinates[item.p0]
      if (!point) continue
      ctx.moveTo(point.x, 0)
      ctx.lineTo(point.x, canvasHeight)
    }
    ctx.stroke()

    ctx.setLineDash([])
    ctx.fillStyle = 'rgba(255, 213, 79, 0.40)'
    ctx.font = '10px Arial'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    for (const item of items) {
      const point = coordinates[item.p0]
      if (point && item.label) ctx.fillText(item.label, point.x, 4)
    }
    ctx.restore()
  },
  checkEventOn: () => false,
})

// 批量防线预警：支撑/压力横线合并绘制。
registerFigure({
  name: 'chan_support_line_batch_figure',
  draw: (ctx, attrs) => {
    const { coordinates, items, renderOptions } = attrs
    if (!coordinates?.length || !items?.length) return
    const width = renderOptions?.width || 2000

    ctx.save()
    resetChanCanvasState(ctx)
    ctx.setLineDash([5, 5])
    ctx.lineWidth = 1
    ctx.strokeStyle = '#64b5f6'
    ctx.fillStyle = '#64b5f6'
    ctx.font = '10px Arial'
    for (const item of items) {
      const point = coordinates[item.p0]
      if (!point) continue
      ctx.beginPath()
      ctx.moveTo(0, point.y)
      ctx.lineTo(width, point.y)
      ctx.stroke()
      if (item.text) ctx.fillText(item.text, 10, point.y - 5)
    }
    ctx.restore()
  },
  checkEventOn: () => false,
})

function resolveOverlayCoordinates(coordinates, overlay, xAxis, yAxis) {
  const points = overlay?.points || []
  return coordinates.map((coordinate, index) => {
    const point = points[index]
    if (!point || typeof point.dataIndex !== 'number') return coordinate

    return {
      x: xAxis.convertToPixel(point.dataIndex),
      y: typeof point.value === 'number' ? yAxis.convertToPixel(point.value) : coordinate.y,
    }
  })
}

// ═══════════════════════════════════════════════════════════════
// Overlay 定义 — 将 Figure 注册为可标注对象
// ═══════════════════════════════════════════════════════════════

const overlayConfigs = [
  {
    name: 'chan_bi_batch',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, xAxis, yAxis }) => [
      { type: 'chan_bi_batch_figure', ignoreEvent: true, attrs: { coordinates: resolveOverlayCoordinates(coordinates, overlay, xAxis, yAxis), items: overlay.extendData?.items || [] } },
    ],
  },
  {
    name: 'chan_seg_batch',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, xAxis, yAxis }) => [
      { type: 'chan_seg_batch_figure', ignoreEvent: true, attrs: { coordinates: resolveOverlayCoordinates(coordinates, overlay, xAxis, yAxis), items: overlay.extendData?.items || [] } },
    ],
  },
  {
    name: 'chan_zs_batch',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, xAxis, yAxis }) => [
      { type: 'chan_zs_batch_figure', ignoreEvent: true, attrs: { coordinates: resolveOverlayCoordinates(coordinates, overlay, xAxis, yAxis), items: overlay.extendData?.items || [], decomp: false, layer: overlay.extendData?.layer || 'bi' } },
    ],
  },
  {
    name: 'chan_zs_decomp_batch',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, xAxis, yAxis }) => [
      { type: 'chan_zs_batch_figure', ignoreEvent: true, attrs: { coordinates: resolveOverlayCoordinates(coordinates, overlay, xAxis, yAxis), items: overlay.extendData?.items || [], decomp: true, layer: overlay.extendData?.layer || 'bi_decomp' } },
    ],
  },
  {
    name: 'chan_bsp_batch',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, xAxis, yAxis }) => [
      { type: 'chan_bsp_batch_figure', ignoreEvent: true, attrs: { coordinates: resolveOverlayCoordinates(coordinates, overlay, xAxis, yAxis), items: overlay.extendData?.items || [] } },
    ],
  },
  {
    name: 'chan_projection_band_batch',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, bounding, xAxis, yAxis }) => [
      { type: 'chan_projection_band_batch_figure', ignoreEvent: true, attrs: { coordinates: resolveOverlayCoordinates(coordinates, overlay, xAxis, yAxis), items: overlay.extendData?.items || [], renderOptions: { width: bounding.width } } },
    ],
  },
  {
    name: 'chan_momentum_compare_batch',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, xAxis, yAxis }) => [
      { type: 'chan_momentum_compare_batch_figure', ignoreEvent: true, attrs: { coordinates: resolveOverlayCoordinates(coordinates, overlay, xAxis, yAxis), items: overlay.extendData?.items || [] } },
    ],
  },
  {
    name: 'chan_decomposition_grid_batch',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, bounding, xAxis, yAxis }) => [
      { type: 'chan_decomposition_grid_batch_figure', ignoreEvent: true, attrs: { coordinates: resolveOverlayCoordinates(coordinates, overlay, xAxis, yAxis), items: overlay.extendData?.items || [], renderOptions: { height: bounding.height } } },
    ],
  },
  {
    name: 'chan_support_line_batch',
    totalStep: 2,
    needDefaultPointFigure: false,
    needDefaultXAxisFigure: false,
    needDefaultYAxisFigure: false,
    createPointFigures: ({ coordinates, overlay, bounding, xAxis, yAxis }) => [
      { type: 'chan_support_line_batch_figure', ignoreEvent: true, attrs: { coordinates: resolveOverlayCoordinates(coordinates, overlay, xAxis, yAxis), items: overlay.extendData?.items || [], renderOptions: { width: bounding.width } } },
    ],
  },
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
 *   bi_zhongshus:    [{ begin_date, end_date, display_begin_date, display_end_date, zg, zd, gg, dd }, ...]
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

  const overlays = buildChanOverlayBatches(data, isDay)
  clearChanOverlays(chart, clearFirst)
  renderChanOverlayBatches(chart, overlays)
}

export function buildChanOverlayBatches(data, isDay = true) {
  if (!data) return []

  const vis = data.vis || {}
  const overlays = []

  // P2-FIX #7: 使用共享的 toTimestamp
  const tsCache = new Map()
  const toTs = (dateStr) => {
    const key = `${isDay ? 'd' : 'm'}:${dateStr}`
    if (!tsCache.has(key)) {
      tsCache.set(key, toTimestamp(dateStr, isDay))
    }
    return tsCache.get(key)
  }
  const pointAt = (dateStr, value) => {
    const timestamp = toTs(dateStr)
    const dataIndex = data.klineIndexByTimestamp?.get?.(timestamp)
    if (Number.isInteger(dataIndex)) {
      return { timestamp, dataIndex, value }
    }
    return { timestamp, value }
  }
  const createBatch = ({ groupId, name, records, layer }) => {
    if (!records.length) return
    overlays.push({
      groupId,
      name,
      paneId: 'candle_pane',
      lock: true,
      visible: true,
      points: records.flatMap((record) => record.points),
      extendData: {
        layer,
        // 每条结构记录只保存点位索引，坐标由 KLineCharts 统一转换。
        items: records.map((record, index) => ({
          ...record.meta,
          layer,
          p0: index * 2,
          p1: index * 2 + 1,
        })),
      },
    })
  }

  appendBasicChanBatches({ data, vis, toTs, pointAt, createBatch })
  appendAdvancedChanBatches({ data, vis, toTs, pointAt, createBatch })
  return overlays
}

export function clearChanOverlays(chart, clearFirst = true) {
  if (!chart || !clearFirst) return
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
}

export function renderChanOverlayBatches(chart, overlays) {
  if (!chart || !overlays?.length) return
  chart.createOverlay(overlays)
}

export function renderChanOverlayBatchesProgressively(chart, overlays, options = {}) {
  let index = 0
  let cancelled = false
  let frameId = null
  const chunkSize = Math.max(1, Number(options.chunkSize) || 1)
  const getCurrentChart = options.getCurrentChart
  const onBatch = options.onBatch
  const onDone = options.onDone
  const onError = options.onError

  const step = () => {
    frameId = null
    if (cancelled) return
    if (!chart || (getCurrentChart && getCurrentChart() !== chart)) {
      cancelled = true
      return
    }
    try {
      const batch = overlays.slice(index, index + chunkSize)
      if (batch.length > 0) {
        chart.createOverlay(batch)
        index += batch.length
        if (onBatch) onBatch({ rendered: index, total: overlays.length })
      }
      if (index < overlays.length) {
        frameId = window.requestAnimationFrame(step)
      } else if (onDone) {
        onDone()
      }
    } catch (e) {
      cancelled = true
      if (onError) onError(e)
    }
  }

  frameId = window.requestAnimationFrame(step)
  return () => {
    cancelled = true
    if (frameId) {
      window.cancelAnimationFrame(frameId)
    }
  }
}

function appendBasicChanBatches({ data, vis, pointAt, createBatch }) {
  // 1. 笔
  if (data.bis?.length > 0) {
    createBatch({
      groupId: 'chan_bi_group',
      name: 'chan_bi_batch',
      records: data.bis.map((bi) => ({
        points: [
          pointAt(bi.x0, bi.y0),
          pointAt(bi.x1, bi.y1),
        ],
        meta: {},
      })),
    })
  }

  // 1.5 线段（已确认 = 实线，未确认 = 虚线）
  if (data.segs?.length > 0) {
    createBatch({
      groupId: 'chan_seg_group',
      name: 'chan_seg_batch',
      records: data.segs.map((seg) => ({
        points: [
          pointAt(seg.x0, seg.y0),
          pointAt(seg.x1, seg.y1),
        ],
        meta: { isSure: seg.is_sure },
      })),
    })
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
    createBatch({
      groupId: 'chan_bi_zs_group',
      name: 'chan_zs_batch',
      layer: 'bi',
      records: data.bi_zhongshus.map((zs) => ({
        points: [
          pointAt(zs.display_begin_date || zs.begin_date, zs.zg),
          pointAt(zs.display_end_date || zs.end_date, zs.zd),
        ],
        meta: { type: getZsType(data.bis, zs.begin_date) },
      })),
    })
  }

  // 2.1 笔中枢（同级别分解，紫色虚线矩形）
  if (data.bi_zhongshus_decomp?.length > 0) {
    createBatch({
      groupId: 'chan_bi_zs_decomp_group',
      name: 'chan_zs_decomp_batch',
      layer: 'bi_decomp',
      records: data.bi_zhongshus_decomp.map((zs) => ({
        points: [
          pointAt(zs.display_begin_date || zs.begin_date, zs.zg),
          pointAt(zs.display_end_date || zs.end_date, zs.zd),
        ],
        meta: { type: getZsType(data.bis, zs.begin_date) },
      })),
    })
  }

  // 2.5 线段中枢（大级别，实线矩形，更醒目）
  if (data.seg_zhongshus?.length > 0) {
    createBatch({
      groupId: 'chan_seg_zs_group',
      name: 'chan_zs_batch',
      layer: 'seg',
      records: data.seg_zhongshus.map((zs) => ({
        points: [
          pointAt(zs.display_begin_date || zs.begin_date, zs.zg),
          pointAt(zs.display_end_date || zs.end_date, zs.zd),
        ],
        meta: { type: getZsType(data.segs, zs.begin_date) },
      })),
    })
  }

  // 3. 买卖点 (待后端新增 bsps 字段)
  if (data.bsps?.length > 0) {
    const enabledTypes = Array.isArray(vis.bsp_types) ? new Set(vis.bsp_types) : null
    const bspRecords = []
    for (const bsp of data.bsps) {
      const typeStr = (bsp.type || '1').toString().toUpperCase()
      const rawType = (bsp.type || '1').toString()
      if (bsp.is_buy && vis.bsp_buy === false) continue
      if (!bsp.is_buy && vis.bsp_sell === false) continue
      if (enabledTypes && !enabledTypes.has(rawType)) continue
      const badgeText = bsp.is_buy ? `B${typeStr}` : `S${typeStr}`
      bspRecords.push({
        points: [
          pointAt(bsp.time, bsp.price),
          pointAt(bsp.time, bsp.price),
        ],
        meta: { text: badgeText, isBuy: bsp.is_buy },
      })
    }
    createBatch({
      groupId: 'chan_bsp_group',
      name: 'chan_bsp_batch',
      records: bspRecords,
    })
  }
}

function appendAdvancedChanBatches({ data, vis, toTs, pointAt, createBatch }) {
  // ──────────────────────────────────────────────────────────────
  // 高级分析图层（alpha ≤ 0.10，空数据静默跳过）
  // ──────────────────────────────────────────────────────────────

  // 4. 区间套投影 — 上级别中枢横向色带
  // 互斥约束已在 LayerPanel 状态机处理，此处无需重复检查
  if (vis.projection && data.higher_zhongshus?.length > 0) {
    createBatch({
      groupId: 'chan_projection_group',
      name: 'chan_projection_band_batch',
      records: data.higher_zhongshus.map((zs) => ({
        points: [
          pointAt(zs.begin_date, zs.zg),
          pointAt(zs.end_date, zs.zd),
        ],
        // 色带方向由外部数据补齐前，沿用既有默认上升中枢配色。
        meta: { type: zs.type || 'UP_ZS' },
      })),
    })
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
    createBatch({
      groupId: 'chan_momentum_group',
      name: 'chan_momentum_compare_batch',
      records: momentumPairs.map((p) => ({
        points: [
          pointAt(p.bi.x1, p.bi.y1),
          pointAt(p.bi.x1, p.bi.y1),
        ],
        meta: { area: p.area, prevArea: p.prevArea, isDiverge: p.isDiverge },
      })),
    })
  }

  // 6. 走势切分 — 线段边界垂直虚线
  // 在每根已确认线段结束处画全高竖线，区分向上/向下/盘整走势区块
  if (vis.decomp_grid && data.segs?.length > 1) {
    const gridRecords = []
    // 不画最后一根线段的结束点（当前走势尚未结束，防止最右侧出现孤立竖线）
    for (let i = 0; i < data.segs.length - 1; i++) {
      const seg = data.segs[i]
      if (!seg.is_sure) continue  // 未确认线段不画切分线
      gridRecords.push({
        points: [
          pointAt(seg.x1, seg.y1),
          pointAt(seg.x1, seg.y1),
        ],
        meta: { label: seg.is_up ? '↓' : '↑' },
      })
    }
    createBatch({
      groupId: 'chan_decomp_group',
      name: 'chan_decomposition_grid_batch',
      records: gridRecords,
    })
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
    createBatch({
      groupId: 'chan_support_wall_group',
      name: 'chan_support_line_batch',
      records: walls.slice(0, 8).map((wall) => ({
        points: [
          { timestamp: anchorTs, value: wall.price },
          { timestamp: anchorTs, value: wall.price },
        ],
        meta: { text: `${wall.isBullish ? '支撑' : '压力'} ×${wall.strength}` },
      })),
    })
  }
}
