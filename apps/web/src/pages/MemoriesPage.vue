<template>
  <div>
    <el-page-header content="偏好记忆 · 长期偏好管理" />
    <p class="hint">
      系统会从你的提问中自动提炼长期偏好（自动记忆），也可手动添加与修正（手动记忆优先，不会被自动提炼覆盖）。
      这些记忆会注入三位造型 Agent 的上下文，跨对话保持一致。遗忘后若再次观察到，自动记忆会复活。
    </p>

    <el-card class="mt">
      <div class="form-row">
        <el-select v-model="form.category" class="cat" placeholder="品类">
          <el-option
            v-for="option in CATEGORY_OPTIONS"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-input
          v-model="form.content"
          class="content"
          placeholder="例如：偏好黑色 / 通勤场合 / 喜欢简约风格"
          @keydown.enter="add"
        />
        <el-slider
          v-model="form.confidence"
          class="confidence"
          :min="0" :max="100" :show-tooltip="false"
        />
        <span class="confidence-value">{{ form.confidence }}%</span>
        <el-button type="primary" :loading="saving" @click="add">新增记忆</el-button>
      </div>
    </el-card>

    <el-alert v-if="message" :title="message" type="success" show-icon class="mt" />
    <el-alert v-if="error" :title="error" type="error" show-icon class="mt" />

    <el-card v-if="!memories.length" class="mt">
      <el-empty description="还没有偏好记忆。提问几次或手动添加一条试试。" />
    </el-card>

    <el-card v-for="memory in memories" :key="memory.memory_id" class="mt memory-card">
      <div v-if="editingId === memory.memory_id" class="form-row">
        <el-select v-model="editForm.category" class="cat">
          <el-option
            v-for="option in CATEGORY_OPTIONS"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        <el-input v-model="editForm.content" class="content" @keydown.enter="saveEdit(memory)" />
        <el-slider
          v-model="editForm.confidence"
          class="confidence"
          :min="0" :max="100" :show-tooltip="false"
        />
        <span class="confidence-value">{{ editForm.confidence }}%</span>
        <el-button type="primary" size="small" :loading="saving" @click="saveEdit(memory)">保存</el-button>
        <el-button size="small" @click="cancelEdit">取消</el-button>
      </div>
      <div v-else class="memory-row">
        <span class="category-tag">{{ categoryLabel(memory.category) }}</span>
        <span class="source-tag" :class="memory.source">{{ memory.source === 'manual' ? '手动' : '自动' }}</span>
        <strong class="memory-content">{{ memory.content }}</strong>
        <span class="confidence-dot" :style="{ width: `${memory.confidence * 100}%` }" />
        <span class="confidence-text">{{ Math.round(memory.confidence * 100) }}%</span>
        <span v-if="memory.occurrences > 1" class="occurrences">出现 {{ memory.occurrences }} 次</span>
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

const CATEGORY_OPTIONS = [
  { value: 'category', label: '品类' },
  { value: 'color', label: '颜色' },
  { value: 'style', label: '风格' },
  { value: 'formality', label: '正式度' },
  { value: 'occasion', label: '场合' },
  { value: 'habit', label: '习惯' },
  { value: 'general', label: '通用' },
]
const CATEGORY_LABELS = Object.fromEntries(CATEGORY_OPTIONS.map((option) => [option.value, option.label]))

const userId = ref(getUserId())
const memories = ref([])
const saving = ref(false)
const message = ref('')
const error = ref('')

const form = reactive({ category: 'color', content: '', confidence: 80 })
const editingId = ref(null)
const editForm = reactive({ category: 'color', content: '', confidence: 80 })

const categoryLabel = (value) => CATEGORY_LABELS[value] || value

async function load() {
  try {
    const res = await listMemories(userId.value)
    memories.value = res.data.memories || []
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

async function add() {
  if (!form.content.trim()) return
  saving.value = true
  message.value = ''
  error.value = ''
  try {
    await createMemory(userId.value, {
      category: form.category,
      content: form.content.trim(),
      confidence: form.confidence / 100,
    })
    form.content = ''
    form.confidence = 80
    message.value = '已添加偏好记忆'
    await load()
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

function startEdit(memory) {
  editingId.value = memory.memory_id
  editForm.category = memory.category
  editForm.content = memory.content
  editForm.confidence = Math.round(memory.confidence * 100)
}

function cancelEdit() {
  editingId.value = null
}

async function saveEdit(memory) {
  if (!editForm.content.trim()) return
  saving.value = true
  message.value = ''
  error.value = ''
  try {
    await updateMemory(userId.value, memory.memory_id, {
      category: editForm.category,
      content: editForm.content.trim(),
      confidence: editForm.confidence / 100,
    })
    editingId.value = null
    message.value = '已更新偏好记忆'
    await load()
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    saving.value = false
  }
}

async function forget(memory) {
  if (!window.confirm(`遗忘「${memory.content}」？之后系统将不再参考这条偏好。`)) return
  try {
    await forgetMemory(userId.value, memory.memory_id)
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
.hint { color: #888; font-size: 13px; line-height: 1.7; max-width: 720px; }
.mt { margin-top: 12px; }
.form-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.form-row .cat { width: 130px; }
.form-row .content { flex: 1; min-width: 240px; }
.form-row .confidence { flex: 1; min-width: 120px; max-width: 220px; }
.confidence-value { width: 44px; text-align: right; color: #555; font-size: 13px; }
.memory-card { padding: 4px; }
.memory-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.category-tag, .source-tag { flex-shrink: 0; padding: 2px 8px; border-radius: 999px; font-size: 12px; }
.category-tag { background: #e4eae4; color: #52675b; }
.source-tag.manual { background: #f3e3d6; color: #a86138; }
.source-tag.auto { background: #e7e9f0; color: #56607a; }
.memory-content { font-size: 14px; }
.confidence-dot { height: 5px; border-radius: 999px; background: var(--el-color-primary); flex-shrink: 0; }
.confidence-text { color: #555; font-size: 12px; }
.occurrences { color: #9aa19c; font-size: 12px; }
.row-actions { margin-left: auto; }
</style>
