const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const root = path.resolve(__dirname, '..')
const flush = () => new Promise(resolve => setImmediate(resolve))
const buildings = [{ id: '27131', name: '西21楼' }, { id: '27136', name: '西16楼' }]
const finishAt = new Date(Date.now() + 7200000).toISOString()
const machine = { id: '42', name: '洗衣机', state: 2, finishAt }
const snapshot = (machines = [machine], id = '27131') => ({
  buildingId: id, washers: machines, dryers: [], stale: false, fetchedAt: new Date().toISOString(),
})
const reminder = (changes = {}) => ({
  reminderId: 'r1', buildingId: '27131', buildingName: '西21楼', deviceId: '42',
  name: '洗衣机', finishAt, minutes: 5, status: 'pending', canCancel: true,
  remindAtISO: new Date(Date.parse(finishAt) - 300000).toISOString(), ...changes,
})

function harness(options = {}) {
  let page, systemTheme = options.theme || 'light', themeHandler, subscriptions = 0
  const storage = { ...(options.storage || {}) }, requests = [], timers = new Map(), toasts = [], nav = []
  let timerId = 0, logins = 0
  const wx = {
    request(opts) {
      const task = { options: opts, done: false, abort() { this.aborted = true } }
      requests.push(task); return task
    },
    getStorageSync(key) { if (options.brokenStorage) throw new Error('storage unavailable'); return storage[key] },
    setStorageSync(key, value) { if (options.brokenStorage) throw new Error('storage unavailable'); storage[key] = value },
    removeStorageSync(key) { if (options.brokenStorage) throw new Error('storage unavailable'); delete storage[key] },
    getAppBaseInfo: () => ({ theme: systemTheme }),
    onThemeChange(fn) { themeHandler = fn },
    offThemeChange(fn) { if (themeHandler === fn) themeHandler = null },
    setNavigationBarColor(values) { nav.push(values) }, setBackgroundColor() {}, setBackgroundTextStyle() {},
    login(opts) { logins++; opts.success({ code: 'test-code' }) },
    requestSubscribeMessage(opts) { subscriptions++; opts.success({ [opts.tmplIds[0]]: 'accept' }) },
    showToast: value => toasts.push(value), showShareMenu() {}, stopPullDownRefresh() {},
  }
  const app = { globalData: { apiBase: 'https://test.invalid' } }
  const context = vm.createContext({ wx, getApp: () => app, Page: value => { page = value },
    console, setTimeout(fn, ms) { timers.set(++timerId, { fn, ms }); return timerId },
    clearTimeout(id) { timers.delete(id) } })
  const modules = new Map()
  function load(filename) {
    filename = filename.endsWith('.js') ? filename : filename + '.js'
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }; modules.set(filename, module)
    const wrapper = vm.runInContext('(function(require,module,exports){' + fs.readFileSync(filename, 'utf8') + '\n})', context)
    wrapper(name => load(path.resolve(path.dirname(filename), name)), module, module.exports)
    return module.exports
  }
  load(path.join(root, 'mini-program/pages/index/index.js'))
  page.data = JSON.parse(JSON.stringify(page.data))
  page.setData = values => Object.assign(page.data, values)
  page.onLoad(options.query || {})
  const take = suffix => {
    const task = requests.find(task => !task.done && task.options.url.endsWith(suffix))
    assert.ok(task, 'expected request: ' + suffix)
    return task
  }
  const answer = async (task, data, statusCode = 200) => {
    task.done = true; task.options.success({ statusCode, data }); await flush()
  }
  async function boot(machines) {
    page.onShow(); await flush()
    await answer(take('/api/config'), { buildings, templateId: 'test-template' })
    const task = take('/api/devices')
    await answer(task, snapshot(machines, task.options.data.id))
  }
  async function open(rows = [reminder()], history = []) {
    page.toggleReminders(); await flush()
    const login = requests.find(task => !task.done && task.options.url.endsWith('/api/wx-login'))
    if (login) await answer(login, { token: 'test-token' })
    await answer(take('/api/wx-reminders'), { ok: true, active: rows, history })
  }
  function pollReminder() {
    const entry = [...timers].find(([, value]) => value.ms === 30000 && value.fn.toString().includes('fetchReminders'))
    assert.ok(entry, 'reminder polling should exist')
    timers.delete(entry[0]); entry[1].fn()
  }
  return { page, requests, storage, timers, toasts, nav, wx, load, take, answer, boot, open, pollReminder,
    logins: () => logins, subscriptions: () => subscriptions,
    theme(value) { systemTheme = value; if (themeHandler) themeHandler({ theme: value }) } }
}

