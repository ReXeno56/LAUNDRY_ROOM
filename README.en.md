# SJTU Laundry Status

[简体中文](README.md) | **English**

A Flask web interface and a native WeChat Mini Program share device lookup, WeChat login, and subscription reminder APIs. This README covers the web and WeChat Mini Program components.

## Installation and Startup

Python 3.10+ is required. Frontend tests also require Node.js 18+.

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

For a first deployment, create `.env` from `.env.example` and set `WX_SECRET`. An existing `.env` can continue to be used.
Building information and default WeChat settings are stored in `config.json`; the `WX_APPID` and `WX_TEMPLATE_ID` environment variables override the defaults.
The WeChat Secret is used only on the server and is never exposed through the public configuration API.
Message cards open the production Mini Program by default. For trial testing, set `WX_MINIPROGRAM_STATE` on the server to `trial`; after publishing the production version, change it back to `formal` and restart the reminder worker.

Start the local web interface:

```bash
venv/bin/python app.py
```

**Start the reminder worker in a separate terminal. Without it, reminders are saved but never dispatched:**

```bash
venv/bin/python -m laundry.worker
```

In production, run the web application with a WSGI server and use a reverse proxy to provide HTTPS:

```bash
venv/bin/gunicorn --workers 2 --threads 4 --timeout 30 --bind 127.0.0.1:5000 app:app
```

The web application and reminder worker must use the same `LAUNDRY_DB`, which defaults to `instance/laundry.sqlite3` in the project directory.
This database holds persistent data and must be retained across deployments; container deployments require a persistent volume. Configure a separate service for each process with automatic restarts.
Importing the web module does not start a sending thread. The reminder worker checks for due tasks every 5 seconds.

## WeChat Mini Program

Import the project root into WeChat Developer Tools (`miniprogramRoot: "mini-program/"` is already configured), or import `mini-program/` directly.
Set the HTTPS API domain in `mini-program/app.js`; the current value is `https://sjtulaundry.xyz`.
Building information and the subscription template ID are loaded from `/api/config`, so no additional Mini Program constants need to be changed.
The AppID in the WeChat Official Accounts Platform must match the server configuration. The existing template must include `thing19`, `time26`, and `thing35`.
Before publishing, configure the allowed HTTPS request domain and test on a physical device. Domain validation is enabled by default; temporarily disabling it in Developer Tools does not replace configuration in the WeChat platform.

**Deploy the API and web interface to the server, and upload the Mini Program source to WeChat through WeChat Developer Tools.** Both steps are required.
See the [WeChat deployment guide (in Chinese)](docs/wechat-deployment.md) for upload directories, domain configuration, the release process, and checks of the current live version.

Subscription permission is requested only when the user taps the confirmation button, and a reminder is submitted only after an `accept` response. If the login session expires, the Mini Program logs in again and retries once without requesting permission again.
Delivery depends on WeChat's response and the user's authorization. Local tests use mocked APIs and do not send real messages to users.

## Interface and Themes

