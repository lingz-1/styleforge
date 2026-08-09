<template>
  <div>
    <el-page-header content="我的衣柜">
      <template #extra>
        <el-button type="primary" @click="openCreate">＋ 上传新衣物</el-button>
      </template>
    </el-page-header>
    <div class="toolbar">
      <el-input v-model="userId" placeholder="用户 ID" style="width: 200px" @change="onUserIdChange" />
      <el-button @click="load">刷新</el-button>
      <el-button v-if="Object.keys(grouped).length" @click="toggleAll">
        {{ allExpanded ? '全部收起' : '全部展开' }}
      </el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />

    <el-collapse v-model="expandedTypes" class="mb">
      <el-collapse-item
        v-for="(items, type) in grouped"
        :key="type"
        :name="type"
        :title="`${typeLabel(type)}（${items.length}）`"
      >
        <el-row :gutter="12">
          <el-col v-for="item in items" :key="item.item_id" :xs="12" :sm="6" :md="4">
            <el-card shadow="hover" class="item-card">
              <el-image :src="imageUrl(item.image_url)" fit="cover" class="item-img">
                <template #error>
                  <div class="img-placeholder">暂无实拍图</div>
                </template>
              </el-image>
              <div class="item-name">{{ item.name || item.item_id }}</div>
              <div class="item-meta">{{ item.item_type }} · {{ item.color }}</div>
              <div class="actions">
                <el-button size="small" @click="openEdit(item)">编辑</el-button>
                <el-button size="small" @click="openPhoto(item)">补图</el-button>
                <el-button size="small" type="danger" plain @click="remove(item.item_id)">
                  移出
                </el-button>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </el-collapse-item>
    </el-collapse>
    <el-empty v-if="loaded && !Object.keys(grouped).length" description="衣柜为空" />

    <!-- 上传新衣物 -->
    <el-dialog v-model="createVisible" title="上传新衣物" width="520px">
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept="image/*"
        :on-change="onCreateFile"
        :on-remove="() => (createForm.file = null)"
      >
        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
        <div class="el-upload__text">拖拽图片到此处，或 <em>点击选择</em></div>
      </el-upload>
      <el-form :model="createForm" label-width="80px" class="mt">
        <el-form-item label="名称"><el-input v-model="createForm.name" placeholder="如：蓝色衬衫" /></el-form-item>
        <el-form-item label="品类" required>
          <el-select v-model="createForm.item_type" placeholder="选择品类（必选）">
            <el-option v-for="c in taxonomy" :key="c.key" :label="c.zh" :value="c.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="细分类">
          <el-select
            v-model="createForm.subtype"
            placeholder="可选，如不确定可留空"
            clearable
            :disabled="!subtypeOptions.length"
          >
            <el-option v-for="s in subtypeOptions" :key="s.key" :label="s.zh" :value="s.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="颜色"><el-input v-model="createForm.color" placeholder="如：blue" /></el-form-item>
        <el-form-item label="人群">
          <el-select v-model="createForm.gender">
            <el-option label="女" value="women" />
            <el-option label="男" value="men" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 编辑衣物 -->
    <el-dialog v-model="editVisible" title="编辑衣物信息" width="480px">
      <el-form :model="editForm" label-width="80px">
        <el-form-item label="名称"><el-input v-model="editForm.name" /></el-form-item>
        <el-form-item label="品类">
          <el-select v-model="editForm.item_type">
            <el-option v-for="c in taxonomy" :key="c.key" :label="c.zh" :value="c.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="颜色"><el-input v-model="editForm.color" /></el-form-item>
        <el-form-item label="人群">
          <el-select v-model="editForm.gender">
            <el-option label="女" value="women" />
            <el-option label="男" value="men" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 补实拍图 -->
    <el-dialog v-model="photoVisible" title="上传实拍图" width="480px">
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept="image/*"
        :on-change="onPhotoFile"
        :on-remove="() => (photoForm.file = null)"
      >
        <div class="el-upload__text">选择实拍图，保存后自动生成图像嵌入</div>
      </el-upload>
      <template #footer>
        <el-button @click="photoVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitPhoto">上传</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import {
  getWardrobe, removeWardrobeItem, createPhotoItem, getTaxonomy,
  updateItem, uploadItemImage, imageUrl,
} from '../services/api'
import { getUserId, setUserId } from '../services/user'

const userId = ref(getUserId())
const items = ref([])
const loaded = ref(false)
const error = ref('')
const saving = ref(false)

// Bilingual taxonomy: main category (required) + subtype (optional).
const taxonomy = ref([])

