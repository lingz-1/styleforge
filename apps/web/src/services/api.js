import axios from 'axios'

const http = axios.create({ baseURL: '/api', timeout: 120000 })

// Health
export const getHealth = () => http.get('/health')

// Catalog taxonomy (main category required, subtype optional)
export const getTaxonomy = () => http.get('/catalog/taxonomy')

// Wardrobe
export const getWardrobe = (userId) => http.get(`/wardrobes/${userId}`)
export const removeWardrobeItem = (userId, itemId) =>
  http.delete(`/wardrobes/${userId}/items/${itemId}`)
export const createPhotoItem = (userId, payload) =>
  http.post(`/wardrobes/${userId}/items/photo`, payload)
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

// Evaluation weights (五维偏好)
export const getEvaluationWeights = (userId) =>
  http.get(`/preferences/${userId}/evaluation`)
export const saveEvaluationWeights = (userId, weights) =>
  http.put(`/preferences/${userId}/evaluation`, { weights })

// Item image URL helper
export const imageUrl = (path) => `/api${path}`