test('mini enhancements: defaults follow system, manual modes persist and update native navigation', () => {
  const h = harness({ theme: 'dark' })
  assert.equal(h.page.data.themeMode, 'system')
  assert.equal(h.page.data.theme, 'dark')
  assert.equal(h.nav.at(-1).frontColor, '#ffffff')
  h.page.chooseTheme({ currentTarget: { dataset: { mode: 'light' } } })
  assert.equal(h.page.data.theme, 'light')
  assert.equal(h.nav.at(-1).backgroundColor, '#f5f7f3')
  h.theme('dark'); assert.equal(h.page.data.theme, 'light')
  const next = harness({ theme: 'dark', storage: h.storage })
  assert.equal(next.page.data.themeMode, 'light')
  next.page.chooseTheme({ currentTarget: { dataset: { mode: 'system' } } })
  next.theme('light'); assert.equal(next.page.data.theme, 'light')
  next.theme('dark'); assert.equal(next.page.data.theme, 'dark')
})

test('mini enhancements: invalid preferences and storage failures fall back safely', async () => {
  for (const prefs of [null, 'broken', { theme: 'other', buildingId: 1, minutes: -2 },
    { theme: 'system', minutes: 31 }, { theme: 'system', minutes: '8' }]) {
    const h = harness({ storage: { laundry_preferences: prefs } })
    assert.equal(h.page.data.remindMin, 5)
    assert.equal(h.page.data.themeMode, 'system')
  }
  const h = harness({ brokenStorage: true }); await h.boot()
  h.page.onRemindMinInput({ detail: { value: 9 } })
  h.page.chooseTheme({ currentTarget: { dataset: { mode: 'dark' } } })
  assert.equal(h.page.data.remindMin, 9); assert.equal(h.page.data.theme, 'dark')
  await h.open([], [])
  assert.equal(h.page.data.remindersLoaded, true)
  assert.equal(h.logins(), 1)
})

test('mini enhancements: saved building, minutes and share links choose the right building', async () => {
  const storage = { laundry_preferences: { theme: 'system', buildingId: '27136', minutes: 12 } }
  const h = harness({ storage }); await h.boot()
  assert.equal(h.page.data.currentIdx, 1); assert.equal(h.page.data.remindMin, 12)
  h.page.showRemindOpts({ detail: h.page.data.washers[0] })
  assert.equal(h.page.data.remindMin, 12)
  h.page.switchBuilding({ currentTarget: { dataset: { idx: 0 } } })
  assert.equal(h.storage.laundry_preferences.buildingId, '27131')
  const link = harness({ storage, query: { id: '27131' } }); await link.boot()
  assert.equal(link.page.data.currentIdx, 0)
  const invalid = harness({ storage: { laundry_preferences: { ...storage.laundry_preferences, buildingId: 'removed' } } })
  await invalid.boot(); assert.equal(invalid.page.data.currentIdx, 0)
})

test('mini enhancements: stable sorting puts idle first, valid finish times next, abnormal last', () => {
  const h = harness(), { sortDevices } = h.load(path.join(root, 'mini-program/utils/devices.js'))
  const list = [
    { id: 'offline', state: 4 }, { id: 'late', state: 2, finishAt: '2030-01-01T02:00:00Z' },
    { id: 'idle1', state: 1 }, { id: 'invalid', state: 2, finishTime: 'bad' },
    { id: 'early1', state: 2, finishAt: '2030-01-01T01:00:00Z' },
    { id: 'idle2', state: 1 }, { id: 'early2', state: 2, finishAt: '2030-01-01T01:00:00Z' },
    { id: 'fault', state: 3 },
  ]
  assert.deepEqual(Array.from(sortDevices(list), row => row.id),
    ['idle1', 'idle2', 'early1', 'early2', 'late', 'invalid', 'offline', 'fault'])
})

test('mini enhancements: idle filter preserves full counts and in-progress reminder edits', async () => {
  const h = harness(); await h.boot([machine, { id: 'idle', state: 1 }, { id: 'offline', state: 4 }])
  h.page.showRemindOpts({ detail: h.page.data.washers.find(row => row.id === '42') })
  h.page.onRemindMinInput({ detail: { value: 13 } })
  h.page.toggleIdleOnly({ detail: { value: true } })
  assert.equal(h.page.data.washers.length, 1)
  assert.equal(h.page.data.washerCount, 3); assert.equal(h.page.data.runningCount, 1)
  h.page.fetchData(); await flush()
  await h.answer(h.take('/api/devices'), snapshot([machine, { id: 'idle', state: 1 }, { id: 'offline', state: 4 }]))
  h.page.toggleIdleOnly({ detail: { value: false } })
  assert.equal(h.page.data.remindingId, '42'); assert.equal(h.page.data.remindMin, 13)
  assert.equal(h.page.data.washers.length, 3)
})

