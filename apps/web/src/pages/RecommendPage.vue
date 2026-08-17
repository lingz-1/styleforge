<template>
  <main class="studio">
    <div class="chat-layout">
      <aside class="session-sidebar">
        <el-button type="primary" size="small" class="new-session" @click="newSession">
          ＋ 新建对话
        </el-button>
        <div
          v-for="session in sessions"
          :key="session.session_id"
          class="session-item"
          :class="{ active: session.session_id === store.sessionId }"
          @click="selectSession(session)"
        >
          <div class="session-line">
            <strong>{{ session.title }}</strong>
            <span class="session-del" title="删除会话" @click.stop="removeSession(session)">🗑</span>
          </div>
          <small>{{ session.last_message?.content || '空会话' }}</small>
        </div>
        <p v-if="!sessions.length" class="session-empty">还没有会话。发送第一条消息即可自动创建并保存。</p>
      </aside>

      <div class="chat-main">
        <section class="hero">
          <div>
            <p class="eyebrow">STYLEFORGE · WARDROBE STUDIO</p>
            <h1>把需求交给三位造型 Agent</h1>
            <p class="lead">说出你想穿什么、想改哪里，或想了解衣橱还缺什么。同一会话内的追问会自动携带上文。</p>
          </div>
          <div class="user-box">
            <span>当前衣橱</span>
            <el-input v-model="userId" aria-label="用户 ID" @change="onUserIdChange" />
          </div>
        </section>

        <WeatherCard />

        <section class="anchor-bar">
          <el-button type="primary" plain size="large" :loading="loading" @click="openPickItem">
            🧥 从衣柜选一件单品搭配
          </el-button>
          <span class="anchor-hint">选择衣柜内任意单品，三位 Agent 会以它为锚点生成整套搭配</span>
        </section>

        <div v-if="activeOutfit || selectedItemId" class="grounding-bar">
          <span class="grounding-label">已定位</span>
          <span v-if="activeOutfit">在「{{ activeOutfit.outfit_id }}」这套的基础上修改</span>
          <span v-if="selectedItemId">单品「{{ selectedItemId }}」（仅消歧辅助）</span>
          <el-button size="small" text type="primary" @click="clearGrounding">清除定位</el-button>
        </div>

        <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="block" />

        <section v-if="store.messages.length" class="chat-history" aria-label="会话记录">
          <div
            v-for="(message, index) in store.messages"
            :key="index"
            class="chat-turn"
            :class="message.role"
          >
            <div v-if="message.role === 'user'" class="chat-bubble user">{{ message.content }}</div>
            <template v-else>
              <div class="chat-bubble assistant" :class="{ failed: message.failed }">
                <span v-if="message.failed" class="failed-tag">执行失败</span>{{ message.content }}
              </div>
              <template v-if="message.payload && !message.failed">
                <section
                  v-if="index === store.messages.length - 1 && hasTrace(message.payload)"
                  class="agent-rail block"
                  aria-label="三 Agent 执行轨迹"
                >
                  <div v-for="(agent, i) in agentRail(message.payload)" :key="agent.node" class="agent-step">
                    <span class="step-index">0{{ i + 1 }}</span>
                    <div><strong>{{ agent.name }}</strong><small>{{ agent.detail }}</small></div>
                    <span class="step-status">{{ agent.done ? '完成' : '未执行' }}</span>
                  </div>
                </section>
                <TaskResultView
                  :payload="message.payload"
                  :user-id="userId"
                  @select-item="onSelectItem"
                  @modify-here="onModifyHere"
                />
              </template>
            </template>
          </div>
          <div v-if="loading" class="chat-turn assistant">
            <div class="chat-bubble assistant typing">三位 Agent 正在处理…</div>
          </div>
        </section>

        <section class="prompt-card">
          <el-input
            ref="promptInput"
            v-model="request"
            type="textarea"
            :rows="4"
            resize="none"
            placeholder="例如：黑色马甲怎么搭？ / 帮我换一件外套 / 更正式一点"
            @keydown.ctrl.enter="run"
            @keydown.meta.enter="run"
          />
          <div class="location-bar">
            <el-button size="small" :loading="locationState.status === 'locating'" @click="useDeviceLocation">
              {{ deviceLocation ? '更新定位' : '使用当前定位' }}
            </el-button>
            <span v-if="locationState.message" :class="['location-msg', locationState.status]">
              {{ locationState.message }}
            </span>
            <span v-else-if="permissionState === 'granted'" class="location-msg ready">
              已启用自动定位，每次提问将使用最新位置
            </span>
          </div>
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

        <el-dialog v-model="pickVisible" title="选择一件单品作为搭配锚点" width="760px" top="6vh">
          <div class="pick-header">
            <el-input v-model="pickKeyword" placeholder="搜索名称 / 品类 / 颜色" clearable />
          </div>
          <div v-if="pickLoading" class="pick-empty">正在加载衣柜…</div>
          <div v-else-if="!filteredItems.length" class="pick-empty">
            {{ pickKeyword ? '没有匹配的单品' : '衣橱还是空的，先到「我的衣橱」添加单品' }}
          </div>
          <div v-else class="pick-grid">
            <button
              v-for="item in filteredItems"
              :key="item.item_id"
              type="button"
              class="pick-card"
              @click="pickAndRun(item)"
            >
              <el-image :src="imageUrl(item.image_url)" fit="cover" class="pick-img">
                <template #error><div class="pick-ph">无图</div></template>
              </el-image>
              <div class="pick-meta">
                <strong>{{ item.name || typeLabel(item.item_type) }}</strong>
                <small>{{ typeLabel(item.item_type) }} · {{ item.color || '—' }}</small>
              </div>
            </button>
          </div>
        </el-dialog>
      </div>
    </div>
  </main>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { storeToRefs } from 'pinia'
