<template>
  <main class="studio">
    <section class="hero">
      <div>
        <p class="eyebrow">STYLEFORGE · WARDROBE STUDIO</p>
        <h1>把需求交给三位造型 Agent</h1>
        <p class="lead">说出你想穿什么、想改哪里，或想了解衣橱还缺什么。系统会自动判断任务。</p>
      </div>
      <div class="user-box">
        <span>当前衣橱</span>
        <el-input v-model="userId" aria-label="用户 ID" @change="onUserIdChange" />
      </div>
    </section>

    <section class="prompt-card">
      <el-input
        v-model="request"
        type="textarea"
        :rows="4"
        resize="none"
        placeholder="例如：黑色马甲怎么搭？ / 要搭配中世纪风格，我的衣柜还缺什么？"
        @keydown.ctrl.enter="run"
        @keydown.meta.enter="run"
      />
      <div class="prompt-footer">
        <div class="examples" aria-label="示例问题">
          <button v-for="example in EXAMPLES" :key="example" type="button" @click="request = example">
            {{ example }}
          </button>
        </div>
        <el-button type="primary" size="large" :loading="loading" @click="run">
          让三位 Agent 处理
        </el-button>
      </div>
      <p class="shortcut">Ctrl / ⌘ + Enter 提交</p>
    </section>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />

    <template v-if="payload">
      <section class="result-head block">
        <div>
          <span class="task-chip">{{ taskLabel }}</span>
          <h2>{{ resultTitle }}</h2>
          <p>运行 {{ payload.run_id }} · {{ statusLabel }}</p>
        </div>
        <div class="metric"><strong>{{ payload.llm_call_count || 0 }}</strong><span>次模型调用</span></div>
      </section>

      <section class="agent-rail block" aria-label="三 Agent 执行轨迹">
        <div v-for="(agent, index) in agentRail" :key="agent.node" class="agent-step">
          <span class="step-index">0{{ index + 1 }}</span>
          <div><strong>{{ agent.name }}</strong><small>{{ agent.detail }}</small></div>
          <span class="step-status">{{ agent.done ? '完成' : '未执行' }}</span>
        </div>
      </section>

      <el-alert
        v-if="result.status === 'needs_clarification' || result.clarification_question"
        :title="result.clarification_question || '需要补充信息后才能继续。'"
        type="warning"
        show-icon
        :closable="false"
        class="block"
      />

      <section v-if="isRecommend" class="block">
        <div class="section-heading"><h3>衣橱搭配方案</h3><span>{{ recommendOutfits.length }} 套</span></div>
        <div class="outfit-grid">
          <article v-for="(outfit, index) in recommendOutfits" :key="outfit.outfit_id" class="outfit-card">
            <header><span>LOOK {{ String(index + 1).padStart(2, '0') }}</span><strong>{{ fmtScore(outfit.score) }}</strong></header>
            <div class="image-grid">
              <el-image v-for="itemId in outfit.item_ids" :key="itemId" :src="itemImage(itemId)" fit="cover">
                <template #error><div class="image-empty">无图</div></template>
              </el-image>
            </div>
            <p v-for="reason in outfit.reasons || []" :key="reason">{{ reason }}</p>
          </article>
        </div>
      </section>

      <section v-else-if="payload.task_type === 'outfit_modify'" class="block">
        <div class="section-heading"><h3>{{ result.message || '局部修改方案' }}</h3><span>仅修改 {{ result.target_slot }}</span></div>
        <p class="muted">锁定：{{ (result.locked_item_ids || []).join('、') || '无' }}</p>
        <div class="outfit-grid">
          <article v-for="(outfit, index) in result.alternatives || []" :key="outfit.outfit_id || index" class="outfit-card">
            <header><span>替换方案 {{ index + 1 }}</span><strong>{{ fmtScore(outfit.score) }}</strong></header>
            <div class="image-grid">
              <el-image v-for="itemId in outfitIds(outfit)" :key="itemId" :src="itemImage(itemId)" fit="cover" />
            </div>
            <p>{{ outfit.reasoning || outfit.reason || '' }}</p>
          </article>
        </div>
      </section>

      <section v-else-if="payload.task_type === 'style_advice'" class="knowledge-layout block">
        <article class="narrative">
          <div class="section-heading"><h3>{{ result.title }}</h3></div>
          <p class="summary">{{ result.summary }}</p>
          <div v-for="(principle, index) in result.principles || []" :key="index" class="principle">
            <strong>{{ principle.title || principle.section || `原则 ${index + 1}` }}</strong>
            <p>{{ principle.content || principle.description }}</p>
          </div>
        </article>
        <article><div class="section-heading"><h3>衣橱内可落实</h3><span>{{ (result.wardrobe_matches || []).length }} 件</span></div><ItemGrid :items="result.wardrobe_matches || []" /></article>
      </section>

      <section v-else-if="payload.task_type === 'item_advice'" class="block">
        <div class="section-heading"><h3>{{ result.title }}</h3><span>{{ result.anchor_source === 'wardrobe' ? '衣橱锚点' : '候选单品' }}</span></div>
        <p class="summary">{{ result.summary }}</p>
        <div v-if="result.anchor_item" class="anchor"><el-image :src="itemImage(result.anchor_item.item_id)" fit="cover" /><div><small>搭配锚点</small><strong>{{ result.anchor_item.name }}</strong><span>{{ result.anchor_item.color }} · {{ result.anchor_item.item_type }}</span></div></div>
        <div v-for="(items, slot) in result.compatible_items_by_slot || {}" :key="slot" class="slot-group">
          <div class="section-heading"><h3>{{ slotLabel(slot) }}</h3><span>{{ items.length }} 件可选</span></div>
          <ItemGrid :items="items" />
        </div>
        <div v-if="result.sample_outfits?.length" class="outfit-grid">
          <article v-for="(outfit, index) in result.sample_outfits" :key="outfit.outfit_id || index" class="outfit-card">
            <header><span>示例搭配 {{ index + 1 }}</span><strong>{{ fmtScore(outfit.score) }}</strong></header>
            <div class="image-grid"><el-image v-for="itemId in outfitIds(outfit)" :key="itemId" :src="itemImage(itemId)" fit="cover" /></div>
            <p>{{ outfit.reasoning || '' }}</p>
          </article>
        </div>
      </section>

      <section v-else-if="payload.task_type === 'wardrobe_compatibility'" class="block">
        <div class="scoreboard">
          <div><strong>{{ fmtScore(result.compatibility_score) }}</strong><span>兼容分</span></div>
          <div><strong>{{ result.complete_outfit_count || 0 }}</strong><span>完整搭配</span></div>
          <div><strong>{{ fmtScore(result.redundancy_score) }}</strong><span>重复度</span></div>
          <div><strong>{{ recommendationLabel(result.recommendation) }}</strong><span>Agent 结论</span></div>
        </div>
        <p class="summary">{{ result.recommendation_text }}</p>
        <div v-for="(items, slot) in result.compatible_items_by_slot || {}" :key="slot" class="slot-group">
          <div class="section-heading"><h3>{{ slotLabel(slot) }}</h3><span>{{ items.length }} 件兼容</span></div>
          <ItemGrid :items="items" />
        </div>
      </section>

      <section v-else-if="payload.task_type === 'wardrobe_gap'" class="block">
        <div class="section-heading"><h3>{{ result.analysis_mode === 'targeted' ? `${result.target?.style || '目标风格'}衣橱缺口` : '整体衣橱缺口' }}</h3><span>{{ result.gap_count || 0 }} 项</span></div>
        <p class="summary">{{ result.summary }}</p>
        <div class="gap-grid">
          <article v-for="(gap, index) in result.gaps || []" :key="gap.id || index" class="gap-card">
            <span>{{ gap.priority || 'medium' }}</span><h4>{{ gap.label || gap.slot || gap.gap_type }}</h4><p>{{ gap.suggestion }}</p>
          </article>
        </div>
        <div v-if="result.covered_elements?.length" class="covered"><strong>已覆盖元素</strong><span v-for="item in result.covered_elements" :key="item.id">{{ item.label }}</span></div>
      </section>

      <el-collapse class="block technical">
        <el-collapse-item title="Context Pack 与 Agent 输出" name="trace">
          <pre>{{ JSON.stringify({ context_pack: payload.context_pack, agent_outputs: payload.agent_outputs, trace: payload.trace }, null, 2) }}</pre>
        </el-collapse-item>
      </el-collapse>
    </template>
  </main>
