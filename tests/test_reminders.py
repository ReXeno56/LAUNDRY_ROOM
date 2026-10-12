from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from app import create_app
from laundry.common import APIError, SHANGHAI, iso_time
from laundry.storage import Store


class ReminderManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'reminders.sqlite3')
        self.app = create_app({'DATABASE': self.path, 'TESTING': True, 'WX_SECRET': 'test-secret'})
        self.client = self.app.test_client()
        self.store = self.app.extensions['laundry_store']
        self.token = self.store.create_session('user')['token']
        self.headers = {'Authorization': 'Bearer ' + self.token}
        self.finish = datetime.now(SHANGHAI) + timedelta(hours=2)

    def task(self, device_id='42', building_id='27131', openid='user', status='pending', created_at=None):
        task = self.store.create_reminder(openid, building_id, device_id, "O'Reilly <洗衣机>",
                                          self.finish.timestamp(), 5, 'template')[0]
        with self.store.connection(transaction=True) as db:
            db.execute('UPDATE reminders SET status=?,created_at=? WHERE id=?',
                       (status, time.time() if created_at is None else created_at, task['id']))
        return self.store.get_reminder(task['id'], openid)

    def cancel(self, task, headers=None):
        return self.client.post('/api/wx-reminders/' + task['id'] + '/cancel',
                                headers=self.headers if headers is None else headers)

    def test_list_and_cancel_require_session_and_hide_other_owners(self):
        own = self.task()
        foreign = self.task('other', openid='other')
        self.assertEqual(self.client.get('/api/wx-reminders').status_code, 401)
        self.assertEqual(self.cancel(own, {}).status_code, 401)
        self.assertEqual(self.cancel(foreign).status_code, 404)
        self.assertEqual(self.client.post('/api/wx-reminders/missing/cancel', headers=self.headers).status_code, 404)
        result = self.client.get('/api/wx-reminders', headers=self.headers).get_json()
        self.assertEqual([r['reminderId'] for r in result['active']], [own['id']])
        self.assertNotIn('openid', result['active'][0])

    def test_list_includes_all_buildings_and_limits_recent_history(self):
        now = time.time()
        self.task('pending', created_at=now - 40 * 86400)
        self.task('retry', building_id='27136', status='retry')
        self.task('sending', status='sending')
        self.task('old', status='sent', created_at=now - 31 * 86400)
        self.task('other-owner', openid='other', status='sent')
        recent = [self.task(str(i), status='cancelled', created_at=now - i) for i in range(55)]
        result = self.client.get('/api/wx-reminders', headers=self.headers).get_json()
        self.assertEqual({r['status'] for r in result['active']}, {'pending', 'retry', 'sending'})
        self.assertEqual({r['buildingId'] for r in result['active']}, {'27131', '27136'})
        self.assertEqual([r['reminderId'] for r in result['history']], [r['id'] for r in recent[:50]])
        retry = next(r for r in result['active'] if r['status'] == 'retry')
        self.assertEqual(retry['buildingName'], '西16楼')
        self.assertEqual(retry['statusText'], '正在重试')
        self.assertTrue(retry['canCancel'])
        for key in ('remindAtISO', 'finishAt', 'createdAt'):
            self.assertTrue(retry[key].endswith('Z'))

    def test_cancel_is_idempotent_and_renewal_preserves_old_record(self):
        original = self.task()
        first = self.cancel(original)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()['status'], 'cancelled')
        self.assertFalse(first.get_json()['canCancel'])
        self.assertEqual(self.cancel(original).get_json(), first.get_json())
        renewed, created = self.store.create_reminder('user', '27131', '42', '洗衣机',
                                                       self.finish.timestamp(), 10, 'template')
        self.assertTrue(created)
        self.assertNotEqual(renewed['id'], original['id'])
        self.assertEqual(renewed['minutes'], 10)
        duplicate, created = self.store.create_reminder('user', '27131', '42', '洗衣机',
                                                        self.finish.timestamp(), 15, 'template')
        self.assertFalse(created)
        self.assertEqual(duplicate['id'], renewed['id'])
        self.assertEqual(duplicate['minutes'], 10)
        self.assertEqual(self.store.get_reminder(original['id'], 'user')['status'], 'cancelled')

    def test_only_pending_and_retry_can_cancel(self):
        for status in ('pending', 'retry', 'sending', 'sent', 'failed', 'uncertain'):
            with self.subTest(status=status):
                task = self.task(status, status=status)
                response = self.cancel(task)
                if status in ('pending', 'retry'):
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.get_json()['status'], 'cancelled')
                else:
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.get_json()['code'], 'reminder_not_cancellable')
                    self.assertEqual(self.store.get_reminder(task['id'], 'user')['status'], status)
                    if status == 'sending':
                        self.assertIn('正在发送', response.get_json()['error'])

    def test_cancel_and_claim_race_has_exactly_one_winner(self):
        for i in range(15):
            task = self.task('race-' + str(i))
            cancel_store, sender_store = Store(self.path), Store(self.path)
            barrier = threading.Barrier(2)
            now = task['remind_ts'] + 1
            def cancel():
                barrier.wait()
                try:
                    return cancel_store.cancel_reminder(task['id'], 'user')['status']
                except APIError as exc:
                    return exc.status
            def claim():
                barrier.wait()
                return sender_store.claim_due(now)
            with ThreadPoolExecutor(max_workers=2) as pool:
                cancellation, claimed = pool.submit(cancel), pool.submit(claim)
                cancellation, claimed = cancellation.result(), claimed.result()
            if cancellation == 'cancelled':
                self.assertIsNone(claimed)
                self.assertEqual(self.store.get_reminder(task['id'], 'user')['attempts'], 0)
            else:
                self.assertEqual(cancellation, 409)
                self.assertEqual(claimed['id'], task['id'])
                self.store.finish_delivery(claimed, 'sent', now=now)

    def test_existing_create_endpoint_can_renew_after_cancellation(self):
        device = {'id': '42', 'name': '洗衣机', 'state': 2, 'finishAt': self.finish.isoformat()}
        self.app.extensions['laundry_devices'] = Mock(get=Mock(return_value={
            'washers': [device], 'dryers': [], 'stale': False}))
        body = {'token': self.token, 'buildingId': '27131', 'deviceId': '42',
                'finishAt': device['finishAt'], 'minutes': 5}
        original = self.client.post('/api/wx-subscribe', json=body).get_json()
        self.client.post('/api/wx-reminders/' + original['reminderId'] + '/cancel', headers=self.headers)
        renewed = self.client.post('/api/wx-subscribe', json={**body, 'minutes': 10}).get_json()
        self.assertTrue(renewed['created'])
        self.assertEqual(renewed['minutes'], 10)
        self.assertNotEqual(renewed['reminderId'], original['reminderId'])
        single = self.client.get('/api/wx-reminders/' + original['reminderId'], headers=self.headers).get_json()
        self.assertEqual(single['status'], 'cancelled')
        self.assertEqual(single['statusText'], '已取消')

    def test_health_requires_fresh_heartbeat_and_returns_only_booleans(self):
        initial = self.client.get('/api/health')
        self.assertEqual(initial.status_code, 503)
        self.assertEqual(initial.get_json(), {'ok': False, 'checks': {'database': True, 'reminderWorker': False}})
        self.store.touch_worker()
        healthy = self.client.get('/api/health')
        self.assertEqual(healthy.status_code, 200)
        self.assertTrue(healthy.get_json()['ok'])
        now = time.time()
        self.store.touch_worker(now - 91)
        self.assertFalse(self.store.health(now)['reminderWorker'])
        self.assertEqual(self.client.get('/api/health').status_code, 503)
        self.store.touch_worker(now - 90)
        self.assertTrue(self.store.health(now)['reminderWorker'])
        with patch.object(self.store, 'connection', side_effect=sqlite3.OperationalError('secret database path')):
            failed = self.client.get('/api/health')
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.get_json()['checks'], {'database': False, 'reminderWorker': False})
        self.assertNotIn('secret', failed.get_data(as_text=True))

    def test_overdue_count_ignores_retry_waiting_and_terminal_tasks(self):
        now = time.time()
        fixtures = [('pending', -61), ('retry', -120), ('retry', 30), ('pending', -20),
                    ('sending', -100), ('sent', -100), ('cancelled', -100), ('failed', -100)]
        for i, (status, offset) in enumerate(fixtures):
            task = self.task(str(i), status=status)
            with self.store.connection(transaction=True) as db:
                db.execute('UPDATE reminders SET next_attempt=?,finish_ts=? WHERE id=?',
                           (now + offset, now - 10 if i == 0 else task['finish_ts'], task['id']))
        self.assertEqual(self.store.overdue_count(now), 2)


class ReminderSchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'old.sqlite3')
        self.store = Store(self.path)
        self.token = self.store.create_session('user')['token']
        self.task = self.store.create_reminder('user', '27131', '42', '洗衣机',
                                               time.time() + 7200, 5, 'template')[0]
        with self.store.connection(transaction=True) as db:
            db.execute('DROP INDEX reminders_active_unique')
            db.execute('DROP INDEX reminders_due')
            db.execute('DROP INDEX reminders_owner')
            db.execute('ALTER TABLE reminders RENAME TO seed_reminders')
            db.execute('''CREATE TABLE reminders (
                id TEXT PRIMARY KEY, openid TEXT NOT NULL, building_id TEXT NOT NULL,
                device_id TEXT NOT NULL, name TEXT NOT NULL, finish_ts REAL NOT NULL,
                remind_ts REAL NOT NULL, minutes INTEGER NOT NULL, template_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL, claimed_at REAL, last_error TEXT, sent_at REAL,
                created_at REAL NOT NULL, UNIQUE(openid,building_id,device_id,finish_ts)
            )''')
            db.execute('INSERT INTO reminders SELECT * FROM seed_reminders')
            db.execute('DROP TABLE seed_reminders')
            db.execute('DROP TABLE worker_heartbeat')
            db.execute('PRAGMA user_version=0')

    def test_atomic_migration_preserves_sessions_records_and_is_repeatable(self):
        migrated = Store(self.path)
        with migrated.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
        self.assertEqual(migrated.get_openid(self.token), 'user')
        self.assertEqual(migrated.get_reminder(self.task['id'], 'user'), self.task)
        migrated.cancel_reminder(self.task['id'], 'user')
        renewed, created = migrated.create_reminder('user', '27131', '42', '洗衣机',
                                                     self.task['finish_ts'], 10, 'template')
        self.assertTrue(created)
        repeat = Store(self.path)
        self.assertEqual(repeat.get_reminder(renewed['id'], 'user'), renewed)
        self.assertEqual(repeat.get_reminder(self.task['id'], 'user')['status'], 'cancelled')
        self.assertEqual(repeat.get_openid(self.token), 'user')
        with repeat.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            db.execute('PRAGMA user_version=7')
        newer = Store(self.path)
        with newer.connection() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 7)

    def test_failed_schema_change_rolls_back_and_can_be_retried(self):
        real_connect = sqlite3.connect
        class InterruptedMigration(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql.startswith('ALTER TABLE reminders_migration'):
                    raise sqlite3.OperationalError('simulated interruption')
                return super().execute(sql, *args, **kwargs)
        def connect(*args, **kwargs):
            return real_connect(*args, factory=InterruptedMigration, **kwargs)
        with patch('laundry.storage.sqlite3.connect', side_effect=connect):
            with self.assertRaises(sqlite3.OperationalError):
                Store(self.path)
        with real_connect(self.path) as db:
            self.assertEqual(db.execute('SELECT id FROM reminders').fetchone()[0], self.task['id'])
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='reminders_migration'").fetchone())
        recovered = Store(self.path)
        self.assertEqual(recovered.get_openid(self.token), 'user')
        self.assertEqual(recovered.get_reminder(self.task['id'], 'user'), self.task)

    def test_concurrent_startup_runs_one_safe_migration(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            stores = list(pool.map(lambda _: Store(self.path), range(6)))
        for store in stores:
            self.assertEqual(store.get_openid(self.token), 'user')
            self.assertEqual(store.get_reminder(self.task['id'], 'user'), self.task)


if __name__ == '__main__':
    unittest.main()