import WeatherCard from '../components/WeatherCard.vue'
import TaskResultView from '../components/TaskResultView.vue'
import { createChatSession, listChatSessions, deleteChatSession, getWardrobe, imageUrl } from '../services/api'
import { useRecommendationStore } from '../stores/recommendation'
import { getUserId, setUserId } from '../services/user'

const EXAMPLES = ['黑色马甲怎么搭？', '帮我换一件外套，其他保持不变。', '更正式一点。']
const AGENTS = [
  { node: 'semantic_retriever_agent', aliases: ['semantic_retriever'], name: 'Agent 1 · 语义检索', detail: '理解意图，读取衣橱与知识事实' },
  { node: 'composer_agent', aliases: ['composer'], name: 'Agent 2 · 方案生成', detail: '基于候选范围完成业务结果' },
  { node: 'critic_agent', aliases: ['critic'], name: 'Agent 3 · 审校决策', detail: '检查依据、边界与最终可用性' },
]

const userId = ref(getUserId())
const request = ref('黑色马甲怎么搭？')
const store = useRecommendationStore()
const { loading, error } = storeToRefs(store)
const route = useRoute()
const router = useRouter()

// --- Stage 1: InteractionContext grounding ---
// 点击单品 = 只设 selected_item_id（消歧辅助，不产生操作）；"在此基础上修改"
// 设 active_outfit（会话内定位）。两者都只是帮 Agent 消歧，用户随后输入文字。
const promptInput = ref(null)
const activeOutfit = ref(null) // { outfit_id, item_ids, ... }，保留到清除或换会话
const selectedItemId = ref('') // 一次性消歧，提交后即清

function onSelectItem(itemId) {
  selectedItemId.value = itemId
  promptInput.value?.focus()
}

function onModifyHere(outfit) {
  activeOutfit.value = outfit
  promptInput.value?.focus()
}

function clearGrounding() {
  activeOutfit.value = null
  selectedItemId.value = ''
}

// --- 首页「从衣柜选单品搭配」：以衣橱内某件单品为锚点直达 item_advice ---
const TYPE_LABELS = {
  top: '上装', pants: '裤装', skirt: '半身裙', dress: '连衣裙', jumpsuit: '连体装',
  outwear: '外套', shoes: '鞋', bag: '包', accessory: '配饰', other: '其他',
}
const pickVisible = ref(false)
const pickLoading = ref(false)
const pickKeyword = ref('')
const wardrobeItems = ref([])
const loadedForUser = ref('')
const typeLabel = (type) => TYPE_LABELS[type] || type

