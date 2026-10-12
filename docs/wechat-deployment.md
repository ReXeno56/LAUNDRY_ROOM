# 微信小程序如何运行与发布

本项目采用“微信小程序前端 + Flask 接口服务”。微信负责分发小程序前端；设备查询、登录、提醒存储和消息派发由服务器运行。
因此，把小程序代码上传到微信后，还需要让服务器上的接口可被手机访问。本地电脑上的 Flask 不适合作为长期在线服务。

## 本次检查到的状态

2026-10-08 已将新版网页和后端部署到阿里云服务器 `106.14.91.217`：

- `https://sjtulaundry.xyz/` 已更新主题菜单，支持跟随系统、浅色和深色，并保存当前设备的选择。
- `/api/config` 和两栋楼的 `/api/devices` 均返回 200；每栋楼本次采集到 4 台洗衣机、1 台烘干机。
- 网页和微信提醒进程分别由 `laundry.service`、`laundry-reminders.service` 管理，已启用开机启动与故障自动重启。
- 服务器获取微信 access_token 成功；已核实配置的订阅模板存在，字段为 `thing19`、`time26`、`thing35`。
- HTTPS 证书验证、公网浏览器楼栋切换与系统主题切换通过。用户已确认现有小程序正式版发布并真机收到提醒。

现有正式版已经发布。本轮主题菜单、“我的提醒”和常用设置优化的小程序代码需再次通过开发者工具上传、审核和发布；网页与后端可独立更新。
服务、数据库与备份位置见 [本轮升级记录](deployment-enhancements-2026-10-08.md)，维护方法见 [运行说明](operations.md)。

## 分别上传哪些文件

| 位置 | 需要的内容 | 发布方式 |
| --- | --- | --- |
| 自己的服务器 | `app.py`、`laundry/`、`templates/`、`static/`、`config.json`、`requirements.txt` | 更新项目代码并重启 Web 服务与提醒进程 |
| 微信平台 | `mini-program/` 内的小程序源码与配置 | 微信开发者工具的“上传” |
| 服务器本地配置与数据 | `.env` 中的 `WX_SECRET`，`instance/laundry.sqlite3` 或 `LAUNDRY_DB` 指定的数据库 | 持久保存，保留已有数据库；Secret 只放服务器 |

## 1. 更新服务器接口

进入服务器上的项目目录，安装依赖，按 `.env.example` 填好服务端配置。保持服务器的 AppID 与开发者工具中一致。
生产 Web 服务和提醒进程分别运行：

```bash
venv/bin/python -m pip install -r requirements.txt
venv/bin/gunicorn --workers 2 --threads 4 --timeout 30 --bind 127.0.0.1:5000 app:app
```

另一个进程：

```bash
venv/bin/python -m laundry.worker
```

两种进程必须使用同一个持久化数据库，并配置服务自动重启。只启动 Gunicorn，提醒会入库但不会发出。
订阅消息默认跳转正式版。测试体验版时，在服务器 `.env` 设置 `WX_MINIPROGRAM_STATE=trial` 并重启提醒进程；正式发布后改回 `formal`。
由现有 Nginx/Caddy 等反向代理将 `https://sjtulaundry.xyz` 转发到 Web 服务。
更新旧版本时先停止旧提醒线程，JSON 迁移方法见 README，避免新旧派发进程同时运行。

更新后，以下两个地址应返回 JSON，而不是 404 或 HTML 错误页：

```text
https://sjtulaundry.xyz/api/config
https://sjtulaundry.xyz/api/devices?id=27131
```

## 2. 配置微信服务器域名

登录小程序公众平台，在“开发 → 开发设置 → 服务器域名”中，将 **request 合法域名** 配置为：

```text
https://sjtulaundry.xyz
```

域名配置只填写协议和域名，不附加 `/api/config` 等接口路径。
按微信当前文档，普通请求使用 HTTPS，域名必须经过 ICP 备案，TLS 须支持 1.2 及以上，并使用有效且受信任的证书。
AppSecret 保留在后端，小程序通过自己的后端完成微信登录与消息发送。[微信网络文档](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html)

开发者工具的“不校验请求域名、TLS 版本及 HTTPS 证书”只用于临时调试。应关闭后再做真机检查；它不能替代正式域名配置。[微信网络文档](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html)

微信云托管是另一种后端部署方式，使用 `callContainer` 可免自定义通讯域名配置；当前项目使用 `wx.request`，继续沿用已有 HTTPS 服务即可，不需要为本次界面调整迁移架构。[微信网络文档](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html)

## 3. 导入、预览、上传和发布小程序

1. 微信开发者工具导入项目根目录，`project.config.json` 的 `miniprogramRoot` 已指向 `mini-program/`；也可以直接导入该子目录。确认 AppID 属于自己的小程序。[项目配置文档](https://developers.weixin.qq.com/miniprogram/dev/devtools/projectconfig.html)
2. 确认 `mini-program/app.js` 的 `apiBase` 与上面的 HTTPS 域名一致，服务器 `/api/config` 返回正确的楼栋、AppID 和模板 ID。
3. 点击“预览”，用开发者微信扫码。检查系统和手动主题、楼栋及分钟数保存、仅看空闲；在“我的提醒”创建并取消一条尚未发送的提醒，再用不同提前量重新设置。另保留一条实际提醒，确认服务通知收到消息。
4. 点击“上传”，填写版本号和备注。在公众平台“版本管理 → 开发版本”设为体验版，并添加需要使用的体验成员。
5. 面向普通用户上线时，提交审核；审核通过后在公众平台手动发布。微信官方流程为预览、上传、提交审核、发布。[发布上线文档](https://developers.weixin.qq.com/miniprogram/dev/framework/quickstart/release.html)

预览可用于开发者真机测试，体验版可用于成员测试；普通用户使用的是审核后发布的线上版本。

## 深浅主题

本项目已按微信 DarkMode 方案配置 `darkmode: true` 和 `themeLocation: "theme.json"`。
默认跟随系统，头部菜单可选择浅色或深色并保存在当前设备。导航栏、刷新背景、页面和设备组件同步切换，无需重新加载数据。[DarkMode 文档](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/darkmode.html)
