const app = getApp()

Page({
  data: {
    overview: null,
    positions: []
  },

  onLoad() {
    if (app.globalData.token) {
      this.fetchData()
    } else {
      app.loginReadyCallback = () => {
        this.fetchData()
      }
    }
  },

  onPullDownRefresh() {
    this.fetchData()
  },

  fetchData() {
    wx.request({
      url: `${app.globalData.apiBase}/positions/overview`,
      // 在标准的 JWT 承载上加上 Bearer（此项目暂无严格拦截，但预留给扩展层）
      header: {
        'Authorization': `Bearer ${app.globalData.token}`
      },
      success: (res) => {
        if (res.data && res.data.positions) {
          const positions = res.data.positions.map(p => ({
            ...p,
            weight_pct: p.weight !== undefined ? (p.weight * 100).toFixed(1) : 0
          }))
          this.setData({
            overview: res.data,
            positions
          })
        }
        wx.stopPullDownRefresh()
      },
      fail: (err) => {
        console.error("Fetch Data failed: ", err)
        wx.stopPullDownRefresh()
      }
    })
  }
})
