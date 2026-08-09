<template>
  <div>
    <el-page-header content="我的偏好 · 五维权重" />
    <p class="hint">
      权重影响检索侧重、组合取舍与最终评分（保存后自动归一化，和为 100%）。
    </p>
    <el-card style="max-width: 560px" class="mt">
      <div v-for="dim in DIMS" :key="dim.key" class="slider-row">
        <span class="label">{{ dim.label }}</span>
        <el-slider
          v-model="weights[dim.key]"
          :min="0" :max="100"
          :show-tooltip="false"
        />
        <span class="value">{{ weights[dim.key] }}%</span>
      </div>
      <el-button type="primary" :loading="saving" @click="save">保存偏好</el-button>
      <el-button @click="reset">重置默认</el-button>
    </el-card>
    <el-alert v-if="message" :title="message" type="success" show-icon class="mt" />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { getEvaluationWeights, saveEvaluationWeights } from '../services/api'
import { getUserId, setUserId } from '../services/user'

const DEFAULT = {
  request_relevance: 25, request_specificity: 25,
  outfit_coordination: 20, wearability: 15, freshness: 15,
}
const DIMS = [
  { key: 'request_relevance', label: '需求还原' },
  { key: 'request_specificity', label: '请求特异' },
  { key: 'outfit_coordination', label: '搭配协调' },
  { key: 'wearability', label: '实穿' },
  { key: 'freshness', label: '新鲜感' },
]

const userId = ref(getUserId())
const weights = reactive({ ...DEFAULT })
const saving = ref(false)
const message = ref('')

async function load() {
  try {
    const res = await getEvaluationWeights(userId.value)
    for (const dim of DIMS) {
      weights[dim.key] = Math.round((res.data.weights[dim.key] || 0) * 100)
    }
  } catch (e) {
    /* 保留默认 */
  }
}

async function save() {
  saving.value = true
  message.value = ''
  try {
    const payload = {}
    for (const dim of DIMS) payload[dim.key] = weights[dim.key] / 100
    await saveEvaluationWeights(userId.value, payload)
    message.value = '已保存，下次推荐按新权重生效'
  } catch (e) {
    message.value = ''
    alert(e.response?.data?.detail || e.message)
  } finally {
    saving.value = false
  }
}

function reset() {
  Object.assign(weights, DEFAULT)
}

onMounted(load)
</script>

<style scoped>
.hint { color: #888; font-size: 13px; }
.mt { margin-top: 12px; }
.slider-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.label { width: 80px; }
.slider-row .el-slider { flex: 1; }
.value { width: 48px; text-align: right; color: #555; }
</style>
