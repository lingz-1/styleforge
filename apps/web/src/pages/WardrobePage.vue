<template>
  <div>
    <el-page-header content="我的衣柜">
      <template #extra>
        <el-button @click="openBatch">批量导入</el-button>
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

    <!-- 批量识别任务（提交后后台处理，进度与结果持久显示） -->
    <div v-if="batchTasks.length" class="mb">
      <el-card
        v-for="task in batchTasks"
        :key="task.batch_id"
        class="batch-card"
        shadow="never"
      >
        <template #header>
          <div class="batch-card-header">
            <span class="batch-title">批量识别任务</span>
            <el-tag size="small" effect="plain" :type="task.status === 'running' ? 'primary' : 'success'">
              {{ task.status === 'running' ? '识别中' : '已完成' }}
            </el-tag>
            <span class="batch-time">{{ fmtTime(task.started_at) }}</span>
          </div>
        </template>
        <template v-if="task.status === 'running'">
          <el-progress :percentage="task.percent || 0" :stroke-width="12" />
          <div class="batch-status">
            已识别 {{ task.done }}/{{ task.total }}
            · 成功 {{ task.succeeded }} · 失败 {{ task.failed }}
            · 预计剩余 {{ fmtEta(task.eta_seconds) }}
          </div>
          <div class="batch-picks">
            <template v-for="r in task.results" :key="r.index">
              <el-image
                v-if="taskFileUrl(task, r)"
                :src="taskFileUrl(task, r)"
                fit="cover"
                class="pick-img"
              />
            </template>
          </div>
        </template>
        <template v-else>
          <el-table :data="task.results" size="small">
            <el-table-column label="图片" width="72">
              <template #default="{ row }">
                <el-image
                  v-if="taskFileUrl(task, row)"
                  :src="taskFileUrl(task, row)"
                  fit="cover"
                  class="task-img"
                  :preview-src-list="[taskFileUrl(task, row)]"
                  preview-teleported
                />
                <span v-else class="no-img">无原图</span>
              </template>
            </el-table-column>
            <el-table-column prop="filename" label="文件" min-width="130" show-overflow-tooltip />
            <el-table-column label="识别结果" min-width="210">
              <template #default="{ row }">
                <template v-if="row.status === 'succeeded'">
                  <el-tag size="small" type="success">已入库</el-tag>
                  <div class="result-detail">
                    {{ vn(row.item_type) }} / {{ vn(row.subtype) || '未细分' }} · {{ vn(row.color) }}
                    <template v-if="row.confidence"> · {{ vnPct(row.confidence) }}</template>
                  </div>
                </template>
                <template v-else>
                  <el-tag size="small" type="danger">{{ reasonText(row.reason) }}</el-tag>
                  <div v-if="row.attributes?.description" class="result-detail">
                    {{ row.attributes.description }}
                  </div>
                </template>
              </template>
            </el-table-column>
            <el-table-column label="处理" min-width="200">
              <template #default="{ row }">
                <template v-if="row.status === 'failed'">
                  <template v-if="isRowDone(task, row)">
                    <el-tag size="small" type="success">已处理</el-tag>
                    <el-button size="small" text type="primary" @click="undoRow(row)">撤销</el-button>
                  </template>
                  <template v-else>
                    <el-button size="small" type="primary" plain @click="manualAdd(row, task)">
                      编辑入库
                    </el-button>
                    <el-button size="small" type="danger" plain @click="dropRow(row, task)">
                      删除
                    </el-button>
                  </template>
                </template>
              </template>
            </el-table-column>
          </el-table>
          <div class="batch-footer">
            <el-button size="small" @click="load">刷新衣柜</el-button>
            <el-button
              v-if="!hasPending(task)"
              size="small"
              type="success"
              @click="confirmTask(task)"
            >
              确认完成，删除记录
            </el-button>
          </div>
        </template>
      </el-card>
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
                {{ typeLabel(item.item_type) }} · {{ item.color }} · {{ item.gender === 'men' ? '男' : '女' }}
              </div>
              <div class="item-attrs">
                <div v-for="line in itemAttrLines(item)" :key="line" class="item-attr-line">{{ line }}</div>
              </div>
              <div class="actions">
                <el-button size="small" type="primary" plain @click="recommendForItem(item)">搭配</el-button>
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
    <el-dialog
      v-model="createVisible"
      title="上传新衣物"
      width="560px"
      @closed="batchManualRef = null"
    >
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
      <template v-if="createForm.attributes">
        <el-divider content-position="left">AI 识别属性（可编辑）</el-divider>
        <el-form label-width="80px" class="attr-form">
          <el-form-item label="季节">
            <el-select v-model="createForm.attributes.season" multiple collapse-tags style="width: 100%">
              <el-option v-for="o in SEASON_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="材质">
            <el-select v-model="createForm.attributes.material" clearable filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in MATERIAL_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="图案">
            <el-select v-model="createForm.attributes.pattern" clearable filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in PATTERN_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="正式度">
            <el-select v-model="createForm.attributes.formality" clearable style="width: 100%">
              <el-option v-for="o in FORMALITY_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="风格">
            <el-select v-model="createForm.attributes.style" multiple collapse-tags style="width: 100%">
              <el-option v-for="o in STYLE_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="场合">
            <el-select v-model="createForm.attributes.occasion" multiple collapse-tags style="width: 100%">
              <el-option v-for="o in OCCASION_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="文化渊源">
            <el-select v-model="createForm.attributes.cultural_origin" multiple collapse-tags style="width: 100%">
              <el-option v-for="o in ORIGIN_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="版型">
            <el-select v-model="createForm.attributes.silhouette" clearable filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in SILHOUETTE_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="领口">
            <el-select v-model="createForm.attributes.neckline" clearable filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in NECKLINE_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="袖长">
            <el-select v-model="createForm.attributes.sleeve_length" clearable filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in SLEEVE_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="细节">
            <el-select v-model="createForm.attributes.features" multiple filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in FEATURE_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="createForm.attributes.description" type="textarea" :rows="2" />
          </el-form-item>
        </el-form>
        <div class="mt-hint">AI 识别结果仅供参考，可直接修改后再入库。</div>
      </template>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="analyzing" @click="submitCreate">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 批量导入（提交后在衣柜顶部查看进度/结果） -->
    <el-dialog
      v-model="batchVisible"
      title="批量导入衣物（AI 自动识别）"
      width="640px"
    >
      <el-upload
        list-type="picture-card"
        multiple
        :limit="30"
        :auto-upload="false"
        accept="image/*"
        :file-list="batchFileList"
        :on-change="onBatchFile"
        :on-remove="onBatchRemove"
        :on-exceed="onBatchExceed"
      >
        <el-icon class="el-icon--upload"><plus /></el-icon>
      </el-upload>
      <el-form label-width="80px" class="mt">
        <el-form-item label="人群">
          <el-select v-model="batchGender">
            <el-option label="女" value="women" />
            <el-option label="男" value="men" />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="mt-hint">
        点击选择或拖拽多张图片（最多 30 张，悬停缩略图可删除）。提交后关闭窗口即可，
        AI 在后台逐张识别，识别成功且可信的自动加入衣柜；进度和结果请在衣柜顶部
        "批量识别任务"查看，失败项可编辑入库或删除。
      </div>
      <template #footer>
        <el-button @click="batchVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="batchSubmitting"
          :disabled="!batchFiles.length"
          @click="submitBatch"
        >
          开始批量识别
        </el-button>
      </template>
    </el-dialog>

    <!-- 编辑衣物 -->
    <el-dialog v-model="editVisible" title="编辑衣物信息" width="560px">
      <el-form :model="editForm" label-width="80px">
        <el-form-item label="名称"><el-input v-model="editForm.name" /></el-form-item>
        <el-form-item label="品类">
          <el-select v-model="editForm.item_type">
            <el-option v-for="c in taxonomy" :key="c.key" :label="c.zh" :value="c.key" />
          </el-select>
        </el-form-item>
        <el-form-item label="细分类">
          <el-select
            v-model="editForm.subtype"
            clearable
            :disabled="!editSubtypeOptions.length"
          >
            <el-option v-for="s in editSubtypeOptions" :key="s.key" :label="s.zh" :value="s.key" />
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
      <template v-if="editForm.attributes">
        <el-divider content-position="left">单品属性（可编辑）</el-divider>
        <el-form label-width="80px" class="attr-form">
          <el-form-item label="季节">
            <el-select v-model="editForm.attributes.season" multiple collapse-tags style="width: 100%">
              <el-option v-for="o in SEASON_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="材质">
            <el-select v-model="editForm.attributes.material" clearable filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in MATERIAL_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="图案">
            <el-select v-model="editForm.attributes.pattern" clearable filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in PATTERN_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="正式度">
            <el-select v-model="editForm.attributes.formality" clearable style="width: 100%">
              <el-option v-for="o in FORMALITY_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="风格">
            <el-select v-model="editForm.attributes.style" multiple collapse-tags style="width: 100%">
              <el-option v-for="o in STYLE_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="场合">
            <el-select v-model="editForm.attributes.occasion" multiple collapse-tags style="width: 100%">
              <el-option v-for="o in OCCASION_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="文化渊源">
            <el-select v-model="editForm.attributes.cultural_origin" multiple collapse-tags style="width: 100%">
              <el-option v-for="o in ORIGIN_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="版型">
            <el-select v-model="editForm.attributes.silhouette" clearable filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in SILHOUETTE_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="领口">
            <el-select v-model="editForm.attributes.neckline" clearable filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in NECKLINE_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="袖长">
            <el-select v-model="editForm.attributes.sleeve_length" clearable filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in SLEEVE_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="细节">
            <el-select v-model="editForm.attributes.features" multiple filterable allow-create default-first-option style="width: 100%">
              <el-option v-for="o in FEATURE_OPTS" :key="o.key" :label="o.label" :value="o.key" />
            </el-select>
          </el-form-item>
          <el-form-item label="描述">
            <el-input v-model="editForm.attributes.description" type="textarea" :rows="2" />
          </el-form-item>
        </el-form>
      </template>
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
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Loading, UploadFilled, Plus } from '@element-plus/icons-vue'
import {
  getWardrobe, removeWardrobeItem, createPhotoItem, analyzeItem, getTaxonomy,
  updateItem, uploadItemImage, imageUrl,
  startBatchRecognition, listBatchRecognition, deleteBatchRecognition,
} from '../services/api'
import { getUserId, setUserId } from '../services/user'

