const { post } = require('../../utils/request')
const { downloadImage } = require('../../utils/image')

const TASK_LABELS = {
  outfit_recommend: '穿搭推荐',
  outfit_modify: '局部修改',
  style_advice: '风格知识',
  item_advice: '单品搭配',
  wardrobe_compatibility: '衣橱兼容性',
  wardrobe_gap: '衣橱缺口',
}

const SLOT_LABELS = {
  top: '上衣', bottom: '下装', footwear: '鞋履', outerwear: '外套',
  one_piece: '连衣裙', bag: '包袋', accessory: '配饰',
}

Page({
  data: {
    request: '黑色马甲怎么搭？',
    examples: ['黑色马甲怎么搭？', '要搭配中世纪风格，我的衣柜还缺什么？', '鞋太正式，只换一双。'],
    loading: false,
    payload: null,
    result: null,
    outfits: [],
    itemGroups: [],
    agentSteps: [],
    traceText: '',
  },

  onInput(e) {
    this.setData({ request: e.detail.value })
  },

  onExample(e) {
    this.setData({ request: e.currentTarget.dataset.value })
  },

  async onRecommend() {
    const app = getApp()
    const request = this.data.request.trim()
    if (!request) {
      wx.showToast({ title: '请输入需求', icon: 'none' })
      return
    }
    this.setData({ loading: true, payload: null, result: null, outfits: [], itemGroups: [] })
    try {
      const payload = await post('/tasks/execute', {
        user_id: app.globalData.userId,
        request,
        max_results: 3,
      })
      const prepared = this.prepare(payload, app.globalData.baseUrl)
      this.setData(prepared)
      await this.downloadAllImages(prepared.outfits, prepared.itemGroups)
    } catch (err) {
      wx.showToast({ title: String(err), icon: 'none', duration: 3000 })
    } finally {
      this.setData({ loading: false })
    }
  },

  prepare(payload, baseUrl) {
    const result = payload.result || {}
    const taskType = payload.task_type
    let sourceOutfits = []
    if (taskType === 'outfit_recommend') {
      sourceOutfits = (result.structured_result && result.structured_result.recommendations) || []
    } else if (taskType === 'outfit_modify') {
      sourceOutfits = result.alternatives || []
    } else if (taskType === 'item_advice' || taskType === 'wardrobe_compatibility') {
      sourceOutfits = result.sample_outfits || []
    }
    const outfits = sourceOutfits.map((outfit, index) => {
      const ids = outfit.item_ids || outfit.wardrobe_item_ids || (outfit.items || []).map((item) => item.item_id)
      return {
        ...outfit,
        label: `方案 ${index + 1}`,
        score_display: typeof outfit.score === 'number' ? outfit.score.toFixed(1) : '',
        images: ids.map((itemId) => ({
          itemId,
          url: `${baseUrl}/items/${itemId}/image`,
          local: '',
        })),
      }
    })
    const grouped = result.compatible_items_by_slot || result.wardrobe_matches_by_slot || {}
    const itemGroups = Object.keys(grouped).map((slot) => ({
      slot,
      label: SLOT_LABELS[slot] || slot,
      items: grouped[slot].map((item) => ({
        ...item,
        url: `${baseUrl}/items/${item.item_id}/image`,
        local: '',
      })),
    }))
    if (!itemGroups.length && result.wardrobe_matches) {
      itemGroups.push({
        slot: 'matches',
        label: '衣橱内可落实',
        items: result.wardrobe_matches.map((item) => ({
          ...item,
          url: `${baseUrl}/items/${item.item_id}/image`,
          local: '',
        })),
      })
    }
    const traceNodes = new Set((payload.trace || []).map((item) => item.node))
    const agentSteps = [
      { name: 'Agent 1 · 语义检索', done: traceNodes.has('semantic_retriever_agent') },
      { name: 'Agent 2 · 方案生成', done: traceNodes.has('composer_agent') },
      { name: 'Agent 3 · 审校决策', done: traceNodes.has('critic_agent') },
    ]
    payload.task_label = TASK_LABELS[taskType] || taskType
    payload.status_label = { completed: '已完成', infeasible: '无可行方案', needs_clarification: '待补充' }[payload.status] || payload.status
    return {
      payload,
      result,
      outfits,
      itemGroups,
      agentSteps,
      traceText: JSON.stringify({ context_pack: payload.context_pack, agent_outputs: payload.agent_outputs, trace: payload.trace }, null, 2),
    }
  },

  async downloadAllImages(outfits, itemGroups) {
    for (let i = 0; i < outfits.length; i += 1) {
      for (let j = 0; j < outfits[i].images.length; j += 1) {
        const local = await downloadImage(outfits[i].images[j].url)
        if (local) this.setData({ [`outfits[${i}].images[${j}].local`]: local })
      }
    }
    for (let i = 0; i < itemGroups.length; i += 1) {
      for (let j = 0; j < itemGroups[i].items.length; j += 1) {
        const local = await downloadImage(itemGroups[i].items[j].url)
        if (local) this.setData({ [`itemGroups[${i}].items[${j}].local`]: local })
      }
    }
  },
})
