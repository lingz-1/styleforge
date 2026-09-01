<template>
  <main class="health-studio">
    <header class="health-header">
      <div>
        <p class="eyebrow">STYLEFORGE / SYSTEM PULSE</p>
        <h1>系统脉搏</h1>
        <p class="lede">沿着一次穿搭请求的真实路径，检查服务状态、失败位置与处理耗时。</p>
      </div>
      <div class="refresh-panel">
        <div class="live-state" :class="overallTone" aria-live="polite">
          <span class="live-dot" aria-hidden="true"></span>
          <span>{{ overallLabel }}</span>
        </div>
        <button class="refresh-button" type="button" :disabled="loading" @click="loadHealth">
          {{ loading ? '正在校准…' : '立即刷新' }}
        </button>
        <small>{{ refreshCaption }}</small>
      </div>
    </header>

    <section v-if="loadError" class="error-banner" role="alert">
      <div>
        <strong>无法读取系统状态</strong>
        <span>{{ loadError.message }}</span>
      </div>
      <code>{{ loadError.code }}</code>
    </section>

    <template v-if="health">
      <section class="summary-strip" aria-label="运行摘要">
        <div>
          <span>进程范围</span>
          <strong>{{ uptime }}</strong>
          <small>重启后重新计数</small>
        </div>
        <div>
          <span>请求总量</span>
          <strong>{{ requests.total }}</strong>
          <small>{{ requestFailureRate }}% 失败率</small>
        </div>
        <div>
          <span>当前处理中</span>
          <strong>{{ requests.in_flight }}</strong>
          <small>HTTP 请求</small>
        </div>
        <div>
          <span>衣物目录</span>
          <strong>{{ formatNumber(health.catalog_items) }}</strong>
          <small>{{ formatNumber(health.embedding_ready_items) }} 条语义就绪</small>
        </div>
      </section>

      <section class="pulse-layout">
        <article class="pipeline-panel">
          <div class="section-heading">
            <div>
              <p>REQUEST PATH</p>
              <h2>从输入到结果</h2>
            </div>
            <span>本进程累计</span>
          </div>

          <ol class="pipeline" aria-label="系统处理链路">
            <li v-for="(stage, index) in pipeline" :key="stage.key" class="pipeline-stage">
              <div class="rail" aria-hidden="true">
                <span>{{ String(index + 1).padStart(2, '0') }}</span>
              </div>
              <div class="stage-copy">
                <div class="stage-title">
                  <div>
                    <strong>{{ stage.name }}</strong>
                    <span>{{ stage.description }}</span>
                  </div>
                  <span class="stage-state" :class="stage.tone">{{ stage.state }}</span>
                </div>
                <div class="stage-measures">
                  <span><b>{{ stage.total }}</b> 调用</span>
                  <span><b>{{ stage.failed }}</b> 异常</span>
                  <span><b>{{ stage.retryable }}</b> 可重试</span>
                  <span><b>{{ stage.retries }}</b> 重试</span>
                  <span><b>{{ stage.degraded }}</b> 降级</span>
                  <span><b>{{ formatDuration(stage.average) }}</b> 平均</span>
                  <span><b>{{ formatDuration(stage.maximum) }}</b> 最慢</span>
                </div>
              </div>
            </li>
          </ol>
        </article>

        <aside class="dependency-panel">
          <div class="section-heading compact">
            <div>
              <p>DEPENDENCIES</p>
              <h2>能力底座</h2>
            </div>
          </div>
          <ul class="dependency-list">
            <li v-for="item in dependencies" :key="item.name">
              <span class="dependency-mark" :class="item.tone" aria-hidden="true"></span>
              <div>
                <strong>{{ item.name }}</strong>
                <small>{{ item.detail }}</small>
              </div>
              <em :class="item.tone">{{ item.state }}</em>
            </li>
          </ul>

          <div class="source-note">
            <span>数据来源</span>
            <div v-if="sourceEntries.length" class="source-tags">
              <span v-for="source in sourceEntries" :key="source.name">
                {{ source.name }} · {{ formatNumber(source.count) }}
              </span>
            </div>
            <p v-else>尚未导入目录数据。</p>
          </div>
        </aside>
      </section>

      <section class="mcp-section">
        <div class="section-heading">
          <div>
            <p>MODEL CONTEXT PROTOCOL</p>
            <h2>MCP 接入状态</h2>
          </div>
          <span>{{ mcpClient.calls }} 次外部工具调用</span>
        </div>
        <div class="mcp-grid">
          <article v-for="server in mcpServers" :key="server.name" class="mcp-card">
            <div>
              <strong>{{ server.name }}</strong>
              <span class="stage-state" :class="server.tone">{{ server.state }}</span>
            </div>
            <small>{{ server.transport }} · {{ server.endpoint }}</small>
            <div class="mcp-tools">
              <code v-for="tool in server.tools" :key="tool">{{ tool }}</code>
            </div>
            <p>{{ server.calls }} 调用 · {{ server.failed }} 失败</p>
          </article>
        </div>
        <div class="mcp-recent">
          <h3>最近 MCP 调用</h3>
          <div v-if="mcpRecentCalls.length" class="mcp-call-list">
            <div v-for="call in mcpRecentCalls" :key="call.call_id">
              <span :class="call.success ? 'healthy' : 'danger'">{{ call.success ? '✓' : '×' }}</span>
              <code>{{ call.server }}/{{ call.tool }}</code>
              <small>{{ call.transport }} · {{ formatDuration(call.duration_ms) }}</small>
            </div>
          </div>
          <p v-else>尚未触发外部 MCP；首次需要天气的穿搭任务会调用官方 Time 与 Fetch Server。</p>
        </div>
      </section>

      <section class="error-section">
        <div class="section-heading">
          <div>
            <p>ERROR MAP</p>
            <h2>运行异常分布</h2>
          </div>
          <span>{{ errors.total }} 次已归类异常（包含已恢复的中间异常）</span>
        </div>

        <div v-if="errors.total" class="error-columns">
          <div>
            <h3>按错误码</h3>
            <div v-for="item in errorCodes" :key="item.name" class="error-row">
              <div><code>{{ item.name }}</code><strong>{{ item.count }}</strong></div>
              <span><i :style="{ width: `${item.percent}%` }"></i></span>
            </div>
          </div>
          <div>
            <h3>按系统模块</h3>
            <div v-for="item in errorComponents" :key="item.name" class="error-row">
              <div><code>{{ moduleLabel(item.name) }}</code><strong>{{ item.count }}</strong></div>
              <span><i :style="{ width: `${item.percent}%` }"></i></span>
            </div>
          </div>
        </div>
        <div v-else class="empty-errors">
          <span>✓</span>
          <div><strong>尚无已记录错误</strong><small>错误统计仅保留低基数分类，不包含用户输入和异常详情。</small></div>
        </div>
      </section>

      <footer class="metrics-footer">
        <div>
          <strong>Prometheus 抓取端点</strong>
          <span>可交给外部 Prometheus 持久化；页面数据本身随 API 进程重启清零。</span>
        </div>
        <a href="/api/metrics" target="_blank" rel="noreferrer">/api/metrics ↗</a>
      </footer>
    </template>

    <section v-else-if="loading" class="initial-loading" aria-live="polite">
      <span></span>
      <p>正在沿处理链路读取状态…</p>
    </section>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getHealth } from '../services/api'