</template>

<script setup>
import { computed, defineComponent, h, ref } from 'vue'
import { ElImage } from 'element-plus'
import { storeToRefs } from 'pinia'
import { imageUrl } from '../services/api'
import { useRecommendationStore } from '../stores/recommendation'
import { getUserId, setUserId } from '../services/user'

const EXAMPLES = ['黑色马甲怎么搭？', '要搭配中世纪风格，我的衣柜还缺什么？', '鞋太正式，只换一双，其他保持不变。']
const TASK_LABELS = { outfit_recommend: '穿搭推荐', outfit_modify: '局部修改', style_advice: '风格知识', item_advice: '单品搭配', wardrobe_compatibility: '衣橱兼容性', wardrobe_gap: '衣橱缺口' }
const SLOT_LABELS = { top: '上衣', bottom: '下装', footwear: '鞋履', outerwear: '外套', one_piece: '连衣裙', bag: '包袋', accessory: '配饰' }
const AGENTS = [
  { node: 'semantic_retriever_agent', aliases: ['semantic_retriever'], name: 'Agent 1 · 语义检索', detail: '理解意图，读取衣橱与知识事实' },
  { node: 'composer_agent', aliases: ['composer'], name: 'Agent 2 · 方案生成', detail: '基于候选范围完成业务结果' },
  { node: 'critic_agent', aliases: ['critic'], name: 'Agent 3 · 审校决策', detail: '检查依据、边界与最终可用性' },
]