const router = useRouter()

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
  // neckline
  round: '圆领', 'v-neck': 'V领', collar: '翻领', stand: '立领', turtleneck: '高领',
  boat: '一字领', 'off-shoulder': '露肩', square: '方领', 'square-neck': '方领',
  // sleeve length
  sleeveless: '无袖', short: '短袖', elbow: '五分袖', 'three-quarter': '七分袖',
  long: '长袖', cap: '盖袖',
}
const vn = (token) => (token ? (VN[token] || token) : '')
const vnList = (arr) => (Array.isArray(arr) ? arr.map(vn).filter(Boolean).join('、') : '')
const vnPct = (n) => (typeof n === 'number' ? `${Math.round(n * 100)}%` : '')

// 可编辑属性字段的候选值（label 用中文词表，未知 token 原样返回）
const vnOptions = (keys) => keys.map((key) => ({ key, label: VN[key] || key }))
const SEASON_OPTS = vnOptions(['spring', 'summer', 'fall', 'winter', 'all-season'])
const MATERIAL_OPTS = vnOptions([
  'cotton', 'denim', 'leather', 'wool', 'polyester', 'silk', 'linen', 'knit',
  'fleece', 'suede', 'velvet', 'nylon', 'canvas', 'cashmere', 'chiffon', 'satin',
  'tulle', 'lace', 'twill', 'corduroy', 'down', 'wool-blend',
])
const PATTERN_OPTS = vnOptions([
  'solid', 'striped', 'plaid', 'checkered', 'floral', 'graphic', 'geometric',
  'polka-dot', 'camouflage', 'animal-print', 'paisley',
])
const FORMALITY_OPTS = vnOptions(['very-casual', 'casual', 'smart-casual', 'business-casual', 'formal'])
const STYLE_OPTS = vnOptions([
  'classic', 'sporty', 'minimalist', 'bohemian', 'preppy', 'streetwear', 'elegant',
  'athletic', 'vintage', 'modern', 'rugged', 'chic', 'romantic',
])
const OCCASION_OPTS = vnOptions([
  'daily', 'work', 'party', 'outdoor', 'sports', 'travel', 'ceremony',
  'date-night', 'home', 'school',
])
const ORIGIN_OPTS = vnOptions([
  'none', 'hanfu', 'qipao-cheongsam', 'tangzhuang', 'ma-mian-skirt', 'ethnic-chinese',
  'kimono', 'hanbok', 'sari', 'kilt', 'poncho', 'middle-eastern', 'traditional-indian',
  'african', 'native-american', 'nordic', 'victorian', 'western-cowboy', 'military',
  'gothic', 'punk', 'vintage-retro',
])
const SILHOUETTE_OPTS = vnOptions([
  'slim', 'regular', 'relaxed', 'oversized', 'tailored', 'cropped', 'A-line',
  'fitted', 'loose', 'boxy', 'bodycon', 'straight', 'flared',
])
const NECKLINE_OPTS = vnOptions([
  'round', 'v-neck', 'collar', 'stand', 'turtleneck', 'boat', 'off-shoulder', 'square',
])
const SLEEVE_OPTS = vnOptions([
  'sleeveless', 'short', 'elbow', 'three-quarter', 'long', 'cap',
])
const FEATURE_OPTS = [] // 细节自由创建，allow-create

