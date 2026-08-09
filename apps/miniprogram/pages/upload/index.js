const { get, post } = require('../../utils/request')

Page({
  data: {
    // Main category (required) loaded from /catalog/taxonomy.
    typeIndex: 0,
    types: [],
    typeKeys: [],
    // Optional subtype; index 0 always means "不选择" (empty key).
    subtypeIndex: 0,
    subtypes: [],
    subtypeKeys: [],
    name: '',
    color: '',
    genderIndex: 0,
    genders: ['women', 'men'],
    imagePath: '',
    submitting: false,
  },

  onLoad() {
    this.loadTaxonomy()
  },

  async loadTaxonomy() {
    try {
      const res = await get('/catalog/taxonomy')
      const categories = (res && res.categories) || []
      this._taxonomy = categories
      this.setData({
        types: categories.map((c) => c.zh),
        typeKeys: categories.map((c) => c.key),
      })
      this.rebuildSubtypes()
    } catch (err) {
      // Fall back to a minimal list if the endpoint is unreachable.
      this._taxonomy = []
      this.setData({
        types: ['上装', '裤装', '半身裙', '连衣裙', '连体装', '外套', '鞋', '包', '配饰'],
        typeKeys: ['top', 'pants', 'skirt', 'dress', 'jumpsuit', 'outwear', 'shoes', 'bag', 'accessory'],
      })
      this.rebuildSubtypes()
    }
  },

  // Subtypes for the currently selected main category; first option is 不选择.
  rebuildSubtypes() {
    const { typeIndex, typeKeys } = this.data
    const categories = this._taxonomy || []
    const cat = categories.find((c) => c.key === typeKeys[typeIndex]) || { subtypes: [] }
    const subtypes = ['不选择'].concat(cat.subtypes.map((s) => s.zh))
    const subtypeKeys = [''].concat(cat.subtypes.map((s) => s.key))
    this.setData({ subtypes, subtypeKeys, subtypeIndex: 0 })
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

  onTypeChange(e) {
    this.setData({ typeIndex: Number(e.detail.value) })
    this.rebuildSubtypes()
  },
  onSubtypeChange(e) { this.setData({ subtypeIndex: Number(e.detail.value) }) },
  onGenderChange(e) { this.setData({ genderIndex: Number(e.detail.value) }) },
  onName(e) { this.setData({ name: e.detail.value }) },
  onColor(e) { this.setData({ color: e.detail.value }) },

  async onSubmit() {
    const {
      imagePath, typeIndex, typeKeys, subtypeIndex, subtypeKeys,
      name, color, genderIndex, genders,
    } = this.data
    if (!imagePath) {
      wx.showToast({ title: '请选择图片', icon: 'none' })
      return
    }
    if (!typeKeys[typeIndex]) {
      wx.showToast({ title: '请选择品类', icon: 'none' })
      return
    }
    this.setData({ submitting: true })
    try {
      const base64 = await this.readFileBase64(imagePath)
      const app = getApp()
      const res = await post(`/wardrobes/${app.globalData.userId}/items/photo`, {
        filename: 'photo.jpg',
        content_base64: base64,
        item_type: typeKeys[typeIndex],
        subtype: subtypeKeys[subtypeIndex] || '',
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
