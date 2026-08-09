/**
 * Download http(s) images to local temp files so the <image> component can
 * render them. WeChat's <image> on real devices only accepts local paths or
 * https URLs — plain http URLs are blocked. wx.downloadFile itself works for
 * http during development (with "不校验合法域名" checked), so we download once
 * and feed the local temp path to <image>.
 */

const cache = new Map() // url -> tempFilePath (already downloaded this session)
const pending = new Map() // url -> Promise (dedupe concurrent downloads)

/**
 * @param {string} url
 * @returns {Promise<string>} local temp file path, or '' on failure
 */
function downloadImage(url) {
  if (!url) return Promise.resolve('')
  if (cache.has(url)) return Promise.resolve(cache.get(url))
  if (pending.has(url)) return pending.get(url)

  const p = new Promise((resolve) => {
    wx.downloadFile({
      url,
      success: (res) => {
        if (res.statusCode === 200 && res.tempFilePath) {
          cache.set(url, res.tempFilePath)
          resolve(res.tempFilePath)
        } else {
          resolve('')
        }
      },
      fail: () => resolve(''),
    })
  })
  pending.set(url, p)
  p.then(() => pending.delete(url), () => pending.delete(url))
  return p
}

module.exports = { downloadImage }