// 确保 attributes 中各字段类型正确（数组字段至少为空数组），供表单 v-model 使用
function ensureAttrDefaults(attrs) {
  const a = { ...(attrs || {}) }
  for (const k of ['season', 'style', 'occasion', 'cultural_origin', 'features']) {
    if (!Array.isArray(a[k])) a[k] = a[k] ? [a[k]] : []
  }
  for (const k of ['material', 'pattern', 'formality', 'silhouette', 'neckline', 'sleeve_length']) {
    if (typeof a[k] !== 'string') a[k] = ''
  }
  if (typeof a.description !== 'string') a.description = ''
  return a
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

function onUserIdChange(value) {
  setUserId(value)
  loadBatches()
}

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

// 衣柜点选单品 → 直达单品搭配：跳到推荐页并以该件为锚点自动发起 item_advice
function recommendForItem(item) {
  const label = item.name
    || [typeLabel(item.item_type), item.color].filter(Boolean).join('·')
    || item.item_id
  router.push({ path: '/recommend', query: { item_id: item.item_id, label } })
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

// 衣柜卡片：完整显示单品属性（逐行）
function itemAttrLines(item) {
  const a = item.attributes || {}
  const lines = []
  if (a.season?.length) lines.push(`季节：${vnList(a.season)}`)
  if (a.material) lines.push(`材质：${vn(a.material)}`)
  if (a.pattern) lines.push(`图案：${vn(a.pattern)}`)
  if (a.formality) lines.push(`正式度：${vn(a.formality)}`)
  if (a.style?.length) lines.push(`风格：${vnList(a.style)}`)
  if (a.occasion?.length) lines.push(`场合：${vnList(a.occasion)}`)
  if (a.cultural_origin?.length) lines.push(`文化：${vnList(a.cultural_origin)}`)
  if (a.silhouette) lines.push(`版型：${vn(a.silhouette)}`)
  if (a.neckline || a.sleeve_length) {
    lines.push(`领口/袖长：${[vn(a.neckline), vn(a.sleeve_length)].filter(Boolean).join(' / ')}`)
  }
  if (a.features?.length) lines.push(`细节：${vnList(a.features)}`)
  if (a.description) lines.push(`描述：${a.description}`)
  return lines
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
  batchManualRef.value = null
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
      if (data.recognized) {
        createForm.attributes = ensureAttrDefaults(data.attributes)
        if (data.item_type && !createForm.item_type) createForm.item_type = data.item_type
        if (data.subtype && !createForm.subtype) createForm.subtype = data.subtype
        if (data.color && !createForm.color) createForm.color = data.color
        if (data.name && !createForm.name) createForm.name = data.name
      } else {
        ElMessage.info('AI 识别未成功，请手动填写衣物属性')
      }
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
    if (batchManualRef.value) {
      const { task, index } = batchManualRef.value
      batchManualRef.value = null
      markRowDone(task, index)
    }
    createVisible.value = false
    load()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message)
  } finally {
    saving.value = false
  }
}

