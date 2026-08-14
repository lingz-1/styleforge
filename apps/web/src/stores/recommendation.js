// Global recommendation store: survives route changes so switching pages
// does not interrupt or discard an in-flight recommendation request.
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { executeTask, getChatSession } from '../services/api'

export const useRecommendationStore = defineStore('recommendation', () => {
  const loading = ref(false)
  const payload = ref(null)
  const error = ref('')

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
    const key = `${userId}|${maxResults}|${request}|${locationContext ? JSON.stringify(locationContext) : ''}|${sid}|${opts.itemId || ''}|${opts.requestedTaskType || ''}`
    requestKey = key
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
      })
      if (requestKey === key) {
        payload.value = res.data
        // Keep the chain in sync with the persisted assistant turn.
        messages.value.push({
          role: 'assistant',
          content: assistantSummary(res.data),
          payload: res.data,
        })
      }
    } catch (e) {
      if (requestKey === key) {
        error.value = e.response?.data?.detail || e.message
        // The backend persisted a failed assistant message too; mirror it here
        // so the live chain reads the same as the reloaded history.
        messages.value.push({
          role: 'assistant',
          content: error.value,
          failed: true,
        })
      }
    } finally {
      if (requestKey === key) {
        loading.value = false
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
        return { role: 'user', content: message.content }
      }
      const result = message.result || {}
      if (result.status === 'failed') {
        return { role: 'assistant', content: result.error || message.content, failed: true }
      }
      return { role: 'assistant', content: message.content, payload: message.result }
    })
  }

  // Begin a fresh conversation (no persisted session, empty chain).
  function newConversation() {
    sessionId.value = ''
    messages.value = []
    payload.value = null
    error.value = ''
  }

  function clear() {
    payload.value = null
    error.value = ''
  }

  return { loading, payload, error, sessionId, messages, run, loadSessionHistory, newConversation, clear }
})
