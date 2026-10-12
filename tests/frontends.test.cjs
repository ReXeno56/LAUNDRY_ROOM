const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '..');
const flush = () => new Promise(resolve => setImmediate(resolve));
const buildings = [{ id: '27131', name: '西21楼' }, { id: '27136', name: '西16楼' }];
const finishAt = new Date(Date.now() + 2 * 3600000).toISOString();
const device = { id: '42', name: "O'Reilly <img src=x onerror=alert(1)>", state: 2,
  finishAt, finishTime: '2030-10-07 12:00:00' };
const snapshot = (bid = '27131', machines = [device]) => ({
  buildingId: bid, washers: machines, dryers: [], fetchedAt: '2026-10-07T04:00:00Z', stale: false,
});

function miniHarness(permission = 'accept') {
  let page; const requests = [], toasts = [], timers = new Map(), storage = {};
  let timerId = 0, subscriptions = 0, logins = 0;
  const app = { globalData: { apiBase: 'https://audit.invalid' } };
  const wx = {
    request(options) {
      const task = { options, aborted: false, abort() { this.aborted = true; } };
      requests.push(task); return task;
    },
    requestSubscribeMessage(options) {
      subscriptions++;
      if (permission === 'fail') options.fail();
      else options.success({ [options.tmplIds[0]]: permission });
    },
    login(options) { logins++; options.success({ code: 'test-code' }); },
    showToast: options => toasts.push(options), showShareMenu() {}, stopPullDownRefresh() {},
    getStorageSync: key => storage[key], setStorageSync: (key, value) => { storage[key] = value; },
    removeStorageSync: key => { delete storage[key]; },
  };
  const context = vm.createContext({ wx, getApp: () => app, Page: value => { page = value; },
    console, setTimeout(fn, ms) { timers.set(++timerId, { fn, ms }); return timerId; },
    clearTimeout(id) { timers.delete(id); } });
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
  page.setData = values => Object.assign(page.data, values);
  page.onLoad({});
  const answer = (task, data, statusCode = 200) => task.options.success({ statusCode, data });
  async function boot() {
    page.onShow(); await flush();
    answer(requests[0], { buildings, templateId: 'test-template' }); await flush();
    answer(requests[1], snapshot()); await flush();
    storage.laundry_session = { token: 'cached-token' };
    page.showRemindOpts({ detail: page.data.washers[0] });
  }
  return { page, wx, requests, toasts, timers, storage, answer, boot, load,
    subscriptions: () => subscriptions, logins: () => logins };
}

test('mini: rejecting subscription does not submit or close the panel', async () => {
  const h = miniHarness('reject'); await h.boot();
  await h.page.confirmReminder({ detail: h.page.data.washers[0] });
  assert.equal(h.requests.length, 2);
  assert.equal(h.page.data.remindingId, '42');
  assert.equal(h.page.data.submitting, false);
  assert.equal(h.toasts.at(-1).icon, 'none');
});

test('mini: HTTP 400 and a false ok never display success', async () => {
  for (const [status, result] of [[400, { error: 'too late' }], [200, { ok: false, error: 'not saved' }]]) {
    const h = miniHarness(); await h.boot();
    const pending = h.page.confirmReminder({ detail: h.page.data.washers[0] }); await flush();
    h.answer(h.requests.at(-1), result, status); await pending;
    assert.equal(h.toasts.at(-1).icon, 'none');
    assert.equal(h.page.data.remindingId, '42');
    assert.equal(h.page.data.submitting, false);
  }
});

test('mini: 401 logs in and retries once without another permission request', async () => {
  const h = miniHarness(); await h.boot();
  const pending = h.page.confirmReminder({ detail: h.page.data.washers[0] }); await flush();
  h.answer(h.requests.at(-1), { error: 'expired' }, 401); await flush();
  assert.match(h.requests.at(-1).options.url, /wx-login/);
  h.answer(h.requests.at(-1), { token: 'new-token', expiresAt: finishAt }); await flush();
  assert.equal(h.requests.at(-1).options.data.token, 'new-token');
  h.answer(h.requests.at(-1), { ok: true, reminderId: 'saved', minutes: 5, status: 'pending' });
  await pending;
  assert.equal(h.subscriptions(), 1);
  assert.equal(h.logins(), 1);
  assert.equal(h.page.data.remindingId, null);
  assert.equal(h.page.data.washers[0].reminderSet, true);
});