// --- 批量导入识别（提交后后台处理，进度与结果在衣柜顶部常驻显示） ---
const batchVisible = ref(false)
const batchFiles = ref([]) // 原始 File[]，索引与后端 results 的 index 对应
const batchGender = ref('women')
const batchSubmitting = ref(false)
// 历史任务列表；每项为后端 snapshot + _files（手动添加回填用）
const batchTasks = ref([])
const batchPollTimer = ref(null)
const batchPolling = ref(false)

// 本地文件缩略图 URL（缓存到文件对象上，避免重复 createObjectURL）
function previewUrl(file) {
  if (!file) return ''
  if (file.__preview) return file.__preview
  return (file.__preview = URL.createObjectURL(file))
}
const batchFileList = computed(() =>
  batchFiles.value.map((file, index) => ({
    name: file.name, uid: index, raw: file, url: previewUrl(file),
  })),
)
// 任务结果表格的图片列：优先取任务保留的原始文件，无则显示占位
function taskFileUrl(task, row) {
  return previewUrl(task._files && task._files[row.index])
}

const REASON_TEXT = {
  low_confidence: '识别不可信', vision_unavailable: '识别服务不可用',
  invalid_json: '识别结果异常', invalid_image: '图片无效',
  create_failed: '入库失败', provider_unavailable: '识别服务不可用',
  internal_error: '处理异常',
}
const reasonText = (reason) => REASON_TEXT[reason] || reason

