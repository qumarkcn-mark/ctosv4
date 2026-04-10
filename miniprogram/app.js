App({
  globalData: {
    token: null,
    userInfo: null,
    // 开发时填本地内网 IP，部署时改域名 (如果要在真机验证，需用机器局域网 IP)
    apiBase: 'http://127.0.0.1:8001/api'
  },
  
  onLaunch() {
    this.checkLogin();
  },

  checkLogin() {
    const token = wx.getStorageSync('token');
    if (token) {
      this.globalData.token = token;
      return;
    }
    // 执行静默登录
    this.login();
  },

  login() {
    wx.login({
      success: (res) => {
        if (res.code) {
          // 请求后端换取 openid 和业务 token
          wx.request({
            url: `${this.globalData.apiBase}/auth/wechat-login`,
            method: 'POST',
            data: { code: res.code },
            success: (resp) => {
              if (resp.data.token) {
                this.globalData.token = resp.data.token;
                this.globalData.userInfo = resp.data;
                wx.setStorageSync('token', resp.data.token);
                // 触发登录就绪事件 (给页面用)
                if (this.loginReadyCallback) {
                  this.loginReadyCallback();
                }
              }
            }
          })
        }
      }
    })
  }
})
