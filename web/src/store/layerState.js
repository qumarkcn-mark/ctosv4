/**
 * CT-OS V4.0 — 图层状态管理
 * 
 * 简单模块级状态 + localStorage 持久化，
 * 供 LayerPanel 和 KlineChart 共享。
 */

const STORAGE_KEY = 'ct_layer_visibility_v4'

const DEFAULT_VISIBILITY = {
  bi: true,        // 笔
  seg: true,       // 线段
  bi_zs: true,     // 笔中枢（全局）
  bi_zs_decomp: false,  // 笔中枢（同级别分解）
  seg_zs: true,    // 线段中枢
  bsp: true,       // 买卖点
  ma: true,        // 均线
  vol: true,       // 成交量
  macd: true,      // MACD
}

const PRESETS = {
  naked:    { bi: false, seg: false, bi_zs: false, bi_zs_decomp: false, seg_zs: false, bsp: false, ma: false, vol: true,  macd: false },
  standard: { bi: true,  seg: true,  bi_zs: true,  bi_zs_decomp: false, seg_zs: true,  bsp: true,  ma: true,  vol: true,  macd: true  },
  full:     { bi: true,  seg: true,  bi_zs: true,  bi_zs_decomp: true,  seg_zs: true,  bsp: true,  ma: true,  vol: true,  macd: true  },
}

export function loadVisibility() {
  try {
    const data = localStorage.getItem(STORAGE_KEY)
    return data ? { ...DEFAULT_VISIBILITY, ...JSON.parse(data) } : { ...DEFAULT_VISIBILITY }
  } catch {
    return { ...DEFAULT_VISIBILITY }
  }
}

export function saveVisibility(vis) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(vis))
}

export function applyPreset(name) {
  const preset = PRESETS[name] || PRESETS.standard
  return { ...preset }
}

export { PRESETS, DEFAULT_VISIBILITY }
