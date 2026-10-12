function request(apiBase, path, options = {}) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: apiBase + path,
      timeout: 15000,
      ...options,
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300 && res.data && typeof res.data === 'object') {
          resolve(res.data)
        } else {
          const error = new Error((res.data && res.data.error) || '服务暂不可用，请稍后重试')
          error.statusCode = res.statusCode
          reject(error)
        }
      },
      fail() { reject(new Error('网络异常，请稍后重试')) },
    })
  })
}

module.exports = { request }
