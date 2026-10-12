// Extracted native notification behavior; App is outside this change.
function isNative() {
  return !!(window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.LocalNotifications);
}
let activeReminders = {};

// Persist reminders across app restarts
(function restoreReminders() {
  try {
    const saved = JSON.parse(localStorage.getItem('laundry_reminders') || '{}');
    for (const [deviceId, r] of Object.entries(saved)) {
      r.remindDt = new Date(r.remindDt);  // revive Date from string
      if (r.remindDt > new Date()) {
        activeReminders[deviceId] = r;
      }
    }
  } catch(e) {}
})();

function saveReminders() {
  const flat = {};
  for (const [id, r] of Object.entries(activeReminders)) {
    flat[id] = { remindDt: r.remindDt.toISOString(), name: r.name };
  }
  localStorage.setItem('laundry_reminders', JSON.stringify(flat));
}

// Single global ticker that survives DOM rebuilds
function tickAll() {
  for (const [deviceId, r] of Object.entries(activeReminders)) {
    const cd = document.getElementById('countdown-' + deviceId);
    if (!cd) continue;
    const diff = r.remindDt - new Date();
    if (diff <= 0) {
      cd.textContent = '即将提醒...';
      cd.style.color = '#e65100';
      if (diff < -30000) {
        delete activeReminders[deviceId];
        saveReminders();
      }
      continue;
    }
    if (diff < 30000) {
      cd.textContent = '即将提醒...';
      cd.style.color = '#e65100';
      continue;
    }
    const mm = String(Math.floor(diff / 60000)).padStart(2, '0');
    const ss = String(Math.floor((diff % 60000) / 1000)).padStart(2, '0');
    cd.textContent = mm + ':' + ss + ' 后提醒';
    cd.style.color = '#4caf50';
  }
}
if (isNative()) setInterval(tickAll, 1000);

function addReminder(name, finishTime, inputId, deviceId) {
  // Prevent double-scheduling
  if (activeReminders[deviceId]) { showToast('已设置过提醒'); return; }

  const min = parseInt(document.getElementById(inputId).value);
  if (!min || min < 1) { showToast('请输入有效的分钟数'); return; }

  const [dp, tp] = finishTime.split(' ');
  const [y, mo, d] = dp.split('-');
  const [H, M, S] = tp.split(':');
  const endDt = new Date(y, mo - 1, d, H, M, S);
  const remindDt = new Date(endDt.getTime() - min * 60000);

  // Capacitor app: schedule local push notification
  if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.LocalNotifications) {
    const now = new Date();
    if (remindDt <= now) { showToast('提醒时间已过'); return; }

    // Cancel old notification for this device if any
    const old = activeReminders[deviceId];
    if (old && old.notifId) {
      window.Capacitor.Plugins.LocalNotifications.cancel({ notifications: [{ id: old.notifId }] }).catch(() => {});
    }
    const notifId = Math.floor(Math.random() * 100000);
    window.Capacitor.Plugins.LocalNotifications.schedule({
      notifications: [{
        id: notifId,
        title: name + ' 快空了',
        body: '预计 ' + H + ':' + M + ' 结束',
        schedule: { at: remindDt },
        smallIcon: 'ic_notification',
        channelId: 'laundry_channel'
      }]
    }).catch((e) => {
      showToast('设置失败: ' + e.message);
    });
    activeReminders[deviceId] = { remindDt, name, notifId };
    saveReminders();
    // Immediately update DOM: hide button + options, show countdown
    document.getElementById('remind-btn-' + deviceId).style.display = 'none';
    document.getElementById('opts-' + deviceId).classList.remove('show');
    const cdNow = document.getElementById('countdown-' + deviceId);
    cdNow.style.display = 'block';
    cdNow.style.color = '#4caf50';
    cdNow.textContent = String(min).padStart(2,'0') + ':00 后提醒';
    showToast('已设置提醒');
    return;
  }

  // Browser fallback: ICS download
  window.location.href = '/api/reminder?name=' + encodeURIComponent(name) + '&finishTime=' + encodeURIComponent(finishTime) + '&minutes=' + min;
}

// Init Capacitor notification channel & request permission
if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.LocalNotifications) {
  window.Capacitor.Plugins.LocalNotifications.createChannel({
    id: 'laundry_channel',
    name: '洗衣机提醒',
    importance: 4
  }).catch(() => {});
  window.Capacitor.Plugins.LocalNotifications.requestPermissions().catch(() => {});
}
