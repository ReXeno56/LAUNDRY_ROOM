# 2026-10-08 阿里云部署记录

本文件记录首次后端部署，公网地址为 <https://sjtulaundry.xyz>。用户随后已发布小程序正式版并确认真机收到提醒。主题菜单、提醒管理与维护功能的后续部署见 [本轮升级记录](deployment-enhancements-2026-10-08.md)。

## 运行位置

| 项目 | 服务器位置 |
| --- | --- |
| 应用代码 | `/root/laundry` |
| Python 环境 | `/root/laundry-venv-20261008-004933` |
| 配置 | `/root/laundry/.env`，沿用原有微信凭据 |
| 会话与提醒数据库 | `/root/laundry/instance/laundry.sqlite3` |
| 部署前备份 | `/root/laundry-backups/20261008-004933` |
| 新版暂存目录 | `/root/laundry-stage-20261008-004933` |

Web 使用 Gunicorn，2 个 worker、每个 4 个线程，监听 `127.0.0.1:5000`。现有 Nginx 继续负责 HTTPS 反向代理。
微信提醒由独立进程运行，两种服务共用上述 SQLite 数据库。

## 服务管理

以下命令在服务器执行：

```bash
systemctl status laundry.service laundry-reminders.service
systemctl restart laundry.service laundry-reminders.service
journalctl -u laundry-reminders.service -n 50 --no-pager
```

服务配置位于 `/etc/systemd/system/laundry.service` 和 `/etc/systemd/system/laundry-reminders.service`。
两项服务均已启用开机启动，异常退出后自动重启。
订阅消息跳转版本默认 `formal`；体验版测试时可按发布说明设置 `WX_MINIPROGRAM_STATE=trial`。

## 备份与迁移

部署前完整备份应用文件（不包含旧虚拟环境）、原服务配置、Nginx 配置和旧提醒 JSON。
停止旧网页进程与旧微信提醒进程后，又保存一次最终提醒 JSON，再执行迁移。本次旧队列为空，导入 0 条。
原有 `.env` 保留，`LaundryApp.apk` 的 SHA-256 与部署前一致。

备份文件包括 `previous-site.tar.gz`、`laundry.service`、`laundry`（Nginx 配置）、`laundry_reminders-final.json` 和 `apk.sha256`。
原 `laundry.service` 在本次部署前已长期启动失败，回退时不能直接恢复该旧服务配置，需要选择可用的 Python 环境。

## 验证结果

- 本地 20 项 Flask 测试、20 项 Node 测试通过；服务器使用新版环境复跑 20 项 Flask 测试通过。
- 新版先在备用端口启动并访问真实设备服务，通过后再切换正式端口。
- 公网首页、配置接口、两栋楼设备接口和静态资源均返回 200；设备数据含正确楼栋、采集时间，非旧缓存状态。
- 微信运行时 token 获取成功；订阅模板与三个字段校验通过，未发送测试订阅消息。
- 公网浏览器读取真实数据，楼栋切换、深浅主题切换和手机宽度布局通过，无 JavaScript 错误。
- 公网日历导出返回 200，事件在结束前 5 分钟开始，`VALARM` 在事件开始时提醒，时间使用 UTC。
- SQLite 完整性检查通过；正式 Web 与提醒服务均为 active/running，复核时退出状态与重启计数为 0。

公网初次核验曾出现一次 Cloudflare 525，随后从不同网络出口复核首页与接口均返回 200；服务器本机 HTTPS 验证正常。
本次没有改动 Cloudflare 或 Nginx 的 TLS 配置。

后续每次更新小程序源码，仍需在微信开发者工具上传 `mini-program/` 对应项目，再扫码复验并按微信流程审核发布。