West 16 combines device status with the [reservation spreadsheet](https://docs.qq.com/sheet/DT1FqTlV4SWVqWWhs?tab=ss_9ipbgd): it distinguishes devices that are currently available, idle but reserved, or awaiting reservation confirmation, and shows the next reservation time slot. See the [reservation lookup guide (in Chinese)](docs/reservations.md) for reading, mapping, and caching rules.

The web interface and Mini Program share light and dark color palettes and follow the system theme by default. Users can select a light or dark theme from the header menu; the choice is saved on the current device.
Both interfaces remember the last selected building and a valid reminder lead time in minutes (1–30, default 5). They remain usable when local storage is unavailable.
Available-device counts are prominent, and available washers and dryers are listed separately. Device cards show the status, estimated finish time, and reminder controls.
The web interface uses two columns on wide screens and one column on phones. Both interfaces support manual refresh, while background refresh preserves reminders being edited.
Counts display `—` while loading. If a request fails, existing data is retained and labeled as the last collected data, so an unloaded state is not shown as zero available devices.

Devices are ordered with idle devices first, followed by devices in use ordered by estimated finish time, then devices with abnormal status. The idle-only filter retains the building-wide summary counts and any input being edited.
The Mini Program uses `darkmode`, `theme.json`, and styles for the active theme; the navigation bar and pull-to-refresh background switch with the theme as well.
See [DESIGN.md (in Chinese)](DESIGN.md) for style and interaction guidelines.

## Data and Reminder Behavior

- The two device types are queried in parallel with automatic pagination. Each building is cached for 10 seconds. SQLite coordinates concurrent queries across processes; identical collected results share the same `fetchedAt`.
- `finishTime` retains the Shanghai time format `YYYY-MM-DD HH:MM:SS`; the additional `finishAt` and `fetchedAt` fields use UTC ISO 8601. Unparseable finish times are set to empty, and reminders for those devices are disabled.
- When an external service fails, cached device data may be returned with `stale: true` and a warning. If no cached data is available, the API returns JSON with HTTP 502/504.
- Application sessions store SHA-256 digests of tokens, remain valid for 7 days, and are shared across restarts and multiple workers.
- Repeated confirmations for the same user, building, device, and finish time reuse the existing reminder ID and lead time without creating a new task. After cancellation, users can set a new reminder with a new ID and lead time. The lead time must be an integer from 1 to 30 minutes.
- Task states are `pending`, `sending`, `retry`, `sent`, `failed`, `uncertain`, and `cancelled`. A task is marked `sent` only when WeChat explicitly returns `errcode=0`; this does not mean the user has read the message.
- Explicitly retryable rejections and connection timeouts allow at most 3 attempts, with retry delays of 10 and 30 seconds. Retries stop when the device's finish time is reached. Permanent rejections remain `failed`.
- Read timeouts, HTTP errors during sending, and responses whose outcome cannot be confirmed are saved as `uncertain`. Dispatch interrupted for more than 60 seconds is also marked `uncertain`. These tasks are not resent automatically, to avoid duplicate notifications.
- The web interface downloads a calendar file containing `VALARM`. Users must import it and allow calendar notifications; actual notifications depend on the calendar client.
- The Mini Program's “My Reminders” view shows active reminders from all buildings and up to 50 history entries from the last 30 days. Only pending tasks and tasks awaiting retry can be cancelled. To change the lead time, cancel the reminder and set it again.
- On first load, an existing database is migrated within a transaction, preserving sessions and all records. When deploying, stop both the web application and reminder worker before running `python -m laundry.maintenance migrate`.

## Local Backups and Operational Checks

```bash
venv/bin/python -m laundry.maintenance backup
venv/bin/python -m laundry.maintenance verify /path/to/backup.tar.gz
venv/bin/python -m laundry.maintenance health
```

An online backup and restore verification run daily at 03:00 Beijing time (UTC+8), with the latest 14 backups retained separately. Every minute, checks cover the public health endpoint, both services, the dispatch heartbeat, and the backlog.
System service templates are in `deploy/systemd/`; replace the virtual environment paths with the actual server paths during installation. See the [operations guide (in Chinese)](docs/operations.md) for management and recovery instructions.

### Migrating from the Legacy JSON Format

Stop the legacy service first, ensure its background reminder thread is no longer running, and retain a backup of the old JSON file. Then run:

```bash
venv/bin/python -m laundry.worker --import-legacy /tmp/laundry_reminders.json
```

Import is transactional and can be repeated; it does not delete the source file. Legacy records have no device ID and are preserved with separate legacy identifiers.
Legacy in-memory sessions cannot be migrated. Clients recover by logging in again after receiving HTTP 401. The new Mini Program must provide building and device identifiers when submitting reminders; publish the updated Mini Program alongside the server deployment.

## API

| Endpoint | Details |
| --- | --- |
| `GET /api/config` | `buildings`, `templateId`, `appId`, and the allowed minute range |
| `GET /api/devices?id=27131` | `washers`, `dryers`, `buildingId`, `fetchedAt`, `stale`, `warning` |
| `GET /api/wx-login?code=...` | `token`, `openid`, `expiresAt` |
| `POST /api/wx-subscribe` | JSON: `token`, `buildingId`, `deviceId`, `finishAt` (or `finishTime`), `minutes`, and optional `templateId`; returns `ok`, `created`, `reminderId`, `status`, `minutes`, `remindAt`, `remindAtISO`, `finishAt` |
| `GET /api/wx-reminders/<id>` | Uses `Authorization: Bearer <token>` to query the user's own reminder status and `lastError` |
| `GET /api/wx-reminders` | Bearer authentication; returns the user's `active` reminders across all buildings and up to 50 `history` entries from the last 30 days |
| `POST /api/wx-reminders/<id>/cancel` | Bearer authentication; cancels pending tasks or tasks awaiting retry. Repeated cancellation succeeds. A task that is sending or has finished returns 409; another user's task or a nonexistent task returns 404 |
| `GET /api/health` | Returns `ok` and booleans for the database and reminder worker heartbeat; HTTP 200 when healthy, 503 otherwise |
| `GET /api/reminder` | Calendar download; parameters: `name`, `finishAt` (or `finishTime`), `minutes`, and optional `buildingId` |

Invalid input returns JSON with HTTP 400, expired sessions return 401, device or template changes return 409, external errors return 502/504, and temporarily unavailable storage returns 503.
Errors include `ok: false`, `error`, and `code`. Existing device-list fields and the calendar endpoint are retained.

## Tests

```bash
venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
npm --prefix tests ci --ignore-scripts
npm --prefix tests test
```

Backend tests cover SQLite persistence, concurrent deduplication, shared caching, pagination, input validation, time zones, calendar alarms, and dispatch failure classification.
Frontend tests mock WeChat APIs with Node VM and check actual web DOM operations with jsdom. They cover permission rejection, 401 retries, out-of-order responses, lifecycle behavior, input preservation, and safe rendering.
