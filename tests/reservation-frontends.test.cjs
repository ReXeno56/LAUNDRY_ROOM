const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '..');
const flush = () => new Promise(resolve => setImmediate(resolve));
const buildings = [{ id: '27131', name: '西21楼' }, { id: '27136', name: '西16楼' }];
const sourceUrl = 'https://docs.qq.com/sheet/DT1FqTlV4SWVqWWhs?tab=ss_9ipbgd';
const slot = { startAt: '2030-01-01T01:00:00Z', endAt: '2030-01-01T02:00:00Z' };
const later = minutes => new Date(Date.now() + minutes * 60000).toISOString();
const device = (id, state = 1, changes = {}) => ({ id, name: id, state, finishAt: later(90), ...changes });
const joined = (id, availability, changes = {}) => device(id, 1, {
  availability, availableNow: availability === 'available' ? true : availability === 'unknown' ? null : false,
  reservation: { status: availability === 'unknown' ? 'unknown' : 'ok',
    current: availability === 'reserved' ? slot : null, next: null }, ...changes,
});
const snapshot = (washers, dryers = [], changes = {}) => ({
  washers, dryers, buildingId: '27136', fetchedAt: later(0), stale: false,
  reservations: { status: 'ok', fetchedAt: later(0), sourceUrl, warning: null }, ...changes,
});

