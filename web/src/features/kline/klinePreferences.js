const PREFIX = 'ct_kline_'

export const DEFAULT_KLINE_PREFS = {
  period: 'day',
  mainIndicator: 'MA',
  subIndicator: 'VOL',
  structureLayer: 'on',
  momentumLayer: 'off',
}

export function readKlinePreference(key, fallback = '') {
  try {
    return localStorage.getItem(`${PREFIX}${key}`) || fallback
  } catch {
    return fallback
  }
}

export function writeKlinePreference(key, value) {
  try {
    localStorage.setItem(`${PREFIX}${key}`, String(value))
  } catch {
    // 无痕模式可能禁用 localStorage；不影响看盘主流程。
  }
}

export function readKlinePreferences() {
  return {
    period: readKlinePreference('period', DEFAULT_KLINE_PREFS.period),
    mainIndicator: readKlinePreference('main_indicator', DEFAULT_KLINE_PREFS.mainIndicator),
    subIndicator: readKlinePreference('sub_indicator', DEFAULT_KLINE_PREFS.subIndicator),
    structureLayer: readKlinePreference('structure_layer', DEFAULT_KLINE_PREFS.structureLayer),
    momentumLayer: readKlinePreference('momentum_layer', DEFAULT_KLINE_PREFS.momentumLayer),
  }
}