test('mini enhancements: reminder list authenticates once, polls while expanded, pauses in background', async () => {
  const h = harness(); await h.boot()
  assert.equal(h.logins(), 0)
  await h.open([reminder(), reminder({ reminderId: 'west16', buildingId: '27136', buildingName: '西16楼' })])
  assert.equal(h.page.data.reminderActive.length, 2)
  assert.equal(h.requests.find(task => task.options.url.endsWith('/api/wx-reminders')).options.header.Authorization, 'Bearer test-token')
  assert.equal(h.page.data.washers[0].reminderSet, true)
  h.pollReminder(); await flush()
  await h.answer(h.take('/api/wx-reminders'), { ok: true, active: [reminder()], history: [] })
  assert.equal(h.logins(), 1); assert.equal(h.subscriptions(), 0)
  h.page.onHide(); assert.equal(h.timers.size, 0)
  h.page.onShow(); await flush()
  assert.ok(h.take('/api/wx-reminders'))
  h.page.toggleReminders()
  await h.answer(h.take('/api/wx-reminders'), { ok: true, active: [reminder()], history: [] })
  assert.equal([...h.timers.values()].filter(row => row.fn.toString().includes('fetchReminders')).length, 0)
})

test('mini enhancements: list failures keep previous records and sending/uncertain states are explicit', async () => {
  const h = harness(); await h.boot()
  await h.open([reminder({ status: 'sending', canCancel: false })],
    [reminder({ reminderId: 'uncertain', status: 'uncertain', canCancel: false }),
      reminder({ reminderId: 'failed', status: 'failed', canCancel: false })])
  assert.equal(h.page.data.reminderActive[0].sending, true)
  assert.equal(h.page.data.reminderHistory[0].statusText, '结果待确认')
  assert.equal(h.page.data.reminderHistory[1].statusText, '发送失败')
  const count = h.requests.length
  await h.page.cancelSavedReminder({ currentTarget: { dataset: { id: 'r1' } } })
  assert.equal(h.requests.length, count)
  h.page.fetchReminders(); await flush()
  await h.answer(h.take('/api/wx-reminders'), { error: 'network failed' }, 502)
  assert.equal(h.page.data.reminderActive.length, 1)
  assert.equal(h.page.data.reminderError, 'network failed')
})

test('mini enhancements: cancelling twice submits once and clears device badge immediately', async () => {
  const h = harness(); await h.boot(); await h.open()
  const event = { currentTarget: { dataset: { id: 'r1' } } }
  const pending = h.page.cancelSavedReminder(event)
  h.page.cancelSavedReminder(event); await flush()
  assert.equal(h.requests.filter(task => task.options.url.endsWith('/r1/cancel')).length, 1)
  await h.answer(h.take('/r1/cancel'), { ok: true, ...reminder({ status: 'cancelled', canCancel: false }) })
  await pending
  assert.equal(h.page.data.washers[0].reminderSet, false)
  assert.equal(h.page.data.reminderActive.length, 0)
  assert.equal(h.page.data.reminderHistory[0].statusText, '已取消')
  assert.equal(h.page.data.reminderCancellingId, '')
})

test('mini enhancements: cancel conflict refreshes state and preserves its last record', async () => {
  const h = harness(); await h.boot(); await h.open()
  const pending = h.page.cancelSavedReminder({ currentTarget: { dataset: { id: 'r1' } } })
  await flush()
  await h.answer(h.take('/r1/cancel'), { error: '提醒正在发送，无法取消' }, 409)
  await pending
  assert.equal(h.page.data.reminderActive.length, 1)
  assert.equal(h.toasts.at(-1).icon, 'none')
  await h.answer(h.take('/api/wx-reminders'), {
    ok: true, active: [reminder({ status: 'sending', canCancel: false })], history: [],
  })
  assert.equal(h.page.data.reminderActive[0].canCancel, false)
})

test('mini enhancements: duplicate creation shows server minutes and list refresh does not delay success', async () => {
  const h = harness(); await h.boot()
  h.storage.laundry_session = { token: 'cached-token' }
  h.page.showRemindOpts({ detail: h.page.data.washers[0] })
  h.page.onRemindMinInput({ detail: { value: 12 } })
  const pending = h.page.confirmReminder({ detail: h.page.data.washers[0] }); await flush()
  await h.answer(h.take('/api/wx-subscribe'), { ok: true, reminderId: 'r1', status: 'pending', minutes: 5 })
  await pending
  assert.equal(h.page.data.submitting, false)
  assert.equal(h.page.data.washers[0].reminderMinutes, 5)
  assert.match(h.toasts.at(-1).title, /提前5分钟.*先取消/)
  assert.equal(h.subscriptions(), 1)
  await h.answer(h.take('/api/wx-reminders'), { error: 'refresh failed' }, 502)
  assert.equal(h.page.data.washers[0].reminderSet, true)
  assert.equal(h.toasts.at(-1).icon, 'success')
})

