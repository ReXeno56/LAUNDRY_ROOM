const KEY = 'laundry_preferences'
const DEFAULTS = { theme: 'system', buildingId: '', minutes: 5 }

function normalise(value) {
  const data = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  return {
    theme: ['system', 'light', 'dark'].includes(data.theme) ? data.theme : 'system',
    buildingId: typeof data.buildingId === 'string' ? data.buildingId : '',
    minutes: Number.isInteger(data.minutes) && data.minutes >= 1 && data.minutes <= 30 ? data.minutes : 5,
  }
}

function readPreferences() {
  try { return normalise(wx.getStorageSync(KEY)) } catch (_) { return { ...DEFAULTS } }
}

function savePreferences(value) {
  const data = normalise(value)
  try { wx.setStorageSync(KEY, data) } catch (_) { /* The current page still keeps the setting. */ }
  return data
}

module.exports = { readPreferences, savePreferences }