function fmtEta(eta) {
  if (eta <= 1) return '即将完成'
  if (eta < 60) return `${eta} 秒`
  const minutes = Math.floor(eta / 60)
  const seconds = eta % 60
  return `${minutes} 分 ${seconds} 秒`
}

function fmtTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

function openBatch() {
  batchFiles.value = []
  batchGender.value = 'women'
  batchVisible.value = true
}
function onBatchFile(file) {
  if (!batchFiles.value.some((f) => f.name === file.name)) {
    batchFiles.value.push(file.raw || file)
  }
}
function onBatchRemove(file) {
  batchFiles.value = batchFiles.value.filter((f) => f.name !== file.name)
}
function onBatchExceed() {
  ElMessage.warning('最多一次选择 30 张图片')
}

async function submitBatch() {
  if (!batchFiles.value.length) {
    ElMessage.warning('请先选择图片')
    return
  }
  batchSubmitting.value = true
  try {
    const base64s = await Promise.all(batchFiles.value.map((f) => fileToBase64(f)))
    const images = batchFiles.value.map((file, index) => ({
      filename: file.name,
      content_base64: base64s[index],
    }))
    const res = await startBatchRecognition(userId.value, images, batchGender.value)
    // 提交成功即关闭对话框，后台继续识别；进度/结果常驻在衣柜顶部任务卡片
    batchTasks.value.unshift({ ...res.data, _files: [...batchFiles.value] })
    batchFiles.value = []
    batchVisible.value = false
    ElMessage.success('已提交，AI 在后台识别，可在衣柜顶部查看进度')
    startBatchPolling()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message)
  } finally {
    batchSubmitting.value = false
  }
}

// 将后端任务列表合并进本地：保留原图引用 _files 与失败项处理标记 _done
function mergeBatchTasks(list) {
  const prevById = new Map(batchTasks.value.map((t) => [t.batch_id, t]))
  batchTasks.value = list.map((b) => {
    const prev = prevById.get(b.batch_id)
    const results = (b.results || []).map((r) => {
      const pRow = prev && prev.results && prev.results[r.index]
      return pRow && pRow._done ? { ...r, _done: true } : r
    })
    return { ...b, _files: prev ? prev._files : [], results }
  })
}

// 拉取历史任务列表；合并本地状态，并按有无运行中任务启停轮询
async function loadBatches() {
  try {
    const res = await listBatchRecognition(userId.value, 50)
    mergeBatchTasks(res.data.batches || [])
    const hasRunning = batchTasks.value.some((t) => t.status === 'running')
    if (hasRunning) startBatchPolling()
    else stopBatchPolling()
  } catch (e) {
    // 静默失败：批次列表加载失败不影响衣柜主流程，下次刷新再试
  }
}

function startBatchPolling() {
  stopBatchPolling()
  batchPollTimer.value = setInterval(pollBatches, 2000)
}
function stopBatchPolling() {
  if (batchPollTimer.value) {
    clearInterval(batchPollTimer.value)
    batchPollTimer.value = null
  }
}
async function pollBatches() {
  if (batchPolling.value) return
  batchPolling.value = true
  try {
    const res = await listBatchRecognition(userId.value, 50)
    mergeBatchTasks(res.data.batches || [])
    const hasRunning = batchTasks.value.some((t) => t.status === 'running')
    if (!hasRunning) stopBatchPolling()
  } catch (e) {
    // 网络抖动：跳过本次轮询，下次再试
  } finally {
    batchPolling.value = false
  }
}

// 失败项处理状态：isRowDone / markRowDone / hasPending（本地标记，仅影响展示）
function isRowDone(task, row) { return !!row._done }
function markRowDone(task, index) {
  const row = task.results && task.results[index]
  if (row) row._done = true
}
function hasPending(task) {
  return (task.results || []).some((r) => r.status === 'failed' && !r._done)
}

