<template>
  <div>
    <el-page-header content="偏好记忆 · 长期偏好管理" />
    <p class="hint">
      系统会从你的提问与行为（采纳、换掉、反馈）中自动沉淀「证据」，再聚合为维度化偏好模型。
      这里是模型的当前假设：可手动添加、修正或遗忘。记忆会按维度/属性/取值注入三位造型 Agent
      的上下文，跨对话保持一致；被遗忘后若再次观察到会重新复活。
    </p>

    <el-card class="mt">
      <div class="form-row">
        <el-select v-model="form.dimension" class="dim" placeholder="维度" @change="onDimensionChange">
          <el-option v-for="d in DIMENSIONS" :key="d.value" :label="d.label" :value="d.value" />
        </el-select>
        <el-select
          v-model="form.attribute"
          class="attr"
          filterable
          allow-create
          default-first-option
          placeholder="属性"
        >
          <el-option v-for="a in attributesFor(form.dimension)" :key="a.value" :label="a.label" :value="a.value" />
        </el-select>
        <el-input v-model="form.value" class="value" placeholder="取值，例如：简约 / 黑色 / 通勤 / 500-1000" @keydown.enter="add" />
        <el-select v-model="form.polarity" class="polarity" placeholder="倾向">
          <el-option label="偏好" value="positive" />
          <el-option label="回避" value="negative" />
        </el-select>
        <el-slider v-model="form.strength" class="confidence" :min="0" :max="100" :show-tooltip="false" />
        <span class="confidence-value">{{ form.strength }}%</span>
        <el-button type="primary" :loading="saving" @click="add">新增记忆</el-button>
      </div>
      <p class="form-hint">属性可从下拉选择或直接输入；强度表示这条显式声明的可信权重（默认为强证据）。</p>
    </el-card>

    <el-alert v-if="message" :title="message" type="success" show-icon class="mt" />
    <el-alert v-if="error" :title="error" type="error" show-icon class="mt" />

    <el-card v-if="!memories.length" class="mt">
      <el-empty description="还没有偏好记忆。提问几次、点几下「采纳/换掉」，或手动添加一条试试。" />
    </el-card>

    <el-card v-for="memory in memories" :key="memory.preference_id" class="mt memory-card">
      <div v-if="editingId === memory.preference_id" class="form-row">
        <el-select v-model="editForm.dimension" class="dim" @change="onDimensionChange">
          <el-option v-for="d in DIMENSIONS" :key="d.value" :label="d.label" :value="d.value" />
        </el-select>
        <el-select
          v-model="editForm.attribute"
          class="attr"
          filterable
          allow-create
          default-first-option
        >
          <el-option v-for="a in attributesFor(editForm.dimension)" :key="a.value" :label="a.label" :value="a.value" />
        </el-select>
        <el-input v-model="editForm.value" class="value" @keydown.enter="saveEdit(memory)" />
        <el-select v-model="editForm.polarity" class="polarity">
          <el-option label="偏好" value="positive" />
          <el-option label="回避" value="negative" />
        </el-select>
        <el-select v-model="editForm.lifecycle" class="lifecycle">
          <el-option v-for="l in LIFECYCLES" :key="l.value" :label="l.label" :value="l.value" />
        </el-select>
        <el-button type="primary" size="small" :loading="saving" @click="saveEdit(memory)">保存</el-button>
        <el-button size="small" @click="cancelEdit">取消</el-button>
      </div>
      <div v-else class="memory-row">
        <span class="dimension-tag">{{ dimensionLabel(memory.dimension) }}</span>
        <strong class="claim">{{ memory.attribute }} = {{ memory.value }}</strong>
        <span class="polarity-tag" :class="memory.polarity">
          {{ memory.polarity === 'positive' ? '偏好' : '回避' }}
        </span>
        <span class="lifecycle-tag" :class="memory.lifecycle">{{ lifecycleLabel(memory.lifecycle) }}</span>
        <span class="confidence-text">置信 {{ Math.round((memory.confidence || 0) * 100) }}%</span>
        <span class="counts" title="支持 / 反对 证据计数">
          支持 {{ memory.support_count || 0 }} / 反对 {{ memory.contradiction_count || 0 }}
        </span>
        <span v-if="memory.decay_policy !== 'none'" class="decay">
          {{ decayLabel(memory.decay_policy) }}<template v-if="memory.expires_at"> · {{ shortDate(memory.expires_at) }} 过期</template>
        </span>
        <span class="row-actions">
          <el-button size="small" link type="primary" @click="startEdit(memory)">编辑</el-button>
          <el-button size="small" link type="danger" @click="forget(memory)">遗忘</el-button>
        </span>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { listMemories, createMemory, updateMemory, forgetMemory } from '../services/api'
import { getUserId, setUserId } from '../services/user'