async function webHarness({ native = false } = {}) {
  const html = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8')
    .replace('{{ public_config|tojson }}', JSON.stringify({ buildings }))
    .replace(/\{\{\s*url_for\('static', filename='([^']+)'\)\s*\}\}/g, (_, name) => '/static/' + name);
  const dom = new JSDOM(html, { url: 'https://test.invalid/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom, { document } = window;
  await flush();
  let timerId = 0; const requests = [], timers = new Map();
  window.matchMedia = () => ({ matches: false, addEventListener() {} });
  window.setTimeout = (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; };
  window.clearTimeout = id => timers.delete(id);
  window.fetch = (url, options) => new Promise(resolve => requests.push({ url, options, resolve }));
  window.localStorage.setItem('laundry_preferences', JSON.stringify({ buildingId: '27136', theme: 'system', minutes: 5 }));
  window.eval(fs.readFileSync(path.join(root, 'static/preferences.js'), 'utf8'));
  window.isNative = () => native; window.activeReminders = {};
  window.eval(fs.readFileSync(path.join(root, 'static/main.js'), 'utf8'));
  const answer = async data => {
    requests.at(-1).resolve({ ok: true, json: async () => data }); await flush();
  };
  const names = () => [...document.querySelectorAll('.device-section')[0].querySelectorAll('.card:not([hidden]) .name')]
    .map(node => node.textContent);
  const refresh = async data => { document.getElementById('refresh-button').click(); await answer(data); };
  return { dom, window, document, requests, answer, names, refresh };
}

function miniHarness() {
  let page, timerId = 0; const requests = [], copied = [], storage = {
    laundry_preferences: { buildingId: '27136', theme: 'system', minutes: 5 },
  };
  const wx = {
    request(options) { const task = { options, done: false, abort() {} }; requests.push(task); return task; },
    getStorageSync(key) { return storage[key]; }, setStorageSync(key, value) { storage[key] = value; },
    getAppBaseInfo() { return { theme: 'dark' }; }, onThemeChange() {}, offThemeChange() {},
    setNavigationBarColor() {}, setBackgroundColor() {}, setBackgroundTextStyle() {}, showShareMenu() {}, showToast() {},
    setClipboardData(options) { copied.push(options.data); options.success(); },
  };
  const context = vm.createContext({ wx, getApp: () => ({ globalData: { apiBase: 'https://test.invalid' } }),
    Page(value) { page = value; }, console, setTimeout() { return ++timerId; }, clearTimeout() {} });
  const modules = new Map();
  function load(filename) {
    filename = filename.endsWith('.js') ? filename : filename + '.js';
    if (modules.has(filename)) return modules.get(filename).exports;
    const module = { exports: {} }; modules.set(filename, module);
    const wrapper = vm.runInContext('(function(require,module,exports){' + fs.readFileSync(filename, 'utf8') + '\n})', context);
    wrapper(name => load(path.resolve(path.dirname(filename), name)), module, module.exports);
    return module.exports;
  }
  load(path.join(root, 'mini-program/pages/index/index.js'));
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = values => Object.assign(page.data, values); page.onLoad({});
  const answer = async (suffix, data) => {
    const task = requests.find(task => !task.done && task.options.url.endsWith(suffix));
    assert.ok(task, suffix); task.done = true; task.options.success({ statusCode: 200, data }); await flush();
  };
  const boot = async data => {
    page.onShow(); await flush();
    await answer('/api/config', { buildings, templateId: 'template' });
    await answer('/api/devices', data);
  };
  const refresh = async data => { page.retry(); await flush(); await answer('/api/devices', data); };
  return { page, boot, refresh, copied, load, requests };
}

test('web reservations: combined counts and stable order separate available, reserved, unknown and running', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  await h.answer(snapshot([
    device('fault', 3), device('late', 2, { finishAt: later(120) }), joined('reserved', 'reserved'),
    joined('free', 'available'), joined('unknown', 'unknown'), device('early', 2, { finishAt: later(60) }),
  ], [joined('dryer', 'reserved')]));
  assert.deepEqual(h.names(), ['free', 'reserved', 'unknown', 'early', 'late', 'fault']);
  assert.equal(h.document.getElementById('idle-count').textContent, '1');
  assert.equal(h.document.getElementById('running-count').textContent, '2');
  assert.equal(h.document.getElementById('error-count').textContent, '1');
  assert.equal(h.document.getElementById('available-detail').textContent, '洗衣 1 台 · 烘干 0 台');
  assert.equal(h.document.getElementById('availability-title').textContent, '已确认可用');
  assert.equal(h.document.querySelector('.card.reserved .status').textContent, '已预约');
  assert.equal(h.document.querySelector('.card.reservation-unknown .status').textContent, '预约待确认');
  assert.ok(!h.document.querySelector('.card.reservation-unknown .time').textContent.includes('可以直接使用'));
  h.document.getElementById('idle-filter').click();
  assert.deepEqual(h.names(), ['free']);
  assert.equal(h.document.querySelector('.section-count').textContent, '6 台 · 1 台可用');
});

test('web reservations: Shanghai dates, source, and reservations on running machines remain visible', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  await h.answer(snapshot([joined('free', 'available', { reservation: { status: 'ok', current: null, next: slot } }),
    device('running', 2, { availableNow: false, availability: 'running', reservation: { status: 'ok', current: slot, next: slot } })]));
  assert.match(h.document.querySelector('.card.idle .time').textContent, /可用至 01月01日 09:00/);
  assert.match(h.document.querySelector('.card.running .reservation-current').textContent, /01月01日 09:00 — 01月01日 10:00/);
  assert.match(h.document.querySelector('.card.running .reservation-next').textContent, /请预留使用时间/);
  assert.equal(h.document.querySelector('.card.running .card-actions').hidden, false);
  const source = h.document.getElementById('reservation-source');
  assert.equal(source.href, sourceUrl); assert.equal(source.target, '_blank');
  assert.match(source.rel, /noopener/);
  assert.equal(h.document.getElementById('reservation-info').hidden, false);
});

test('web reservations: changed bookings and unavailable table preserve reminder edits, input and focus', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  const running = device('running', 2);
  await h.answer(snapshot([joined('free', 'available'), running]));
  h.document.getElementById('remind-btn-running').click();
  const input = h.document.getElementById('input-running'); input.value = '13'; input.focus();
  await h.refresh(snapshot([joined('free', 'reserved'), running]));
  assert.equal(h.document.getElementById('idle-count').textContent, '0');
  assert.equal(h.document.activeElement, input); assert.equal(input.value, '13');
  assert.ok(h.document.getElementById('opts-running').classList.contains('show'));
  await h.refresh(snapshot([joined('free', 'unknown'), running], [], { reservations: { status: 'unavailable', sourceUrl, fetchedAt: null } }));
  assert.match(h.document.getElementById('reservation-warning').textContent, /预约表暂不可用，设备空闲不代表可直接使用/);
  assert.equal(h.document.getElementById('input-running'), input);
  h.document.getElementById('idle-filter').click();
  assert.equal(h.document.querySelector('#content .placeholder p').textContent, '暂无可用设备');
  h.document.getElementById('idle-filter').click(); assert.equal(input.value, '13');
});

test('web reservations: old responses are compatible and unsafe document links are hidden', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  await h.answer(snapshot([device('old-free')], [], { reservations: undefined }));
  assert.equal(h.document.getElementById('idle-count').textContent, '1');
  assert.equal(h.document.querySelector('.time').textContent, '可以直接使用');
  assert.equal(h.document.getElementById('reservation-info').hidden, true);
  await h.refresh(snapshot([joined('unknown', 'unknown')], [], { reservations: { status: 'unavailable', sourceUrl: 'javascript:alert(1)' } }));
  assert.equal(h.document.getElementById('reservation-source').hidden, true);
  assert.equal(h.document.getElementById('reservation-source').hasAttribute('href'), false);
});

test('web reservations: manual themes retain the new reservation presentation and native branch uses physical state', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  await h.answer(snapshot([joined('reserved', 'reserved')]));
  for (const theme of ['dark', 'light']) {
    const select = h.document.getElementById('theme-mode'); select.value = theme;
    select.dispatchEvent(new h.window.Event('change'));
    assert.equal(h.document.documentElement.dataset.theme, theme);
    assert.equal(h.document.querySelector('.status').textContent, '已预约');
  }
  const native = await webHarness({ native: true }); t.after(() => native.dom.window.close());
  await native.answer(snapshot([joined('reserved', 'reserved')]));
  assert.equal(native.document.getElementById('idle-count').textContent, '1');
  assert.equal(native.document.querySelector('.status').textContent, '空闲');
  assert.equal(native.document.getElementById('reservation-info').hidden, true);
});

