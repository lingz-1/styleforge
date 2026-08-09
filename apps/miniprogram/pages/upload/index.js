const { post } = require('../../utils/request')

const TYPES = [
  'top', 'pants', 'skirt', 'dress', 'jumpsuit', 'outwear',
  'shoes', 'bag', 'accessory',
]
const TYPE_LABELS = [
  '上装', '裤装', '半身裙', '连衣裙', '连体装', '外套',
  '鞋', '包', '配饰',
]

Page({
  data: {
    typeIndex: 0,
    types: TYPE_LABELS,
    name: '',
    color: '',
    genderIndex: 0,
    genders: ['women', 'men'],
    imagePath: '',
    submitting: false,
  },

  onChooseImage() {
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sourceType: ['album', 'camera'],
      success: (res) => {
        this.setData({ imagePath: res.tempFiles[0].tempFilePath })
      },
    })
  },

  onTypeChange(e) { this.setData({ typeIndex: Number(e.detail.value) }) },
  onGenderChange(e) { this.setData({ genderIndex: Number(e.detail.value) }) },
  onName(e) { this.setData({ name: e.detail.value }) },
  onColor(e) { this.setData({ color: e.detail.value }) },

  async onSubmit() {
    const { imagePath, typeIndex, types, name, color, genderIndex, genders } = this.data
    if (!imagePath) {
      wx.showToast({ title: '请选择图片', icon: 'none' })
      return
    }
    this.setData({ submitting: true })
    try {
      const base64 = await this.readFileBase64(imagePath)
      const app = getApp()
      const res = await post(`/wardrobes/${app.globalData.userId}/items/photo`, {
        filename: 'photo.jpg',
        content_base64: base64,
        item_type: types[typeIndex],
        name,
        color,
        gender: genders[genderIndex],
      })
      wx.showToast({ title: '已加入衣柜', icon: 'success' })
      setTimeout(() => wx.navigateBack(), 800)
    } catch (err) {
      wx.showToast({ title: String(err), icon: 'none' })
    } finally {
      this.setData({ submitting: false })
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
})
