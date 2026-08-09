<template>
  <div>
    <el-page-header content="订单导入" />
    <p class="hint">上传购物订单 Excel，预览收货/退款状态，确认后加入衣柜并自动生成嵌入。</p>

    <el-card class="mt">
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept=".xlsx"
        :on-change="onFile"
        :on-remove="() => (file = null)"
      >
        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
        <div class="el-upload__text">拖拽订单 Excel 到此处，或 <em>点击选择</em></div>
      </el-upload>
      <div class="toolbar">
        <el-select v-model="defaultAudience" placeholder="默认人群（无法从商品名识别时）" clearable style="width: 260px">
          <el-option v-for="a in AUDIENCES" :key="a" :label="a" :value="a" />
        </el-select>
        <el-button type="primary" :loading="previewing" @click="preview">生成导入预览</el-button>
      </div>
    </el-card>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mt" />

    <template v-if="batch">
      <el-row :gutter="12" class="mt">
        <el-col :span="6"><el-statistic title="订单行" :value="statistics.total_rows || 0" /></el-col>
        <el-col :span="6"><el-statistic title="已收货行" :value="statistics.eligible_order_rows || 0" /></el-col>
        <el-col :span="6"><el-statistic title="服饰候选" :value="statistics.candidate_rows || 0" /></el-col>
        <el-col :span="6"><el-statistic title="默认排除" :value="statistics.excluded_rows || 0" /></el-col>
      </el-row>

      <el-card class="mt">
        <template #header>
          <div class="card-header">
            <span>候选记录（勾选要加入衣柜的）</span>
            <el-checkbox :model-value="allSelected" @change="toggleAll">全选候选</el-checkbox>
          </div>
        </template>
        <el-table :data="rows" @selection-change="onSelectionChange" max-height="480">
          <el-table-column type="selection" width="48" :selectable="isSelectable" />
          <el-table-column prop="order_status" label="订单状态" width="110" />
          <el-table-column prop="order_eligibility" label="准入" width="80" />
          <el-table-column prop="product_name" label="商品名称" min-width="200" show-overflow-tooltip />
          <el-table-column prop="predicted_item_type" label="品类" width="90" />
          <el-table-column prop="predicted_subtype" label="子类" width="90" />
          <el-table-column prop="predicted_color" label="颜色" width="80" />
          <el-table-column prop="predicted_audience" label="人群" width="80" />
          <el-table-column prop="confidence" label="置信度" width="80">
            <template #default="{ row }">{{ (row.confidence * 100).toFixed(0) }}%</template>
          </el-table-column>
        </el-table>
        <div class="footer">
          <span class="hint">性别判断不出的用下方选择兜底（未选默认女）；未识别品类的记录自动跳过。</span>
          <el-select v-model="genderOverride" placeholder="性别兜底" clearable style="width: 140px">
            <el-option label="女" value="women" />
            <el-option label="男" value="men" />
          </el-select>
          <el-button type="primary" :loading="committing" @click="commit">确认并加入衣柜</el-button>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { previewImport, getImportPreview, commitImport } from '../services/api'
import { getUserId, setUserId } from '../services/user'

const AUDIENCES = ['women', 'men', 'girls', 'boys', 'baby', 'life']
const userId = ref(getUserId())
const file = ref(null)
const defaultAudience = ref('')
const genderOverride = ref('')
const previewing = ref(false)
const committing = ref(false)
const error = ref('')
const batch = ref(null)
const rows = ref([])
const selectedRows = ref([])

const statistics = computed(() => batch.value?.statistics || {})
const isSelectable = (row) => row.decision === 'candidate' || row.decision === 'committed'

function onFile(f) { file.value = f.raw || f }

async function preview() {
  if (!file.value) { ElMessage.warning('请选择订单 Excel'); return }
  previewing.value = true
  error.value = ''
  try {
    const b64 = await fileToBase64(file.value)
    const res = await previewImport(userId.value, file.value.name, b64, defaultAudience.value)
    batch.value = res.data.batch
    rows.value = res.data.rows
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  } finally {
    previewing.value = false
  }
}

function fileToBase64(fileObj) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(fileObj)
  })
}

function onSelectionChange(sel) {
  selectedRows.value = sel
}

const allSelected = computed(
  () => rows.value.length > 0 && selectedRows.value.length === rows.value.filter(isSelectable).length
)
function toggleAll(val) {
  // Element Plus selection 通过 ref 控制，简化：提示用全选按钮
}

async function commit() {
  const keep = selectedRows.value.filter((r) => r.predicted_item_type)
  if (!keep.length) { ElMessage.warning('请至少选择一条已识别品类的记录'); return }
  const selections = keep.map((r) => ({
    row_id: r.row_id,
    item_type: r.predicted_item_type,
    subtype: r.predicted_subtype || '',
    color: r.predicted_color || '',
    size: r.predicted_size || '',
    audience: r.predicted_audience || genderOverride.value || 'women',
  }))
  committing.value = true
  try {
    const res = await commitImport(userId.value, batch.value.batch_id, selections)
    ElMessage.success(`已加入 ${res.data.committed_item_count} 件衣物`)
    const embedding = res.data.embedding || {}
    if (embedding.status === 'failed') {
      ElMessage.warning(`衣柜已保存，但嵌入失败：${embedding.error || ''}`)
    }
    // 重新拉取预览（已提交行变为 committed）
    const fresh = await getImportPreview(userId.value, batch.value.batch_id)
    rows.value = fresh.data.rows
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message)
  } finally {
    committing.value = false
  }
}

onMounted(() => {})
</script>

<style scoped>
.hint { color: #888; font-size: 13px; }
.mt { margin-top: 12px; }
.toolbar { display: flex; gap: 12px; margin-top: 12px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.footer { display: flex; gap: 12px; align-items: center; margin-top: 12px; justify-content: flex-end; }
</style>
