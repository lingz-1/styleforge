import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 120000 })
const taskTimeoutMs = Number(import.meta.env.VITE_TASK_TIMEOUT_MS || 120000)

export class ApiError extends Error {
  constructor(message, options = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = options.code || 'UNKNOWN_ERROR'
    this.status = options.status || 0
    this.requestId = options.requestId || ''
    this.retryable = Boolean(options.retryable)
    this.details = options.details || null
    // Preserve Axios compatibility for existing views during migration.
    this.response = options.response
  }
}

export function normalizeApiError(error) {
  if (error instanceof ApiError) return error
  const response = error?.response
  const payload = response?.data || {}
  const envelope = payload?.error || {}
  const detail = typeof payload?.detail === 'string' ? payload.detail : ''
  const status = Number(response?.status || 0)
  const message = envelope.message || detail || error?.message || '网络请求失败'
  const requestId = envelope.request_id || response?.headers?.['x-request-id'] || ''
  const retryable = typeof envelope.retryable === 'boolean'
    ? envelope.retryable
    : (!response || status === 429 || status >= 500)
  return new ApiError(message, {
    code: envelope.code || error?.code || (response ? 'HTTP_ERROR' : 'NETWORK_ERROR'),
    status,
    requestId,
    retryable,
    details: envelope.details || null,
    response,
  })
}

http.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(normalizeApiError(error)),
)

// Health
export const getHealth = () => http.get('/health')

// Today's weather for the home card (location | latitude/longitude | default city)
export const getWeatherNow = (params = {}) => http.get('/weather/now', { params })

// Catalog taxonomy (main category required, subtype optional)
export const getTaxonomy = () => http.get('/catalog/taxonomy')

// Wardrobe
export const getWardrobe = (userId) => http.get(`/wardrobes/${userId}`)
export const removeWardrobeItem = (userId, itemId) =>
  http.delete(`/wardrobes/${userId}/items/${itemId}`)
export const createPhotoItem = (userId, payload) =>
  http.post(`/wardrobes/${userId}/items/photo`, payload)
export const analyzeItem = (userId, filename, contentBase64) =>
  http.post(`/wardrobes/${userId}/items/analyze`, {
    filename,
    content_base64: contentBase64,
  })
export const startBatchRecognition = (userId, images, gender = 'women') =>
  http.post(`/wardrobes/${userId}/items/batch-recognize`, {
    images,
    default_gender: gender,
  })
export const getBatchRecognition = (userId, batchId) =>
  http.get(`/wardrobes/${userId}/recognition-batches/${batchId}`)
export const listBatchRecognition = (userId, limit = 20) =>
  http.get(`/wardrobes/${userId}/recognition-batches`, { params: { limit } })
export const deleteBatchRecognition = (userId, batchId) =>
  http.delete(`/wardrobes/${userId}/recognition-batches/${batchId}`)
export const retryBatchEmbedding = (userId, batchId) =>
  http.post(`/wardrobes/${userId}/recognition-batches/${batchId}/retry-embedding`)
export const retryBatchRecognition = (userId, batchId) =>
  http.post(`/wardrobes/${userId}/recognition-batches/${batchId}/retry`)
export const batchRecognitionImageUrl = (userId, batchId, itemIndex) =>
  `/api/wardrobes/${encodeURIComponent(userId)}/recognition-batches/${encodeURIComponent(batchId)}/items/${itemIndex}/image`
export const retryPersonalEmbeddings = (userId, itemIds = []) =>
  http.post(`/wardrobes/${userId}/embeddings/retry`, { item_ids: itemIds })
export const updateItem = (userId, itemId, fields) =>
  http.put(`/wardrobes/${userId}/items/${itemId}`, fields)
export const uploadItemImage = (userId, itemId, filename, contentBase64) =>
  http.post(`/wardrobes/${userId}/items/${itemId}/image`, {
    filename,
    content_base64: contentBase64,
  })

// Order import (预览 / 提交)
export const previewImport = (userId, filename, contentBase64, defaultAudience = '') =>
  http.post(`/wardrobes/${userId}/imports`, {
    filename,
    content_base64: contentBase64,
    default_audience: defaultAudience,
  })
export const getImportPreview = (userId, batchId) =>
  http.get(`/wardrobes/${userId}/imports/${batchId}`)
export const commitImport = (userId, batchId, selections, autoEmbed = true) =>
  http.post(`/wardrobes/${userId}/imports/${batchId}/commit`, {
    selections,
    auto_embed: autoEmbed,
  })

// Unified v3.3 task execution (recommend / modify / advice / compatibility / gap)
export const executeTask = (payload) => http.post('/tasks/execute', payload, { timeout: taskTimeoutMs })
export const getTaskRun = (userId, runId) => http.get(`/tasks/${userId}/${runId}`)

// Evaluation weights (五维偏好)
export const getEvaluationWeights = (userId) =>
  http.get(`/preferences/${userId}/evaluation`)
export const saveEvaluationWeights = (userId, weights) =>
  http.put(`/preferences/${userId}/evaluation`, { weights })

// Chat sessions (multi-turn conversation persistence)
export const createChatSession = (userId, title = '') =>
  http.post(`/users/${userId}/chat-sessions`, { title })
export const listChatSessions = (userId) =>
  http.get(`/users/${userId}/chat-sessions`)
export const getChatSession = (userId, sessionId) =>
  http.get(`/chat-sessions/${sessionId}`, { params: { user_id: userId } })
export const renameChatSession = (userId, sessionId, title) =>
  http.patch(`/chat-sessions/${sessionId}`, { title }, { params: { user_id: userId } })
export const deleteChatSession = (userId, sessionId) =>
  http.delete(`/chat-sessions/${sessionId}`, { params: { user_id: userId } })

// Long-term preference memories (dimensioned preference model: 手动修正)
export const listMemories = (userId) =>
  http.get(`/preferences/${userId}/memories`)
export const createMemory = (userId, payload) =>
  http.post(`/preferences/${userId}/memories`, payload)
export const updateMemory = (userId, preferenceId, fields) =>
  http.patch(`/preferences/${userId}/memories/${preferenceId}`, fields)
export const forgetMemory = (userId, preferenceId) =>
  http.delete(`/preferences/${userId}/memories/${preferenceId}`)

// User behavior events (行为埋点：采纳/换掉/反馈 → preference evidence)
export const recordBehaviorEvent = (userId, payload) =>
  http.post(`/users/${userId}/events`, payload)

// Saved outfits (Agentic contract Stage 1: SaveOutfit 命令)
export const saveOutfit = (payload) => http.post('/outfits/save', payload)
export const listSavedOutfits = (userId) =>
  http.get(`/users/${userId}/saved-outfits`)
export const deleteSavedOutfit = (userId, outfitId) =>
  http.delete(`/users/${userId}/saved-outfits/${outfitId}`)

// Item image URL helper
export const imageUrl = (path) => `/api${path}`
