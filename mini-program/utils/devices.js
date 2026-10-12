const STATES = {
  1: { cls: 'idle', text: '空闲' }, 2: { cls: 'running', text: '使用中' },
  3: { cls: 'error', text: '故障' }, 4: { cls: 'error', text: '离线' },
}

function finishTimestamp(device) {
  if (device.finishAt) {
    const timestamp = Date.parse(device.finishAt)
    if (Number.isFinite(timestamp)) return timestamp
  }
  if (typeof device.finishTime === 'string' && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(device.finishTime)) {
    const timestamp = Date.parse(device.finishTime.replace(' ', 'T') + '+08:00')
    if (Number.isFinite(timestamp)) return timestamp
  }
  return null
}

function shanghaiTime(timestamp, seconds = false) {
  if (!Number.isFinite(timestamp)) return ''
  return new Date(timestamp + 8 * 3600000).toISOString().slice(11, seconds ? 19 : 16)
}

function reminderDate(value) {
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return ''
  const date = new Date(timestamp + 8 * 3600000).toISOString()
  return date.slice(5, 7) + '月' + date.slice(8, 10) + '日 ' + date.slice(11, 16)
}

function availableNow(device) {
  return device.state === 1 && (Object.prototype.hasOwnProperty.call(device, 'availableNow')
    ? device.availableNow === true : (!device.reservation || (device.reservation.status !== 'unknown' && !device.reservation.current)))
}

function reservationTime(value) {
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return ''
  const date = new Date(timestamp + 8 * 3600000).toISOString()
  const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10)
  const tomorrow = new Date(Date.now() + 32 * 3600000).toISOString().slice(0, 10)
  const day = date.slice(0, 10) === today ? '今天' : date.slice(0, 10) === tomorrow ? '明天'
    : date.slice(5, 7) + '月' + date.slice(8, 10) + '日'
  return day + ' ' + date.slice(11, 16)
}

function reservationSlot(slot) {
  if (!slot) return ''
  const start = reservationTime(slot.startAt), end = reservationTime(slot.endAt)
  return start && end ? start + ' — ' + end : ''
}

function decorate(device, stale, reminders) {
  const state = { ...(STATES[device.state] || { cls: 'error', text: '未知' }) }
  const finish = finishTimestamp(device)
  const saved = reminders[String(device.id)]
  const reminderSet = !!(saved && saved.finish === finish)
  let deviceTimeText = device.state === 1 ? '可以直接使用'
    : device.state === 2 ? (finish !== null ? '预计 ' + shanghaiTime(finish) + ' 结束' : '暂未提供结束时间')
    : '暂时不可用'
  const reservation = device.reservation
  const applicable = reservation ? reservation.status !== 'not_applicable'
    : Object.prototype.hasOwnProperty.call(device, 'availableNow') && device.availableNow !== true
  if (applicable && device.state === 1) {
    if (device.availability === 'reserved' || (reservation && reservation.current)) {
      state.cls = 'reserved'; state.text = '已预约'
      deviceTimeText = '设备空闲 · 当前预约' + (reservation && reservation.current
        ? '至 ' + reservationTime(reservation.current.endAt) : '中')
    } else if (!availableNow(device)) {
      state.cls = 'reservation-unknown'; state.text = '预约待确认'
      deviceTimeText = '设备空闲 · 请先确认预约文档'
    } else if (reservation && reservation.next) {
      deviceTimeText = '当前可用 · 可用至 ' + reservationTime(reservation.next.startAt)
    } else deviceTimeText = '当前可用 · 暂无后续预约'
  }
  return { ...device, id: String(device.id), stateClass: state.cls, stateText: state.text,
    deviceTimeText,
    currentReservationText: applicable && reservation && reservation.current ? '当前预约 ' + reservationSlot(reservation.current) : '',
    nextReservationText: applicable && reservation && reservation.next ? '下一场预约 ' + reservationSlot(reservation.next) + ' · 请预留使用时间' : '',
    finishTimeText: shanghaiTime(finish), canRemind: !stale && device.state === 2 && finish !== null,
    reminderSet, reminderMinutes: reminderSet ? saved.minutes : null }
}

function sortDevices(devices) {
  const rank = device => availableNow(device) ? 0 : device.state === 1 ? 1 : device.state === 2 ? 2 : 3
  return devices.map((device, index) => ({ device, index })).sort((a, b) => {
    const order = rank(a.device) - rank(b.device)
    if (order) return order
    if (a.device.state === 2 && b.device.state === 2) {
      const first = finishTimestamp(a.device), second = finishTimestamp(b.device)
      const delta = (first === null ? Infinity : first) - (second === null ? Infinity : second)
      if (delta && Number.isFinite(delta)) return delta
      if (first === null && second !== null) return 1
      if (first !== null && second === null) return -1
    }
    return a.index - b.index
  }).map(item => item.device)
}

module.exports = { finishTimestamp, shanghaiTime, reminderDate, reservationTime, availableNow, decorate, sortDevices }