const health = ref(null)
const loading = ref(false)
const loadError = ref(null)
const updatedAt = ref(null)
const clock = ref(Date.now())
let refreshTimer
let clockTimer

const emptyDuration = { total: 0, average: 0, maximum: 0 }
const requests = computed(() => health.value?.observability?.requests || {
  in_flight: 0,
  total: 0,
  failed: 0,
  duration_ms: emptyDuration,
})
const errors = computed(() => health.value?.observability?.errors || {
  total: 0,
  by_code: {},
  by_component: {},
})

const operationDefinitions = [
  ['task_run', '任务编排', '识别任务、组织上下文并推进工作流'],
  ['agent_call', 'Agent 决策', '规划、造型、评估与结果解释'],
  ['llm_call', '模型推理', '结构化调用外部语言模型'],
  ['tool_call', '工具执行', '天气、检索与领域能力调用'],
  ['mcp_call', '外部 MCP', '协议握手、工具发现与标准化工具调用'],
  ['database_session', '数据落库', '衣橱、偏好、会话与结果持久化'],
]

const pipeline = computed(() => {
  const operations = health.value?.observability?.operations || {}
  const http = requests.value
  const stages = [{
    key: 'http',
    name: 'HTTP 接入',
    description: '验证请求、建立追踪标识并统一错误响应',
    total: http.total,
    failed: http.failed,
    retryable: 0,
    retries: 0,
    degraded: 0,
    average: http.duration_ms?.average || 0,
    maximum: http.duration_ms?.maximum || 0,
  }]
  for (const [key, name, description] of operationDefinitions) {
    const item = operations[key] || {}
    stages.push({
      key,
      name,
      description,
      total: item.total || 0,
      failed: item.failed || 0,
      retryable: item.retryable_failures || 0,
      retries: item.retries || 0,
      degraded: item.degraded || 0,
      average: item.duration_ms?.average || 0,
      maximum: item.duration_ms?.maximum || 0,
    })
  }
  return stages.map((stage) => {
    if (stage.failed > 0 && ['http', 'task_run'].includes(stage.key)) {
      return { ...stage, state: '最终失败', tone: 'danger' }
    }
    if (stage.failed > 0) {
      const state = stage.retryable >= stage.failed ? '可重试异常' : '中间异常'
      return { ...stage, state, tone: 'warning' }
    }
    if (stage.degraded > 0 || stage.retries > 0) return { ...stage, state: '有降级', tone: 'warning' }
    if (stage.total === 0) return { ...stage, state: '待触发', tone: 'neutral' }
    return { ...stage, state: '正常', tone: 'healthy' }
  })
})

