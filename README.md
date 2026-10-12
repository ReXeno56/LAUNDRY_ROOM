# 交大洗衣状态查询

**简体中文** | [English](README.en.md)

Flask 网页与原生微信小程序，共用设备查询、微信登录及订阅提醒接口。App 工程不在本次调整范围。

## 安装与启动

需要 Python 3.10+；前端测试需要 Node.js 18+。

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

首次部署时按 `.env.example` 创建 `.env`，填写 `WX_SECRET`。已有 `.env` 可继续使用。
楼栋和默认微信配置统一放在 `config.json`；环境变量 `WX_APPID`、`WX_TEMPLATE_ID` 可覆盖默认配置。
微信 Secret 只在服务端使用，不出现在公开配置接口。
消息卡片默认跳转正式版；体验测试可将服务器的 `WX_MINIPROGRAM_STATE` 设为 `trial`，发布正式版后改回 `formal` 并重启提醒进程。

本地网页启动：

```bash
venv/bin/python app.py
```

**另开一个终端启动提醒进程，否则提醒只能入库，不会派发：**

```bash
venv/bin/python -m laundry.worker
```

生产网页使用 WSGI 服务，反向代理提供 HTTPS：

```bash
venv/bin/gunicorn --workers 2 --threads 4 --timeout 30 --bind 127.0.0.1:5000 app:app
```

网页与提醒进程必须使用同一 `LAUNDRY_DB`，默认是项目的 `instance/laundry.sqlite3`。
它是持久化数据，不应随发布删除；容器部署须挂载持久卷。为两种进程各配置一个服务并自动重启。
导入 Web 模块不会启动发送线程；提醒进程每 5 秒检查一次到期任务。

## 微信小程序

用微信开发者工具导入项目根目录（已配置 `miniprogramRoot: "mini-program/"`），也可直接导入 `mini-program/`。
在 `mini-program/app.js` 配置 API HTTPS 域名，当前是 `https://sjtulaundry.xyz`。
楼栋与订阅模板 ID 从 `/api/config` 加载，无需另改小程序中的常量。
公众平台的 AppID 必须与服务端一致；现有模板需包含 `thing19`、`time26`、`thing35`。
正式发布前配置 HTTPS request 合法域名并进行真机测试。项目默认开启域名检查；开发工具临时关闭检查不能替代公众平台配置。

**服务器部署接口与网页，小程序源码通过微信开发者工具上传到微信。** 两个步骤都要完成。
具体上传目录、域名配置、发布流程和当前线上版本检查见 [微信部署说明](docs/wechat-deployment.md)。

提醒授权只在用户点击确认时请求，`accept` 后才提交；登录过期会重新登录并重试一次，不重复请求授权。
发送结果依赖微信返回值与用户授权；本地测试使用模拟接口，不会向用户发送真实消息。

## 界面与主题