test('mini: a second 401 stops retrying and clears the session', async () => {
  const h = miniHarness(); await h.boot();
  const pending = h.page.confirmReminder({ detail: h.page.data.washers[0] }); await flush();
  h.answer(h.requests.at(-1), { error: 'expired' }, 401); await flush();
  h.answer(h.requests.at(-1), { token: 'new-token' }); await flush();
  h.answer(h.requests.at(-1), { error: 'expired again' }, 401); await pending;
  assert.equal(h.requests.filter(x => /wx-subscribe/.test(x.options.url)).length, 2);
  assert.equal(h.storage.laundry_session, undefined);
  assert.equal(h.toasts.at(-1).icon, 'none');
});

test('mini: double confirm only submits once', async () => {
  const h = miniHarness(); await h.boot();
  const event = { detail: h.page.data.washers[0] };
  const pending = h.page.confirmReminder(event); h.page.confirmReminder(event); await flush();
  assert.equal(h.subscriptions(), 1);
  assert.equal(h.requests.filter(x => /wx-subscribe/.test(x.options.url)).length, 1);
  h.answer(h.requests.at(-1), { ok: true, reminderId: 'saved', minutes: 5 }); await pending;
});

test('mini: concurrent login callers share one login', async () => {
  const h = miniHarness();
  const session = h.load(path.join(root, 'mini-program/utils/session.js'));
  const one = session.ensureSession('https://audit.invalid');
  const two = session.ensureSession('https://audit.invalid'); await flush();
  assert.equal(h.logins(), 1); assert.equal(h.requests.length, 1);
  h.answer(h.requests[0], { token: 'new-token' });
  assert.equal((await one).token, (await two).token);
});

test('mini: late old-building response cannot overwrite the selected building', async () => {
  const h = miniHarness(); await h.boot();
  h.page.fetchData(); await flush(); const old = h.requests.at(-1);
  h.page.switchBuilding({ currentTarget: { dataset: { idx: 1 } } }); await flush();
  const current = h.requests.at(-1);
  h.answer(current, snapshot('27136', [{ ...device, id: 'B', name: 'B building' }])); await flush();
  h.answer(old, snapshot()); await flush();
  assert.equal(old.aborted, true);
  assert.equal(h.page.data.currentIdx, 1);
  assert.equal(h.page.data.washers[0].name, 'B building');
});

test('mini: lifecycle pauses polling and does not overlap requests', async () => {
  const h = miniHarness(); await h.boot();
  assert.equal(h.timers.size, 1);
  h.page.onHide(); assert.equal(h.timers.size, 0);
  const devices = () => h.requests.filter(task => /\/api\/devices/.test(task.options.url));
  const count = devices().length;
  h.page.onShow(); h.page.fetchData(); await flush();
  assert.equal(devices().length, count + 1);
  h.answer(devices().at(-1), snapshot());
  const reminderRequest = h.requests.find(task => /\/api\/wx-reminders$/.test(task.options.url));
  assert.ok(reminderRequest, 'resuming a cached session refreshes reminder state');
  h.answer(reminderRequest, { ok: true, active: [], history: [] }); await flush();
  assert.equal(h.timers.size, 1);
  h.page.onUnload(); assert.equal(h.timers.size, 0);
});

test('mini: failed initial loading is not an empty result, stale data keeps its timestamp', async () => {
  const h = miniHarness(); h.page.onShow(); await flush();
  h.answer(h.requests[0], { buildings, templateId: 'test-template' }); await flush();
  h.requests[1].options.fail(); await flush();
  assert.equal(h.page.data.hasData, false); assert.ok(h.page.data.error);
  h.page.fetchData(); await flush(); h.answer(h.requests.at(-1), snapshot()); await flush();
  const timestamp = h.page.data.lastUpdate;
  h.page.fetchData(); await flush(); h.answer(h.requests.at(-1), { error: 'failed' }, 503); await flush();
  assert.equal(h.page.data.lastUpdate, timestamp);
  assert.equal(h.page.data.hasData, true);
  assert.equal(h.page.data.washers[0].canRemind, false);
});