const dependencies = computed(() => {
  const data = health.value || {}
  const retrieval = data.wardrobe_retrieval || {}
  const weather = data.weather || {}
  const mcp = data.mcp?.client || {}
  const mcpServer = data.mcp?.server || {}
  const images = Object.values(data.image_sources || {})
  const imageReady = images.some((source) => source.available)
  return [
    {
      name: 'PostgreSQL',
      detail: data.database?.backend || '主数据存储',
      state: data.database?.status === 'connected' ? '已连接' : '异常',
      tone: data.database?.status === 'connected' ? 'healthy' : 'danger',
    },
    {
      name: '语义检索',
      detail: `${formatNumber(data.embedding_ready_items)} 条目录向量 · ${formatNumber(data.personal_embedding_items)} 条个人向量`,
      state: retrieval.semantic_artifacts_available ? '已就绪' : '关键词降级',
      tone: retrieval.semantic_artifacts_available ? 'healthy' : 'warning',
    },
    {
      name: '天气服务',
      detail: weather.enabled ? (weather.provider || '已配置提供方') : '未启用，不影响基础推荐',
      state: weather.enabled ? '已启用' : '可选',
      tone: weather.enabled ? 'healthy' : 'neutral',
    },
    {
      name: 'Model Context Protocol',
      detail: `${(mcp.servers || []).length} 个外部 Server · ${(mcpServer.tools || []).length} 个 StyleForge tools`,
      state: mcp.enabled && mcpServer.enabled ? '双向接入' : '未启用',
      tone: mcp.enabled && mcpServer.enabled ? (mcp.failed_calls ? 'warning' : 'healthy') : 'neutral',
    },
    {
      name: '衣物图像源',
      detail: `${images.length} 个已登记数据源`,
      state: imageReady ? '可访问' : '不可用',
      tone: imageReady ? 'healthy' : 'warning',
    },
  ]
})

const mcpClient = computed(() => health.value?.mcp?.client || {
  enabled: false,
  servers: [],
  calls: 0,
  failed_calls: 0,
  recent_calls: [],
})
const mcpServers = computed(() => {
  const external = (mcpClient.value.servers || []).map((server) => ({
    name: server.name,
    transport: server.transport,
    endpoint: server.endpoint,
    tools: server.tools || [],
    calls: server.calls || 0,
    failed: server.failed_calls || 0,
    state: server.failed_calls ? '有降级' : (server.calls ? '已调用' : '已配置'),
    tone: server.failed_calls ? 'warning' : 'healthy',
  }))
  const own = health.value?.mcp?.server
  if (own?.enabled) {
    external.unshift({
      name: own.name,
      transport: (own.transports || []).join(' + '),
      endpoint: own.endpoint,
      tools: own.tools || [],
      calls: 0,
      failed: 0,
      state: '对外服务',
      tone: 'healthy',
    })
  }
  return external
})
const mcpRecentCalls = computed(() => (mcpClient.value.recent_calls || []).slice(-8).reverse())

const sourceEntries = computed(() => Object.entries(health.value?.catalog_items_by_source || {})
  .map(([name, count]) => ({ name, count }))
  .sort((a, b) => b.count - a.count))

