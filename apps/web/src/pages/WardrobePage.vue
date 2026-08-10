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
              <div class="item-meta">
                {{ item.item_type }} · {{ item.color }}
                <template v-if="attrText(item)"> · {{ attrText(item) }}</template>
              </div>
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
    <el-dialog v-model="createVisible" title="上传新衣物" width="560px">
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept="image/*"
        :on-change="onCreateFile"
        :on-remove="onRemoveCreateFile"
      >
        <el-icon class="el-icon--upload"><upload-filled /></el-icon>
        <div class="el-upload__text">拖拽图片到此处，或 <em>点击选择</em></div>
      </el-upload>
      <div v-if="analyzing" class="analyzing-hint">
        <el-icon class="is-loading"><loading /></el-icon>
        AI 正在识别图片属性…
      </div>
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
      <template v-if="createForm.attributes && Object.keys(createForm.attributes).length">
        <el-divider content-position="left">AI 识别属性</el-divider>
        <el-descriptions :column="1" size="small" border class="attr-panel">
          <el-descriptions-item v-for="row in attrRows" :key="row.label" :label="row.label">
            {{ row.value }}
          </el-descriptions-item>
        </el-descriptions>
        <div class="mt-hint">识别结果仅供参考，可在上方修改后确认入库。</div>
      </template>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="analyzing" @click="submitCreate">
          创建
        </el-button>
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
import { Loading, UploadFilled } from '@element-plus/icons-vue'
import {
  getWardrobe, removeWardrobeItem, createPhotoItem, analyzeItem, getTaxonomy,
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

// AI 识别词表 → 中文（模型输出英文 token，这里仅用于展示）
const VN = {
  // season
  spring: '春季', summer: '夏季', fall: '秋季', winter: '冬季', 'all-season': '四季通用',
  // material
  cotton: '棉', denim: '牛仔', leather: '皮革', wool: '羊毛', polyester: '涤纶',
  silk: '丝绸', linen: '亚麻', knit: '针织', fleece: '抓绒', suede: '麂皮',
  velvet: '天鹅绒', nylon: '尼龙', canvas: '帆布', cashmere: '羊绒', chiffon: '雪纺',
  satin: '缎面', tulle: '薄纱', lace: '蕾丝', twill: '斜纹', corduroy: '灯芯绒',
  down: '羽绒', 'wool-blend': '羊毛混纺',
  // pattern
  solid: '纯色', striped: '条纹', plaid: '格纹', checkered: '棋盘格', floral: '碎花',
  graphic: '图案', geometric: '几何', 'polka-dot': '波点', camouflage: '迷彩',
  'animal-print': '动物纹', paisley: '佩斯利',
  // formality
  'very-casual': '非常休闲', casual: '休闲', 'smart-casual': '半正式',
  'business-casual': '商务休闲', formal: '正式',
  // style
  classic: '经典', sporty: '运动', minimalist: '极简', bohemian: '波西米亚',
  preppy: '学院', streetwear: '街头', elegant: '优雅', athletic: '运动',
  vintage: '复古', modern: '现代', rugged: '粗犷', chic: '时髦', romantic: '浪漫',
  // fit / silhouette
  slim: '修身', regular: '常规', relaxed: '宽松', oversized: '超大', tailored: '合体剪裁',
  cropped: '短款', 'A-line': 'A字', fitted: '修身', loose: '宽松', boxy: '方正',
  bodycon: '包身', straight: '直筒', flared: '喇叭',
  // occasion
  daily: '日常', work: '工作', party: '派对', outdoor: '户外', sports: '运动',
  travel: '旅行', ceremony: '典礼', 'date-night': '约会', home: '居家', school: '校园',
  // cultural origin
  none: '无', hanfu: '汉服', 'qipao-cheongsam': '旗袍', tangzhuang: '唐装',
  'ma-mian-skirt': '马面裙', 'ethnic-chinese': '中国民族服饰', kimono: '和服',
  hanbok: '韩服', sari: '纱丽', kilt: '苏格兰裙', poncho: '斗篷',
  'middle-eastern': '中东服饰', 'traditional-indian': '印度传统', african: '非洲民族服饰',
  'native-american': '美洲原住民', nordic: '北欧传统', victorian: '维多利亚',
  'western-cowboy': '西部牛仔', military: '军旅', gothic: '哥特', punk: '朋克',
  'vintage-retro': '复古',
}
const vn = (token) => (token ? (VN[token] || token) : '')
const vnList = (arr) => (Array.isArray(arr) ? arr.map(vn).filter(Boolean).join('、') : '')
const vnPct = (n) => (typeof n === 'number' ? `${Math.round(n * 100)}%` : '')

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
const analyzing = ref(false)
const createForm = reactive({
  name: '', item_type: '', subtype: '', color: '', gender: 'women', file: null,
  attributes: null,
})
const subtypeOptions = computed(() => {
  const cat = taxonomy.value.find((c) => c.key === createForm.item_type)
  return cat ? cat.subtypes : []
})

// 识别属性只读面板：从 attributes 生成展示行
const attrRows = computed(() => {
  const a = createForm.attributes || {}
  const rows = []
  if (a.item_type) rows.push({ label: '品类', value: `${vn(a.item_type)} / ${vn(a.subtype) || '未细分'}` })
  if (a.primary_color) rows.push({ label: '主色', value: vn(a.primary_color) })
  if (a.season?.length) rows.push({ label: '季节', value: vnList(a.season) })
  if (a.material) rows.push({ label: '材质', value: vn(a.material) })
  if (a.pattern) rows.push({ label: '图案', value: vn(a.pattern) })
  if (a.style?.length) rows.push({ label: '风格', value: vnList(a.style) })
  if (a.formality) rows.push({ label: '正式度', value: vn(a.formality) })
  if (a.occasion?.length) rows.push({ label: '场合', value: vnList(a.occasion) })
  if (a.cultural_origin?.length) rows.push({ label: '文化渊源', value: vnList(a.cultural_origin) })
  if (a.silhouette) rows.push({ label: '版型', value: vn(a.silhouette) })
  if (a.neckline || a.sleeve_length) {
    rows.push({ label: '领口/袖长', value: [vn(a.neckline), vn(a.sleeve_length)].filter(Boolean).join(' / ') })
  }
  if (a.features?.length) rows.push({ label: '细节', value: vnList(a.features) })
  if (a.description) rows.push({ label: 'AI 描述', value: a.description })
  if (typeof a.confidence === 'number') rows.push({ label: '置信度', value: vnPct(a.confidence) })
  return rows
})

// 衣柜卡片：追加季节/材质摘要
function attrText(item) {
  const a = item.attributes || {}
  const parts = []
  if (a.season?.length) parts.push(vnList(a.season))
  if (a.material) parts.push(vn(a.material))
  return parts.join(' · ')
}

function openCreate() {
  createForm.name = ''
  createForm.item_type = ''
  createForm.subtype = ''
  createForm.color = ''
  createForm.gender = 'women'
  createForm.file = null
  createForm.attributes = null
  analyzing.value = false
  createVisible.value = true
}
async function onCreateFile(file) {
  createForm.file = file
  createForm.attributes = null
  const b64 = await fileToBase64(file)
  if (!b64) return
  analyzing.value = true
  try {
    const res = await analyzeItem(userId.value, file.name, b64)
    const data = res.data
    if (data && data.status === 'available' && data.attributes) {
      createForm.attributes = data.attributes
      if (data.item_type && !createForm.item_type) createForm.item_type = data.item_type
      if (data.subtype && !createForm.subtype) createForm.subtype = data.subtype
      if (data.color && !createForm.color) createForm.color = data.color
      if (data.name && !createForm.name) createForm.name = data.name
    }
  } catch (e) {
    // 识别失败降级为手动填写，不阻塞上传
    if (e.response?.status !== 503 && e.response?.status !== 502) {
      ElMessage.warning(`AI 识别失败（已降级为手动填写）：${e.response?.data?.detail || e.message}`)
    } else {
      ElMessage.warning('AI 识别暂不可用，请手动填写属性')
    }
  } finally {
    analyzing.value = false
  }
}
function onRemoveCreateFile() {
  createForm.file = null
  createForm.attributes = null
}
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
      attributes: createForm.attributes,
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
.analyzing-hint {
  display: flex; align-items: center; gap: 8px;
  margin-top: 10px; padding: 8px 12px;
  background: #f4f8ff; border-radius: 6px; color: #409eff; font-size: 13px;
}
.attr-panel { margin-top: 8px; }
.mt-hint { margin-top: 8px; font-size: 12px; color: #999; }
</style>
