<template>
  <section v-if="payload" class="result-block">
    <section class="result-head block">
      <div>
        <span class="task-chip">{{ taskLabel }}</span>
        <h2>{{ resultTitle }}</h2>
        <p>运行 {{ payload.run_id }} · {{ statusLabel }}</p>
      </div>
      <div class="metric"><strong>{{ payload.llm_call_count || 0 }}</strong><span>次模型调用</span></div>
    </section>

    <el-alert
      v-if="needsClarification"
      :title="result.clarification_question || '需要补充信息后才能继续。'"
      type="warning"
      show-icon
      :closable="false"
      class="block"
    />

    <section v-if="isRecommend && weatherFacts" class="weather-facts block">
      <div class="weather-main">
        <span class="weather-label">天气上下文 · {{ weatherSourceLabel }}</span>
        <strong v-if="weatherFacts.status === 'available'">
          {{ weatherLocation }} · {{ weatherWindow }}
          <span v-if="weatherFacts.default_applied" class="weather-badge">近 3 天默认窗口</span>
        </strong>
        <strong v-else>天气暂不可用，三个 Agent 已继续按衣橱事实处理</strong>
        <div class="temp-unit-switch" role="group" aria-label="温度单位切换">
          <button type="button" :class="{ active: tempUnit === 'celsius' }" @click="setTempUnit('celsius')">°C</button>
          <button type="button" :class="{ active: tempUnit === 'fahrenheit' }" @click="setTempUnit('fahrenheit')">°F</button>
        </div>
      </div>
      <div class="weather-body">
        <template v-if="weatherFacts.status === 'available'">
          <div v-if="weatherDays.length" class="weather-days">
            <div v-for="day in weatherDays" :key="day.date" class="weather-day">
              <strong>{{ day.date.slice(5) }}</strong>
              <span class="weather-icon" :title="day.condition">{{ weatherIcon(day.weather_code) }} {{ day.condition }}</span>
              <span>{{ formatTemp(day.temperature_min_c) }}–{{ formatTemp(day.temperature_max_c) }}</span>
              <span>体感 {{ formatTemp(day.feels_like_c) }} · 降水 {{ day.precipitation_probability_percent }}% · 风 {{ day.wind_speed_kmh }} km/h</span>
              <div v-if="day.key_periods && day.key_periods.length" class="weather-periods">
                <span v-for="period in day.key_periods" :key="period.label" class="weather-period">
                  {{ period.label }}：体感 {{ formatTemp(period.feels_like_c) }} · 降水 {{ period.precipitation_probability_percent }}% · 风 {{ period.wind_speed_kmh }} km/h<template v-if="period.uv_index_max != null"> · UV{{ Math.round(period.uv_index_max) }}</template>
                </span>
              </div>
              <span class="weather-hint">💡 {{ weatherHint(day) }}</span>
            </div>
          </div>
          <dl v-else>
            <div><dt>温度</dt><dd>{{ formatTemp(weatherFacts.temperature_min_c) }}–{{ formatTemp(weatherFacts.temperature_max_c) }}</dd></div>
            <div><dt>体感</dt><dd>{{ formatTemp(weatherFacts.feels_like_c) }}</dd></div>
            <div><dt>降水概率</dt><dd>{{ weatherFacts.precipitation_probability_percent }}%</dd></div>
            <div><dt>风速</dt><dd>{{ weatherFacts.wind_speed_kmh }} km/h</dd></div>
          </dl>
          <small v-if="weatherLocationNote" class="weather-note">{{ weatherLocationNote }}</small>
        </template>
        <small v-else>{{ weatherFacts.error_message }}</small>
      </div>
    </section>

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
          <template v-if="envByOutfit[outfit.outfit_id]">
            <div
              v-for="adjustment in envByOutfit[outfit.outfit_id].environment_adjustments || []"
              :key="adjustment.action"
              class="env-adjustment"
            >
              <strong>环境调整</strong>
              <span>{{ adjustment.impact }} → {{ adjustment.action }}</span>
            </div>
            <div
              v-if="(envByOutfit[outfit.outfit_id].carry_recommendations || []).length"
              class="carry-recommendations"
            >
              <strong>随身</strong>
              <span
                v-for="item in envByOutfit[outfit.outfit_id].carry_recommendations"
                :key="item.name"
                class="carry-chip"
              >{{ item.name }}</span>
            </div>
          </template>
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

    <el-collapse v-if="hasTechnical" class="block technical">
      <el-collapse-item title="Context Pack 与 Agent 输出" name="trace">
        <pre>{{ JSON.stringify({ context_pack: payload.context_pack, agent_outputs: payload.agent_outputs, trace: payload.trace }, null, 2) }}</pre>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<script setup>
