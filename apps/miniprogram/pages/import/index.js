const { post } = require('../../utils/request')

Page({
  data: {
    statistics: null,
    rows: [],
    checked: {},
    batchId: '',
    previewing: false,
    committing: false,
    genderOverride: '',
    selectedCount: 0,
  },

  onChooseFile() {
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['xlsx'],
      success: (res) => this.previewFile(res.tempFiles[0]),
      fail: () => {},
    })
  },

  async previewFile(file) {
    this.setData({ previewing: true })
    try {
      const app = getApp()
      const base64 = await this.readFileBase64(file.path)
      const res = await post(`/wardrobes/${app.globalData.userId}/imports`, {
        filename: file.name,
        content_base64: base64,
        default_audience: '',
      })
      const rows = res.rows.filter(
        (r) => r.decision === 'candidate' || r.decision === 'committed'
      )
      this.setData({
        statistics: res.batch.statistics,
        rows,
        batchId: res.batch.batch_id,
        checked: {},
        selectedCount: 0,
      })
    } catch (err) {
      wx.showToast({ title: String(err), icon: 'none' })
    } finally {
      this.setData({ previewing: false })
    }
  },

  onToggle(e) {
    const rowId = e.currentTarget.dataset.rowId
    this.setData({ [`checked.${rowId}`]: !this.data.checked[rowId] })
    this.refreshCount()
  },

  refreshCount() {
    const { rows, checked } = this.data
    const count = rows.filter((r) => checked[r.row_id] && r.predicted_item_type).length
    this.setData({ selectedCount: count })
  },

  onGender(e) { this.setData({ genderOverride: e.detail.value }) },

  async onSubmit() {
    const { rows, checked, batchId, genderOverride } = this.data
    const selected = rows.filter((r) => checked[r.row_id])
    const keep = selected.filter((r) => r.predicted_item_type)
    if (!keep.length) {
      wx.showToast({ title: '请勾选已识别品类的记录', icon: 'none' })
      return
    }
    this.setData({ committing: true })
    try {
      const app = getApp()
      const selections = keep.map((r) => ({
        row_id: r.row_id,
        item_type: r.predicted_item_type,
        subtype: r.predicted_subtype || '',
        color: r.predicted_color || '',
        size: r.predicted_size || '',
        audience: r.predicted_audience || genderOverride || 'women',
      }))
      const res = await post(
        `/wardrobes/${app.globalData.userId}/imports/${batchId}/commit`,
        { selections, auto_embed: true }
      )
      wx.showToast({ title: `已加入 ${res.committed_item_count} 件`, icon: 'success' })
      this.setData({ rows: [], statistics: null, batchId: '' })
    } catch (err) {
      wx.showToast({ title: String(err), icon: 'none' })
    } finally {
      this.setData({ committing: false })
    }
  },

  readFileBase64(path) {
    return new Promise((resolve, reject) => {
      const fs = wx.getFileSystemManager()
      fs.readFile({
        filePath: path,
        encoding: 'base64',
        success: (r) => resolve(r.data),
        fail: reject,
      })
    })
  },
})
