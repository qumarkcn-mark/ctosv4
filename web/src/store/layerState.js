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
  bsp_buy: true,   // 买点
  bsp_sell: true,  // 卖点
  bsp_types: ['1', '1p', '2', '2s', '3a', '3b'],  // 买卖点类型过滤
  cchan_preset: 'live_tolerant',  // CChan 算法预设
  // ── 高级分析（默认关闭，避免初始视觉过载）
  projection:       false,  // 区间套投影：叠加上级别中枢色带
  momentum_compare: false,  // 背驰辅助：标注同向相邻笔的 MACD 面积比
  support_wall:     false,  // 防线预警：自动聚类顶/底分型形成支撑压力位
  decomp_grid:      false,  // 走势切分：在线段边界处画垂直虚线
}

const PRESETS = {
  naked: {
    bi: false, seg: false, bi_zs: false, bi_zs_decomp: false, seg_zs: false,
    bsp: false, bsp_buy: true, bsp_sell: true, bsp_types: ['1', '1p', '2', '2s', '3a', '3b'],
    ma: false, vol: true, macd: false, cchan_preset: 'live_tolerant',
    projection: false, momentum_compare: false, support_wall: false, decomp_grid: false,
  },
  standard: {
    bi: true, seg: true, bi_zs: true, bi_zs_decomp: false, seg_zs: true,
    bsp: true, bsp_buy: true, bsp_sell: true, bsp_types: ['1', '1p', '2', '2s', '3a', '3b'],
    ma: true, vol: true, macd: true, cchan_preset: 'live_tolerant',
    projection: false, momentum_compare: false, support_wall: false, decomp_grid: false,
  },
  full: {
    bi: true, seg: true, bi_zs: true, bi_zs_decomp: true, seg_zs: true,
    bsp: true, bsp_buy: true, bsp_sell: true, bsp_types: ['1', '1p', '2', '2s', '3a', '3b'],
    ma: true, vol: true, macd: true, cchan_preset: 'live_tolerant',
    // 全标注预设：背驰辅助和防线预警开启，区间套/切分保持关闭（防止网格叠加）
    projection: false, momentum_compare: true, support_wall: true, decomp_grid: false,
  },
}

export function loadVisibility() {
  try {
    const data = localStorage.getItem(STORAGE_KEY)
    const merged = data ? { ...DEFAULT_VISIBILITY, ...JSON.parse(data) } : { ...DEFAULT_VISIBILITY }
    if (!Array.isArray(merged.bsp_types)) {
      merged.bsp_types = [...DEFAULT_VISIBILITY.bsp_types]
    } else {
      const allowed = new Set(DEFAULT_VISIBILITY.bsp_types)
      merged.bsp_types = merged.bsp_types.filter((item) => allowed.has(item))
    }
    if (!['live_tolerant', 'textbook_strict', 'sensitive_probe'].includes(merged.cchan_preset)) {
      merged.cchan_preset = DEFAULT_VISIBILITY.cchan_preset
    }
    return merged
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
