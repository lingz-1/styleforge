// Global recommendation store: survives route changes so switching pages
// does not interrupt or discard an in-flight recommendation request.
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { executeTask, getChatSession } from '../services/api'
import {
  isRequestTimeout,
  waitForPersistedAssistant,
} from '../services/recommendation-request'

export const useRecommendationStore = defineStore('recommendation', () => {
  const loading = ref(false)
  const payload = ref(null)
  const error = ref('')
  const elapsedSeconds = ref(0)
  const progressStage = ref('')

  // Multi-turn chat state. `messages` is the in-memory chain for the current
  // session; `sessionId` mirrors the active persisted chat session ('' = none).
  const sessionId = ref('')
  const messages = ref([])

  // Guards against an old request overwriting a newer one.
  let requestKey = ''

  function assistantSummary(data) {
    const result = data?.result || {}
    const text = result.message || result.summary || data?.request || '已完成'
    return typeof text === 'string' ? text : JSON.stringify(text)
  }

  async function run(userId, request, maxResults = 3, locationContext = null, sid = '', opts = {}) {
    if (loading.value) return // already generating
    loading.value = true
    error.value = ''
    elapsedSeconds.value = 0
    progressStage.value = '正在执行任务编排与工具调用'
    const key = `${userId}|${maxResults}|${request}|${locationContext ? JSON.stringify(locationContext) : ''}|${sid}|${opts.itemId || ''}|${opts.requestedTaskType || ''}|${opts.activeOutfitId || ''}|${opts.selectedItemId || ''}`
    requestKey = key
    const assistantCount = messages.value.filter((message) => message.role === 'assistant').length
    const startedAt = Date.now()
    const elapsedTimer = setInterval(() => {
      if (requestKey === key) {
        elapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
      }
    }, 1000)
    // Optimistically show the user turn; the backend also persists it on send.
    const userMessage = { role: 'user', content: request }
    messages.value.push(userMessage)
    try {
      const res = await executeTask({
        user_id: userId,
        request,
        max_results: maxResults,
        session_id: sid,
        ...(locationContext ? { location_context: locationContext } : {}),
        // 衣柜点选单品直达：精确锁定锚点并显式路由到 item_advice
        ...(opts.itemId ? { item_id: opts.itemId } : {}),
        ...(opts.requestedTaskType ? { requested_task_type: opts.requestedTaskType } : {}),
        // Agentic 契约 Stage 1：InteractionContext grounding
        // （active_outfit_id 复用旧 current_outfit_id 通道，不动后端路由）
        ...(opts.activeOutfitId ? { current_outfit_id: opts.activeOutfitId } : {}),
        ...(opts.selectedItemId ? { selected_item_id: opts.selectedItemId } : {}),
      })
      if (requestKey === key) {
        payload.value = res.data
        // Keep the chain in sync with the persisted assistant turn.
        messages.value.push({
          role: 'assistant',
          content: assistantSummary(res.data),
          payload: res.data,
          messageId: res.data.message_id || '',
        })
      }
    } catch (e) {
      if (requestKey === key) {
        let recovered = null
        if (sid && isRequestTimeout(e)) {
          progressStage.value = '连接已超时，但任务仍在后台执行，正在恢复结果'
          recovered = await waitForPersistedAssistant({
            getSession: getChatSession,
            userId,
            sessionId: sid,
            previousAssistantCount: assistantCount,
            isCurrent: () => requestKey === key,
          })
        }
        if (recovered && requestKey === key) {
          const result = recovered.result || {}
          const failed = result.status === 'failed'
          if (failed) error.value = result.error || recovered.content || '任务执行失败'
          else payload.value = result
          messages.value.push({
            role: 'assistant',
            content: recovered.content || assistantSummary(result),
            payload: failed ? null : result,
            failed,
            messageId: recovered.message_id || '',
          })
        } else if (requestKey === key) {
          error.value = e.response?.data?.detail || e.message
          messages.value.push({
            role: 'assistant',
            content: error.value,
            failed: true,
          })
        }
      }
    } finally {
      clearInterval(elapsedTimer)
      if (requestKey === key) {
        loading.value = false
        progressStage.value = ''
      }
    }
  }

  // Restore a persisted session into the chain (user + assistant turns, with
  // each assistant turn carrying its trimmed result snapshot for re-render).
  async function loadSessionHistory(userId, session) {
    sessionId.value = session.session_id
    payload.value = null
    error.value = ''
    const res = await getChatSession(userId, session.session_id)
    messages.value = (res.data.messages || []).map((message) => {
      if (message.role === 'user') {
        return { role: 'user', content: message.content, messageId: message.message_id }
      }
      const result = message.result || {}
      if (result.status === 'failed') {
        return {
          role: 'assistant',
          content: result.error || message.content,
          failed: true,
          messageId: message.message_id,
        }
      }
      return {
        role: 'assistant',
        content: message.content,
        payload: message.result,
        messageId: message.message_id,
      }
    })
  }

  // Begin a fresh conversation (no persisted session, empty chain).
  function newConversation() {
    sessionId.value = ''
    messages.value = []
    payload.value = null
    error.value = ''
    progressStage.value = ''
    elapsedSeconds.value = 0
  }

  function clear() {
    payload.value = null
    error.value = ''
  }

  return {
    loading,
    payload,
    error,
    elapsedSeconds,
    progressStage,
    sessionId,
    messages,
    run,
    loadSessionHistory,
    newConversation,
    clear,
  }
})
