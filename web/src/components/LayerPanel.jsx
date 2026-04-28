import { useState } from 'react'
import { PRESETS, applyPreset, saveVisibility } from '../store/layerState.js'
import './LayerPanel.css'

const DISPLAY_GROUPS = [
  {
    group: '结构',
    items: [
  { key: 'bi',     label: '笔',         group: '缠论' },
  { key: 'seg',    label: '线段',       group: '缠论' },
  { key: 'bi_zs',  label: '笔中枢',    group: '缠论' },
  { key: 'bi_zs_decomp', label: '笔中枢(分解)', group: '缠论' },
  { key: 'seg_zs', label: '段中枢',    group: '缠论' },
    ],
  },
  {
    group: '辅助',
    items: [
      { key: 'projection',       label: '区间套投影', hint: '叠加上级别中枢色带（与走势切分互斥）' },
      { key: 'momentum_compare', label: '背驰辅助',   hint: '标注相邻同向笔的 MACD 面积衰减比' },
      { key: 'support_wall',     label: '防线预警',   hint: '自动聚类顶底分型，标注密集支撑压力位' },
      { key: 'decomp_grid',      label: '走势切分',   hint: '在线段边界处画垂直虚线（与区间套投影互斥）' },
    ],
  },
  {
    group: '指标',
    items: [
      { key: 'ma',     label: '均线' },
      { key: 'vol',    label: '成交量' },
      { key: 'macd',   label: '副图' },
    ],
  },
]

const PRESET_ITEMS = [
  { key: 'naked', label: '裸K', title: '只显示 K 线' },
  { key: 'standard', label: '标准', title: '缠论 + 指标' },
  { key: 'full', label: '全标注', title: '所有图层打开' },
]

const BSP_TYPES = ['1', '1p', '2', '2s', '3a', '3b']

const CCHAN_PRESETS = [
  {
    key: 'live_tolerant',
    label: '实盘容错',
    summary: '宽松笔 / loss 分型 / 跳空当K线',
  },
  {
    key: 'textbook_strict',
    label: '严格验算',
    summary: '严格笔 / strict 分型 / 跳空不单列',
  },
  {
    key: 'sensitive_probe',
    label: '敏感观察',
    summary: '宽松笔 / 更敏感买卖点观察',
  },
]

export default function LayerPanel({ visibility, onChange }) {
  const [expanded, setExpanded] = useState(false)
  const [activeTab, setActiveTab] = useState('display')

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

  const updateVisibility = (patch) => {
    const next = { ...visibility, ...patch }
    saveVisibility(next)
    onChange(next)
  }

  const handlePreset = (name) => {
    const next = { ...applyPreset(name), cchan_preset: visibility.cchan_preset || 'live_tolerant' }
    saveVisibility(next)
    onChange(next)
  }

  const toggleBspType = (type) => {
    const current = Array.isArray(visibility.bsp_types) ? visibility.bsp_types : []
    const nextTypes = current.includes(type)
      ? current.filter((item) => item !== type)
      : [...current, type]
    updateVisibility({ bsp_types: nextTypes })
  }

  const activePreset = CCHAN_PRESETS.find((item) => item.key === visibility.cchan_preset) || CCHAN_PRESETS[0]

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

          <div className="layer-tabs">
            <button className={activeTab === 'display' ? 'active' : ''} onClick={() => setActiveTab('display')}>显示</button>
            <button className={activeTab === 'bsp' ? 'active' : ''} onClick={() => setActiveTab('bsp')}>买卖点</button>
            <button className={activeTab === 'algo' ? 'active' : ''} onClick={() => setActiveTab('algo')}>算法</button>
          </div>

          {activeTab === 'display' && (
            <>
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

              {DISPLAY_GROUPS.map(({ group, items }) => (
                <div key={group} className="layer-group">
                  <div className="layer-group-label">{group}</div>
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
            </>
          )}

          {activeTab === 'bsp' && (
            <div className="layer-tab-content">
              <div className="layer-group">
                <div className="layer-group-label">总开关</div>
                <label className="layer-switch">
                  <span className="layer-label">买卖点</span>
                  <input type="checkbox" checked={!!visibility.bsp} onChange={() => toggle('bsp')} />
                  <span className="switch-slider" />
                </label>
              </div>

              <div className="layer-group">
                <div className="layer-group-label">方向</div>
                <div className="layer-chip-row">
                  <button className={visibility.bsp_buy !== false ? 'selected' : ''} onClick={() => updateVisibility({ bsp_buy: visibility.bsp_buy === false })}>买点</button>
                  <button className={visibility.bsp_sell !== false ? 'selected' : ''} onClick={() => updateVisibility({ bsp_sell: visibility.bsp_sell === false })}>卖点</button>
                </div>
              </div>

              <div className="layer-group">
                <div className="layer-group-label">类型</div>
                <div className="layer-chip-grid">
                  {BSP_TYPES.map((type) => (
                    <button
                      key={type}
                      className={(visibility.bsp_types || []).includes(type) ? 'selected' : ''}
                      onClick={() => toggleBspType(type)}
                    >
                      {type}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {activeTab === 'algo' && (
            <div className="layer-tab-content">
              <div className="layer-group">
                <div className="layer-group-label">CChan 预设</div>
                <div className="algo-preset-list">
                  {CCHAN_PRESETS.map((preset) => (
                    <button
                      key={preset.key}
                      className={visibility.cchan_preset === preset.key ? 'active' : ''}
                      onClick={() => updateVisibility({ cchan_preset: preset.key })}
                    >
                      <strong>{preset.label}</strong>
                      <span>{preset.summary}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="layer-group">
                <div className="layer-group-label">当前算法</div>
                <div className="algo-summary">
                  <div><span>当前</span><strong>{activePreset.label}</strong></div>
                  <div><span>笔</span><strong>{visibility.cchan_preset === 'textbook_strict' ? '严格' : '宽松'}</strong></div>
                  <div><span>分型</span><strong>{visibility.cchan_preset === 'textbook_strict' ? 'strict' : 'loss'}</strong></div>
                  <div><span>跳空</span><strong>{visibility.cchan_preset === 'textbook_strict' ? '不单列' : '当K线'}</strong></div>
                  <div><span>逐K推进</span><strong>开启</strong></div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