function rankedErrors(values) {
  const entries = Object.entries(values || {}).sort((a, b) => b[1] - a[1])
  const maximum = Math.max(1, ...entries.map(([, count]) => count))
  return entries.map(([name, count]) => ({ name, count, percent: (count / maximum) * 100 }))
}

const errorCodes = computed(() => rankedErrors(errors.value.by_code))
const errorComponents = computed(() => rankedErrors(errors.value.by_component))
const requestFailureRate = computed(() => requests.value.total
  ? ((requests.value.failed / requests.value.total) * 100).toFixed(1)
  : '0.0')

const isDegraded = computed(() => dependencies.value.some((item) => item.tone === 'danger'))
const hasWarning = computed(() => dependencies.value.some((item) => item.tone === 'warning'))
const overallTone = computed(() => {
  if (loadError.value || isDegraded.value) return 'danger'
  if (hasWarning.value) return 'warning'
  return 'healthy'
})
const overallLabel = computed(() => ({
  healthy: '核心链路正常',
  warning: '部分能力降级',
  danger: '系统需要处理',
})[overallTone.value])

const uptime = computed(() => {
  const started = health.value?.observability?.started_at
  if (!started) return '—'
  const seconds = Math.max(0, Math.floor((clock.value - new Date(started).getTime()) / 1000))
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days) return `${days}天 ${hours}小时`
  if (hours) return `${hours}小时 ${minutes}分`
  return `${minutes}分 ${seconds % 60}秒`
})

const refreshCaption = computed(() => updatedAt.value
  ? `更新于 ${updatedAt.value.toLocaleTimeString('zh-CN', { hour12: false })} · 每 15 秒自动刷新`
  : '每 15 秒自动刷新')

async function loadHealth() {
  if (loading.value) return
  loading.value = true
  try {
    const response = await getHealth()
    health.value = response.data
    loadError.value = null
    updatedAt.value = new Date()
  } catch (error) {
    loadError.value = error
  } finally {
    loading.value = false
  }
}

function formatNumber(value) {
  return new Intl.NumberFormat('zh-CN').format(Number(value || 0))
}

function formatDuration(value) {
  const duration = Number(value || 0)
  if (duration < 1) return `${duration.toFixed(2)} ms`
  if (duration < 1000) return `${duration.toFixed(1)} ms`
  return `${(duration / 1000).toFixed(2)} s`
}

function moduleLabel(value) {
  return ({ api: 'API 边界', tool_runtime: '工具运行时', database: '数据库' })[value] || value
}

onMounted(() => {
  loadHealth()
  refreshTimer = window.setInterval(loadHealth, 15000)
  clockTimer = window.setInterval(() => { clock.value = Date.now() }, 1000)
})

onBeforeUnmount(() => {
  window.clearInterval(refreshTimer)
  window.clearInterval(clockTimer)
})
</script>

<style scoped>
.health-studio {
  --ink: #18201d;
  --moss: #52675b;
  --moss-dark: #34483d;
  --paper: #f6f3eb;
  --paper-deep: #ece7db;
  --copper: #a86138;
  --red: #a8463d;
  max-width: 1320px;
  margin: 0 auto;
  color: var(--ink);
}

