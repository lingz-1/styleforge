const { get, post, del } = require('../../utils/request')

const TYPE_LABELS = {
  top: '上装', bottom: '下装', one_piece: '连体装', outerwear: '外套',
  footwear: '鞋', bag: '包', accessory: '配饰', other: '其他',
}

Page({
  data: {
    groups: [],
    loading: false,
  },

  onShow() {
    this.loadWardrobe()
  },

  onUpload() {
    wx.navigateTo({ url: '/pages/upload/index' })
  },

  onImport() {
    wx.navigateTo({ url: '/pages/import/index' })
  },

  // 点击无图卡片直接进入补图
  onTapCard(e) {
    const { item } = e.currentTarget.dataset
    if (item && item.image_status !== 'available') {
      this.pickAndUploadImage(item)
    }
  },

  onLongPress(e) {
    const { item } = e.currentTarget.dataset
    wx.showActionSheet({
      itemList: ['补实拍图', '移出衣柜'],
      success: (res) => {
        if (res.tapIndex === 0) this.pickAndUploadImage(item)
        else if (res.tapIndex === 1) this.removeItem(item)
      },
    })
  },

  pickAndUploadImage(item) {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => this.uploadImage(item, res.tempFiles[0].tempFilePath),
    })
  },

  async uploadImage(item, path) {
    wx.showLoading({ title: '上传中' })
    try {
      const app = getApp()
      const base64 = await this.readFileBase64(path)
      await post(
        `/wardrobes/${app.globalData.userId}/items/${item.item_id}/image`,
        { filename: 'photo.jpg', content_base64: base64 }
      )
      wx.hideLoading()
      wx.showToast({ title: '已上传', icon: 'success' })
      this.loadWardrobe()
    } catch (err) {
      wx.hideLoading()
      wx.showToast({ title: String(err), icon: 'none' })
    }
  },

  async removeItem(item) {
    try {
      const app = getApp()
      await del(`/wardrobes/${app.globalData.userId}/items/${item.item_id}`)
      wx.showToast({ title: '已移出', icon: 'success' })
      this.loadWardrobe()
    } catch (err) {
      wx.showToast({ title: String(err), icon: 'none' })
    }
  },

  readFileBase64(path) {
    return new Promise((resolve, reject) => {
      const fs = wx.getFileSystemManager()
      fs.readFile({
        filePath: path,
        encoding: 'base64',
        success: (res) => resolve(res.data),
        fail: reject,
      })
    })
  },

  async loadWardrobe() {
    this.setData({ loading: true })
    try {
      const app = getApp()
      const res = await get(`/wardrobes/${app.globalData.userId}`)
      const map = {}
      for (const item of res.items) {
        const type = item.item_type || 'other'
        if (!map[type]) map[type] = { label: TYPE_LABELS[type] || type, items: [] }
        map[type].items.push({
          item_id: item.item_id,
          name: item.name || item.item_id,
          color: item.color,
          image_status: item.image_status,
          image_url:
            item.image_status === 'available'
              ? `${app.globalData.baseUrl}${item.image_url}`
              : '',
        })
      }
      const groups = Object.keys(map).map((type) => map[type])
      this.setData({ groups })
    } catch (err) {
      wx.showToast({ title: String(err), icon: 'none' })
    } finally {
      this.setData({ loading: false })
    }
  },
})
