import { useState } from 'react'
import { PRESETS, applyPreset, saveVisibility } from '../store/layerState.js'
import './LayerPanel.css'

const LAYER_ITEMS = [
  { key: 'bi',     label: '笔',     group: '缠论' },
  { key: 'seg',    label: '线段',   group: '缠论' },
  { key: 'bi_zs',  label: '笔中枢', group: '缠论' },
  { key: 'bi_zs_decomp', label: '笔中枢(分解)', group: '缠论' },
  { key: 'seg_zs', label: '段中枢', group: '缠论' },
  { key: 'bsp',    label: '买卖点', group: '缠论' },
  { key: 'ma',     label: '均线',   group: '指标' },
  { key: 'vol',    label: '成交量', group: '指标' },
  { key: 'macd',   label: 'MACD',   group: '指标' },
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
              {items.map(({ key, label }) => (
                <label key={key} className="layer-switch">
                  <span className="layer-label">{label}</span>
                  <input
                    type="checkbox"
                    checked={visibility[key] !== false}
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
