/**
 * CT-OS V4.0 — 工具函数
 */

/**
 * 日期字符串 → Unix 毫秒时间戳
 * @param {string} dateStr - "2024-01-02" 或 "2024-01-02 09:30:00"
 * @param {boolean} isDay - 是否日线格式
 * @returns {number} Unix 毫秒
 */
export function toTimestamp(dateStr, isDay = true) {
  if (!dateStr) return 0
  if (isDay) return new Date(dateStr + 'T00:00:00+08:00').getTime()
  return new Date(dateStr.replace(' ', 'T') + '+08:00').getTime()
}