// 从"手动添加"打开的创建对话框来源；提交成功时消费它
const batchManualRef = ref(null) // { task, index }

// 删除该单品：不入库，标记为已处理
function dropRow(row, task) {
  markRowDone(task, row.index)
  ElMessage.success('已删除该单品（不入库）')
}

// 撤销处理：恢复为未处理，可重新编辑入库或删除
function undoRow(row) {
  row._done = false
}

// 所有失败项处理完后，用户确认：删除整条导入记录
async function confirmTask(task) {
  try {
    await deleteBatchRecognition(userId.value, task.batch_id)
    batchTasks.value = batchTasks.value.filter((t) => t.batch_id !== task.batch_id)
    ElMessage.success('已删除该导入记录')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || e.message)
  }
}

// 失败项手动补录：回填原图与识别属性到单图创建对话框
function manualAdd(row, task) {
  const file = (task._files && task._files[row.index]) || null
  openCreate()
  createForm.file = file
  createForm.attributes = ensureAttrDefaults(row.attributes)
  if (row.attributes && (row.item_type || row.attributes.item_type)) {
    if (row.item_type && !createForm.item_type) createForm.item_type = row.item_type
    if (row.subtype && !createForm.subtype) createForm.subtype = row.subtype
    if (row.color && !createForm.color) createForm.color = row.color
    if (row.name && !createForm.name) createForm.name = row.name
  } else if (file) {
    onCreateFile(file) // 无属性：走单图自动识别预填
  }
  // 记录来源：创建对话框提交成功后把该项标记为已处理
  batchManualRef.value = { task, index: row.index }
}

function revokeBatchUrls() {
  for (const task of batchTasks.value) {
    for (const file of task._files || []) {
      if (file.__preview) {
        URL.revokeObjectURL(file.__preview)
        file.__preview = null
      }
    }
  }
}
onBeforeUnmount(() => {
  stopBatchPolling()
  revokeBatchUrls()
})

// --- 编辑 ---
const editVisible = ref(false)
const editForm = reactive({
  item_id: '', name: '', item_type: '', subtype: '', color: '', gender: '',
  attributes: null,
})
const editSubtypeOptions = computed(() => {
  const cat = taxonomy.value.find((c) => c.key === editForm.item_type)
  return cat ? cat.subtypes : []
})
function openEdit(item) {
  Object.assign(editForm, {
    item_id: item.item_id,
    name: item.name,
    item_type: item.item_type,
    subtype: item.subtype || '',
    color: item.color,
    gender: item.gender || 'women',
    attributes: ensureAttrDefaults(item.attributes),
  })
  editVisible.value = true
}
async function submitEdit() {
  saving.value = true
  try {
    const fields = {}
    if (editForm.name) fields.name = editForm.name
    if (editForm.item_type) fields.item_type = editForm.item_type
    if (editForm.subtype) fields.subtype = editForm.subtype
    if (editForm.color) fields.color = editForm.color
    if (editForm.gender) fields.gender = editForm.gender
    if (editForm.attributes) fields.attributes = editForm.attributes
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
  loadBatches()
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
.item-meta { font-size: 12px; color: #888; margin-bottom: 4px; }
.item-attrs { font-size: 12px; color: #777; margin-bottom: 8px; }
.item-attr-line { line-height: 1.5; }
.attr-form { margin-top: 4px; }
.actions { display: flex; gap: 4px; }
.analyzing-hint {
  display: flex; align-items: center; gap: 8px;
  margin-top: 10px; padding: 8px 12px;
  background: #f4f8ff; border-radius: 6px; color: #409eff; font-size: 13px;
}
.attr-panel { margin-top: 8px; }
.mt-hint { margin-top: 8px; font-size: 12px; color: #999; }
.batch-status { margin-top: 12px; font-size: 13px; color: #555; }
.result-detail { margin-top: 4px; font-size: 12px; color: #888; line-height: 1.4; }
.batch-card { margin-bottom: 12px; }
.batch-card-header { display: flex; align-items: center; gap: 10px; }
.batch-title { font-weight: 600; }
.batch-time { font-size: 12px; color: #999; margin-left: auto; }
.task-img { width: 48px; height: 48px; border-radius: 4px; }
.no-img { font-size: 12px; color: #bbb; }
.batch-picks { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.pick-img { width: 48px; height: 48px; border-radius: 4px; }
.batch-footer { display: flex; gap: 8px; margin-top: 12px; }
</style>
