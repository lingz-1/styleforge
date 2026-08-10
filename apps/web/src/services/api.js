import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 120000 })

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

// Recommendation
export const recommend = (userId, request, maxResults = 3) =>
  http.post('/recommendations', {
    user_id: userId,
    request,
    max_results: maxResults,
  })

// Unified v3.3 task execution (recommend / modify / advice / compatibility / gap)
export const executeTask = (payload) => http.post('/tasks/execute', payload)
export const getTaskRun = (userId, runId) => http.get(`/tasks/${userId}/${runId}`)

// Evaluation weights (五维偏好)
export const getEvaluationWeights = (userId) =>
  http.get(`/preferences/${userId}/evaluation`)
export const saveEvaluationWeights = (userId, weights) =>
  http.put(`/preferences/${userId}/evaluation`, { weights })

// Item image URL helper
export const imageUrl = (path) => `/api${path}`
