/**
 * CT-OS V4.0 — 全局配置
 */

// API 基础地址：优先读 Vite 环境变量，兜底 localhost
export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8001/api'
