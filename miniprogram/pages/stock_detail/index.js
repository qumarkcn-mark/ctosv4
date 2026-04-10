Page({
  data: {
    symbol: '',
    matrixA: [],
    matrixB: [],
    matrixMode: 'A', // A: 30/5, B: 60/15
    currentMatrix: [],
    loading: true,
    adviceText: '等待引擎推算...'
  },

  onLoad(options) {
    if (options.symbol) {
      this.setData({ symbol: options.symbol });
      this.fetchMatrix(options.symbol);
    }
  },

  fetchMatrix(symbol) {
    const app = getApp();
    this.setData({ loading: true });
    
    wx.request({
      url: `${app.globalData.apiBase}/chan/matrix/${symbol}`,
      method: 'GET',
      header: {
        'Authorization': `Bearer ${app.globalData.token}`
      },
      success: (res) => {
        if (res.statusCode === 200 && res.data.status === 'success') {
          const data = res.data.data;
          const current = this.data.matrixMode === 'A' ? data.matrix_a : data.matrix_b;
          this.setData({
            matrixA: data.matrix_a,
            matrixB: data.matrix_b,
            currentMatrix: current,
            adviceText: this.computeAdvice(current),
            loading: false
          });
        }
      },
      fail: () => {
        wx.showToast({ title: '网络异常', icon: 'error' });
        this.setData({ loading: false });
      }
    });
  },

  switchMode(e) {
    const mode = e.currentTarget.dataset.mode;
    const current = mode === 'A' ? this.data.matrixA : this.data.matrixB;
    this.setData({
      matrixMode: mode,
      currentMatrix: current,
      adviceText: this.computeAdvice(current)
    });
  },

  computeAdvice(matrix) {
    if (!matrix || matrix.length < 3) return "数据不足";
    const [l1, l2] = matrix; 
    
    if (l1.state === 'DOWNWARD_LEAVING' && l2.state !== 'THIRD_BUY_CONFIRMED') {
      return "⚠️ 主级别向下破位中，极度弱势，切勿盲目接飞刀！";
    }
    if (l1.state === 'WAITING_FOR_PULLBACK' && l2.state === 'THIRD_BUY_CONFIRMED') {
      return "🔥 【核弹级信号】主级别完成离开段寻找支撑，次级别已率先确立三买，准备起飞！";
    }
    if (l1.state === 'THIRD_BUY_CONFIRMED') {
      return "🚀 大级别主升浪三买已确立，持单飞翔。";
    }
    if (l1.state === 'IN_CENTER_OSC') {
      return "⚖️ 维持中枢内部震荡中，可贴近 ZD 低吸，靠近 ZG 高抛。";
    }
    return "保持观望，等待结构明朗。";
  }
});
