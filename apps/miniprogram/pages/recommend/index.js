const { post } = require('../../utils/request')
const { downloadImage } = require('../../utils/image')

const DECISION = {
  accept: '✅ 采纳',
  recompose: '🔄 重新组合',
  retrieve_more: '🔍 扩展检索',
  wardrobe_gap: '🧥 衣橱缺口',
}

Page({
  data: {
    request: '明天参加互联网公司的面试，希望正式但不要太老气，不穿红色。',
    loading: false,
    payload: null,
    decisionLabel: '',
  },

  onInput(e) {
    this.setData({ request: e.detail.value })
  },

  async onRecommend() {
    const app = getApp()
    if (!this.data.request.trim()) {
      wx.showToast({ title: '请输入需求', icon: 'none' })
      return
    }
    this.setData({ loading: true, payload: null })
    try {
      const res = await post('/recommendations', {
        user_id: app.globalData.userId,
        request: this.data.request,
        max_results: 3,
      })
      // WXML 不支持函数调用，预格式化展示字段
      if (res.request_signature) {
        const rs = res.request_signature
        res.request_signature.unique_mood_text = (rs.unique_mood || []).join('、')
        res.request_signature.practical_context_text = (rs.practical_context || []).join('、')
      }
      const recs = (res.structured_result && res.structured_result.recommendations) || []
      for (const rec of recs) {
        rec.score_display = Number(rec.score || 0).toFixed(1)
        // Download each item image to a local temp path; <image> on real
        // devices rejects http URLs.
        rec.imgs = rec.item_ids.map((itemId) => ({
          itemId,
          url: `${app.globalData.baseUrl}/items/${itemId}/image`,
          local: '',
        }))
      }
      res.baseUrl = app.globalData.baseUrl
      res.decisionLabel = DECISION[res.decision] || res.decision || '—'
      this.setData({ payload: res, decisionLabel: res.decisionLabel })
      this.downloadImages(recs)
    } catch (err) {
      wx.showToast({ title: String(err), icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },

  async downloadImages(recs) {
    for (let i = 0; i < recs.length; i += 1) {
      for (let j = 0; j < recs[i].imgs.length; j += 1) {
        const img = recs[i].imgs[j]
        if (img.local) continue
        const local = await downloadImage(img.url)
        if (local) {
          this.setData({
            [`payload.structured_result.recommendations[${i}].imgs[${j}].local`]: local,
          })
        }
      }
    }
  },
})
