// Global recommendation store: survives route changes so switching pages
// does not interrupt or discard an in-flight recommendation request.
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { executeTask } from '../services/api'

export const useRecommendationStore = defineStore('recommendation', () => {
  const loading = ref(false)
  const payload = ref(null)
  const error = ref('')

  // Guards against an old request overwriting a newer one.
  let requestKey = ''

  async function run(userId, request, maxResults = 3) {
    if (loading.value) return // already generating
    loading.value = true
    error.value = ''
    const key = `${userId}|${maxResults}|${request}`
    requestKey = key
    try {
      const res = await executeTask({ user_id: userId, request, max_results: maxResults })
      if (requestKey === key) {
        payload.value = res.data
      }
    } catch (e) {
      if (requestKey === key) {
        error.value = e.response?.data?.detail || e.message
      }
    } finally {
      if (requestKey === key) {
        loading.value = false
      }
    }
  }

  function clear() {
    payload.value = null
    error.value = ''
  }

  return { loading, payload, error, run, clear }
})