import { computed, defineComponent, h } from 'vue'
import { ElImage } from 'element-plus'
import { imageUrl } from '../services/api'
import { useTempUnit, weatherHint, weatherIcon } from '../utils/weather'

const props = defineProps({
  payload: { type: Object, default: null },
})

const TASK_LABELS = { outfit_recommend: '穿搭推荐', outfit_modify: '局部修改', style_advice: '风格知识', item_advice: '单品搭配', wardrobe_compatibility: '衣橱兼容性', wardrobe_gap: '衣橱缺口' }
const SLOT_LABELS = { top: '上衣', bottom: '下装', footwear: '鞋履', outerwear: '外套', one_piece: '连衣裙', bag: '包袋', accessory: '配饰' }

const result = computed(() => props.payload?.result || {})
const isRecommend = computed(() => props.payload?.task_type === 'outfit_recommend')
const recommendation = computed(() => (isRecommend.value ? result.value : {}))
const recommendOutfits = computed(() => recommendation.value.structured_result?.recommendations || [])
const weatherFacts = computed(() => recommendation.value.environment_context?.weather || null)
const weatherLocation = computed(() => weatherFacts.value?.resolved_location?.display_name
  || [weatherFacts.value?.resolved_location?.name, weatherFacts.value?.resolved_location?.admin1, weatherFacts.value?.resolved_location?.country].filter(Boolean).join('，')
  || weatherFacts.value?.requested_location
  || '未命名地点')
const resolvedLocationContext = computed(() => recommendation.value.resolved_location_context || null)
const resolvedTimeContext = computed(() => recommendation.value.resolved_time_context || null)
const weatherDays = computed(() => weatherFacts.value?.days || [])
const weatherWindow = computed(() => {
  const start = weatherFacts.value?.start_date
  const end = weatherFacts.value?.end_date
  if (start && end) return start === end ? start : `${start} ~ ${end}`
  return weatherFacts.value?.forecast_date || ''
})
const weatherSourceLabel = computed(() => {
  const source = resolvedLocationContext.value?.source || weatherFacts.value?.source
  const labels = { device: '设备定位', profile: '默认城市', global: '全局默认城市', named: '显式地点', resolved: '已解析地点' }
  const bucket = resolvedLocationContext.value?.accuracy_bucket
  const bucketLabel = { high: '高精度', medium: '中精度', low: '低精度' }[bucket]
  return [labels[source] || source, bucketLabel].filter(Boolean).join(' · ')
})
const weatherLocationNote = computed(() => {
  const time = resolvedTimeContext.value
  if (!time) return ''
  if (time.status === 'unsupported') return `日期表达式「${time.expression}」暂不支持，已继续按衣橱事实推荐`
  if (time.default_applied) return '未指定日期，自动查询近 3 天窗口'
  if (time.status === 'resolved') {
    if (time.granularity === 'hourly' && time.period_label) {
      const day = (time.start_date || '').slice(5).replace('-', '/')
      return `按「${time.expression || time.start_date}」查询（${day} ${time.period_label} · 小时级）`
    }
    return `按「${time.expression || time.start_date}」查询`
  }
  return ''
})
const envByOutfit = computed(() => {
  const map = {}
  for (const proposal of recommendation.value.proposals || []) {
    map[proposal.outfit_id] = proposal
  }
  return map
})
const taskLabel = computed(() => TASK_LABELS[props.payload?.task_type] || props.payload?.task_type)
const statusLabel = computed(() => ({ completed: '已完成', infeasible: '无可行方案', needs_clarification: '待补充' }[props.payload?.status] || props.payload?.status))
const resultTitle = computed(() => result.value.title || result.value.message || result.value.summary || taskLabel.value)
const needsClarification = computed(() => result.value.status === 'needs_clarification' || !!result.value.clarification_question)
const hasTechnical = computed(() => !!(props.payload?.context_pack || props.payload?.agent_outputs || props.payload?.trace))

