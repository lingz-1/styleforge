App({
  globalData: {
    // 开发阶段：真机预览改为电脑当前局域网 IP（如 http://192.168.x.x:8000），
    // 并在微信开发者工具勾选「不校验合法域名」。
    // 手机开热点、电脑连热点时，此处填电脑在热点网段拿到的 IP（ipconfig 查看，
    // 本例手机热点分配为 10.158.100.26）；同一 WiFi 局域网时改为该网段 IP（如 10.63.28.38）。
    baseUrl: 'http://10.158.100.26:8000',
    userId: 'demo-user',
  },

  onLaunch() {
    // 记住当前用户，跨会话保持登录状态
    const saved = wx.getStorageSync('sf_user_id')
    if (saved) {
      this.globalData.userId = saved
    }
  },

  setUserId(id) {
    const value = (id || '').trim()
    if (!value) return
    this.globalData.userId = value
    wx.setStorageSync('sf_user_id', value)
  },
})