西16支持设备状态与 [预约文档](https://docs.qq.com/sheet/DT1FqTlV4SWVqWWhs?tab=ss_9ipbgd) 联合查询：区分当前可用、空闲但已预约和预约待确认，并显示下一场时段。读取、映射和缓存规则见 [预约查询说明](docs/reservations.md)。

网页和小程序使用同一组深浅配色，默认跟随系统。头部菜单可手动选择浅色或深色，选择在当前设备保存。
两端记住上次楼栋和合法提前分钟数（1–30，默认5）；存储不可用时仍可正常使用。
空闲数量优先展示，并分别列出可用洗衣机和烘干机；设备卡片包含状态、预计结束时间和提醒入口。
网页宽屏显示双列，手机显示单列。两端支持手动刷新，后台刷新保留正在编辑的提醒。
加载时数量显示 `—`；失败时保留旧数据并标记“上次采集”，避免把未加载当成零台可用。

设备按空闲优先、使用中预计结束时间、异常状态排列；“仅看空闲”筛选保持整栋概览统计，保留正在编辑的输入。
小程序通过 `darkmode`、`theme.json` 和有效主题样式适配，导航栏、下拉刷新背景也同步切换。
样式与交互规范见 [DESIGN.md](DESIGN.md)。

## 数据与提醒行为

- 设备两类并行查询，自动翻页；每栋缓存 10 秒。SQLite 协调跨进程并发查询，相同采集结果使用相同 `fetchedAt`。
- `finishTime` 保留上海时间格式 `YYYY-MM-DD HH:MM:SS`；新增 `finishAt`、`fetchedAt` 为 UTC ISO 8601。无法解析的结束时间置空，停用对应提醒。
- 外部服务异常可返回旧设备数据，同时提供 `stale: true` 与警告；无旧数据时返回 JSON 502/504。
- 业务会话保存 Token 的 SHA-256 摘要，有效期 7 天，重启和多 worker 共享。
- 同一用户、楼栋、设备、结束时间的重复确认复用原提醒 ID 和分钟数，不新建任务；已取消后可重新设置新ID和提前量。提醒分钟范围是整数 1–30。
- 任务状态为 `pending`、`sending`、`retry`、`sent`、`failed`、`uncertain`、`cancelled`。仅微信明确返回 `errcode=0` 才标记 `sent`，这不代表用户已读。
- 明确可重试的拒绝或连接超时最多尝试 3 次，重试间隔 10、30 秒；到设备结束时停止重试。永久拒绝保留为 `failed`。
- 读超时、发送 HTTP 异常或无法确认的响应保存为 `uncertain`；派发过程中断超过 60 秒也标为 `uncertain`，不自动重发，避免重复通知。
- 网页下载的是带 `VALARM` 的日历文件。需用户导入并允许日历通知，实际通知取决于日历客户端。
- 小程序“我的提醒”展示所有楼栋的进行中提醒，以及最近30天最多50条历史记录。仅待发送和重试中的任务可取消；修改提前量需先取消再设置。
- 原有数据库首次加载会事务迁移，保留会话及全部记录；上线时先停止 Web 与提醒进程，再执行 `python -m laundry.maintenance migrate`。

## 本机备份与运行检查

```bash
venv/bin/python -m laundry.maintenance backup
venv/bin/python -m laundry.maintenance verify /path/to/backup.tar.gz
venv/bin/python -m laundry.maintenance health
```

每日北京时间03:00执行在线备份并恢复校验，独立保存最近14份；每分钟检查公网健康接口、两个服务、派发心跳和积压。
系统服务模板在 `deploy/systemd/`，安装时将虚拟环境路径替换为服务器实际位置。具体管理与恢复说明见 [运维说明](docs/operations.md)。

### 从旧版 JSON 迁移

先停止旧版服务，确保其后台提醒线程不再运行，保留旧 JSON 备份；随后执行：

```bash
venv/bin/python -m laundry.worker --import-legacy /tmp/laundry_reminders.json
```

导入是事务性的且可重复执行，不删除原文件。旧记录没有设备 ID，使用独立的 legacy 标识保留。
旧版内存会话无法迁移；客户端通过 401 后重新登录恢复。新小程序提交提醒须提供楼栋和设备标识，部署后同步发布新版小程序。

## 接口

| 接口 | 内容 |
| --- | --- |
| `GET /api/config` | `buildings`、`templateId`、`appId`、分钟范围 |
| `GET /api/devices?id=27131` | `washers`、`dryers`、`buildingId`、`fetchedAt`、`stale`、`warning` |
| `GET /api/wx-login?code=...` | `token`、`openid`、`expiresAt` |
| `POST /api/wx-subscribe` | JSON：`token`、`buildingId`、`deviceId`、`finishAt`（或 `finishTime`）、`minutes`、可选 `templateId`；返回 `ok`、`created`、`reminderId`、`status`、`minutes`、`remindAt`、`remindAtISO`、`finishAt` |
| `GET /api/wx-reminders/<id>` | 使用 `Authorization: Bearer <token>` 查询自己的提醒状态及 `lastError` |
| `GET /api/wx-reminders` | Bearer 鉴权，返回本人所有楼栋的 `active` 与最近30天最多50条 `history` |
| `POST /api/wx-reminders/<id>/cancel` | Bearer 鉴权，取消待发送/重试任务；重复取消成功，已发送中或结束为409，其他用户或不存在为404 |
| `GET /api/health` | 返回 `ok` 和数据库、提醒进程心跳布尔值；健康200，异常503 |
| `GET /api/reminder` | 日历下载；参数 `name`、`finishAt`（或 `finishTime`）、`minutes`，可选 `buildingId` |

输入错误为 JSON 400，过期会话为 401，设备或模板变化为 409，外部错误为 502/504；存储暂不可用为 503。
异常包含 `ok: false`、`error` 和 `code`。原设备列表字段和日历查询路径保留。

## 测试

```bash
venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
npm --prefix tests ci --ignore-scripts
npm --prefix tests test
```

后端测试覆盖 SQLite 持久化、并发去重、共享缓存、分页、输入校验、时区、日历闹铃与派发失败分类。
前端测试用 Node VM 模拟微信 API，用 jsdom 检查网页真实 DOM 操作，覆盖授权拒绝、401 重试、倒序响应、生命周期、输入保持和安全渲染。