// Collapsible per-category groups; collapsed by default (catalog is large).
const expandedTypes = ref([])
const allExpanded = computed(
  () => Object.keys(grouped.value).length > 0
    && expandedTypes.value.length === Object.keys(grouped.value).length,
)
function toggleAll() {
  expandedTypes.value = allExpanded.value ? [] : Object.keys(grouped.value)
}

const TYPE_LABELS = {
  top: '上装', pants: '裤装', skirt: '半身裙', dress: '连衣裙', jumpsuit: '连体装',
  outwear: '外套', shoes: '鞋', bag: '包', accessory: '配饰', other: '其他',
}

const grouped = computed(() => {
  const map = {}
  for (const item of items.value) {
    const type = item.item_type || 'other'
    if (!map[type]) map[type] = []
    map[type].push(item)
  }
  return map
})
const typeLabel = (type) => {
  const cat = taxonomy.value.find((c) => c.key === type)
  if (cat) return cat.zh
  return TYPE_LABELS[type] || type
}

function onUserIdChange(value) { setUserId(value) }

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = reject
    reader.readAsDataURL(file.raw || file)
  })
}

async function load() {
  error.value = ''
  try {
    const res = await getWardrobe(userId.value)
    items.value = res.data.items
    loaded.value = true
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

async function remove(itemId) {
  try {
    await removeWardrobeItem(userId.value, itemId)
    ElMessage.success('已移出衣柜')
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message)
  }
}

// --- 创建 ---
const createVisible = ref(false)
const createForm = reactive({
  name: '', item_type: '', subtype: '', color: '', gender: 'women', file: null,
})
const subtypeOptions = computed(() => {
  const cat = taxonomy.value.find((c) => c.key === createForm.item_type)
  return cat ? cat.subtypes : []
})

function openCreate() {
  createForm.name = ''
  createForm.item_type = ''
  createForm.subtype = ''
  createForm.color = ''
  createForm.gender = 'women'
  createForm.file = null
  createVisible.value = true
}
function onCreateFile(file) { createForm.file = file }
async function submitCreate() {
  if (!createForm.file || !createForm.item_type) {
    ElMessage.warning('请选择图片和品类')
    return
  }
  saving.value = true
  try {
    const b64 = await fileToBase64(createForm.file)
    const res = await createPhotoItem(userId.value, {
      filename: createForm.file.name,
      content_base64: b64,
      item_type: createForm.item_type,
      subtype: createForm.subtype,
      name: createForm.name,
      color: createForm.color,
      gender: createForm.gender,
    })
    ElMessage.success('已创建并加入衣柜')
    createVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message)
  } finally {
    saving.value = false
  }
}

// --- 编辑 ---
const editVisible = ref(false)
const editForm = reactive({ item_id: '', name: '', item_type: '', color: '', gender: '' })
function openEdit(item) {
  Object.assign(editForm, { item_id: item.item_id, name: item.name, item_type: item.item_type, color: item.color, gender: item.gender })
  editVisible.value = true
}
async function submitEdit() {
  saving.value = true
  try {
    const fields = {}
    if (editForm.name) fields.name = editForm.name
    if (editForm.item_type) fields.item_type = editForm.item_type
    if (editForm.color) fields.color = editForm.color
    if (editForm.gender) fields.gender = editForm.gender
    await updateItem(userId.value, editForm.item_id, fields)
    ElMessage.success('已保存')
    editVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message)
  } finally {
    saving.value = false
  }
}

// --- 补图 ---
const photoVisible = ref(false)
const photoForm = reactive({ item_id: '', file: null })
function openPhoto(item) {
  Object.assign(photoForm, { item_id: item.item_id, file: null })
  photoVisible.value = true
}
function onPhotoFile(file) { photoForm.file = file }
async function submitPhoto() {
  if (!photoForm.file) { ElMessage.warning('请选择图片'); return }
  saving.value = true
  try {
    const b64 = await fileToBase64(photoForm.file)
    await uploadItemImage(userId.value, photoForm.item_id, photoForm.file.name, b64)
    ElMessage.success('图片已上传并生成嵌入')
    photoVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message)
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  try {
    const res = await getTaxonomy()
    taxonomy.value = res.data.categories || []
  } catch (e) {
    ElMessage.error(`加载分类失败：${e.response?.data?.detail || e.message}`)
  }
  load()
})
</script>

<style scoped>
.toolbar { display: flex; gap: 12px; margin: 12px 0; }
.mb { margin-bottom: 12px; }
.mt { margin-top: 12px; }
.item-card { margin-bottom: 12px; }
.item-img { width: 100%; height: 160px; }
.img-placeholder { height: 160px; display: flex; align-items: center; justify-content: center; color: #999; }
.item-name { font-size: 13px; margin-top: 8px; }
.item-meta { font-size: 12px; color: #888; margin-bottom: 8px; }
.actions { display: flex; gap: 4px; }
</style>