const DIMENSIONS = [
  { value: 'style', label: '风格' },
  { value: 'garment', label: '单品' },
  { value: 'appearance', label: '穿搭' },
  { value: 'shopping', label: '购物' },
]
const ATTRIBUTES = {
  style: [
    { value: 'style', label: '风格' },
    { value: 'formality', label: '正式度' },
    { value: 'color', label: '色系' },
    { value: 'fit', label: '版型' },
    { value: 'silhouette', label: '廓形' },
  ],
  garment: [
    { value: 'category', label: '品类' },
    { value: 'fit', label: '版型' },
    { value: 'material', label: '材质' },
    { value: 'color', label: '色系' },
    { value: 'brand', label: '品牌' },
    { value: 'length', label: '长度' },
  ],
  appearance: [
    { value: 'color_combination', label: '配色' },
    { value: 'layering', label: '叠穿' },
    { value: 'silhouette', label: '廓形' },
    { value: 'proportion', label: '比例' },
  ],
  shopping: [
    { value: 'budget', label: '预算' },
    { value: 'price_range', label: '价格区间' },
    { value: 'brand', label: '品牌' },
    { value: 'channel', label: '渠道' },
    { value: 'season', label: '季节' },
  ],
}
const LIFECYCLES = [
  { value: 'short_term', label: '短期' },
  { value: 'long_term_candidate', label: '长期候选' },
  { value: 'long_term', label: '长期' },
]
const DECAY_LABELS = { none: '不衰减', slow: '慢速衰减', normal: '常规衰减' }
const DIMENSION_LABELS = Object.fromEntries(DIMENSIONS.map((d) => [d.value, d.label]))
const LIFECYCLE_LABELS = Object.fromEntries(LIFECYCLES.map((l) => [l.value, l.label]))

const userId = ref(getUserId())
const memories = ref([])
const saving = ref(false)
const message = ref('')
const error = ref('')

const form = reactive({ dimension: 'style', attribute: 'style', value: '', polarity: 'positive', strength: 90 })
const editingId = ref(null)
const editForm = reactive({ dimension: 'style', attribute: '', value: '', polarity: 'positive', lifecycle: 'short_term' })

const dimensionLabel = (value) => DIMENSION_LABELS[value] || value
const lifecycleLabel = (value) => LIFECYCLE_LABELS[value] || value
const decayLabel = (value) => DECAY_LABELS[value] || value

const attributesFor = (dimension) => ATTRIBUTES[dimension] || ATTRIBUTES.style

function onDimensionChange() {
  // Keep the attribute default aligned with the chosen dimension.
  const defaults = { style: 'style', garment: 'category', appearance: 'color_combination', shopping: 'budget' }
  const target = editingId.value ? editForm : form
  const current = target.dimension
  target.attribute = defaults[current] || attributesFor(current)[0].value
}

function shortDate(value) {
  return value ? value.slice(0, 10) : ''
}

async function load() {
  try {
    const res = await listMemories(userId.value)
    memories.value = res.data.memories || []
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

async function add() {
  if (!form.value.trim() || !form.attribute.trim()) return
  saving.value = true
  message.value = ''
  error.value = ''
  try {
    await createMemory(userId.value, {
      dimension: form.dimension,
      attribute: form.attribute,
      value: form.value.trim(),
      polarity: form.polarity,
      strength: form.strength / 100,
    })
    form.value = ''
    form.strength = 90
    message.value = '已添加偏好记忆（作为强显式证据）'
    await load()
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

function startEdit(memory) {
  editingId.value = memory.preference_id
  editForm.dimension = memory.dimension
  editForm.attribute = memory.attribute
  editForm.value = memory.value
  editForm.polarity = memory.polarity
  editForm.lifecycle = memory.lifecycle
}

function cancelEdit() {
  editingId.value = null
}

async function saveEdit(memory) {
  if (!editForm.value.trim() || !editForm.attribute.trim()) return
  saving.value = true
  message.value = ''
  error.value = ''
  try {
    await updateMemory(userId.value, memory.preference_id, {
      dimension: editForm.dimension,
      attribute: editForm.attribute,
      value: editForm.value.trim(),
      polarity: editForm.polarity,
      lifecycle: editForm.lifecycle,
    })
    editingId.value = null
    message.value = '已更新偏好记忆（模型为当前假设，可被后续证据重算）'
    await load()
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

async function forget(memory) {
  const claim = `${memory.attribute} = ${memory.value}`
  if (!window.confirm(`遗忘「${claim}」？之后系统将不再参考这条偏好。`)) return
  try {
    await forgetMemory(userId.value, memory.preference_id)
    message.value = '已遗忘该偏好记忆'
    error.value = ''
    await load()
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

function onUserIdChange() {
  setUserId(userId.value)
  message.value = ''
  error.value = ''
  void load()
}

onMounted(load)
</script>

<style scoped>
.hint { color: #888; font-size: 13px; line-height: 1.7; max-width: 760px; }
.form-hint { margin: 8px 0 0; color: #9aa19c; font-size: 12px; }
.mt { margin-top: 12px; }
.form-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.form-row .dim { width: 110px; }
.form-row .attr { width: 130px; }
.form-row .value { flex: 1; min-width: 220px; }
.form-row .polarity { width: 90px; }
.form-row .lifecycle { width: 120px; }
.form-row .confidence { flex: 1; min-width: 120px; max-width: 200px; }
.confidence-value { width: 44px; text-align: right; color: #555; font-size: 13px; }
.memory-card { padding: 4px; }
.memory-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.dimension-tag, .polarity-tag, .lifecycle-tag, .decay { flex-shrink: 0; padding: 2px 8px; border-radius: 999px; font-size: 12px; }
.dimension-tag { background: #e4eae4; color: #52675b; }
.polarity-tag.positive { background: #e3eee4; color: #3d7a46; }
.polarity-tag.negative { background: #f5e3e0; color: #a83a38; }
.lifecycle-tag.short_term { background: #f0ece2; color: #8a6d2f; }
.lifecycle-tag.long_term_candidate { background: #e9eef4; color: #4d6d8f; }
.lifecycle-tag.long_term { background: #e4eae4; color: #52675b; }
.decay { background: #f4f4f4; color: #7c8580; }
.claim { font-size: 14px; }
.confidence-text { color: #555; font-size: 12px; }
.counts { color: #9aa19c; font-size: 12px; }
.row-actions { margin-left: auto; }
</style>
