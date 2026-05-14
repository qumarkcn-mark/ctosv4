const app = getApp()

const LEVELS = ['week', 'day', '30', '5']

Page({
  data: {
    symbol: '',
    name: '',
    loading: true,
    refreshing: false,
    error: '',
    structureState: null,
    statusLabel: '检测中',
    statusTone: 'checking',
    reminderCount: 0,
    activeReminderCount: 0,
    outcomeCount: 0,
    mistakeCount: 0,
    memoryNote: '暂无复盘记忆',
  },

  onLoad(options) {
    const symbol = options.symbol || ''
    this.setData({ symbol })
    this.runWhenAuthed(() => this.fetchStructureState({ ensurePipeline: true }))
  },

  onPullDownRefresh() {
    this.fetchStructureState({ ensurePipeline: true })
  },

  refreshStructureState() {
    this.fetchStructureState({ ensurePipeline: true })
  },

  runWhenAuthed(callback) {
    if (app.globalData.token) {
      callback()
      return
    }
    app.loginReadyCallback = callback
  },

  fetchStructureState({ ensurePipeline = false } = {}) {
    if (!this.data.symbol) return
    this.setData({
      loading: !this.data.structureState,
      refreshing: Boolean(this.data.structureState),
      error: '',
    })

    wx.request({
      url: `${app.globalData.apiBase}/ai-structure/workspace/bootstrap`,
      method: 'POST',
      header: {
        'Authorization': `Bearer ${app.globalData.token}`,
        'Content-Type': 'application/json',
      },
      data: {
        sources: ['positions', 'recent_chat', 'watchlist'],
        focus_symbols: [this.data.symbol],
        levels: LEVELS,
        client: 'miniprogram',
        include: ['context_status', 'reminders', 'outcomes'],
        ensure_pipeline: ensurePipeline,
        reason: ensurePipeline ? 'miniprogram_structure_detail_refresh' : 'miniprogram_structure_detail',
      },
      success: (res) => {
        if (res.statusCode !== 200 || !res.data || res.data.status !== 'success') {
          this.setData({
            loading: false,
            refreshing: false,
            error: '结构状态读取失败',
          })
          return
        }
        const item = this.findSymbolState(res.data.data)
        if (!item) {
          this.setData({
            loading: false,
            refreshing: false,
            error: '当前股票还未进入 AI 结构池',
          })
          return
        }
        this.applyStructureState(item)
      },
      fail: () => {
        this.setData({
          loading: false,
          refreshing: false,
          error: '网络异常',
        })
      },
      complete: () => {
        wx.stopPullDownRefresh()
      },
    })
  },

  findSymbolState(workspace) {
    const target = normalizeSymbol(this.data.symbol)
    return ((workspace || {}).symbols || []).find((item) => normalizeSymbol(item.symbol) === target)
  },

  applyStructureState(item) {
    const status = item.context_status || {}
    const reminders = item.reminders || { count: 0, items: [] }
    const outcomes = item.outcomes || { count: 0, items: [], memory: {} }
    const memory = outcomes.memory || {}
    const stats = memory.stats || {}
    const mistakeCount = Number(
      stats.mistake_count_30d
        || stats.ignored_invalidation_count_30d
        || stats.mistake_outcomes
        || 0,
    )

    this.setData({
      name: item.name || item.symbol,
      structureState: item,
      statusLabel: statusLabel(status),
      statusTone: statusTone(status),
      reminderCount: Number(reminders.count || 0),
      activeReminderCount: (reminders.items || []).filter((row) => row.status === 'ACTIVE').length,
      outcomeCount: Number(outcomes.count || 0),
      mistakeCount,
      memoryNote: mistakeCount > 0
        ? `已有 ${mistakeCount} 条错误复盘，问 AI 时会优先提醒纪律偏差`
        : '暂无错误复盘记忆',
      loading: false,
      refreshing: false,
      error: '',
    })
  },
})

function normalizeSymbol(symbol) {
  const raw = String(symbol || '').trim().replace('.', '').toLowerCase()
  if (/^(sh|sz)\d{6}$/.test(raw)) return raw
  if (/^\d{6}$/.test(raw)) return `${/^[659]/.test(raw) ? 'sh' : 'sz'}${raw}`
  return raw
}

function statusLabel(status) {
  const value = status.status || ''
  if (value === 'fresh') return '结构就绪'
  if (value === 'stale') return '待复核'
  if (value === 'pending') return '后台生成中'
  if (value === 'failed') return '生成失败'
  if (value === 'no_snapshot') return '等待快照'
  return '检测中'
}

function statusTone(status) {
  const value = status.status || ''
  if (value === 'fresh') return 'ready'
  if (value === 'stale') return 'warn'
  if (value === 'failed') return 'error'
  if (value === 'pending') return 'working'
  return 'waiting'
}