test('mini: ISO and malformed timestamps do not crash device formatting', () => {
  const h = miniHarness(); const formatter = h.load(path.join(root, 'mini-program/utils/devices.js'));
  assert.equal(formatter.decorate({ ...device, finishAt: '2030-10-07T04:00:00Z' }, false, {}).finishTimeText, '12:00');
  assert.equal(formatter.decorate({ id: 'x', state: 2, finishTime: 'broken' }, false, {}).canRemind, false);
});

async function webHarness() {
  const html = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8')
    .replace('{{ public_config|tojson }}', JSON.stringify({ buildings }))
    .replace(/\{\{\s*url_for\('static', filename='([^']+)'\)\s*\}\}/g, (_, name) => '/static/' + name);
  const dom = new JSDOM(html, { url: 'https://audit.invalid/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom; const { document } = window;
  await flush();
  const requests = [], timers = new Map(); let id = 0;
  window.setTimeout = (fn, ms) => { timers.set(++id, { fn, ms }); return id; };
  window.clearTimeout = key => timers.delete(key);
  window.setInterval = window.setTimeout;
  window.fetch = (url, options) => new Promise(resolve => requests.push({ url, options, resolve }));
  window.URL.createObjectURL = () => 'blob:test'; window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = () => {};
  window.eval(fs.readFileSync(path.join(root, 'static/native-reminders.js'), 'utf8'));
  window.eval(fs.readFileSync(path.join(root, 'static/preferences.js'), 'utf8'));
  window.eval(fs.readFileSync(path.join(root, 'static/main.js'), 'utf8'));
  const answer = async (req, data, status = 200) => {
    req.resolve({ ok: status >= 200 && status < 300, status,
      json: async () => data, blob: async () => new window.Blob(['calendar']) }); await flush();
  };
  const poll = () => {
    const entry = [...timers].find(([, timer]) => timer.ms === 30000);
    assert.ok(entry, 'a next poll should exist'); timers.delete(entry[0]); entry[1].fn();
  };
  return { dom, window, document, requests, timers, answer, poll };
}

test('web: names are text and apostrophes keep buttons usable', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  await h.answer(h.requests[0], snapshot());
  assert.equal(h.document.querySelector('.name').textContent, device.name);
  assert.equal(h.document.querySelector('img'), null);
  assert.equal(h.document.querySelector('[onclick]'), null);
  h.document.querySelector('.remind-btn').click();
  assert.ok(h.document.querySelector('.remind-opts').classList.contains('show'));
});

test('web: reverse response order cannot mix buildings', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  const old = h.requests[0]; h.document.querySelectorAll('.tab')[1].click();
  await h.answer(h.requests[1], snapshot('27136', [{ ...device, name: 'B building' }]));
  await h.answer(old, snapshot());
  assert.equal(h.document.querySelector('.name').textContent, 'B building');
  assert.equal(old.options.signal.aborted, true);
});

test('web: refresh preserves expanded panel, input node and focus', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  await h.answer(h.requests[0], snapshot());
  h.document.querySelector('.remind-btn').click();
  const input = h.document.querySelector('input'); input.value = '12'; input.focus();
  h.poll(); await h.answer(h.requests[1], snapshot());
  assert.equal(h.document.querySelector('input'), input);
  assert.equal(input.value, '12'); assert.equal(h.document.activeElement, input);
  assert.ok(h.document.querySelector('.remind-opts').classList.contains('show'));
});

test('web: refresh error keeps the last list and acquisition timestamp', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  await h.answer(h.requests[0], snapshot());
  const timestamp = h.document.getElementById('refresh-info').textContent;
  h.poll(); await h.answer(h.requests[1], { error: 'upstream unavailable' }, 503);
  assert.equal(h.document.querySelector('.name').textContent, device.name);
  assert.equal(h.document.getElementById('refresh-info').textContent, timestamp);
  assert.equal(h.document.getElementById('error-banner').hidden, false);
  assert.equal(h.document.querySelector('.remind-btn').disabled, true);
});

