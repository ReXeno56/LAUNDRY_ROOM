# 备份、健康检查与恢复

服务器 Web、微信派发进程和维护脚本必须使用同一个 `LAUNDRY_DB`。默认会话及提醒数据库为 `instance/laundry.sqlite3`。

## 每日备份

`python -m laundry.maintenance backup` 使用 SQLite 在线备份 API 生成一致快照，关闭快照连接后归档，避免遗漏 WAL 中的新记录。
归档包含数据库、存在的 `.env`、`config.json`、`release.json` 与 SHA-256 清单。
每份归档发布前在私有临时目录实际恢复并检查校验和、数据库完整性及会话/提醒表。
备份目录权限为700，归档权限为600；失败时不发布半成品，也不清理既有备份。

默认备份目录是项目同级的 `laundry-backups/daily`，可设置 `LAUNDRY_BACKUP_DIR`。
只保留该目录中本工具格式的最近14份归档，不清理其他手工备份。
本机备份适用于数据误操作和版本回退；备份文件仍位于同一台服务器。

```bash
python -m laundry.maintenance backup
python -m laundry.maintenance verify /root/laundry-backups/daily/backup-实际文件名.tar.gz
```

恢复校验命令只使用临时目录，不替换运行中的数据库。结果输出会话/提醒数量与模式版本，不输出用户数据或凭据。

## 定时器与日志

`deploy/systemd/` 提供备份、健康检查的服务及定时器模板。安装时把模板的 `/root/laundry/venv/bin/python` 替换为实际虚拟环境解释器路径。
备份时间为每天北京时间03:00；健康检查每分钟运行。

```bash
systemctl enable --now laundry-backup.timer laundry-health.timer
systemctl list-timers laundry-backup.timer laundry-health.timer --all
systemctl start laundry-backup.service
journalctl -u laundry-backup.service -u laundry-health.service -n 50 --no-pager
```

手动运行时也使用对应虚拟环境：

```bash
python -m laundry.maintenance health
```

检查包括公网 `/api/health`、两个 systemd 服务、数据库可用性、90秒内的派发心跳和逾期超过60秒的待处理任务。
检查失败返回非零并由 systemd 写入日志。历史发送失败记录通过小程序提醒列表查看，不把历史失败当作当前服务停止。
公网健康接口只提供布尔状态，异常返回503；派发进程每轮（空闲时约5秒）更新心跳。

## 数据迁移与回退

更新前保存代码、配置和一致数据库备份；停止 Web 与派发进程后执行：

```bash
python -m laundry.maintenance migrate
```

迁移在单一事务中移除原提醒表的全表唯一约束，建立排除 `cancelled` 的部分唯一索引，保留会话与所有记录。
重复执行安全，当前模式版本为1。Web 初始化也能完成同一迁移。

实际恢复生产数据库前，停止两个服务及维护定时器，先备份当前数据，再校验拟恢复归档。
选择匹配的代码版本，将完整快照恢复到数据库位置，关闭旧连接后清理对应旧 WAL/SHM；恢复 `.env` 和配置时保留受限权限。
启动服务与定时器后核对 `/api/health`、会话和提醒记录。恢复旧备份会回到备份时刻，应保留替换前的数据副本。
