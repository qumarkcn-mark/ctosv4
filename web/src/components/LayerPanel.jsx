import { useState } from 'react'
import { PRESETS, applyPreset, saveVisibility } from '../store/layerState.js'
import './LayerPanel.css'

const LAYER_ITEMS = [
  { key: 'bi',     label: '笔',         group: '缠论' },
  { key: 'seg',    label: '线段',       group: '缠论' },
  { key: 'bi_zs',  label: '笔中枢',    group: '缠论' },
  { key: 'bi_zs_decomp', label: '笔中枢(分解)', group: '缠论' },
  { key: 'seg_zs', label: '段中枢',    group: '缠论' },
  { key: 'bsp',    label: '买卖点',    group: '缠论' },
  { key: 'ma',     label: '均线',       group: '指标' },
  { key: 'vol',    label: '成交量',     group: '指标' },
  { key: 'macd',   label: 'MACD',       group: '指标' },
  // ── 高级分析（Advanced）──
  { key: 'projection',       label: '区间套投影', group: '高级分析', hint: '叠加上级别中枢色带（与走势切分互斥）' },
  { key: 'momentum_compare', label: '背驰辅助',   group: '高级分析', hint: '标注相邻同向笔的 MACD 面积衰减比' },
  { key: 'support_wall',     label: '防线预警',   group: '高级分析', hint: '自动聚类顶底分型，标注密集支撑压力位' },
  { key: 'decomp_grid',      label: '走势切分',   group: '高级分析', hint: '在线段边界处画垂直虚线（与区间套投影互斥）' },
]

const PRESET_ITEMS = [
  { key: 'naked', label: '🕯 裸K', title: '只显示 K 线' },
  { key: 'standard', label: '📐 标准', title: '缠论 + 指标' },
  { key: 'full', label: '🔬 全标注', title: '所有图层打开' },
]

export default function LayerPanel({ visibility, onChange }) {
  const [expanded, setExpanded] = useState(false)

  const toggle = (key) => {
    const next = { ...visibility, [key]: !visibility[key] }
    // 笔中枢 和 笔中枢(分解) 互斥：打开一个自动关闭另一个
    if (key === 'bi_zs' && next.bi_zs) next.bi_zs_decomp = false
    if (key === 'bi_zs_decomp' && next.bi_zs_decomp) next.bi_zs = false
    // 区间套投影 与 走势切分 互斥：横竖网格同时开启会淹没 K 线
    if (key === 'projection' && next.projection) next.decomp_grid = false
    if (key === 'decomp_grid' && next.decomp_grid) next.projection = false
    saveVisibility(next)
    onChange(next)
  }

  const handlePreset = (name) => {
    const next = applyPreset(name)
    saveVisibility(next)
    onChange(next)
  }

  const groups = {}
  LAYER_ITEMS.forEach((item) => {
    if (!groups[item.group]) groups[item.group] = []
    groups[item.group].push(item)
  })

  return (
    <div className="layer-panel-wrapper">
      <button
        className="layer-toggle-btn"
        onClick={() => setExpanded(!expanded)}
        title="图层控制"
      >
        📐
      </button>

      {expanded && (
        <div className="layer-panel">
          <div className="layer-panel-header">
            <span>图层控制</span>
          </div>

          {/* 预设 */}
          <div className="layer-presets">
            {PRESET_ITEMS.map(({ key, label, title }) => (
              <button
                key={key}
                className="preset-btn"
                onClick={() => handlePreset(key)}
                title={title}
              >
                {label}
              </button>
            ))}
          </div>

          {/* 开关列表 */}
          {Object.entries(groups).map(([groupName, items]) => (
            <div key={groupName} className="layer-group">
              <div className="layer-group-label">{groupName}</div>
              {items.map(({ key, label, hint }) => (
                <label key={key} className="layer-switch" title={hint || ''}>
                  <span className="layer-label">{label}</span>
                  <input
                    type="checkbox"
                    checked={!!visibility[key]}
                    onChange={() => toggle(key)}
                  />
                  <span className="switch-slider" />
                </label>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