const filteredItems = computed(() => {
  const kw = pickKeyword.value.trim().toLowerCase()
  if (!kw) return wardrobeItems.value
  return wardrobeItems.value.filter((item) => {
    const hay = [item.name, item.item_type, typeLabel(item.item_type), item.color]
      .filter(Boolean).join(' ').toLowerCase()
    return hay.includes(kw)
  })
})

async function openPickItem() {
  pickKeyword.value = ''
  if (loadedForUser.value !== userId.value) {
    loadedForUser.value = userId.value
    wardrobeItems.value = []
    pickLoading.value = true
    try {
      const res = await getWardrobe(userId.value)
      wardrobeItems.value = res.data.items || []
    } catch (e) {
      error.value = e.response?.data?.detail || e.message
    } finally {
      pickLoading.value = false
    }
  }
  pickVisible.value = true
}

function pickAndRun(item) {
  pickVisible.value = false
  const label = item.name
    || [typeLabel(item.item_type), item.color].filter(Boolean).join('·')
    || item.item_id
  run({ itemId: item.item_id, label })
}

// --- Multi-turn chat sessions (persisted per user, restored on reload) ---
const sessions = ref([])
const sessionStorageKey = () => `${userId.value}:sf_session_id`

async function refreshSessions() {
  try {
    const res = await listChatSessions(userId.value)
    sessions.value = res.data.sessions || []
  } catch {
    sessions.value = []
  }
}

async function selectSession(session) {
  localStorage.setItem(sessionStorageKey(), session.session_id)
  try {
    await store.loadSessionHistory(userId.value, session)
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
  void refreshSessions()
}

function newSession() {
  localStorage.removeItem(sessionStorageKey())
  store.newConversation()
  request.value = ''
  clearGrounding()
}

async function removeSession(session) {
  if (!window.confirm(`删除会话「${session.title}」？其中的消息也会一并删除。`)) return
  try {
    await deleteChatSession(userId.value, session.session_id)
    if (store.sessionId === session.session_id) {
      localStorage.removeItem(sessionStorageKey())
      store.newConversation()
    }
    await refreshSessions()
  } catch (e) {
    error.value = e.response?.data?.detail || e.message
  }
}

function hasTrace(payload) {
  return !!(payload?.trace?.length || payload?.result?.trace?.length)
}

function agentRail(payload) {
  const trace = payload?.trace || []
  const nested = payload?.result?.trace || []
  const nodes = new Set([...trace, ...nested].map((item) => item.node))
  return AGENTS.map((agent) => ({ ...agent, done: nodes.has(agent.node) || agent.aliases.some((name) => nodes.has(name)) }))
}

async function run({ itemId = '', label = '' } = {}) {
  // 衣柜点选单品直达：以该件为锚点构造请求文本，其余复用会话与提交逻辑。
  let requestText = request.value.trim()
  if (itemId) {
    requestText = `以「${label || '这件单品'}」为锚点，搭配一整套`
    request.value = requestText
  }
  if (!requestText || loading.value) return
  // 已授予「使用期间允许」定位权限时，每次新提问都静默取一次最新位置；
  // 未授权不打扰（不实时定位，明确目的地仍由后端按地点名解析天气）。
  await refreshLocationIfGranted()
  // 首次提问自动创建并保存会话，之后追问复用同一 session_id 恢复上文。
  let sid = store.sessionId
  if (!sid) {
    try {
      const res = await createChatSession(userId.value)
      sid = res.data.session_id
      store.sessionId = sid
      localStorage.setItem(sessionStorageKey(), sid)
    } catch {
      sid = '' // 降级为无会话执行（不落库）
    }
  }
  await store.run(userId.value, requestText, 3, deviceLocation.value, sid, {
    itemId,
    requestedTaskType: itemId ? 'item_advice' : undefined,
    // Stage 1 请求通道：active_outfit_id 复用后端 current_outfit_id 字段，
    // selected_item_id 为新增字段。两者都由 Agent 端消费，不影响旧路由。
    activeOutfitId: activeOutfit.value?.outfit_id || '',
    selectedItemId: selectedItemId.value,
  })
  // Stage 4 多轮 grounding：修改成功后把最新候选设为 active outfit，下一轮
  // 的 current_outfit_id 指向新候选（修复多轮修改回退到最初套的问题）。
  const latest = store.payload?.result
  if (latest?.status === 'completed' && latest.alternatives?.[0]) {
    activeOutfit.value = latest.alternatives[0]
  }
  request.value = ''
  selectedItemId.value = '' // 本次消歧已消费，单击定位只对下一次输入生效
  void refreshSessions()
}

function onUserIdChange(value) {
  setUserId(value)
  localStorage.removeItem(sessionStorageKey())
  store.newConversation()
  request.value = ''
  clearGrounding()
  void refreshSessions()
}

// --- V2.2: 定位授权交互（设备定位 → DeviceLocationContext） ---
// 设备坐标只在请求内使用：后端 Location Resolver 会把坐标取整并保证
// 坐标永不进入 payloads/traces/persistence。
//
// 自动定位规则：
// - permissionState === 'granted'（用户已授予「使用期间允许」）：每次新提问
//   前静默 getCurrentPosition 取一次最新位置，不再需要手动点按钮。
// - permissionState === 'prompt' / 'denied' / 'unsupported'：不实时定位，
//   不打扰用户；「使用当前定位」按钮仅用于首次授权或手动刷新。
// - 未授权时不传坐标，但请求里写明目的地（如「去北京出差」）时，后端
//   Location Resolver 的 explicit 分支仍会用地点名解析天气。
const NETWORK_LOCATION_FALLBACK = import.meta.env.VITE_ENABLE_IP_LOCATION_FALLBACK === 'true'
const locationState = ref({ status: 'idle', message: '' })
const deviceLocation = ref(null)
const permissionState = ref('unknown') // granted | prompt | denied | unsupported | unknown

async function checkLocationPermission() {
  if (!navigator.permissions?.query) {
    permissionState.value = 'unsupported'
    return
  }
  try {
    const status = await navigator.permissions.query({ name: 'geolocation' })
    permissionState.value = status.state
    status.onchange = () => { permissionState.value = status.state }
  } catch {
    // Safari 等浏览器不支持 geolocation 权限查询，退化为仅按钮定位。
    permissionState.value = 'unsupported'
  }
}

function geolocationErrorMessage(error) {
  const reasons = {
    1: '定位权限被拒绝，可继续使用默认城市或手动指定地点',
    2: '定位暂不可用，请稍后重试',
    3: '定位请求超时，请重试',
  }
  return reasons[error.code] || `定位失败：${error.message || '未知错误'}`
}

function getPosition() {
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: 5000,
      maximumAge: 0, // 每次都要最新位置
    })
  })
}

