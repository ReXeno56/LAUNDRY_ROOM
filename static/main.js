function showToast(message) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.add('show');
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove('show'), 3000);
}

function toggleOpts(id) {
  const element = document.getElementById(id);
  if (element) element.classList.toggle('show');
}

(() => {
  const config = JSON.parse(document.getElementById('laundry-config').textContent);
  const content = document.getElementById('content');
  const banner = document.getElementById('error-banner');
  const retry = document.getElementById('retry-button');
  const refreshButton = document.getElementById('refresh-button');
  const dataStatus = document.getElementById('data-status');
  const preferences = window.LaundryPreferences;
  const themeSelect = document.getElementById('theme-mode');
  const idleFilter = document.getElementById('idle-filter');
  const views = new Map();
  const tabs = new Map();
  let currentId = preferences ? preferences.getBuilding(config.buildings) : config.buildings[0].id;
  let onlyIdle = false;
  let sequence = 0;
  let controller = null;
  let timer = null;
  let snapshot = null;
  let groups = null;

  function element(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function shanghaiTime(value) {
    const time = Date.parse(value);
    if (!Number.isFinite(time)) return '';
    return new Date(time + 8 * 3600000).toISOString().slice(11, 19);
  }

  function finishAt(device) {
    const value = device.finishAt || (typeof device.finishTime === 'string'
      ? device.finishTime.replace(' ', 'T') + '+08:00' : '');
    return Number.isFinite(Date.parse(value)) ? value : '';
  }

  function usesReservations() { return !isNative() && currentId === '27136'; }

  function available(device) {
    if (isNative()) return device.state === 1;
    if (Object.prototype.hasOwnProperty.call(device, 'availableNow')) return device.state === 1 && device.availableNow === true;
    return device.state === 1 && (!device.reservation || (device.reservation.status !== 'unknown' && !device.reservation.current));
  }

  function reservationTime(value) {
    const time = Date.parse(value);
    if (!Number.isFinite(time)) return '';
    const date = new Date(time + 8 * 3600000).toISOString();
    const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
    const tomorrow = new Date(Date.now() + 32 * 3600000).toISOString().slice(0, 10);
    const day = date.slice(0, 10) === today ? '今天' : date.slice(0, 10) === tomorrow ? '明天'
      : date.slice(5, 7) + '月' + date.slice(8, 10) + '日';
    return day + ' ' + date.slice(11, 16);
  }

  function slotText(slot) {
    if (!slot) return '';
    const start = reservationTime(slot.startAt), end = reservationTime(slot.endAt);
    return start && end ? start + ' — ' + end : '';
  }

  function reservationView(device) {
    const state = { 1: { cls: 'idle', text: '空闲' }, 2: { cls: 'running', text: '使用中' },
      3: { cls: 'error', text: '故障' }, 4: { cls: 'error', text: '离线' } }[device.state]
      || { cls: 'error', text: '未知' };
    const finish = finishAt(device);
    let time = device.state === 1 ? '可以直接使用'
      : device.state === 2 ? (finish ? '预计 ' + shanghaiTime(finish).slice(0, 5) + ' 结束' : '暂未提供结束时间')
      : '暂时不可用';
    if (isNative() || (!device.reservation && !Object.prototype.hasOwnProperty.call(device, 'availableNow'))
        || (device.reservation && device.reservation.status === 'not_applicable')) {
      return { ...state, time, current: '', next: '' };
    }
    const reservation = device.reservation || { status: 'unknown', current: null, next: null };
    if (device.state === 1) {
      if (device.availability === 'reserved' || reservation.current) {
        state.cls = 'reserved'; state.text = '已预约';
        time = '设备空闲 · 当前预约' + (reservation.current ? '至 ' + reservationTime(reservation.current.endAt) : '中');
      } else if (!available(device)) {
        state.cls = 'reservation-unknown'; state.text = '预约待确认';
        time = '设备空闲 · 请先确认预约文档';
      } else if (reservation.next) time = '当前可用 · 可用至 ' + reservationTime(reservation.next.startAt);
      else time = '当前可用 · 暂无后续预约';
    }
    return { ...state, time,
      current: reservation.current ? '当前预约 ' + slotText(reservation.current) : '',
      next: reservation.next ? '下一场预约 ' + slotText(reservation.next) + ' · 请预留使用时间' : '' };
  }

  function updateReservationInfo(data) {
    const info = document.getElementById('reservation-info');
    const meta = data && data.reservations;
    info.hidden = !usesReservations() || !meta || meta.status === 'not_applicable';
    if (info.hidden) return;
    const failed = meta.status !== 'ok';
    info.classList.toggle('unavailable', failed);
    document.getElementById('reservation-title').textContent = failed ? '预约信息待确认' : '已结合西16预约文档';
    document.getElementById('reservation-updated').textContent = meta.fetchedAt
      ? '采集于 ' + reservationTime(meta.fetchedAt) : '';
    document.getElementById('reservation-warning').textContent = failed
      ? '预约表暂不可用，设备空闲不代表可直接使用'
      : '请按预约时段使用，开始前预留足够时间。';
    const link = document.getElementById('reservation-source');
    let safe = false;
    try { const url = new URL(meta.sourceUrl); safe = url.protocol === 'https:' && url.hostname === 'docs.qq.com'; } catch (_) {}
    link.hidden = !safe;
    if (safe) link.href = meta.sourceUrl;
    else link.removeAttribute('href');
  }

  function sortedDevices(devices) {
    const rank = device => available(device) ? 0 : device.state === 1 ? 1 : device.state === 2 ? 2 : 3;
    const end = device => Date.parse(finishAt(device)) || Infinity;
    return devices.map((device, index) => ({ device, index })).sort((left, right) => {
      const order = rank(left.device) - rank(right.device);
      if (order) return order;
      if (left.device.state === 2 && right.device.state === 2) {
        const leftEnd = end(left.device), rightEnd = end(right.device);
        if (leftEnd !== rightEnd) return leftEnd < rightEnd ? -1 : 1;
      }
      return left.index - right.index;
    }).map(entry => entry.device);
  }

  function reconcile(parent, desired) {
    const keep = new Set(desired);
    Array.from(parent.children).forEach(node => { if (!keep.has(node)) node.remove(); });
    desired.forEach((node, index) => {
      if (parent.children[index] !== node) parent.insertBefore(node, parent.children[index] || null);
    });
  }

  function placeholder(title, detail, loading = false) {
    const box = element('div', 'placeholder');
    const icon = element('span', loading ? 'loading-ring' : 'machine-icon');
    icon.setAttribute('aria-hidden', 'true');
    box.append(icon, element('p', '', title), element('span', '', detail));
    return box;
  }

  function updateSummary(data) {
    const all = data ? data.washers.concat(data.dryers) : [];
    const idle = category => data[category].filter(available).length;
    document.getElementById('availability-title').textContent = usesReservations()
      ? (all.some(device => device.availableNow === null) ? '已确认可用' : '当前可用') : '现在空闲';
    document.getElementById('idle-filter-label').textContent = usesReservations() ? '仅看可用' : '仅看空闲';
    document.getElementById('sort-hint').textContent = (usesReservations() ? '可用优先' : '空闲优先') + ' · 按结束时间排序';
    document.getElementById('idle-count').textContent = data ? all.filter(available).length : '—';
    document.getElementById('running-count').textContent = data ? all.filter(d => d.state === 2).length : '—';
    document.getElementById('error-count').textContent = data ? all.filter(d => d.state !== 1 && d.state !== 2).length : '—';
    document.getElementById('available-detail').textContent = data
      ? '洗衣 ' + idle('washers') + ' 台 · 烘干 ' + idle('dryers') + ' 台'
      : '正在获取设备状态';
    dataStatus.textContent = data ? (data.stale ? '上次采集' : '每30秒更新') : '正在加载';
    dataStatus.classList.toggle('stale', !!(data && data.stale));
    updateReservationInfo(data);
  }

  function setPanel(view, open) {
    view.opts.classList.toggle('show', open);
    view.button.setAttribute('aria-expanded', String(open));
    view.button.textContent = open ? '收起提醒设置' : (isNative() ? '提醒我' : '添加到日历');
  }

  function createCard(key, device) {
    const card = element('div', 'card');
    const header = element('div', 'card-header');
    const icon = element('div', 'card-icon');
    icon.setAttribute('aria-hidden', 'true');
    icon.append(element('span', 'machine-icon'));
    const left = element('div', 'card-main');
    const name = element('div', 'name');
    const time = element('div', 'time');
    const reservation = element('div', 'card-reservations');
    const currentReservation = element('p', 'reservation-current');
    const nextReservation = element('p', 'reservation-next');
    reservation.append(currentReservation, nextReservation); reservation.hidden = true;
    const button = element('button', 'remind-btn', isNative() ? '提醒我' : '添加到日历');
    button.type = 'button';
    button.id = 'remind-btn-' + device.id;
    const countdown = element('div', 'countdown');
    countdown.id = 'countdown-' + device.id;
    const opts = element('div', 'remind-opts');
    opts.id = 'opts-' + device.id;
    button.setAttribute('aria-controls', opts.id);
    button.setAttribute('aria-expanded', 'false');
    const input = element('input', 'remind-input');
    input.id = 'input-' + device.id;
    input.type = 'number'; input.min = '1'; input.step = '1';
    input.value = String(preferences && !isNative() ? preferences.getMinutes() : 5);
    if (!isNative()) input.max = '30';
    input.setAttribute('aria-label', '提前提醒分钟数');
    input.addEventListener('input', () => {
      if (preferences && !isNative()) preferences.setMinutes(Number(input.value));
    });
    const confirm = element('button', 'remind-confirm', isNative() ? '确定' : '下载日历');
    confirm.type = 'button';
    opts.append(document.createTextNode('提前'), input, document.createTextNode('分钟'), confirm);
    const actions = element('div', 'card-actions');
    actions.append(button, countdown, opts);
    const status = element('span', 'status');
    left.append(name, time); header.append(icon, left, status); card.append(header, reservation, actions);
    const view = { card, name, time, button, countdown, opts, input, confirm, status, actions,
      reservation, currentReservation, nextReservation, device, buildingId: currentId, pending: false };
    button.addEventListener('click', () => setPanel(view, !opts.classList.contains('show')));
    confirm.addEventListener('click', () => addCalendar(view));
    views.set(key, view);
    return view;
  }

  async function addCalendar(view) {
    if (view.pending) return;
    if (isNative()) {
      addReminder(view.device.name, view.device.finishTime, view.input.id, view.device.id);
      return;
    }
    const minutes = Number(view.input.value);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 30) {
      showToast('请输入 1–30 的整数分钟数'); return;
    }
    if (preferences) preferences.setMinutes(minutes);
    const device = view.device;
    const finish = finishAt(device);
    if (!finish || Date.parse(finish) - minutes * 60000 <= Date.now()) {
      showToast('提醒时间已过，请减少提前分钟数'); return;
    }
    view.pending = true; view.confirm.disabled = true;
    const downloadController = new AbortController();
    const timeout = window.setTimeout(() => downloadController.abort(), 15000);
    try {
      const query = new URLSearchParams({ name: device.name, finishAt: finish,
        minutes: String(minutes), buildingId: view.buildingId, deviceId: device.id });
      const response = await fetch('/api/reminder?' + query, { signal: downloadController.signal });
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.error || '日历生成失败');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = element('a'); link.href = url; link.download = 'reminder.ics';
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setPanel(view, false);
      showToast('日历已下载，请完成导入并允许通知');
    } catch (error) {
      showToast(error.name === 'AbortError' ? '日历请求超时，请重试' : error.message);
    } finally {
      window.clearTimeout(timeout);
      view.pending = false; view.confirm.disabled = false;
    }
  }

  function render(data) {
    const focused = document.activeElement;
    updateSummary(data);
    if (!groups) {
      groups = {};
      content.replaceChildren();
      ['washers', 'dryers'].forEach((category, index) => {
        const section = element('section', 'device-section');
        const heading = element('div', 'section-head');
        const title = element('h2', 'section-title', index ? '烘干机' : '洗衣机');
        const count = element('span', 'section-count');
        const list = element('div', 'device-list');
        heading.append(title, count); section.append(heading, list); content.append(section);
        groups[category] = { section, list, count };
      });
      groups.empty = placeholder('暂无设备', '该楼栋还没有可展示的设备'); content.append(groups.empty);
    }
    const keys = new Set();
    ['washers', 'dryers'].forEach(category => {
      let visibleCount = 0;
      const nodes = sortedDevices(data[category]).map(device => {
        const key = currentId + ':' + category + ':' + device.id;
        keys.add(key);
        const view = views.get(key) || createCard(key, device);
        view.device = device;
        const details = reservationView(device), cls = details.cls;
        view.card.className = 'card ' + cls;
        view.card.hidden = onlyIdle && !available(device);
        if (!view.card.hidden) visibleCount += 1;
        view.name.textContent = device.name;
        view.status.className = 'status ' + cls;
        view.status.textContent = details.text;
        const finish = finishAt(device);
        view.time.textContent = details.time;
        view.currentReservation.textContent = details.current;
        view.currentReservation.hidden = !details.current;
        view.nextReservation.textContent = details.next;
        view.nextReservation.hidden = !details.next;
        view.reservation.hidden = !details.current && !details.next;
        view.actions.hidden = device.state !== 2 || !finish;
        view.button.disabled = !!data.stale;
        view.confirm.disabled = !!data.stale || view.pending;
        if (view.actions.hidden) setPanel(view, false);
        if (isNative() && activeReminders[device.id]) {
          view.button.style.display = 'none'; view.countdown.style.display = 'block';
          view.opts.classList.remove('show');
        }
        return view.card;
      });
      reconcile(groups[category].list, nodes);
      groups[category].section.hidden = !visibleCount;
      groups[category].count.textContent = (isNative() && onlyIdle ? visibleCount : nodes.length) + ' 台 · '
        + data[category].filter(available).length + (usesReservations() ? ' 台可用' : ' 台空闲');
    });
    for (const [key] of views) if (!keys.has(key)) views.delete(key);
    const all = data.washers.concat(data.dryers);
    groups.empty.hidden = onlyIdle ? all.some(available) : !!all.length;
    groups.empty.querySelector('p').textContent = onlyIdle && all.length
      ? (usesReservations() ? '暂无可用设备' : '暂无空闲设备') : '暂无设备';
    groups.empty.lastElementChild.textContent = onlyIdle && all.length
      ? '关闭“' + (usesReservations() ? '仅看可用' : '仅看空闲') + '”可查看全部设备' : '该楼栋还没有可展示的设备';
    if (focused && focused.tagName === 'INPUT' && focused.isConnected && !focused.closest('[hidden]')
        && document.activeElement !== focused) focused.focus({ preventScroll: true });
  }

  function stop() {
    window.clearTimeout(timer); timer = null;
    sequence += 1;
    if (controller) controller.abort();
    controller = null;
  }

  async function refresh() {
    if (document.hidden || controller) return;
    window.clearTimeout(timer);
    const requestSequence = ++sequence;
    const buildingId = currentId;
    const task = new AbortController(); controller = task;
    refreshButton.disabled = true;
    content.setAttribute('aria-busy', 'true');
    dataStatus.textContent = snapshot ? '更新中' : '正在加载';
    const timeout = window.setTimeout(() => task.abort(), 13000);
    try {
      const response = await fetch('/api/devices?id=' + encodeURIComponent(buildingId), { signal: task.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '设备数据加载失败');
      if (requestSequence !== sequence || buildingId !== currentId) return;
      if (!Array.isArray(data.washers) || !Array.isArray(data.dryers)
          || (data.buildingId && data.buildingId !== buildingId)) throw new Error('设备数据格式错误');
      snapshot = data;
      render(data);
      banner.textContent = data.stale ? (data.warning || '以下为上次采集的数据') : '';
      banner.hidden = !data.stale; retry.hidden = !data.stale;
      document.getElementById('refresh-info').textContent = '采集于 ' + shanghaiTime(data.fetchedAt)
        + ' · 每30秒自动刷新' + (data.stale ? ' · 数据未更新' : '');
    } catch (error) {
      if (requestSequence !== sequence) return;
      banner.textContent = (error.name === 'AbortError' ? '设备数据请求超时' : error.message)
        + (snapshot ? '；以下为上次采集的数据' : '，请重试');
      banner.hidden = false; retry.hidden = false;
      if (snapshot) render({ ...snapshot, stale: true });
      else {
        content.replaceChildren(placeholder('设备数据加载失败', '请检查网络后重新加载'));
        document.getElementById('available-detail').textContent = '暂时无法获取设备状态';
        dataStatus.textContent = '更新失败'; dataStatus.classList.add('stale');
      }
    } finally {
      window.clearTimeout(timeout);
      if (requestSequence === sequence) {
        controller = null;
        refreshButton.disabled = false;
        content.setAttribute('aria-busy', 'false');
        if (!document.hidden) timer = window.setTimeout(refresh, 30000);
      }
    }
  }

  function switchBuilding(id) {
    if (id === currentId) return;
    stop(); currentId = id; snapshot = null; groups = null; views.clear();
    if (preferences) preferences.setBuilding(id);
    for (const [bid, tab] of tabs) {
      tab.classList.toggle('active', bid === id);
      tab.setAttribute('aria-pressed', String(bid === id));
    }
    banner.hidden = true; retry.hidden = true;
    content.replaceChildren(placeholder('正在查询设备', '稍等一下，马上就好', true));
    updateSummary(null);
    document.getElementById('refresh-info').textContent = '';
    refresh();
  }

  config.buildings.forEach(building => {
    const selected = building.id === currentId;
    const tab = element('button', 'tab' + (selected ? ' active' : ''), building.name);
    tab.type = 'button'; tab.setAttribute('aria-pressed', String(selected));
    tab.addEventListener('click', () => switchBuilding(building.id));
    tabs.set(building.id, tab); document.getElementById('building-tabs').append(tab);
  });
  if (preferences) {
    themeSelect.value = preferences.getTheme();
    themeSelect.addEventListener('change', () => preferences.setTheme(themeSelect.value));
  }
  idleFilter.addEventListener('click', () => {
    onlyIdle = !onlyIdle;
    idleFilter.setAttribute('aria-pressed', String(onlyIdle));
    if (snapshot) render({ ...snapshot, stale: !banner.hidden });
  });
  document.getElementById('calendar-hint').hidden = isNative();
  retry.addEventListener('click', refresh);
  refreshButton.addEventListener('click', refresh);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); else refresh(); });
  window.addEventListener('pagehide', stop);
  window.addEventListener('pageshow', refresh);
  refresh();
})();
