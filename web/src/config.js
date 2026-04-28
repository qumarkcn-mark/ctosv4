/**
 * CT-OS V4.0 — 全局配置
 */

// API 基础地址：开发态默认走 Vite /api proxy，避免 8000/8001 端口漂移。
export const API_BASE = import.meta.env.VITE_API_BASE || '/api'
