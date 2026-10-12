const app = getApp()
const { request } = require('../../utils/api')
const { submitReminder, hasSession, getReminders, cancelReminder } = require('../../utils/session')
const { readPreferences, savePreferences } = require('../../utils/preferences')
const { finishTimestamp, shanghaiTime, reminderDate, reservationTime, availableNow, decorate, sortDevices } = require('../../utils/devices')
const THEME_LABELS = { system: '跟随系统', light: '浅色', dark: '深色' }
const REMINDER_STATES = { pending: '待发送', retry: '正在重试', sending: '发送中',
  sent: '已发送', failed: '发送失败', permanent_failure: '发送失败', uncertain: '结果待确认', cancelled: '已取消' }

Page({
  data: {
    buildings: [], currentIdx: 0, washers: [], dryers: [], washerCount: 0, dryerCount: 0,
    idleCount: 0, runningCount: 0, errorCount: 0, washerIdleCount: 0, dryerIdleCount: 0,
    loading: true, hasData: false, error: '', stale: false, lastUpdate: '', idleOnly: false,
    remindingId: null, remindMin: 5, remindTimeText: '', submitting: false,
    themeMode: 'system', theme: 'light', themeLabel: '跟随系统', themeMenuOpen: false,
    remindersExpanded: false, reminderActive: [], reminderHistory: [], remindersLoaded: false,
    reminderLoading: false, reminderError: '', reminderUpdatedAt: '', reminderCancellingId: '',
    hasReservations: false, reservationStatus: '', reservationUpdatedAt: '', reservationSourceUrl: '', availabilityUnconfirmed: false,
  },

  onLoad(options) {
    this._options = options || {}
    this._sequence = 0
    this._visible = false
    this._destroyed = false
    this._reminders = {}
    this._allWashers = []; this._allDryers = []
    this._callbacks = []
    this._reminderSequence = 0; this._reminderVersion = 0
    this._preferences = readPreferences()
    this._systemTheme = this._readSystemTheme()
    this.setData({ remindMin: this._preferences.minutes })
    this._applyTheme()
    this._themeHandler = event => {
      this._systemTheme = event.theme === 'dark' ? 'dark' : 'light'
      if (this.data.themeMode === 'system') this._applyTheme()
    }
    if (typeof wx.onThemeChange === 'function') wx.onThemeChange(this._themeHandler)
    wx.showShareMenu({ menus: ['shareAppMessage', 'shareTimeline'] })
  },

  _readSystemTheme() {
    try {
      const info = typeof wx.getAppBaseInfo === 'function' ? wx.getAppBaseInfo()
        : typeof wx.getSystemInfoSync === 'function' ? wx.getSystemInfoSync() : {}
      return info.theme === 'dark' ? 'dark' : 'light'
    } catch (_) { return 'light' }
  },

  _applyTheme() {
    const mode = this._preferences.theme
    const theme = mode === 'system' ? this._systemTheme : mode
    this.setData({ themeMode: mode, theme, themeLabel: THEME_LABELS[mode] })
    const dark = theme === 'dark'
    if (typeof wx.setNavigationBarColor === 'function') wx.setNavigationBarColor({
      frontColor: dark ? '#ffffff' : '#000000', backgroundColor: dark ? '#111316' : '#f5f7f3',
      animation: { duration: 0, timingFunc: 'linear' },
    })
    if (typeof wx.setBackgroundColor === 'function') wx.setBackgroundColor({
      backgroundColor: dark ? '#111316' : '#f5f7f3',
      backgroundColorTop: dark ? '#111316' : '#f5f7f3',
      backgroundColorBottom: dark ? '#111316' : '#f5f7f3',
    })
    if (typeof wx.setBackgroundTextStyle === 'function') wx.setBackgroundTextStyle({ textStyle: dark ? 'light' : 'dark' })
  },

  toggleThemeMenu() { this.setData({ themeMenuOpen: !this.data.themeMenuOpen }) },
  chooseTheme(e) {
    const mode = e.currentTarget.dataset.mode
    if (!THEME_LABELS[mode]) return
    this._preferences = savePreferences({ ...this._preferences, theme: mode })
    this.setData({ themeMenuOpen: false })
    this._applyTheme()
  },

  onShow() {
    this._visible = true
    this._systemTheme = this._readSystemTheme()
    this._applyTheme()
    this.fetchData()
    if (this.data.remindersExpanded || hasSession()) this.fetchReminders()
  },

  onHide() {
    this._visible = false
    this.setData({ themeMenuOpen: false })
    this._stopFetching()
    this._stopReminderPolling()
  },

  onUnload() {
    this._visible = false
    this._destroyed = true
    this._stopFetching()
    this._stopReminderPolling()
    if (typeof wx.offThemeChange === 'function') wx.offThemeChange(this._themeHandler)
  },

  _stopFetching() {
    clearTimeout(this.timer)
    this.timer = null
    this._sequence += 1
    this._fetching = false
    if (this._request) this._request.abort()
    this._request = null
    this._callbacks.splice(0).forEach(cb => cb())
  },

  _stopReminderPolling() {
    clearTimeout(this.reminderTimer)
    this.reminderTimer = null
    this._reminderSequence += 1
    this._reminderFetching = false
    this._reminderRefreshAgain = false
    if (!this._destroyed) this.setData({ reminderLoading: false })
  },

  async _loadConfig() {
    if (this._configReady) return
    if (!this._configPromise) {
      this._configPromise = request(app.globalData.apiBase, '/api/config')
        .then(config => {
          if (!Array.isArray(config.buildings) || !config.buildings.length
              || typeof config.templateId !== 'string' || !config.templateId
              || config.buildings.some(b => typeof b.id !== 'string' || typeof b.name !== 'string')) {
            throw new Error('服务配置无效，请稍后重试')
          }
          app.globalData.buildings = config.buildings
          app.globalData.templateId = config.templateId
          if (!this._destroyed) {
            const wanted = this._options.id || this._preferences.buildingId
            const selected = config.buildings.findIndex(b => b.id === wanted)
            const currentIdx = Math.max(0, selected)
            this.setData({ buildings: config.buildings, currentIdx })
            this._preferences = savePreferences({ ...this._preferences, buildingId: config.buildings[currentIdx].id })
          }
          this._configReady = true
        }).finally(() => { this._configPromise = null })
    }
    return this._configPromise
  },

  onShareAppMessage() {
    const b = this.data.buildings[this.data.currentIdx] || { name: '', id: '' }
    return { title: `${b.name} · ${b.id === '27136' ? '可用' : '空闲'}${this.data.idleCount} 使用中${this.data.runningCount} — 洗衣状态`,
      path: `/pages/index/index?id=${b.id}` }
  },

  onShareTimeline() {
    const share = this.onShareAppMessage()
    const b = this.data.buildings[this.data.currentIdx] || { id: '' }
    return { title: share.title, query: `id=${b.id}` }
  },

  onPullDownRefresh() {
    this.fetchData(() => wx.stopPullDownRefresh())
    if (this.data.remindersExpanded || hasSession()) this.fetchReminders()
  },
  retry() { this.fetchData() },

  switchBuilding(e) {
    const idx = Number(e.currentTarget.dataset.idx)
    if (this.data.submitting || idx === this.data.currentIdx || !this.data.buildings[idx]) return
    this._stopFetching()
    this._allWashers = []; this._allDryers = []
    this._preferences = savePreferences({ ...this._preferences, buildingId: this.data.buildings[idx].id })
    this.setData({ currentIdx: idx, washers: [], dryers: [], washerCount: 0, dryerCount: 0,
      loading: true, hasData: false, error: '', stale: false, lastUpdate: '',
      idleCount: 0, runningCount: 0, errorCount: 0, washerIdleCount: 0, dryerIdleCount: 0,
      remindingId: null, remindTimeText: '' })
    this.setData({ hasReservations: false, reservationStatus: '', reservationUpdatedAt: '', reservationSourceUrl: '', availabilityUnconfirmed: false })
    this.fetchData()
  },

  _currentReminders() {
    const building = this.data.buildings[this.data.currentIdx]
    if (!building) return {}
    const result = {}
    Object.keys(this._reminders).forEach(key => {
      const item = this._reminders[key]
      if (item.buildingId === building.id) result[item.deviceId] = item
    })
    return result
  },

  _setDeviceLists(washers = this._allWashers, dryers = this._allDryers, stale = this.data.stale) {
    const reminders = this._currentReminders()
    this._allWashers = sortDevices(washers.map(d => decorate(d, stale, reminders)))
    this._allDryers = sortDevices(dryers.map(d => decorate(d, stale, reminders)))
    const all = this._allWashers.concat(this._allDryers)
    const visible = list => this.data.idleOnly ? list.filter(availableNow) : list
    this.setData({
      washers: visible(this._allWashers), dryers: visible(this._allDryers),
      washerCount: this._allWashers.length, dryerCount: this._allDryers.length,
      availabilityUnconfirmed: all.some(device => device.availableNow === null),
      idleCount: all.filter(availableNow).length,
      washerIdleCount: this._allWashers.filter(availableNow).length,
      dryerIdleCount: this._allDryers.filter(availableNow).length,
      runningCount: all.filter(d => d.state === 2).length,
      errorCount: all.filter(d => d.state !== 1 && d.state !== 2).length,
    })
  },

  toggleIdleOnly(e) {
    this.setData({ idleOnly: !!e.detail.value })
    this._setDeviceLists()
  },

  copyReservationSource() {
    const source = this.data.reservationSourceUrl
    if (!/^https:\/\/docs\.qq\.com\//.test(source)) return
    wx.setClipboardData({ data: source,
      success: () => wx.showToast({ title: '预约文档链接已复制', icon: 'none' }),
      fail: () => wx.showToast({ title: '复制失败，请重试', icon: 'none' }) })
  },

  onRemindMinInput(e) {
    if (this.data.submitting || this.data.reminderCancellingId) return
    const minutes = Math.max(1, Math.min(30, Math.round(Number(e.detail.value)) || 5))
    this._preferences = savePreferences({ ...this._preferences, minutes })
    this.setData({ remindMin: minutes, remindTimeText: shanghaiTime(this._remindFinish - minutes * 60000) })
  },

  showRemindOpts(e) {
    const device = e.detail
    if (this.data.submitting || this.data.reminderCancellingId || !device.canRemind) return
    this._remindFinish = finishTimestamp(device)
    this.setData({ remindingId: this.data.remindingId === device.id ? null : device.id,
      remindTimeText: shanghaiTime(this._remindFinish - this.data.remindMin * 60000) })
  },

  async confirmReminder(e) {
    if (this.data.submitting || this.data.reminderCancellingId) return
    const device = e.detail
    const building = this.data.buildings[this.data.currentIdx]
    const finish = finishTimestamp(device)
    const minutes = this.data.remindMin
    if (!building || !device.canRemind || finish === null || finish - minutes * 60000 <= Date.now()) {
      wx.showToast({ title: '提醒时间已过，请刷新或减少分钟数', icon: 'none' }); return
    }
    this.setData({ submitting: true })
    try {
      const templateId = app.globalData.templateId
      // Invoke authorization directly in the tap handler, before asynchronous login.
      await new Promise((resolve, reject) => {
        wx.requestSubscribeMessage({
          tmplIds: [templateId],
          success(res) {
            if (res[templateId] === 'accept') resolve()
            else reject(new Error('未授权订阅消息，提醒未设置'))
          },
          fail() { reject(new Error('订阅授权失败，请重试')) },
        })
      })
      const result = await submitReminder(app.globalData.apiBase, {
        buildingId: building.id, deviceId: device.id, templateId,
        finishAt: new Date(finish).toISOString(), minutes,
      })
      this._reminderVersion += 1
      this._reminders[building.id + ':' + device.id] = {
        buildingId: building.id, deviceId: device.id, finish,
        reminderId: result.reminderId, minutes: result.minutes,
      }
      if (!this._destroyed) {
        this.setData({ remindingId: null })
        this._setDeviceLists()
        wx.showToast({ title: result.status === 'sent' ? '该次提醒已发送'
          : result.minutes !== minutes ? `已有提前${result.minutes}分钟提醒，请先取消再重设`
          : `已设置提前${result.minutes}分钟提醒`, icon: 'success' })
        this.fetchReminders()
      }
    } catch (error) {
      if (!this._destroyed) wx.showToast({ title: error.message, icon: 'none' })
    } finally {
      if (!this._destroyed) this.setData({ submitting: false })
    }
  },

  toggleReminders() {
    const expanded = !this.data.remindersExpanded
    this.setData({ remindersExpanded: expanded })
    if (expanded) this.fetchReminders()
    else { clearTimeout(this.reminderTimer); this.reminderTimer = null }
  },

  _viewReminder(item) {
    const statusText = REMINDER_STATES[item.status] || item.statusText || '状态未知'
    return { ...item, statusText, remindDateText: reminderDate(item.remindAtISO) || item.remindAt,
      canCancel: (item.status === 'pending' || item.status === 'retry') && item.canCancel === true,
      sending: item.status === 'sending',
      statusClass: item.status === 'pending' || item.status === 'sent' ? 'positive'
        : item.status === 'retry' || item.status === 'uncertain' ? 'warning'
        : (item.status === 'failed' || item.status === 'permanent_failure') ? 'negative' : 'muted' }
  },

  async fetchReminders() {
    if (!this._visible || this._destroyed) return
    if (this._reminderFetching) { this._reminderRefreshAgain = true; return }
    clearTimeout(this.reminderTimer)
    this._reminderFetching = true
    const sequence = ++this._reminderSequence, version = this._reminderVersion
    this.setData({ reminderLoading: true })
    try {
      const result = await getReminders(app.globalData.apiBase)
      if (sequence !== this._reminderSequence || !this._visible || this._destroyed) return
      if (version !== this._reminderVersion) { this._reminderRefreshAgain = true; return }
      const active = result.active.map(item => this._viewReminder(item))
      const history = result.history.map(item => this._viewReminder(item))
      const reminders = {}
      active.concat(history).forEach(item => {
        const key = item.buildingId + ':' + item.deviceId
        if (item.status !== 'cancelled' && (!reminders[key] || Date.parse(item.finishAt) > reminders[key].finish)) reminders[key] = {
          buildingId: item.buildingId, deviceId: item.deviceId, finish: Date.parse(item.finishAt),
          reminderId: item.reminderId, minutes: item.minutes,
        }
      })
      this._reminders = reminders
      this.setData({ reminderActive: active, reminderHistory: history, remindersLoaded: true,
        reminderError: '', reminderUpdatedAt: shanghaiTime(Date.now(), true) })
      this._setDeviceLists()
    } catch (error) {
      if (sequence === this._reminderSequence && !this._destroyed) this.setData({ reminderError: error.message })
    } finally {
      if (sequence === this._reminderSequence) {
        this._reminderFetching = false
        this.setData({ reminderLoading: false })
        if (this._visible && !this._destroyed) {
          if (this._reminderRefreshAgain) {
            this._reminderRefreshAgain = false
            this.fetchReminders()
          } else if (this.data.remindersExpanded) {
            this.reminderTimer = setTimeout(() => this.fetchReminders(), 30000)
          }
        }
      }
    }
  },

  async cancelSavedReminder(e) {
    if (this.data.reminderCancellingId || this.data.submitting) return
    const id = e.currentTarget.dataset.id
    const item = this.data.reminderActive.find(row => row.reminderId === id)
    if (!item || !item.canCancel) return
    this.setData({ reminderCancellingId: id })
    try {
      await cancelReminder(app.globalData.apiBase, id)
      this._reminderVersion += 1
      Object.keys(this._reminders).forEach(key => {
        if (this._reminders[key].reminderId === id) delete this._reminders[key]
      })
      if (!this._destroyed) {
        this.setData({
          reminderActive: this.data.reminderActive.filter(row => row.reminderId !== id),
          reminderHistory: [{ ...item, status: 'cancelled', statusText: '已取消',
            canCancel: false, sending: false, statusClass: 'muted' }]
            .concat(this.data.reminderHistory).slice(0, 50),
        })
        this._setDeviceLists()
        wx.showToast({ title: '提醒已取消，可重新设置', icon: 'success' })
        this.fetchReminders()
      }
    } catch (error) {
      if (!this._destroyed) {
        wx.showToast({ title: error.message, icon: 'none' })
        if (error.statusCode === 409) this.fetchReminders()
      }
    } finally {
      if (!this._destroyed) this.setData({ reminderCancellingId: '' })
    }
  },

  async fetchData(cb) {
    if (cb) this._callbacks.push(cb)
    if (this._fetching) return
    if (!this._visible || this._destroyed) {
      this._callbacks.splice(0).forEach(callback => callback())
      return
    }
    clearTimeout(this.timer)
    this._fetching = true
    this.setData({ loading: true })
    const sequence = ++this._sequence
    try {
      await this._loadConfig()
      if (sequence !== this._sequence || !this._visible || this._destroyed) return
      const buildingId = this.data.buildings[this.data.currentIdx].id
      const response = await new Promise((resolve, reject) => {
        this._request = wx.request({
          url: app.globalData.apiBase + '/api/devices', data: { id: buildingId }, timeout: 15000,
          success: resolve, fail: () => reject(new Error('网络异常，请重试')),
        })
      })
      if (sequence !== this._sequence || buildingId !== this.data.buildings[this.data.currentIdx].id) return
      const data = response.data
      if (response.statusCode !== 200) throw new Error((data && data.error) || '设备数据加载失败')
      if (!data || !Array.isArray(data.washers) || !Array.isArray(data.dryers)
          || (data.buildingId && data.buildingId !== buildingId)) throw new Error('设备数据格式错误')
      this.setData({ loading: false, hasData: true,
        error: data.stale ? (data.warning || '数据未更新') : '', stale: !!data.stale,
        lastUpdate: shanghaiTime(Date.parse(data.fetchedAt), true) })
      const reservations = data.reservations
      this.setData({
        hasReservations: buildingId === '27136' && !!reservations && reservations.status !== 'not_applicable',
        reservationStatus: reservations ? reservations.status : '',
        reservationUpdatedAt: reservations ? reservationTime(reservations.fetchedAt) : '',
        reservationSourceUrl: reservations && typeof reservations.sourceUrl === 'string'
          && /^https:\/\/docs\.qq\.com\//.test(reservations.sourceUrl) ? reservations.sourceUrl : '',
      })
      this._setDeviceLists(data.washers, data.dryers, !!data.stale)
      const all = this._allWashers.concat(this._allDryers)
      const expanded = all.find(d => d.id === this.data.remindingId && d.state === 2 && finishTimestamp(d) !== null)
      if (expanded) this._remindFinish = finishTimestamp(expanded)
      this.setData({ remindingId: expanded ? this.data.remindingId : null,
        remindTimeText: expanded ? shanghaiTime(this._remindFinish - this.data.remindMin * 60000) : '' })
    } catch (error) {
      if (sequence === this._sequence && !this._destroyed) {
        this.setData({ loading: false, error: error.message, stale: this.data.hasData })
        this._setDeviceLists(this._allWashers, this._allDryers, true)
      }
    } finally {
      if (sequence === this._sequence) {
        this._fetching = false; this._request = null
        this._callbacks.splice(0).forEach(callback => callback())
        if (this._visible && !this._destroyed) this.timer = setTimeout(() => this.fetchData(), 30000)
      }
    }
  },
})
