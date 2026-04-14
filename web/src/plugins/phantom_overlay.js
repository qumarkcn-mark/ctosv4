/**
 * CT-OS V4.0 — 推演幻影 K 线覆盖层
 * 
 * 使用 KlineCharts v10 overlay 的 points 系统绘制推演路径。
 * 每个 scenario 渲染为一条半透明的区域路径（上沿=high，下沿=low）。
 * 
 * 与 chanOverlay.js 使用相同的架构：
 *   1. registerFigure() — 自定义绘图逻辑
 *   2. registerOverlay() — 将 figure 注册为 overlay
 *   3. renderPhantomOverlays() — 批量渲染函数
 */

import { registerFigure, registerOverlay } from 'klinecharts'

// ═══════════════════════════════════════════════════════════════
// Figure: 推演路径（使用带区域的折线）
// ═══════════════════════════════════════════════════════════════

registerFigure({
  name: 'phantom_path_figure',
  draw: (ctx, attrs) => {
    const { coordinates, scenario } = attrs
    if (!coordinates || coordinates.length < 2) return

    const isDominant = (scenario?.probability || 0) > 33.4
    const sType = scenario?.type || 'unknown'

    // 颜色选择
    let lineColor, fillColor
    if (sType === 'structural_breakdown') {
      lineColor = isDominant ? 'rgba(239, 83, 80, 0.8)' : 'rgba(239, 83, 80, 0.4)'
      fillColor = isDominant ? 'rgba(239, 83, 80, 0.12)' : 'rgba(239, 83, 80, 0.05)'
    } else if (sType === 'zhongshu_oscillation') {
      lineColor = isDominant ? 'rgba(158, 158, 158, 0.7)' : 'rgba(158, 158, 158, 0.3)'
      fillColor = isDominant ? 'rgba(158, 158, 158, 0.1)' : 'rgba(158, 158, 158, 0.04)'
    } else {
      // right_side_major_wave or default
      lineColor = isDominant ? 'rgba(240, 185, 11, 0.9)' : 'rgba(240, 185, 11, 0.5)'
      fillColor = isDominant ? 'rgba(240, 185, 11, 0.15)' : 'rgba(240, 185, 11, 0.06)'
    }

    // 提取 high/low 序列（从 extendData 中的 geometry 数据）
    const geometry = scenario?.phantom_geometry || []

    // 绘制路径连线（close 值 → 对应 coordinates 的 y 坐标）
    ctx.save()
    ctx.beginPath()
    ctx.setLineDash([6, 4])
    ctx.lineWidth = isDominant ? 2 : 1
    ctx.strokeStyle = lineColor

    // coordinates 是 points 的像素映射：每个 {x, y} 对应一个 {timestamp, value(=close)}
    for (let i = 0; i < coordinates.length; i++) {
      if (i === 0) ctx.moveTo(coordinates[i].x, coordinates[i].y)
      else ctx.lineTo(coordinates[i].x, coordinates[i].y)
    }
    ctx.stroke()

    // 绘制起点标记
    if (coordinates.length > 0) {
      ctx.beginPath()
      ctx.arc(coordinates[0].x, coordinates[0].y, 3, 0, Math.PI * 2)
      ctx.fillStyle = lineColor
      ctx.fill()
    }

    // 绘制终点标签
    if (coordinates.length > 1) {
      const last = coordinates[coordinates.length - 1]
      const prob = scenario?.probability || 0
      const label = `${scenario?.name || ''}  ${prob.toFixed(0)}%`

      ctx.font = '11px JetBrains Mono, monospace'
      ctx.fillStyle = lineColor
      ctx.textBaseline = 'middle'
      ctx.textAlign = 'left'
      ctx.fillText(label, last.x + 6, last.y)
    }

    ctx.restore()
  },
  checkEventOn: () => false,
})


// ═══════════════════════════════════════════════════════════════
// Overlay 注册
// ═══════════════════════════════════════════════════════════════

export const phantomOverlay = {
  name: 'phantom_wave',
  totalStep: 0, // 不需要用户绘制步骤
  needDefaultPointFigure: false,
  needDefaultXAxisFigure: false,
  needDefaultYAxisFigure: false,
  createPointFigures: ({ coordinates, overlay }) => {
    const scenario = overlay.extendData?.scenario || {}
    return [{
      type: 'phantom_path_figure',
      attrs: { coordinates, scenario }
    }]
  },
}


// ═══════════════════════════════════════════════════════════════
// 批量渲染函数
// ═══════════════════════════════════════════════════════════════

/**
 * 将推演结果批量渲染到图表上
 * @param {object} chart - KLineCharts 实例
 * @param {Array} scenarios - API 返回的 scenarios 数组（含 phantom_geometry）
 */
export function renderPhantomOverlays(chart, scenarios) {
  if (!chart || !scenarios?.length) return

  // 清除旧的推演层
  chart.removeOverlay({ groupId: 'phantom_group' })

  const overlays = []

  for (const scenario of scenarios) {
    const geometry = scenario.phantom_geometry || []
    if (!geometry.length) continue

    // 每个 phantom bar 的 close 作为路径点
    const points = geometry.map(bar => ({
      timestamp: bar.timestamp,
      value: bar.close,
    }))

    overlays.push({
      groupId: 'phantom_group',
      name: 'phantom_wave',
      lock: true,
      extendData: { scenario },
      points,
    })
  }

  if (overlays.length > 0) {
    chart.createOverlay(overlays)
    console.log(`[PhantomOverlay] 渲染 ${overlays.length} 条推演路径`)
  }
}