function makeDeviceLocation({ latitude, longitude, accuracy_m }) {
  return {
    latitude: Math.round(latitude * 1e6) / 1e6,
    longitude: Math.round(longitude * 1e6) / 1e6,
    accuracy_m,
    captured_at: new Date().toISOString(),
    source: 'device',
    consent_granted: true,
  }
}

// 静默取一次最新位置。成功返回 true；失败返回 false（不抛出、不阻塞提问，
// 已有 deviceLocation 会被保留）。
async function locateDevice() {
  if (!navigator.geolocation) return false
  try {
    const position = await getPosition()
    deviceLocation.value = makeDeviceLocation({
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
      accuracy_m: position.coords.accuracy,
    })
    return true
  } catch (error) {
    if (error && error.code === 1) permissionState.value = 'denied'
    return false
  }
}

async function refreshLocationIfGranted() {
  if (permissionState.value !== 'granted') return
  await locateDevice()
}

async function detectLocationFromNetwork() {
  const response = await fetch('https://ipapi.co/json/')
  if (!response.ok) throw new Error('网络定位服务不可用')
  const data = await response.json()
  if (data.error || typeof data.latitude !== 'number' || typeof data.longitude !== 'number') {
    throw new Error(data.reason || data.message || '无法从网络确定位置')
  }
  return { latitude: data.latitude, longitude: data.longitude, accuracy_m: 5000 }
}

async function fallbackToNetworkLocation(reason) {
  if (!NETWORK_LOCATION_FALLBACK) {
    locationState.value = { status: 'denied', message: reason }
    return
  }
  locationState.value = { status: 'locating', message: `${reason}，尝试网络定位…` }
  try {
    const position = await detectLocationFromNetwork()
    deviceLocation.value = makeDeviceLocation(position)
    locationState.value = { status: 'ready', message: '已使用网络定位（约 5 公里精度）' }
  } catch {
    locationState.value = { status: 'denied', message: reason }
  }
}

