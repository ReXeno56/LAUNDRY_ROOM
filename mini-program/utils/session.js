const { request } = require('./api')
let session = null
let loginPromise = null

function clearSession() {
  session = null
  try { wx.removeStorageSync('laundry_session') } catch (_) {}
}

function cachedSession() {
  if (!session) {
    try { session = wx.getStorageSync('laundry_session') || null } catch (_) { session = null }
  }
  if (session && typeof session.token === 'string' && session.token
      && (!session.expiresAt || Date.parse(session.expiresAt) > Date.now())) return session
  return null
}

function hasSession() { return !!cachedSession() }

function ensureSession(apiBase, force = false) {
  if (force) clearSession()
  if (loginPromise) return loginPromise
  const current = cachedSession()
  if (current) return Promise.resolve(current)
  loginPromise = new Promise((resolve, reject) => {
    wx.login({
      success(res) {
        if (res.code) resolve(res.code)
        else reject(new Error('微信登录失败，请重试'))
      },
      fail() { reject(new Error('微信登录失败，请重试')) },
    })
  }).then(code => request(apiBase, '/api/wx-login', { data: { code }, timeout: 10000 }))
    .then(data => {
      if (typeof data.token !== 'string' || !data.token) throw new Error('登录响应无效，请重试')
      session = data
      try { wx.setStorageSync('laundry_session', session) } catch (_) {}
      return session
    }).finally(() => { loginPromise = null })
  return loginPromise
}

async function authenticatedRequest(apiBase, path, options = {}, includeToken = false) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const current = await ensureSession(apiBase, !!attempt)
    try {
      const result = await request(apiBase, path, {
        ...options,
        header: { ...(options.header || {}), Authorization: 'Bearer ' + current.token },
        ...(includeToken ? { data: { ...(options.data || {}), token: current.token } } : {}),
      })
      if (result.ok !== true) throw new Error(result.error || '操作失败，请重试')
      return result
    } catch (error) {
      if (error.statusCode === 401 && !attempt) continue
      if (error.statusCode === 401) clearSession()
      throw error
    }
  }
}

async function submitReminder(apiBase, data) {
  const result = await authenticatedRequest(apiBase, '/api/wx-subscribe', { method: 'POST', data }, true)
  if (typeof result.reminderId !== 'string') throw new Error('提醒设置失败，请重试')
  return result
}

async function getReminders(apiBase) {
  const result = await authenticatedRequest(apiBase, '/api/wx-reminders')
  if (!Array.isArray(result.active) || !Array.isArray(result.history)) throw new Error('提醒列表格式错误')
  return result
}

function cancelReminder(apiBase, reminderId) {
  return authenticatedRequest(apiBase, '/api/wx-reminders/' + encodeURIComponent(reminderId) + '/cancel',
    { method: 'POST' })
}

module.exports = { ensureSession, hasSession, submitReminder, getReminders, cancelReminder }
