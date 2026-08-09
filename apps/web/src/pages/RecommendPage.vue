<template>
  <div>
    <el-page-header content="穿搭推荐" />
    <div class="toolbar">
      <el-input v-model="userId" placeholder="用户 ID" style="width: 180px" @change="onUserIdChange" />
      <el-input
        v-model="request"
        type="textarea"
        :rows="3"
        placeholder="今天想怎么穿？例如：明天参加互联网公司的面试，希望正式但不要太老气，不穿红色。"
        style="flex: 1"
      />
      <el-button type="primary" :loading="loading" @click="run">
        生成搭配
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />

    <template v-if="payload">
      <!-- 决策状态条 -->
      <el-row :gutter="12" class="mb">
        <el-col :span="6"><el-statistic title="决策" :value="decisionLabel" /></el-col>
        <el-col :span="6"><el-statistic title="LLM 调用" :value="payload.llm_call_count || 0" /></el-col>
        <el-col :span="6"><el-statistic title="回退次数" :value="payload.fallback_count || 0" /></el-col>
        <el-col :span="6"><el-statistic title="候选池" :value="payload.pool?.total || 0" /></el-col>
      </el-row>

      <el-alert
        v-if="payload.degraded_reason"
        :title="`本次有环节降级：${payload.degraded_reason}`"
        type="warning" show-icon class="mb"
      />

      <!-- 请求画像 -->
      <el-collapse class="mb">
        <el-collapse-item title="🧭 请求画像与检索计划" name="signature">
          <p><b>主题</b>：{{ payload.request_signature?.theme }}</p>
          <p><b>独特情绪</b>：{{ (payload.request_signature?.unique_mood || []).join('、') }}</p>
          <p><b>实际场景</b>：{{ (payload.request_signature?.practical_context || []).join('、') }}</p>
          <ul>
            <li v-for="(t, i) in payload.request_signature?.generic_tendencies_to_avoid || []" :key="i">
              要避免：{{ t }}
            </li>
          </ul>
          <el-table :data="payload.retrieval_plans || []" size="small">
            <el-table-column prop="type" label="类型" width="120" />
            <el-table-column prop="score_weight" label="权重" width="80" />
            <el-table-column prop="query" label="检索查询（英文）" />
          </el-table>
        </el-collapse-item>
      </el-collapse>

      <!-- 推荐方案 -->
      <el-row :gutter="12">
        <el-col
          v-for="(rec, idx) in structured.recommendations || []"
          :key="rec.outfit_id" :xs="24" :sm="12" :md="8"
        >
          <el-card shadow="hover" class="outfit-card">
            <template #header>
              <div class="outfit-header">
                <span>方案 {{ idx + 1 }} · {{ rec.score?.toFixed(1) }} 分</span>
                <el-tag v-if="rec.outfit_id === preferredId" type="success" size="small">⭐ 首选</el-tag>
              </div>
            </template>
            <el-row :gutter="8">
              <el-col v-for="itemId in rec.item_ids" :key="itemId" :span="12">
                <el-image
                  :src="imageUrl(`/items/${itemId}/image`)"
                  fit="cover" class="item-img"
                >
                  <template #error><div class="img-ph">无图</div></template>
                </el-image>
              </el-col>
            </el-row>
            <ul class="reasons">
              <li v-for="(r, i) in rec.reasons || []" :key="i">{{ r }}</li>
            </ul>
          </el-card>
        </el-col>
      </el-row>
      <el-empty v-if="structured.status === 'infeasible'" description="当前衣柜没有满足约束的完整搭配" />

      <!-- 评审判定 -->
      <el-collapse class="mt" v-if="payload.critic">
        <el-collapse-item title="⚖️ 评审判定" name="critic">
          <p><b>首选方案</b>：{{ payload.critic.outfit_assessment?.outfit_id }}</p>
          <el-row :gutter="8">
            <el-col v-for="(v, k) in payload.critic.outfit_assessment?.dimension_scores" :key="k" :span="4">
              <el-statistic :title="dimLabel(k)" :value="v" />
            </el-col>
          </el-row>
          <p>{{ payload.critic.outfit_assessment?.reasoning }}</p>
          <p v-if="payload.critic.outfit_assessment?.improvements" class="dim">
            改进建议：{{ payload.critic.outfit_assessment.improvements }}
          </p>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { recommend, imageUrl } from '../services/api'
import { getUserId, setUserId } from '../services/user'

const userId = ref(getUserId())
const request = ref('明天参加互联网公司的面试，希望正式但不要太老气，不穿红色。')
const loading = ref(false)
const error = ref('')
const payload = ref(null)

const DECISION = { accept: '✅ 采纳', recompose: '🔄 重新组合', retrieve_more: '🔍 扩展检索', wardrobe_gap: '🧥 衣橱缺口' }
const DIM = {
  request_relevance: '需求还原', request_specificity: '请求特异',
  outfit_coordination: '搭配协调', wearability: '实穿', freshness: '新鲜感',
}

const structured = computed(() => payload.value?.structured_result || {})
const preferredId = computed(() => payload.value?.critic?.outfit_assessment?.outfit_id)
const decisionLabel = computed(() => DECISION[payload.value?.decision] || payload.value?.decision || '—')
const dimLabel = (k) => DIM[k] || k

function onUserIdChange(value) { setUserId(value) }

async function run() {
  loading.value = true
  error.value = ''
  try {
    const res = await recommend(userId.value, request.value)
    payload.value = res.data
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.toolbar { display: flex; gap: 12px; margin: 12px 0; align-items: flex-start; }
.mb { margin-bottom: 12px; }
.mt { margin-top: 16px; }
.outfit-card { margin-bottom: 12px; }
.outfit-header { display: flex; justify-content: space-between; align-items: center; }
.item-img { width: 100%; height: 140px; margin-bottom: 6px; }
.img-ph { height: 140px; display: flex; align-items: center; justify-content: center; color: #999; }
.reasons { font-size: 13px; padding-left: 16px; }
.dim { color: #888; font-size: 13px; }
</style>