async function useDeviceLocation() {
  if (locationState.value.status === 'locating') return
  if (!navigator.geolocation) {
    void fallbackToNetworkLocation('当前浏览器不支持设备定位')
    return
  }
  // prompt / unknown 状态：getCurrentPosition 会触发浏览器授权询问。
  locationState.value = { status: 'locating', message: '正在请求定位授权…' }
  const ok = await locateDevice()
  if (!ok) {
    void fallbackToNetworkLocation('定位失败，可继续使用默认城市或手动指定地点')
    return
  }
  permissionState.value = 'granted'
  locationState.value = { status: 'ready', message: '已授权，每次提问将自动使用最新位置' }
}

// 组件初始化即检查一次权限状态（等价于 onMounted）。
void checkLocationPermission()

onMounted(async () => {
  await refreshSessions()
  // 从衣柜点选单品跳转而来（URL 带 item_id）：立即以该件为锚点发起单品搭配，
  // 完成后清掉 query，避免刷新页面重复触发。
  const anchorItemId = route.query.item_id
  if (anchorItemId) {
    await run({ itemId: String(anchorItemId), label: String(route.query.label || '') })
    router.replace({ path: '/recommend' })
    return
  }
  // 恢复上次会话（localStorage 按用户隔离，跨刷新/跨设备保持）。
  const saved = localStorage.getItem(sessionStorageKey())
  if (saved && sessions.value.some((session) => session.session_id === saved)) {
    const session = sessions.value.find((item) => item.session_id === saved)
    await store.loadSessionHistory(userId.value, session)
  }
})
</script>