.health-header { display: flex; justify-content: space-between; gap: 40px; align-items: flex-end; padding: 30px 0 34px; border-bottom: 1px solid #cfd4ce; }
.eyebrow, .section-heading p { margin: 0 0 8px; color: var(--copper); font-size: 11px; font-weight: 700; letter-spacing: .18em; }
h1, h2 { font-family: Georgia, 'Noto Serif SC', serif; font-weight: 500; }
h1 { margin: 0; font-size: clamp(40px, 6vw, 70px); line-height: .98; letter-spacing: -.035em; }
.lede { max-width: 620px; margin: 15px 0 0; color: #68736d; line-height: 1.7; }
.refresh-panel { display: grid; justify-items: end; gap: 9px; min-width: 250px; }
.refresh-panel small { color: #818a85; font-size: 11px; }
.live-state { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700; }
.live-dot { width: 9px; height: 9px; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 5px color-mix(in srgb, currentColor 14%, transparent); animation: pulse 2.4s ease-out infinite; }
.healthy { color: #477158; }.warning { color: #a86138; }.danger { color: var(--red); }.neutral { color: #7f8783; }
.refresh-button { padding: 9px 15px; border: 1px solid #bbc5bd; border-radius: 2px; background: transparent; color: var(--moss-dark); font: inherit; font-size: 12px; cursor: pointer; }
.refresh-button:hover:not(:disabled) { border-color: var(--moss); background: #edf0eb; }.refresh-button:disabled { cursor: wait; opacity: .55; }

.error-banner { display: flex; justify-content: space-between; gap: 24px; margin-top: 20px; padding: 14px 16px; border-left: 3px solid var(--red); background: #f7e9e5; color: #793b35; }
.error-banner div { display: grid; gap: 4px; }.error-banner span { font-size: 13px; }.error-banner code { align-self: center; font-size: 11px; }

.summary-strip { display: grid; grid-template-columns: repeat(4, 1fr); margin: 28px 0; border: 1px solid #ccd2cb; background: var(--paper); }
.summary-strip > div { min-width: 0; padding: 18px 22px; border-right: 1px solid #ccd2cb; }
.summary-strip > div:last-child { border-right: 0; }
.summary-strip span, .summary-strip strong, .summary-strip small { display: block; }
.summary-strip span { color: #737d77; font-size: 11px; letter-spacing: .08em; }
.summary-strip strong { margin-top: 7px; overflow: hidden; font-family: Georgia, serif; font-size: clamp(23px, 3vw, 32px); font-weight: 500; text-overflow: ellipsis; }
.summary-strip small { margin-top: 3px; color: #8b918e; font-size: 11px; }

.pulse-layout { display: grid; grid-template-columns: minmax(0, 1.8fr) minmax(300px, .8fr); gap: 26px; }
.pipeline-panel, .dependency-panel, .error-section, .mcp-section { border: 1px solid #d1d6d0; background: #fff; }
.pipeline-panel, .error-section, .mcp-section { padding: 26px; }.dependency-panel { padding: 26px 22px; background: var(--paper); }
.section-heading { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin-bottom: 24px; }
.section-heading h2 { margin: 0; font-size: 28px; }.section-heading > span { color: #858d88; font-size: 11px; }.section-heading.compact { margin-bottom: 16px; }

.pipeline { margin: 0; padding: 0; list-style: none; }
.pipeline-stage { display: grid; grid-template-columns: 48px minmax(0, 1fr); }
.rail { position: relative; display: flex; justify-content: center; }
.rail::after { position: absolute; top: 29px; bottom: -2px; width: 1px; content: ''; background: #c9d0ca; }
.pipeline-stage:last-child .rail::after { display: none; }
.rail span { z-index: 1; display: grid; place-items: center; width: 28px; height: 28px; border: 1px solid var(--moss); border-radius: 50%; background: #fff; color: var(--moss); font-family: Georgia, serif; font-size: 10px; }
.stage-copy { padding: 2px 0 23px; }
.stage-title { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; }
.stage-title > div { display: grid; gap: 4px; }.stage-title strong { font-size: 15px; }.stage-title div span { color: #7d8681; font-size: 12px; }
.stage-state { padding: 3px 8px; border: 1px solid currentColor; border-radius: 999px; font-size: 10px; white-space: nowrap; }
.stage-measures { display: grid; grid-template-columns: repeat(6, 1fr); gap: 8px; margin-top: 13px; }
.stage-measures span { padding-top: 8px; border-top: 1px solid #e4e6e2; color: #89908c; font-size: 10px; }
.stage-measures b { display: block; margin-bottom: 2px; color: #425049; font-size: 12px; font-weight: 600; }

.dependency-list { margin: 0; padding: 0; list-style: none; }
.dependency-list li { display: grid; grid-template-columns: 9px minmax(0, 1fr) auto; gap: 11px; align-items: center; padding: 15px 0; border-bottom: 1px solid #d8d8cf; }
.dependency-mark { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.dependency-list div { display: grid; gap: 3px; }.dependency-list strong { font-size: 13px; }.dependency-list small { color: #777f7a; font-size: 10px; line-height: 1.45; }
.dependency-list em { font-size: 10px; font-style: normal; }
.source-note { margin-top: 24px; }.source-note > span { color: #777f7a; font-size: 10px; letter-spacing: .1em; }
.source-tags { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 9px; }.source-tags span { padding: 5px 8px; border: 1px solid #cdd1ca; background: #fff; color: #59655f; font-size: 10px; }
.source-note p { color: #777f7a; font-size: 12px; }

.mcp-section { margin-top: 26px; }
.mcp-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.mcp-card { min-width: 0; padding: 17px; border: 1px solid #d9ddd7; background: var(--paper); }
.mcp-card > div:first-child { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
.mcp-card > small { display: block; margin-top: 8px; overflow: hidden; color: #7c857f; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.mcp-card > p { margin: 12px 0 0; color: #737d77; font-size: 11px; }
.mcp-tools { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 13px; }
.mcp-tools code { padding: 4px 6px; border: 1px solid #cdd3cc; background: #fff; color: #4d5a53; font-size: 9px; }
.mcp-recent { margin-top: 22px; padding-top: 18px; border-top: 1px solid #e1e4df; }
.mcp-recent h3 { margin: 0 0 12px; color: #616c66; font-size: 11px; letter-spacing: .1em; }
.mcp-recent > p { color: #7c857f; font-size: 12px; }
.mcp-call-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 24px; }
.mcp-call-list > div { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto; gap: 7px; align-items: center; padding: 7px 0; border-bottom: 1px solid #eceeea; }
.mcp-call-list code { overflow: hidden; color: #46544d; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.mcp-call-list small { color: #858d88; font-size: 9px; }

.error-section { margin-top: 26px; }
.error-columns { display: grid; grid-template-columns: repeat(2, 1fr); gap: 54px; }
.error-columns h3 { margin: 0 0 15px; color: #616c66; font-size: 11px; letter-spacing: .1em; }
.error-row { margin-bottom: 13px; }.error-row div { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 6px; }.error-row code { color: #4e5b54; font-size: 11px; }.error-row strong { color: var(--copper); font-size: 11px; }
.error-row > span { display: block; height: 4px; overflow: hidden; background: #ece9e2; }.error-row i { display: block; height: 100%; background: var(--copper); }
.empty-errors { display: flex; align-items: center; gap: 14px; padding: 22px; border: 1px dashed #cfd5cf; background: #f7f8f5; }
.empty-errors > span { display: grid; place-items: center; width: 31px; height: 31px; border-radius: 50%; background: var(--moss); color: #fff; }.empty-errors div { display: grid; gap: 3px; }.empty-errors small { color: #7d8681; }

.metrics-footer { display: flex; justify-content: space-between; gap: 28px; align-items: center; margin: 26px 0 8px; padding: 18px 20px; background: var(--moss-dark); color: #eef1ec; }
.metrics-footer div { display: grid; gap: 4px; }.metrics-footer strong { font-size: 13px; }.metrics-footer span { color: #bac4bd; font-size: 11px; }
.metrics-footer a { padding: 8px 11px; border: 1px solid #819087; color: #fff; font-family: Consolas, monospace; font-size: 11px; text-decoration: none; white-space: nowrap; }
.metrics-footer a:hover { background: #52675b; }

.initial-loading { display: grid; place-items: center; min-height: 360px; color: #747e78; }
.initial-loading span { width: 58px; height: 1px; background: var(--moss); animation: calibrate 1.3s ease-in-out infinite alternate; }.initial-loading p { margin-top: -130px; font-size: 12px; }

@keyframes pulse { 0% { box-shadow: 0 0 0 0 currentColor; } 65%, 100% { box-shadow: 0 0 0 8px transparent; } }
@keyframes calibrate { from { transform: scaleX(.25); } to { transform: scaleX(1); } }

@media (max-width: 980px) {
  .pulse-layout { grid-template-columns: 1fr; }
  .dependency-list { display: grid; grid-template-columns: repeat(2, 1fr); gap: 0 20px; }
  .mcp-grid { grid-template-columns: 1fr; }
}

@media (max-width: 700px) {
  .health-header { display: grid; padding-top: 12px; }.refresh-panel { justify-items: start; min-width: 0; }
  .summary-strip { grid-template-columns: repeat(2, 1fr); }.summary-strip > div:nth-child(2) { border-right: 0; }.summary-strip > div:nth-child(-n + 2) { border-bottom: 1px solid #ccd2cb; }
  .pipeline-panel, .dependency-panel, .error-section, .mcp-section { padding: 19px 15px; }
  .stage-measures { grid-template-columns: repeat(3, 1fr); }.stage-title div span { line-height: 1.45; }
  .dependency-list, .error-columns { grid-template-columns: 1fr; }.error-columns { gap: 28px; }
  .mcp-call-list { grid-template-columns: 1fr; }
  .metrics-footer { align-items: flex-start; flex-direction: column; }
}

@media (prefers-reduced-motion: reduce) {
  .live-dot, .initial-loading span { animation: none; }
}
</style>