const ItemGrid = defineComponent({
  props: { items: { type: Array, default: () => [] } },
  setup(props) {
    return () => h('div', { class: 'item-grid' }, props.items.map((item) => h('article', { class: 'item-tile', key: item.item_id }, [
      h(ElImage, { src: itemImage(item.item_id), fit: 'cover' }),
      h('strong', item.name || item.item_id),
      h('span', [item.color, item.slot || item.item_type].filter(Boolean).join(' · ')),
    ])))
  },
})

const userId = ref(getUserId())
const request = ref('黑色马甲怎么搭？')
const store = useRecommendationStore()
const { loading, payload, error } = storeToRefs(store)
const result = computed(() => payload.value?.result || {})
const isRecommend = computed(() => payload.value?.task_type === 'outfit_recommend')
const recommendationPayload = computed(() => isRecommend.value ? result.value : {})
const recommendOutfits = computed(() => recommendationPayload.value.structured_result?.recommendations || [])
const taskLabel = computed(() => TASK_LABELS[payload.value?.task_type] || payload.value?.task_type)
const statusLabel = computed(() => ({ completed: '已完成', infeasible: '无可行方案', needs_clarification: '待补充' }[payload.value?.status] || payload.value?.status))
const resultTitle = computed(() => result.value.title || result.value.message || result.value.summary || taskLabel.value)
const agentRail = computed(() => {
  const trace = payload.value?.trace || []
  const nested = recommendationPayload.value.trace || []
  const nodes = new Set([...trace, ...nested].map((item) => item.node))
  return AGENTS.map((agent) => ({ ...agent, done: nodes.has(agent.node) || agent.aliases.some((name) => nodes.has(name)) }))
})

const itemImage = (itemId) => imageUrl(`/items/${itemId}/image`)
const outfitIds = (outfit) => outfit.item_ids || outfit.wardrobe_item_ids || (outfit.items || []).map((item) => item.item_id)
const fmtScore = (value) => typeof value === 'number' ? value.toFixed(1) : '—'
const slotLabel = (slot) => SLOT_LABELS[slot] || slot
const recommendationLabel = (value) => ({ recommended: '建议', consider: '可考虑', not_recommended: '不建议', unknown: '待判断' }[value] || value || '—')
function onUserIdChange(value) { setUserId(value) }
async function run() { if (request.value.trim()) await store.run(userId.value, request.value) }
</script>

