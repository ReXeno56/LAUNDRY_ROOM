const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '..');
const flush = () => new Promise(resolve => setImmediate(resolve));
const buildings = [{ id: '27131', name: '西21楼' }, { id: '27136', name: '西16楼' }];
const preferencesKey = 'laundry_preferences';
const later = minutes => new Date(Date.now() + minutes * 60000).toISOString();
const machine = (id, state, minutes = 120) => ({ id, name: id, state, finishAt: later(minutes) });
const snapshot = (washers, dryers = [], buildingId = '27131') => ({
  washers, dryers, buildingId, stale: false, fetchedAt: later(0),
});

async function harness({ saved, dark = false, blockedStorage = false } = {}) {
  const html = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8')
    .replace('{{ public_config|tojson }}', JSON.stringify({ buildings }))
    .replace(/\{\{\s*url_for\('static', filename='([^']+)'\)\s*\}\}/g, (_, name) => '/static/' + name);
  const dom = new JSDOM(html, { url: 'https://test.invalid/', runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom, { document } = window;
  await flush();
  const listeners = [], requests = [], timers = new Map(); let timerId = 0;
  const media = { matches: dark, addEventListener(name, fn) { if (name === 'change') listeners.push(fn); } };
  window.matchMedia = () => media;
  window.setTimeout = (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; };
  window.clearTimeout = id => timers.delete(id);
  window.setInterval = window.setTimeout;
  window.fetch = (url, options) => new Promise(resolve => requests.push({ url, options, resolve }));
  window.URL.createObjectURL = () => 'blob:test'; window.URL.revokeObjectURL = () => {};
  window.HTMLAnchorElement.prototype.click = () => {};
  if (saved !== undefined) window.localStorage.setItem(preferencesKey, typeof saved === 'string' ? saved : JSON.stringify(saved));
  if (blockedStorage) Object.defineProperty(window, 'localStorage', { get() { throw new Error('Storage disabled'); } });
  window.eval(fs.readFileSync(path.join(root, 'static/preferences.js'), 'utf8'));
  window.eval(fs.readFileSync(path.join(root, 'static/native-reminders.js'), 'utf8'));
  window.eval(fs.readFileSync(path.join(root, 'static/main.js'), 'utf8'));
  const answer = async (request, data) => {
    request.resolve({ ok: true, json: async () => data, blob: async () => new window.Blob(['calendar']) }); await flush();
  };
  const changeTheme = mode => {
    const select = document.getElementById('theme-mode'); select.value = mode;
    select.dispatchEvent(new window.Event('change'));
  };
  const systemTheme = value => { media.matches = value; listeners.forEach(fn => fn({ matches: value })); };
  const visibleNames = category => [...document.querySelectorAll('.device-section')[category].querySelectorAll('.card:not([hidden]) .name')]
    .map(node => node.textContent);
  return { dom, window, document, requests, timers, answer, changeTheme, systemTheme, visibleNames };
}

test('web preferences: theme bootstrap runs before styles and follows changing system settings', async t => {
  const source = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8');
  assert.ok(source.indexOf("filename='preferences.js'") < source.indexOf('rel="stylesheet"'));
  const h = await harness({ dark: true }); t.after(() => h.dom.window.close());
  assert.equal(h.document.documentElement.dataset.theme, 'dark');
  assert.equal(h.document.getElementById('theme-mode').value, 'system');
  h.systemTheme(false); assert.equal(h.document.documentElement.dataset.theme, 'light');
  h.systemTheme(true); assert.equal(h.document.documentElement.dataset.theme, 'dark');
});

test('web preferences: manual themes override system changes and survive reloading', async t => {
  const h = await harness({ dark: true }); t.after(() => h.dom.window.close());
  h.changeTheme('light'); h.systemTheme(false); h.systemTheme(true);
  assert.equal(h.document.documentElement.dataset.theme, 'light');
  assert.equal(h.document.getElementById('theme-color').content, '#f5f7f3');
  const saved = h.window.localStorage.getItem(preferencesKey);
  const reload = await harness({ saved, dark: true }); t.after(() => reload.dom.window.close());
  assert.equal(reload.document.documentElement.dataset.theme, 'light');
  assert.equal(reload.document.getElementById('theme-mode').value, 'light');
  reload.changeTheme('dark'); reload.systemTheme(false);
  assert.equal(reload.document.documentElement.dataset.theme, 'dark');
  reload.changeTheme('system');
  assert.equal(reload.document.documentElement.dataset.theme, 'light');
});

test('web preferences: damaged, invalid and unavailable storage use usable defaults', async t => {
  for (const options of [{ saved: '{broken' }, { saved: [] },
    { saved: { theme: 'other', buildingId: 'deleted', minutes: 31 } }, { blockedStorage: true }]) {
    const h = await harness(options); t.after(() => h.dom.window.close());
    assert.equal(h.document.getElementById('theme-mode').value, 'system');
    assert.match(h.requests[0].url, /id=27131$/);
    await h.answer(h.requests[0], snapshot([machine('A', 2)]));
    assert.equal(h.document.querySelector('.remind-input').value, '5');
    h.changeTheme('dark');
    assert.equal(h.document.documentElement.dataset.theme, 'dark');
    h.document.querySelectorAll('.tab')[1].click();
    assert.match(h.requests.at(-1).url, /id=27136$/);
  }
});

test('web preferences: remembers the building and valid minutes without retaining invalid input', async t => {
  const h = await harness({ saved: { buildingId: '27136', minutes: 12 } }); t.after(() => h.dom.window.close());
  assert.match(h.requests[0].url, /id=27136$/);
  assert.equal(h.document.querySelectorAll('.tab')[1].getAttribute('aria-pressed'), 'true');
  await h.answer(h.requests[0], snapshot([machine('A', 2)], [], '27136'));
  const input = h.document.querySelector('.remind-input'); assert.equal(input.value, '12');
  for (const value of ['30', '0', '31', '1.5', '']) {
    input.value = value; input.dispatchEvent(new h.window.Event('input'));
  }
  assert.equal(JSON.parse(h.window.localStorage.getItem(preferencesKey)).minutes, 30);
  h.document.querySelectorAll('.tab')[0].click();
  assert.equal(JSON.parse(h.window.localStorage.getItem(preferencesKey)).buildingId, '27131');
  const reload = await harness({ saved: h.window.localStorage.getItem(preferencesKey) }); t.after(() => reload.dom.window.close());
  await reload.answer(reload.requests[0], snapshot([machine('B', 2)]));
  assert.equal(reload.document.querySelector('.remind-input').value, '30');
});

test('web devices: sorts each category by availability and completion time with stable ties', async t => {
  const h = await harness(); t.after(() => h.dom.window.close());
  const same = later(60);
  await h.answer(h.requests[0], snapshot([
    machine('fault', 3), machine('later', 2, 90), machine('idle-1', 1),
    { ...machine('earlier-1', 2), finishAt: same }, { ...machine('earlier-2', 2), finishAt: same },
    machine('idle-2', 1), { id: 'unknown-time', name: 'unknown-time', state: 2 },
  ], [machine('dryer-running', 2), machine('dryer-idle', 1), machine('dryer-offline', 4)]));
  assert.deepEqual(h.visibleNames(0), ['idle-1', 'idle-2', 'earlier-1', 'earlier-2', 'later', 'unknown-time', 'fault']);
  assert.deepEqual(h.visibleNames(1), ['dryer-idle', 'dryer-running', 'dryer-offline']);
  assert.equal(h.document.getElementById('idle-filter').getAttribute('aria-pressed'), 'false');
});

test('web devices: filtering preserves totals, reminder input and expanded panel across refreshes', async t => {
  const h = await harness(); t.after(() => h.dom.window.close());
  const data = snapshot([machine('running', 2), machine('idle', 1), machine('fault', 3)]);
  await h.answer(h.requests[0], data);
  h.document.querySelector('#remind-btn-running').click();
  const input = h.document.querySelector('#input-running'); input.value = '17';
  const panel = h.document.querySelector('#opts-running');
  const filter = h.document.getElementById('idle-filter'); filter.click();
  assert.deepEqual(h.visibleNames(0), ['idle']);
  assert.equal(h.document.getElementById('running-count').textContent, '1');
  assert.equal(h.document.getElementById('error-count').textContent, '1');
  h.document.getElementById('refresh-button').click(); await h.answer(h.requests[1], data);
  filter.click();
  assert.equal(h.document.querySelector('#input-running'), input);
  assert.equal(input.value, '17');
  assert.equal(h.document.querySelector('#opts-running'), panel);
  assert.ok(panel.classList.contains('show'));
});

test('web devices: sorting after a changed finish time retains the focused reminder input', async t => {
  const h = await harness(); t.after(() => h.dom.window.close());
  await h.answer(h.requests[0], snapshot([machine('A', 2, 60), machine('B', 2, 90)]));
  h.document.querySelector('#remind-btn-A').click();
  const input = h.document.querySelector('#input-A'); input.value = '9'; input.focus();
  h.document.getElementById('refresh-button').click();
  await h.answer(h.requests[1], snapshot([machine('A', 2, 120), machine('B', 2, 45)]));
  assert.deepEqual(h.visibleNames(0), ['B', 'A']);
  assert.equal(h.document.activeElement, input);
  assert.equal(input.value, '9');
  assert.ok(h.document.getElementById('opts-A').classList.contains('show'));
});

test('web devices: the no-idle result is distinct from a building without any devices', async t => {
  const h = await harness(); t.after(() => h.dom.window.close());
  await h.answer(h.requests[0], snapshot([machine('A', 2)]));
  h.document.getElementById('idle-filter').click();
  assert.equal(h.document.querySelector('#content .placeholder p').textContent, '暂无空闲设备');
  h.document.getElementById('refresh-button').click(); await h.answer(h.requests[1], snapshot([]));
  assert.equal(h.document.querySelector('#content .placeholder p').textContent, '暂无设备');
});
