App({
  globalData: {
    // 开发阶段：真机预览改为电脑当前局域网 IP（如 http://192.168.x.x:8000），
    // 并在微信开发者工具勾选「不校验合法域名」。
    baseUrl: 'http://10.63.28.38:8000',
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
