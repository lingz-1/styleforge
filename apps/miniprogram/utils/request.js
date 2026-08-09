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
      header: { 'Content-Type': 'application/json' },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 400) {
          resolve(res.data)
        } else {
          reject(res.data && res.data.detail ? res.data.detail : `请求失败 ${res.statusCode}`)
        }
      },
      fail: (err) => reject(err.errMsg || '网络异常'),
    })
  })
}

module.exports = {
  get: (path) => request(path),
  post: (path, data) => request(path, 'POST', data),
  put: (path, data) => request(path, 'PUT', data),
  del: (path) => request(path, 'DELETE'),
}