test('web: hidden pages stop polling and visible pages refresh immediately', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  await h.answer(h.requests[0], snapshot());
  Object.defineProperty(h.document, 'hidden', { configurable: true, value: true });
  h.document.dispatchEvent(new h.window.Event('visibilitychange'));
  assert.equal([...h.timers.values()].filter(x => x.ms === 30000).length, 0);
  Object.defineProperty(h.document, 'hidden', { configurable: true, value: false });
  h.document.dispatchEvent(new h.window.Event('visibilitychange'));
  h.document.dispatchEvent(new h.window.Event('visibilitychange'));
  assert.equal(h.requests.length, 2);
});

test('web: calendar failures keep the panel and never announce a download', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  await h.answer(h.requests[0], snapshot());
  h.document.querySelector('.remind-btn').click();
  h.document.querySelector('.remind-confirm').click();
  assert.equal(h.document.querySelector('.remind-confirm').disabled, true);
  await h.answer(h.requests[1], { error: 'too late' }, 400);
  assert.equal(h.document.getElementById('toast').textContent, 'too late');
  assert.ok(h.document.querySelector('.remind-opts').classList.contains('show'));
  assert.equal(h.document.querySelector('.remind-confirm').disabled, false);
});

test('web: malformed finish time remains renderable without a reminder', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  await h.answer(h.requests[0], snapshot('27131', [{ ...device, finishAt: null, finishTime: 'broken' }]));
  assert.equal(h.document.querySelector('.name').textContent, device.name);
  assert.equal(h.document.querySelector('.remind-btn').parentElement.hidden, true);
});

test('mini: availability separates washers and dryers and resets when switching buildings', async () => {
  const h = miniHarness(); await h.boot();
  h.page.retry(); await flush();
  assert.equal(h.page.data.loading, true);
  h.answer(h.requests.at(-1), { ...snapshot('27131', [device, { id: 'idle-w', state: 1 }]),
    dryers: [{ id: 'idle-d', state: 1 }, { id: 'offline', state: 4 }] }); await flush();
  assert.equal(h.page.data.idleCount, 2);
  assert.equal(h.page.data.washerIdleCount, 1);
  assert.equal(h.page.data.dryerIdleCount, 1);
  assert.equal(h.page.data.runningCount, 1);
  assert.equal(h.page.data.errorCount, 1);
  assert.equal(h.page.data.loading, false);
  h.page.switchBuilding({ currentTarget: { dataset: { idx: 1 } } }); await flush();
  assert.equal(h.page.data.hasData, false);
  assert.equal(h.page.data.washerIdleCount, 0);
  assert.equal(h.page.data.dryerIdleCount, 0);
});

test('web: summary counts update from the selected building and stay unknown while loading', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  assert.equal(h.document.getElementById('idle-count').textContent, '—');
  await h.answer(h.requests[0], { ...snapshot('27131', [device, { id: 'idle-w', name: 'W', state: 1 }]),
    dryers: [{ id: 'idle-d', name: 'D', state: 1 }, { id: 'offline', name: 'E', state: 4 }] });
  assert.equal(h.document.getElementById('idle-count').textContent, '2');
  assert.equal(h.document.getElementById('running-count').textContent, '1');
  assert.equal(h.document.getElementById('error-count').textContent, '1');
  assert.equal(h.document.getElementById('available-detail').textContent, '洗衣 1 台 · 烘干 1 台');
  h.document.querySelectorAll('.tab')[1].click();
  assert.equal(h.document.getElementById('idle-count').textContent, '—');
  await h.answer(h.requests[1], snapshot('27136', []));
  assert.equal(h.document.getElementById('idle-count').textContent, '0');
  assert.match(h.document.getElementById('content').textContent, /暂无设备/);
});

test('web: manual refresh disables repeated clicks and marks stale summaries on failure', async t => {
  const h = await webHarness(); t.after(() => h.dom.window.close());
  const button = h.document.getElementById('refresh-button');
  assert.equal(button.disabled, true);
  await h.answer(h.requests[0], snapshot());
  button.click(); button.click();
  assert.equal(button.disabled, true);
  assert.equal(h.requests.length, 2);
  await h.answer(h.requests[1], { error: 'failed' }, 503);
  assert.equal(button.disabled, false);
  assert.equal(h.document.getElementById('data-status').textContent, '上次采集');
  assert.equal(h.document.getElementById('content').getAttribute('aria-busy'), 'false');
  assert.equal(h.document.getElementById('running-count').textContent, '1');
});