const { tempUnit, setTempUnit, formatTemp } = useTempUnit()

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

const itemImage = (itemId) => imageUrl(`/items/${itemId}/image`)
const outfitIds = (outfit) => outfit.item_ids || outfit.wardrobe_item_ids || (outfit.items || []).map((item) => item.item_id)
const fmtScore = (value) => (typeof value === 'number' ? value.toFixed(1) : '—')
const slotLabel = (slot) => SLOT_LABELS[slot] || slot
const recommendationLabel = (value) => ({ recommended: '建议', consider: '可考虑', not_recommended: '不建议', unknown: '待判断' }[value] || value || '—')
</script>

<style scoped>
.result-block { --ink: #18201d; --moss: #52675b; --copper: #a86138; color: var(--ink); }
.block { margin-top: 24px; }
.result-head { display: flex; justify-content: space-between; align-items: end; border-bottom: 2px solid var(--ink); padding-bottom: 14px; }
.result-head h2 { margin: 9px 0 4px; font-family: Georgia, 'Noto Serif SC', serif; font-size: 28px; font-weight: 500; }
.result-head p { margin: 0; color: #7c8580; font-size: 12px; }
.task-chip { color: var(--copper); font-size: 12px; font-weight: 700; letter-spacing: .12em; }
.metric { text-align: right; }.metric strong { display: block; font-size: 32px; }.metric span { color: #7c8580; font-size: 12px; }
.section-heading { display: flex; justify-content: space-between; align-items: baseline; border-bottom: 1px solid #d7ddd8; margin-bottom: 14px; }
.section-heading h3 { margin: 0 0 8px; font-family: Georgia, 'Noto Serif SC', serif; font-size: 23px; font-weight: 500; }.section-heading span { color: #78827c; font-size: 12px; }
.weather-facts { display: flex; justify-content: space-between; gap: 24px; padding: 16px 18px; border-left: 3px solid var(--copper); background: #f3f1ea; }
.weather-facts > div strong, .weather-label { display: block; }.weather-label { margin-bottom: 5px; color: var(--copper); font-size: 11px; letter-spacing: .08em; }
.weather-main { min-width: 220px; }.weather-body { display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }
.weather-facts dl { display: flex; gap: 22px; margin: 0; }.weather-facts dl div { min-width: 70px; }.weather-facts dt { color: #7c8580; font-size: 11px; }.weather-facts dd { margin: 4px 0 0; font-weight: 700; }.weather-facts small { color: #7c8580; }
.weather-badge { display: inline-block; margin-left: 8px; padding: 2px 8px; border: 1px solid var(--copper); border-radius: 999px; color: var(--copper); font-size: 11px; vertical-align: middle; }
.weather-days { display: flex; gap: 10px; }.weather-day { min-width: 160px; max-width: 230px; padding: 8px 10px; border: 1px solid #dcd9ce; background: #fff; }.weather-day strong, .weather-day span { display: block; }.weather-day strong { color: var(--moss); font-size: 12px; }.weather-day span { margin-top: 3px; color: #5e6863; font-size: 12px; }
.weather-icon { font-size: 15px; }
.weather-periods { display: flex; flex-direction: column; gap: 2px; margin-top: 5px; padding-top: 5px; border-top: 1px dashed #dcd9ce; }.weather-period { color: var(--copper) !important; font-size: 11px !important; }
.weather-hint { margin-top: 6px !important; padding-top: 5px; border-top: 1px dashed #dcd9ce; color: #4b5751 !important; font-size: 12px !important; line-height: 1.5; }
.temp-unit-switch { display: inline-flex; margin-top: 6px; border: 1px solid #c9cec8; border-radius: 999px; overflow: hidden; }.temp-unit-switch button { border: 0; background: #fff; color: #7c8580; font-size: 11px; line-height: 1; padding: 5px 9px; cursor: pointer; }.temp-unit-switch button + button { border-left: 1px solid #c9cec8; }.temp-unit-switch button.active { background: var(--moss); color: #fff; }
.weather-note { display: block; max-width: 460px; text-align: right; }
.env-adjustment { margin-top: 8px; padding: 7px 9px; border-left: 2px solid var(--copper); background: #f4f2eb; font-size: 12px; line-height: 1.5; }.env-adjustment strong { display: block; color: var(--copper); font-size: 10px; letter-spacing: .08em; }.env-adjustment span { color: #5e6863; }
.carry-recommendations { margin-top: 8px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }.carry-recommendations strong { color: var(--moss); font-size: 12px; }.carry-chip { padding: 3px 8px; border: 1px solid #cbd2cc; border-radius: 999px; font-size: 12px; color: #4b5751; }
.outfit-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }.outfit-card { padding: 14px; border: 1px solid #d5dbd6; background: #fff; }
.outfit-card header { display: flex; justify-content: space-between; margin-bottom: 10px; color: #69736e; font-size: 12px; letter-spacing: .08em; }.outfit-card header strong { color: var(--copper); font-size: 16px; }
.image-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; }.image-grid :deep(.el-image) { width: 100%; height: 150px; background: #ecefea; }.image-empty { height: 150px; display: grid; place-items: center; color: #9ba39f; }
.outfit-card p { color: #5e6863; font-size: 13px; line-height: 1.6; }.knowledge-layout { display: grid; grid-template-columns: .9fr 1.1fr; gap: 28px; }.summary { font-size: 16px; line-height: 1.8; color: #4f5a54; }
.principle { padding: 13px 0; border-top: 1px solid #e0e4e0; }.principle p { margin-bottom: 0; color: #68726d; }.item-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }.item-tile { min-width: 0; }.item-tile :deep(.el-image) { width: 100%; height: 130px; background: #edf0ec; }.item-tile strong, .item-tile span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.item-tile strong { margin-top: 7px; font-size: 13px; }.item-tile span { color: #818984; font-size: 11px; }
.anchor { display: flex; gap: 14px; width: fit-content; min-width: 300px; margin: 16px 0 28px; padding: 10px; border: 1px solid #d6dbd6; }.anchor :deep(.el-image) { width: 96px; height: 110px; }.anchor div { display: flex; flex-direction: column; justify-content: center; }.anchor small { color: var(--copper); }.anchor strong { margin: 6px 0; }.anchor span, .muted { color: #7c8580; font-size: 12px; }.slot-group { margin-top: 26px; }
.scoreboard { display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid #d5dbd6; }.scoreboard div { padding: 18px; border-right: 1px solid #d5dbd6; }.scoreboard div:last-child { border: 0; }.scoreboard strong, .scoreboard span { display: block; }.scoreboard strong { font-family: Georgia, serif; font-size: 28px; }.scoreboard span { margin-top: 4px; color: #7c8580; font-size: 12px; }
.gap-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }.gap-card { padding: 16px; border-top: 3px solid var(--copper); background: #f4f2eb; }.gap-card > span { text-transform: uppercase; color: var(--copper); font-size: 10px; letter-spacing: .12em; }.gap-card h4 { margin: 9px 0; }.gap-card p { color: #626d67; line-height: 1.6; }.covered { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }.covered strong { width: 100%; }.covered span { padding: 5px 9px; border: 1px solid #cbd2cc; border-radius: 999px; font-size: 12px; }.technical pre { max-height: 440px; overflow: auto; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
@media (max-width: 900px) { .outfit-grid, .gap-grid { grid-template-columns: 1fr; }.weather-facts { flex-direction: column; }.weather-facts dl, .weather-days { flex-wrap: wrap; }.weather-body { align-items: flex-start; }.weather-note { text-align: left; }.knowledge-layout { grid-template-columns: 1fr; }.item-grid { grid-template-columns: repeat(2, 1fr); }.scoreboard { grid-template-columns: repeat(2, 1fr); } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; } }
</style>
