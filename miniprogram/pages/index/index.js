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
  },

  subscribeAlerts() {
    // 这里替换为您真实的微信小程序订阅消息模板 ID
    const tmplId = "TMPL_STOP_LOSS_123";
    
    wx.requestSubscribeMessage({
      tmplIds: [tmplId],
      success(res) {
        if (res[tmplId] === 'accept') {
          wx.showToast({
            title: '护盘预警已开启',
            icon: 'success'
          })
          // TODO: 将同意状态上报给后端 api/alerts/subscribe
        } else {
          wx.showToast({
            title: '授权已取消',
            icon: 'none'
          })
        }
      },
      fail(err) {
        console.error('订阅消息失败', err)
      }
    })
  }
})