<style scoped>
.studio { --ink: #18201d; --moss: #52675b; --copper: #a86138; max-width: 1240px; margin: 0 auto; color: var(--ink); }
.hero { display: flex; justify-content: space-between; gap: 32px; padding: 34px 0 24px; border-bottom: 1px solid #d8ddd8; }
.eyebrow { margin: 0 0 8px; color: var(--copper); font-size: 12px; letter-spacing: .18em; font-weight: 700; }
h1 { margin: 0; max-width: 720px; font-family: Georgia, 'Noto Serif SC', serif; font-size: clamp(34px, 5vw, 58px); line-height: 1.05; font-weight: 500; }
.lead { max-width: 660px; margin: 16px 0 0; color: #65706b; font-size: 16px; }
.user-box { width: 190px; align-self: flex-start; }
.user-box span { display: block; margin-bottom: 8px; color: #77817c; font-size: 12px; }
.prompt-card { margin-top: 24px; padding: 20px; background: #f3f1ea; border: 1px solid #d9d3c5; border-radius: 4px; box-shadow: 8px 8px 0 #e1e5df; }
.prompt-card :deep(textarea) { background: transparent; border: 0; box-shadow: none; font-size: 18px; line-height: 1.7; }
.prompt-footer { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; margin-top: 12px; }
.examples { display: flex; flex-wrap: wrap; gap: 8px; }
.examples button { border: 1px solid #c9cec8; background: #fff; color: #4b5751; border-radius: 999px; padding: 7px 12px; cursor: pointer; }
.examples button:hover, .examples button:focus-visible { border-color: var(--copper); color: var(--copper); outline: none; }
.shortcut { margin: 8px 0 0; color: #929994; font-size: 11px; text-align: right; }
.block { margin-top: 24px; }
.result-head { display: flex; justify-content: space-between; align-items: end; border-bottom: 2px solid var(--ink); padding-bottom: 14px; }
.result-head h2 { margin: 9px 0 4px; font-family: Georgia, 'Noto Serif SC', serif; font-size: 28px; font-weight: 500; }
.result-head p { margin: 0; color: #7c8580; font-size: 12px; }
.task-chip { color: var(--copper); font-size: 12px; font-weight: 700; letter-spacing: .12em; }
.metric { text-align: right; }.metric strong { display: block; font-size: 32px; }.metric span { color: #7c8580; font-size: 12px; }
.agent-rail { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid #d9ded9; }
.agent-step { position: relative; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 12px; min-height: 82px; padding: 14px 18px; border-right: 1px solid #d9ded9; background: #fbfcfa; }
.agent-step:last-child { border-right: 0; }.step-index { color: var(--copper); font-family: Georgia, serif; font-size: 23px; }
.agent-step strong, .agent-step small { display: block; }.agent-step small { margin-top: 3px; color: #7e8882; }
.step-status { color: var(--moss); font-size: 12px; }.section-heading { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 1px solid #d7ddd8; margin-bottom: 14px; }
.section-heading h3 { margin: 0 0 8px; font-family: Georgia, 'Noto Serif SC', serif; font-size: 23px; font-weight: 500; }.section-heading span { color: #78827c; font-size: 12px; }
.outfit-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }.outfit-card { padding: 14px; border: 1px solid #d5dbd6; background: #fff; }
.outfit-card header { display: flex; justify-content: space-between; margin-bottom: 10px; color: #69736e; font-size: 12px; letter-spacing: .08em; }.outfit-card header strong { color: var(--copper); font-size: 16px; }
.image-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }.image-grid :deep(.el-image) { width: 100%; height: 150px; background: #ecefea; }.image-empty { height: 150px; display: grid; place-items: center; color: #9ba39f; }
.outfit-card p { color: #5e6863; font-size: 13px; line-height: 1.6; }.knowledge-layout { display: grid; grid-template-columns: .9fr 1.1fr; gap: 28px; }.summary { font-size: 16px; line-height: 1.8; color: #4f5a54; }
.principle { padding: 13px 0; border-top: 1px solid #e0e4e0; }.principle p { margin-bottom: 0; color: #68726d; }.item-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }.item-tile { min-width: 0; }.item-tile :deep(.el-image) { width: 100%; height: 130px; background: #edf0ec; }.item-tile strong, .item-tile span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.item-tile strong { margin-top: 7px; font-size: 13px; }.item-tile span { color: #818984; font-size: 11px; }
.anchor { display: flex; gap: 14px; width: fit-content; min-width: 300px; margin: 16px 0 28px; padding: 10px; border: 1px solid #d6dbd6; }.anchor :deep(.el-image) { width: 96px; height: 110px; }.anchor div { display: flex; flex-direction: column; justify-content: center; }.anchor small { color: var(--copper); }.anchor strong { margin: 6px 0; }.anchor span, .muted { color: #7c8580; font-size: 12px; }.slot-group { margin-top: 26px; }
.scoreboard { display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid #d5dbd6; }.scoreboard div { padding: 18px; border-right: 1px solid #d5dbd6; }.scoreboard div:last-child { border: 0; }.scoreboard strong, .scoreboard span { display: block; }.scoreboard strong { font-family: Georgia, serif; font-size: 28px; }.scoreboard span { margin-top: 4px; color: #7c8580; font-size: 12px; }
.gap-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }.gap-card { padding: 16px; border-top: 3px solid var(--copper); background: #f4f2eb; }.gap-card > span { text-transform: uppercase; color: var(--copper); font-size: 10px; letter-spacing: .12em; }.gap-card h4 { margin: 9px 0; }.gap-card p { color: #626d67; line-height: 1.6; }.covered { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }.covered strong { width: 100%; }.covered span { padding: 5px 9px; border: 1px solid #cbd2cc; border-radius: 999px; font-size: 12px; }.technical pre { max-height: 440px; overflow: auto; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
@media (max-width: 900px) { .hero { flex-direction: column; }.user-box { width: 100%; }.agent-rail, .outfit-grid, .gap-grid { grid-template-columns: 1fr; }.agent-step { border-right: 0; border-bottom: 1px solid #d9ded9; }.knowledge-layout { grid-template-columns: 1fr; }.item-grid { grid-template-columns: repeat(2, 1fr); }.scoreboard { grid-template-columns: repeat(2, 1fr); }.prompt-footer { align-items: stretch; flex-direction: column; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; } }
</style>
