from flask import Flask, jsonify, render_template_string, request, Response, send_file
from datetime import datetime, timedelta
import requests

app = Flask(__name__)

API_URL = "https://yshz-user.haier-ioc.com/position/deviceDetailPage"

# 楼栋配置，添加新楼栋只需在这里加一条
BUILDINGS = [
    {"id": "27131", "name": "西21楼"},
    {"id": "27136", "name": "西16楼"},
]


def fetch_devices(position_id, category_code="00"):
    body = {
        "positionId": position_id,
        "categoryCode": category_code,
        "page": 1,
        "floorCode": "",
        "pageSize": 20,
    }
    resp = requests.post(API_URL, json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if data["code"] != 0:
        raise RuntimeError(f"API error: {data['message']}")
    return data["data"]["items"]


@app.route("/api/devices")
def api_devices():
    position_id = request.args.get("id", BUILDINGS[0]["id"])
    washers = fetch_devices(position_id, "00")
    dryers = fetch_devices(position_id, "02")
    return jsonify({"washers": washers, "dryers": dryers})


@app.route("/api/reminder")
def api_reminder():
    name = request.args.get("name", "洗衣机")
    finish_time = request.args.get("finishTime", "")
    minutes = int(request.args.get("minutes", 5))

    dt = datetime.strptime(finish_time, "%Y-%m-%d %H:%M:%S")
    remind_time = dt - timedelta(minutes=minutes)
    now = datetime.now()

    uid = f"{now.timestamp()}@laundry"
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//Laundry//CN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTAMP:{now.strftime('%Y%m%dT%H%M%S')}\r\n"
        f"DTSTART:{remind_time.strftime('%Y%m%dT%H%M%S')}\r\n"
        f"DTEND:{dt.strftime('%Y%m%dT%H%M%S')}\r\n"
        f"SUMMARY:{name} 快空了\r\n"
        f"DESCRIPTION:预计 {dt.strftime('%H:%M')} 结束，提前{minutes}分钟提醒你去检查\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR"
    )

    return Response(
        ics,
        mimetype="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": "inline; filename=reminder.ics",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.route("/app.apk")
def download_apk():
    return send_file("/root/laundry/LaundryApp.apk", mimetype="application/vnd.android.package-archive")


@app.route("/")
def index():
    return render_template_string(HTML, buildings=BUILDINGS)


HTML = """
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>交大洗衣机</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, sans-serif; background: #f5f5f5; padding: 16px; }
h1 { font-size: 20px; text-align: center; margin-bottom: 4px; }
.subtitle { text-align: center; color: #888; font-size: 13px; margin-bottom: 16px; }
.tabs { display: flex; gap: 0; margin-bottom: 16px; border-radius: 12px;
        overflow: hidden; border: 1px solid #4caf50; }
.tab { flex: 1; text-align: center; padding: 10px 0; font-size: 15px;
       background: #fff; color: #4caf50; cursor: pointer; font-weight: 500; }
.tab.active { background: #4caf50; color: #fff; }
.section-title { font-size: 16px; font-weight: 600; margin: 16px 0 8px; }
.card { background: #fff; border-radius: 12px; padding: 14px 16px; margin-bottom: 10px;
        display: flex; justify-content: space-between; align-items: center;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
.name { font-size: 16px; font-weight: 500; }
.status { font-size: 14px; padding: 4px 12px; border-radius: 20px; font-weight: 500; }
.idle { background: #e8f5e9; color: #2e7d32; }
.running { background: #fff3e0; color: #e65100; }
.error { background: #fce4ec; color: #c62828; }
.time { font-size: 12px; color: #888; margin-top: 2px; }
.remind-btn { display: inline-block; margin-top: 6px; padding: 3px 10px; font-size: 12px;
             border: 1px solid #4caf50; color: #4caf50; border-radius: 12px;
             background: none; cursor: pointer; text-decoration: none; }
.remind-opts { display: none; margin-top: 6px; gap: 6px; align-items: center; }
.remind-opts.show { display: flex; }
.remind-input { width: 50px; padding: 3px 6px; font-size: 13px; border: 1px solid #ccc;
               border-radius: 8px; text-align: center; }
.remind-confirm { padding: 3px 10px; font-size: 12px; border: 1px solid #4caf50;
                 color: #fff; background: #4caf50; border-radius: 12px;
                 cursor: pointer; }
.countdown { display: none; margin-top: 6px; font-size: 13px; color: #4caf50; font-weight: 600; }
.toast { position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
         background: #333; color: #fff; padding: 10px 24px; border-radius: 20px;
         font-size: 14px; z-index: 999; opacity: 0; transition: opacity 0.3s; }
.toast.show { opacity: 1; }
.refresh { text-align: center; color: #999; font-size: 12px; margin-top: 16px; }
</style>
</head>
<body>
<h1>洗衣机状态</h1>
<p class="subtitle">上海交通大学闵行校区</p>
<div class="tabs">
{% for b in buildings %}
  <div class="tab{{ ' active' if loop.first }}" onclick="switchBuilding('{{ b.id }}', this)">{{ b.name }}</div>
{% endfor %}
</div>
<div id="content">加载中...</div>
<p class="refresh" id="refresh-info"></p>
<div class="toast" id="toast"></div>

<script>
let currentId = '{{ buildings[0].id }}';
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

function switchBuilding(id, el) {
  currentId = id;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  refresh();
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2000);
}

function toggleOpts(id) {
  document.getElementById(id).classList.toggle('show');
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
setInterval(tickAll, 1000);

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

function renderCard(d) {
  const stateClass = {1:'idle', 2:'running', 3:'error', 4:'error'}[d.state] || 'error';
  const stateText = {1:'空闲', 2:'使用中', 3:'故障', 4:'离线'}[d.state] || '未知';
  const active = activeReminders[d.id];
  let rightHtml = `<span class="status ${stateClass}">${stateText}</span>`;
  let leftExtra = '';
  if (d.finishTime) {
    const t = d.finishTime.split(' ')[1].slice(0,5);
    leftExtra += `<div class="time">预计 ${t} 结束</div>`;
    if (d.state === 2) {
      const optsId = 'opts-' + d.id;
      const inputId = 'input-' + d.id;
      const btnId = 'remind-btn-' + d.id;
      const cdId = 'countdown-' + d.id;
      if (active) {
        leftExtra += `<a class="remind-btn" id="${btnId}" style="display:none" href="javascript:void(0)" onclick="toggleOpts('${optsId}')">提醒我</a>`;
        leftExtra += `<div class="countdown" id="${cdId}" style="display:block"></div>`;
        leftExtra += `<div class="remind-opts" id="${optsId}">`;
        leftExtra += `</div>`;
      } else {
        leftExtra += `<a class="remind-btn" id="${btnId}" href="javascript:void(0)" onclick="toggleOpts('${optsId}')">提醒我</a>`;
        leftExtra += `<div class="countdown" id="${cdId}" style="display:none"></div>`;
        leftExtra += `<div class="remind-opts" id="${optsId}">`;
        leftExtra += `提前<input class="remind-input" id="${inputId}" type="number" value="5" min="1">分钟`;
        leftExtra += `<button class="remind-confirm" onclick="addReminder('${d.name}','${d.finishTime}','${inputId}','${d.id}')">确定</button>`;
        leftExtra += `</div>`;
      }
    }
  }
  return `<div class="card">
    <div><div class="name">${d.name}</div>${leftExtra}</div>
    ${rightHtml}
  </div>`;
}

async function refresh() {
  try {
    const resp = await fetch('/api/devices?id=' + currentId);
    const data = await resp.json();
    let html = '<div class="section-title">洗衣机</div>';
    data.washers.forEach(d => html += renderCard(d));
    if (data.dryers.length) {
      html += '<div class="section-title">烘干机</div>';
      data.dryers.forEach(d => html += renderCard(d));
    }
    document.getElementById('content').innerHTML = html;
    const now = new Date().toLocaleTimeString('zh', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
    document.getElementById('refresh-info').textContent = `上次更新: ${now} · 每30秒自动刷新`;
  } catch(e) {
    document.getElementById('content').innerHTML = '<p style="text-align:center;color:#c62828">加载失败，请检查网络</p>';
  }
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

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