<style scoped>
.studio { --ink: #18201d; --moss: #52675b; --copper: #a86138; max-width: 1240px; margin: 0 auto; color: var(--ink); }
.chat-layout { display: flex; gap: 24px; align-items: flex-start; }
.session-sidebar { width: 220px; flex-shrink: 0; position: sticky; top: 20px; max-height: calc(100vh - 40px); overflow: auto; padding: 14px; border: 1px solid #d9ded9; background: #f7f8f5; border-radius: 4px; }
.new-session { width: 100%; margin-bottom: 10px; }
.session-item { padding: 9px 10px; margin-bottom: 6px; border: 1px solid transparent; border-radius: 3px; cursor: pointer; }
.session-item:hover { background: #eef1ec; }
.session-item.active { border-color: var(--moss); background: #e4eae4; }
.session-line { display: flex; justify-content: space-between; align-items: center; gap: 6px; }
.session-item strong { display: block; font-size: 13px; color: var(--ink); }
.session-item small { display: block; margin-top: 3px; color: #8a928d; font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-del { color: #a8b0ab; cursor: pointer; }
.session-del:hover { color: var(--copper); }
.session-empty { color: #98a09b; font-size: 12px; line-height: 1.6; }
.chat-main { flex: 1; min-width: 0; }
.hero { display: flex; justify-content: space-between; gap: 32px; padding: 34px 0 24px; border-bottom: 1px solid #d8ddd8; }
.eyebrow { margin: 0 0 8px; color: var(--copper); font-size: 12px; letter-spacing: .18em; font-weight: 700; }
h1 { margin: 0; max-width: 720px; font-family: Georgia, 'Noto Serif SC', serif; font-size: clamp(34px, 5vw, 58px); line-height: 1.05; font-weight: 500; }
.lead { max-width: 660px; margin: 16px 0 0; color: #65706b; font-size: 16px; }
.user-box { width: 190px; align-self: flex-start; }
.user-box span { display: block; margin-bottom: 8px; color: #77817c; font-size: 12px; }
.prompt-card { position: sticky; bottom: 16px; margin-top: 24px; padding: 20px; background: #f3f1ea; border: 1px solid #d9d3c5; border-radius: 4px; box-shadow: 8px 8px 0 #e1e5df; z-index: 5; }
.prompt-card :deep(textarea) { background: transparent; border: 0; box-shadow: none; font-size: 18px; line-height: 1.7; }
.prompt-footer { display: flex; align-items: flex-end; justify-content: space-between; gap: 18px; margin-top: 12px; }
.examples { display: flex; flex-wrap: wrap; gap: 8px; }
.examples button { border: 1px solid #c9cec8; background: #fff; color: #4b5751; border-radius: 999px; padding: 7px 12px; cursor: pointer; }
.examples button:hover, .examples button:focus-visible { border-color: var(--copper); color: var(--copper); outline: none; }
.shortcut { margin: 8px 0 0; color: #929994; font-size: 11px; text-align: right; }
.block { margin-top: 24px; }
.chat-history { margin-top: 28px; display: flex; flex-direction: column; gap: 18px; }
.chat-turn { display: flex; flex-direction: column; }
.chat-turn.user { align-items: flex-end; }
.chat-turn.assistant { align-items: stretch; }
.chat-bubble { max-width: 680px; padding: 12px 16px; border-radius: 10px; font-size: 15px; line-height: 1.7; white-space: pre-wrap; }
.chat-bubble.user { align-self: flex-end; background: var(--moss); color: #fff; }
.chat-bubble.assistant { align-self: flex-start; background: #f3f1ea; border: 1px solid #ddd7c8; color: #3f4a44; }
.chat-bubble.assistant.failed { border-color: #d9a8a0; background: #faf0ee; color: #a53d36; }
.failed-tag { display: inline-block; margin-right: 8px; padding: 1px 7px; border: 1px solid currentColor; border-radius: 999px; font-size: 11px; }
.chat-bubble.typing { color: #8a928d; font-style: italic; }
.agent-rail { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid #d9ded9; }
.agent-step { position: relative; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 12px; min-height: 82px; padding: 14px 18px; border-right: 1px solid #d9ded9; background: #fbfcfa; }
.agent-step:last-child { border-right: 0; }.step-index { color: var(--copper); font-family: Georgia, serif; font-size: 23px; }
.agent-step strong, .agent-step small { display: block; }.agent-step small { margin-top: 3px; color: #7e8882; }
.step-status { color: var(--moss); font-size: 12px; }
.location-bar { display: flex; align-items: center; gap: 10px; margin-top: 12px; }.location-msg { font-size: 12px; color: #7c8580; }.location-msg.ready { color: var(--moss); }.location-msg.denied { color: #a86138; }.location-msg.error { color: #a83a38; }
.anchor-bar { display: flex; align-items: center; gap: 14px; margin-top: 20px; }.anchor-hint { color: #7c8580; font-size: 13px; }
.grounding-bar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 14px; padding: 10px 14px; border-left: 3px solid var(--copper); background: #f3f1ea; font-size: 13px; color: #5e6863; }.grounding-bar .grounding-label { color: var(--copper); font-size: 11px; letter-spacing: .08em; font-weight: 700; }
.pick-header { margin-bottom: 14px; }
.pick-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(112px, 1fr)); gap: 12px; max-height: 56vh; overflow: auto; padding: 4px; }
.pick-card { display: block; width: 100%; padding: 8px; border: 1px solid #d9ded9; border-radius: 6px; background: #fff; cursor: pointer; text-align: left; transition: border-color .15s, box-shadow .15s; }
.pick-card:hover, .pick-card:focus-visible { border-color: var(--copper); box-shadow: 0 2px 8px rgba(0, 0, 0, .06); outline: none; }
.pick-img { display: block; width: 100%; height: 96px; border-radius: 4px; }
.pick-ph { height: 96px; display: flex; align-items: center; justify-content: center; background: #eef1ec; color: #98a09b; font-size: 12px; }
.pick-meta { margin-top: 6px; }
.pick-meta strong, .pick-meta small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pick-meta strong { font-size: 12px; color: var(--ink); }.pick-meta small { margin-top: 2px; color: #8a928d; font-size: 11px; }
.pick-empty { padding: 34px 0; text-align: center; color: #98a09b; font-size: 13px; }
@media (max-width: 900px) { .hero { flex-direction: column; }.user-box { width: 100%; }.chat-layout { flex-direction: column; }.session-sidebar { position: static; width: 100%; max-height: none; }.agent-rail { grid-template-columns: 1fr; }.agent-step { border-right: 0; border-bottom: 1px solid #d9ded9; }.prompt-footer { align-items: stretch; flex-direction: column; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; } }
</style>
