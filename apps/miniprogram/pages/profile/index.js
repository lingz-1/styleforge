const { get, put } = require('../../utils/request')

const DIMS = [
  { key: 'request_relevance', label: '需求还原', value: 25 },
  { key: 'request_specificity', label: '请求特异', value: 25 },
  { key: 'outfit_coordination', label: '搭配协调', value: 20 },
  { key: 'wearability', label: '实穿', value: 15 },
  { key: 'freshness', label: '新鲜感', value: 15 },
]

Page({
  data: {
    userId: 'demo-user',
    dims: DIMS,
    saving: false,
  },

  onShow() {
    const app = getApp()
    this.setData({ userId: app.globalData.userId })
    this.loadWeights()
  },

  onUserIdInput(e) {
    this.setData({ userId: e.detail.value })
  },

  onSaveUser() {
    const app = getApp()
    app.setUserId(this.data.userId)
    wx.showToast({ title: '已切换用户', icon: 'success' })
  },

  async loadWeights() {
    try {
      const app = getApp()
      const res = await get(`/preferences/${app.globalData.userId}/evaluation`)
      const dims = this.data.dims.map((dim) => ({
        ...dim,
        value: Math.round((res.weights[dim.key] || 0) * 100),
      }))
      this.setData({ dims })
    } catch (e) {
      /* 保留默认 */
    }
  },

  onSlider(e) {
    const { index } = e.currentTarget.dataset
    const key = `dims[${index}].value`
    this.setData({ [key]: e.detail.value })
  },

  async onSave() {
    const app = getApp()
    this.setData({ saving: true })
    try {
      const weights = {}
      for (const dim of this.data.dims) weights[dim.key] = dim.value / 100
      await put(`/preferences/${app.globalData.userId}/evaluation`, { weights })
      wx.showToast({ title: '已保存', icon: 'success' })
    } catch (err) {
      wx.showToast({ title: String(err), icon: 'none' })
    } finally {
      this.setData({ saving: false })
    }
  },
})