test('mini reservations: joined availability controls counts, ordering and filtering without losing running edits', async () => {
  const h = miniHarness();
  const rows = [device('fault', 3), device('running', 2), joined('reserved', 'reserved'),
    joined('free', 'available'), joined('unknown', 'unknown')];
  await h.boot(snapshot(rows, [joined('dryer', 'reserved')]));
  assert.deepEqual(Array.from(h.page.data.washers, row => row.id), ['free', 'reserved', 'unknown', 'running', 'fault']);
  assert.equal(h.page.data.idleCount, 1); assert.equal(h.page.data.runningCount, 1); assert.equal(h.page.data.errorCount, 1);
  assert.equal(h.page.data.availabilityUnconfirmed, true);
  assert.equal(h.page.data.dryerIdleCount, 0);
  assert.equal(h.page.data.washers.find(row => row.id === 'reserved').stateText, '已预约');
  assert.equal(h.page.data.washers.find(row => row.id === 'unknown').stateText, '预约待确认');
  const running = h.page.data.washers.find(row => row.id === 'running');
  assert.equal(running.canRemind, true);
  h.page.showRemindOpts({ detail: running }); h.page.onRemindMinInput({ detail: { value: 13 } });
  h.page.toggleIdleOnly({ detail: { value: true } });
  assert.deepEqual(Array.from(h.page.data.washers, row => row.id), ['free']);
  assert.equal(h.page.data.washerCount, 5); assert.equal(h.page.data.remindingId, 'running');
  await h.refresh(snapshot([joined('free', 'reserved'), device('running', 2)]));
  assert.equal(h.page.data.idleCount, 0); assert.equal(h.page.data.washers.length, 0);
  assert.equal(h.page.data.remindingId, 'running'); assert.equal(h.page.data.remindMin, 13);
  h.page.toggleIdleOnly({ detail: { value: false } });
  assert.equal(h.page.data.remindingId, 'running');
});

test('mini reservations: Shanghai dates show current and next reservations even while running', async () => {
  const h = miniHarness();
  await h.boot(snapshot([joined('free', 'available', { reservation: { status: 'ok', current: null, next: slot } }),
    device('running', 2, { reservation: { status: 'ok', current: slot, next: slot }, availableNow: false })]));
  assert.match(h.page.data.washers[0].deviceTimeText, /可用至 01月01日 09:00/);
  const running = h.page.data.washers[1];
  assert.match(running.currentReservationText, /01月01日 09:00 — 01月01日 10:00/);
  assert.match(running.nextReservationText, /请预留使用时间/); assert.equal(running.canRemind, true);
  assert.equal(h.page.data.hasReservations, true); assert.equal(h.page.data.reservationStatus, 'ok');
});

test('mini reservations: unavailable metadata and explicit unknown never advertise availability; source only copies on tap', async () => {
  const h = miniHarness();
  await h.boot(snapshot([joined('unknown', 'unknown')], [], { reservations: { status: 'unavailable', sourceUrl, fetchedAt: null } }));
  assert.equal(h.page.data.idleCount, 0); assert.equal(h.page.data.reservationStatus, 'unavailable');
  assert.equal(h.page.data.reservationUpdatedAt, ''); assert.deepEqual(h.copied, []);
  assert.match(h.page.data.washers[0].deviceTimeText, /请先确认预约文档/);
  h.page.copyReservationSource(); assert.deepEqual(h.copied, [sourceUrl]);
  await h.refresh(snapshot([joined('unknown', 'unknown')], [], { reservations: { status: 'unavailable', sourceUrl: 'https://evil.invalid/' } }));
  h.page.copyReservationSource(); assert.deepEqual(h.copied, [sourceUrl]);
  h.page.chooseTheme({ currentTarget: { dataset: { mode: 'light' } } });
  assert.equal(h.page.data.theme, 'light'); assert.equal(h.page.data.washers[0].stateText, '预约待确认');
  h.page.chooseTheme({ currentTarget: { dataset: { mode: 'dark' } } });
  assert.equal(h.page.data.theme, 'dark'); assert.equal(h.page.data.washers[0].stateText, '预约待确认');
});

test('mini reservations: older API data works and switching buildings clears reservation source', async () => {
  const h = miniHarness(); await h.boot(snapshot([device('old-idle')], [], { reservations: undefined }));
  assert.equal(h.page.data.idleCount, 1); assert.equal(h.page.data.washers[0].deviceTimeText, '可以直接使用');
  await h.refresh(snapshot([joined('free', 'available')]));
  assert.equal(h.page.data.hasReservations, true);
  h.page.switchBuilding({ currentTarget: { dataset: { idx: 0 } } });
  assert.equal(h.page.data.hasReservations, false); assert.equal(h.page.data.reservationSourceUrl, '');
});
