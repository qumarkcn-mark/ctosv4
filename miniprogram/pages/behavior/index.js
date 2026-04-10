const app = getApp();

Page({
  data: {
    loading: true,
    score: 0,
    metrics: {},
    diagnosis: [],
    radarPoints: ''
  },

  onLoad() {
    this.fetchReport();
  },

  fetchReport() {
    this.setData({ loading: true });
    wx.request({
      url: `${app.globalData.apiBase}/behavior/report`,
      header: { 'Authorization': `Bearer ${app.globalData.token}` },
      success: (res) => {
        if (res.statusCode === 200 && res.data.status === 'success') {
          const d = res.data.data;
          this.setData({
            score: d.discipline_score,
            metrics: d.metrics,
            diagnosis: d.diagnosis,
            radarPoints: this.computeRadar(d.metrics),
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

  computeRadar(m) {
    // 六维归一化到 0-100，映射到六边形 SVG 坐标
    const dims = [
      Math.min(m.win_rate || 0, 100),
      Math.min((m.profit_loss_ratio || 0) * 33, 100),
      Math.min(m.stop_loss_execution_rate || 0, 100),
      Math.min(100 - (m.counter_trend_rate || 0), 100),
      Math.min(100 - (m.impulse_trade_rate || 0), 100),
      Math.min(Math.min((m.avg_hold_days || 0) * 5, 100), 100),
    ];
    // 六边形中心 (50, 50), 半径 40
    const cx = 50, cy = 50, r = 40;
    const angles = dims.map((_, i) => (Math.PI * 2 * i) / 6 - Math.PI / 2);
    const points = dims.map((v, i) => {
      const ratio = v / 100;
      const x = cx + r * ratio * Math.cos(angles[i]);
      const y = cy + r * ratio * Math.sin(angles[i]);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return points.join(' ');
  }
});