test('mini enhancements: reminder GET reauthenticates once on 401 without subscription authorization', async () => {
  const h = harness(); await h.boot()
  h.storage.laundry_session = { token: 'expired-token' }
  h.page.toggleReminders(); await flush()
  await h.answer(h.take('/api/wx-reminders'), { error: 'expired' }, 401)
  await h.answer(h.take('/api/wx-login'), { token: 'fresh-token' })
  const retry = h.take('/api/wx-reminders')
  assert.equal(retry.options.header.Authorization, 'Bearer fresh-token')
  await h.answer(retry, { ok: true, active: [], history: [] })
  assert.equal(h.logins(), 1); assert.equal(h.subscriptions(), 0)
  assert.equal(h.page.data.remindersLoaded, true)
})

test('mini enhancements: newer active reminder is not overwritten by older history for the same device', async () => {
  const h = harness(); await h.boot()
  await h.open([reminder({ minutes: 11 })], [reminder({ reminderId: 'old', status: 'sent', minutes: 2,
    finishAt: new Date(Date.parse(finishAt) - 3600000).toISOString(), canCancel: false })])
  assert.equal(h.page.data.washers[0].reminderMinutes, 11)
})

test('mini enhancements: cancellation permits a new reminder with changed minutes and a new ID', async () => {
  const h = harness(); await h.boot(); await h.open()
  const cancel = h.page.cancelSavedReminder({ currentTarget: { dataset: { id: 'r1' } } }); await flush()
  const before = h.requests.length
  await h.page.confirmReminder({ detail: h.page.data.washers[0] })
  assert.equal(h.requests.length, before)
  await h.answer(h.take('/r1/cancel'), { ok: true, ...reminder({ status: 'cancelled', canCancel: false }) })
  await cancel
  await h.answer(h.take('/api/wx-reminders'), {
    ok: true, active: [], history: [reminder({ status: 'cancelled', canCancel: false })],
  })
  h.page.showRemindOpts({ detail: h.page.data.washers[0] })
  h.page.onRemindMinInput({ detail: { value: 12 } })
  const create = h.page.confirmReminder({ detail: h.page.data.washers[0] }); await flush()
  const task = h.take('/api/wx-subscribe')
  assert.equal(task.options.data.minutes, 12)
  await h.answer(task, { ok: true, reminderId: 'r2', minutes: 12, status: 'pending' })
  await create
  assert.equal(h.page.data.washers[0].reminderMinutes, 12)
  assert.equal(h.page._reminders['27131:42'].reminderId, 'r2')
  assert.equal(h.subscriptions(), 1)
})

test('mini enhancements: old in-flight list does not resurrect a cancelled reminder', async () => {
  const h = harness(); await h.boot(); await h.open()
  h.page.fetchReminders(); await flush()
  const oldList = h.take('/api/wx-reminders')
  const pending = h.page.cancelSavedReminder({ currentTarget: { dataset: { id: 'r1' } } }); await flush()
  await h.answer(h.take('/r1/cancel'), { ok: true, ...reminder({ status: 'cancelled', canCancel: false }) })
  await pending
  await h.answer(oldList, { ok: true, active: [reminder()], history: [] })
  assert.equal(h.page.data.reminderActive.length, 0)
  assert.equal(h.page.data.washers[0].reminderSet, false)
  await h.answer(h.take('/api/wx-reminders'), {
    ok: true, active: [], history: [reminder({ status: 'cancelled', canCancel: false })],
  })
  assert.equal(h.page.data.reminderHistory[0].statusText, '已取消')
})

test('mini enhancements: failed device refresh keeps edits for a later successful refresh', async () => {
  const h = harness(); await h.boot()
  h.page.showRemindOpts({ detail: h.page.data.washers[0] })
  h.page.onRemindMinInput({ detail: { value: 17 } })
  h.page.fetchData(); await flush()
  await h.answer(h.take('/api/devices'), { error: 'upstream unavailable' }, 502)
  assert.equal(h.page.data.remindingId, '42')
  assert.equal(h.page.data.remindMin, 17)
  assert.equal(h.page.data.washers[0].canRemind, false)
  h.page.fetchData(); await flush()
  await h.answer(h.take('/api/devices'), snapshot())
  assert.equal(h.page.data.remindingId, '42')
  assert.equal(h.page.data.remindMin, 17)
  assert.equal(h.page.data.washers[0].canRemind, true)
})
