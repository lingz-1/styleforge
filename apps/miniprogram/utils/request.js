/**
 * 轻量请求封装：wx.request + Promise。
 * baseUrl 来自 app.globalData（真机预览时改为电脑局域网 IP）。
 */
const request = (path, method = 'GET', data = {}) => {
  const app = getApp()
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${app.globalData.baseUrl}${path}`,
      method,
      data,
      timeout: 120000,
      header: { 'Content-Type': 'application/json' },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(res.data)
        } else {
          const payload = res.data || {}
          const envelope = payload.error || {}
          const detail = typeof payload.detail === 'string' ? payload.detail : ''
          const error = new Error(envelope.message || detail || `请求失败 ${res.statusCode}`)
          error.code = envelope.code || 'HTTP_ERROR'
          error.status = res.statusCode
          const headers = res.header || {}
          error.requestId = envelope.request_id
            || headers['X-Request-ID']
            || headers['x-request-id']
            || ''
          error.retryable = typeof envelope.retryable === 'boolean'
            ? envelope.retryable
            : (res.statusCode === 429 || res.statusCode >= 500)
          reject(error)
        }
      },
      fail: (err) => {
        const error = new Error(err.errMsg || '网络异常')
        error.code = 'NETWORK_ERROR'
        error.status = 0
        error.requestId = ''
        error.retryable = true
        reject(error)
      },
    })
  })
}

module.exports = {
  get: (path) => request(path),
  post: (path, data) => request(path, 'POST', data),
  put: (path, data) => request(path, 'PUT', data),
  del: (path) => request(path, 'DELETE'),
}
