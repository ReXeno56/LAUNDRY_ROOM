from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import requests

from app import create_app
from laundry.common import APIError, SHANGHAI, iso_time, parse_finish
from laundry.devices import DeviceService, fetch_devices, normalize_device
from laundry.storage import Store
from laundry.wechat import DeliveryError, WeChat
from laundry.worker import dispatch_once


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = self.temp.name + '/laundry.sqlite3'
        self.config = {'DATABASE': self.path, 'TESTING': True, 'WX_SECRET': 'test-secret', 'WX_APPID': 'test-app',
                       'WX_TEMPLATE_ID': 'test-template'}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.store = self.app.extensions['laundry_store']
        self.finish = datetime.now(SHANGHAI) + timedelta(hours=2)
        self.device = normalize_device({'id': 42, 'name': "O'Reilly <洗衣机>", 'state': 2,
                                       'finishTime': self.finish.isoformat()})
        self.snapshot = {'buildingId': '27131', 'washers': [self.device], 'dryers': [],
                         'fetchedAt': iso_time(time.time()), 'stale': False}
        self.app.extensions['laundry_devices'] = Mock(get=Mock(return_value=self.snapshot))
        self.token = self.store.create_session('user')['token']

    def body(self, **changes):
        return {'token': self.token, 'buildingId': '27131', 'deviceId': '42',
                'finishAt': self.device['finishAt'], 'minutes': 5, **changes}

    def task(self, device_id='42'):
        return self.store.create_reminder('user', '27131', device_id, '洗衣机',
                                          self.finish.timestamp(), 30, 'test-template')[0]

    def row(self, task):
        return self.store.get_reminder(task['id'], 'user')

    def test_public_config_and_web_assets(self):
        config = self.client.get('/api/config').get_json()
        self.assertEqual(config['templateId'], 'test-template')
        self.assertNotIn('test-secret', json.dumps(config))
        for route in ['/', '/static/main.js', '/static/native-reminders.js', '/static/laundry.css']:
            with self.client.get(route) as response:
                self.assertEqual(response.status_code, 200)

    def test_input_validation_returns_json_400(self):
        for value in ['abc', None, [], {}, True, 2.5, -5, 0, 31]:
            with self.subTest(minutes=value):
                response = self.client.post('/api/wx-subscribe', json=self.body(minutes=value))
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()['ok'])
        for body in [[], None, 'bad']:
            response = self.client.post('/api/wx-subscribe', data=json.dumps(body), content_type='application/json')
            self.assertEqual(response.status_code, 400)
        for value in [None, 123, 'broken']:
            self.assertEqual(self.client.post('/api/wx-subscribe', json=self.body(finishAt=value)).status_code, 400)
        self.assertEqual(self.client.get('/api/devices?id=unknown').status_code, 400)
        for query in [{}, {'finishTime': 'broken'}, {'finishTime': self.finish.isoformat(), 'minutes': 'abc'}]:
            response = self.client.get('/api/reminder', query_string=query)
            self.assertEqual(response.status_code, 400)
            self.assertTrue(response.is_json)

    def test_login_and_session_survive_app_restart(self):
        self.app.extensions['laundry_wechat'].login = Mock(return_value='user')
        result = self.client.get('/api/wx-login?code=fake').get_json()
        other = create_app(self.config)
        other.extensions['laundry_devices'] = Mock(get=Mock(return_value=self.snapshot))
        response = other.test_client().post('/api/wx-subscribe', json=self.body(token=result['token']))
        self.assertEqual(response.status_code, 200)
        self.assertIn('expiresAt', result)
        with self.store.connection() as db:
            self.assertNotEqual(db.execute('SELECT token_hash FROM sessions LIMIT 1').fetchone()[0], self.token)

    def test_expired_session_and_status_authorization(self):
        response = self.client.post('/api/wx-subscribe', json=self.body())
        rid = response.get_json()['reminderId']
        self.assertEqual(self.client.get('/api/wx-reminders/' + rid).status_code, 401)
        second = self.store.create_session('other')['token']
        self.assertEqual(self.client.get('/api/wx-reminders/' + rid,
                                         headers={'Authorization': 'Bearer ' + second}).status_code, 404)
        with self.store.connection() as db:
            db.execute('UPDATE sessions SET expires_at=0')
        self.assertEqual(self.client.post('/api/wx-subscribe', json=self.body()).status_code, 401)

    def test_reminder_uses_server_identity_and_deduplicates(self):
        first = self.client.post('/api/wx-subscribe', json=self.body(name='client supplied name')).get_json()
        second = self.client.post('/api/wx-subscribe', json=self.body(minutes=10)).get_json()
        self.assertTrue(first['created'])
        self.assertFalse(second['created'])
        self.assertEqual(first['reminderId'], second['reminderId'])
        self.assertEqual(second['minutes'], 5)
        self.assertEqual(self.store.get_reminder(first['reminderId'], 'user')['name'], self.device['name'])

    def test_device_and_template_changes_are_rejected(self):
        self.assertEqual(self.client.post('/api/wx-subscribe', json=self.body(deviceId='foreign')).status_code, 400)
        self.assertEqual(self.client.post('/api/wx-subscribe', json=self.body(templateId='old')).status_code, 409)
        self.assertEqual(self.client.post('/api/wx-subscribe', json=self.body(
            finishAt=iso_time(self.finish.timestamp() + 60))).status_code, 409)
        self.snapshot['stale'] = True
        self.assertEqual(self.client.post('/api/wx-subscribe', json=self.body()).status_code, 503)
        self.snapshot['stale'] = False
        self.device['state'] = 1
        self.assertEqual(self.client.post('/api/wx-subscribe', json=self.body()).status_code, 409)

    def test_upstream_errors_are_json_502_504(self):
        for error, status in [(requests.Timeout(), 504), (requests.ConnectionError(), 502)]:
            with patch('laundry.common.http_session', return_value=Mock(request=Mock(side_effect=error))):
                response = self.client.get('/api/wx-login?code=fake')
                self.assertEqual(response.status_code, status)
                self.assertFalse(response.get_json()['ok'])

    def test_calendar_has_alarm_utc_and_utf8_folding(self):
        response = self.client.get('/api/reminder', query_string={
            'name': '洗衣机' * 40 + '\r\nBEGIN:FAKE', 'finishTime': '2030-10-07 12:00:00', 'minutes': 5})
        self.assertEqual(response.status_code, 200)
        ics = response.get_data(as_text=True)
        self.assertIn('DTSTART:20301007T035500Z\r\n', ics)
        self.assertIn('DTEND:20301007T040000Z\r\n', ics)
        self.assertIn('BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:PT0S', ics)
        self.assertNotIn('\r\nBEGIN:FAKE', ics)
        self.assertTrue(next(x for x in ics.splitlines() if x.startswith('DTSTAMP:')).endswith('Z'))
        self.assertTrue(all(len(x.encode('utf-8')) <= 75 for x in ics.split('\r\n')))
        past = self.client.get('/api/reminder', query_string={'finishTime': '2000-01-01 12:00:00'})
        self.assertEqual(past.status_code, 400)

    def test_normalization_handles_iso_malformed_times_and_string_states(self):
        device = normalize_device({'id': 1, 'name': 'test', 'state': '2', 'finishTime': '2030-10-07T12:00:00+08:00'})
        self.assertEqual(device['finishTime'], '2030-10-07 12:00:00')
        self.assertEqual(parse_finish(device['finishAt']).hour, 12)
        self.assertEqual(device['state'], 2)
        for value in ['bad', None, 42]:
            self.assertIsNone(normalize_device({'id': 1, 'finishTime': value})['finishAt'])

    def test_pagination_and_repeated_page_detection(self):
        pages = [[{'id': i, 'state': 1} for i in range(20)], [{'id': i, 'state': 1} for i in range(20, 25)]]
        replies = [{'code': 0, 'data': {'items': items, 'total': 25}} for items in pages]
        with patch('laundry.devices.request_json', side_effect=replies) as fetch:
            result = fetch_devices('27131')
            self.assertEqual(len(result), 25)
            self.assertEqual(fetch.call_args_list[1].kwargs['json']['page'], 2)
        repeated = {'code': 0, 'data': {'items': pages[0]}}
        with patch('laundry.devices.request_json', return_value=repeated):
            with self.assertRaises(APIError): fetch_devices('27131')

    def test_cache_coalesces_across_store_instances_and_preserves_timestamp(self):
        calls, lock = [], threading.Lock()
        def fetcher(bid, category, deadline):
            with lock: calls.append(category)
            time.sleep(0.03)
            return [self.device] if category == '00' else []
        services = [DeviceService(Store(self.path), fetcher) for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda service: service.get('27131'), services))
        self.assertEqual(sorted(calls), ['00', '02'])
        self.assertEqual(len({r['fetchedAt'] for r in responses}), 1)
        self.assertFalse(any(r['stale'] for r in responses))

    def test_cache_failure_preserves_previous_snapshot(self):
        service = DeviceService(self.store, lambda *args: [self.device])
        first = service.get('27131')
        with self.store.connection() as db: db.execute('UPDATE device_cache SET expires_at=0')
        def failing(*args): raise APIError('failed', 502, 'upstream_error')
        service.fetcher = failing
        result = service.get('27131')
        self.assertTrue(result['stale'])
        self.assertEqual(first['fetchedAt'], result['fetchedAt'])
        self.assertEqual(first['washers'], result['washers'])

    def test_concurrent_reminder_creation(self):
        stores = [Store(self.path) for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda store: store.create_reminder('user','27131','42','test',
                self.finish.timestamp(),5,'template'), stores))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({task['id'] for task, _ in results}), 1)

    def test_delivery_success_and_atomic_claim(self):
        task = self.task(); now = task['remind_ts'] + 1
        claimed = self.store.claim_due(now)
        self.assertEqual(claimed['id'], task['id'])
        self.assertIsNone(Store(self.path).claim_due(now))
        self.store.finish_delivery(claimed, 'sent', now=now)
        self.assertEqual(self.row(task)['status'], 'sent')
        self.assertIsNotNone(self.row(task)['sent_at'])

    def test_delivery_retry_is_bounded_and_failure_is_retained(self):
        task = self.task(); now = task['remind_ts'] + 1
        sender = Mock(send=Mock(side_effect=DeliveryError('retry', 'system_busy')))
        for attempt in range(3):
            self.assertTrue(dispatch_once(self.store, sender, now))
            row = self.row(task)
            self.assertEqual(row['attempts'], attempt + 1)
            now = row['next_attempt']
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['last_error'], 'system_busy')
        self.assertIsNone(row['sent_at'])

    def test_permanent_failure_does_not_drop_other_due_tasks(self):
        first, second = self.task('a'), self.task('b')
        now = max(first['remind_ts'], second['remind_ts']) + 1
        sender = Mock(send=Mock(side_effect=[DeliveryError('failed', 'wechat_error:43101'), None]))
        dispatch_once(self.store, sender, now)
        dispatch_once(self.store, sender, now)
        self.assertEqual(self.row(first)['status'], 'failed')
        self.assertEqual(self.row(second)['status'], 'sent')

    def test_uncertain_and_interrupted_delivery_are_not_replayed(self):
        task = self.task(); now = task['remind_ts'] + 1
        sender = Mock(send=Mock(side_effect=DeliveryError('uncertain', 'read_timeout')))
        dispatch_once(self.store, sender, now)
        self.assertEqual(self.row(task)['status'], 'uncertain')
        self.assertFalse(dispatch_once(self.store, sender, now + 120))
        other = self.task('b'); self.store.claim_due(now)
        self.assertIsNone(self.store.claim_due(now + 61))
        self.assertEqual(self.row(other)['status'], 'uncertain')

    def test_wechat_send_classifies_results_and_refreshes_rejected_token(self):
        task = self.task()
        sender = WeChat(self.config)
        sender.get_token = Mock(return_value='fake-token')
        for error, category in [(requests.ConnectTimeout(), 'retry'), (requests.ReadTimeout(), 'uncertain')]:
            with patch('laundry.wechat.http_session', return_value=Mock(post=Mock(side_effect=error))):
                with self.assertRaises(DeliveryError) as caught: sender.send(task)
                self.assertEqual(caught.exception.category, category)
        for code, category in [(43101, 'failed'), (-1, 'retry')]:
            response = Mock(json=Mock(return_value={'errcode': code}))
            with patch('laundry.wechat.http_session', return_value=Mock(post=Mock(return_value=response))):
                with self.assertRaises(DeliveryError) as caught: sender.send(task)
                self.assertEqual(caught.exception.category, category)
        sender.get_token.reset_mock()
        replies = [Mock(json=Mock(return_value={'errcode': 40014})), Mock(json=Mock(return_value={'errcode': 0}))]
        with patch('laundry.wechat.http_session', return_value=Mock(post=Mock(side_effect=replies))):
            sender.send(task)
        self.assertEqual([c.kwargs for c in sender.get_token.call_args_list], [{'force': False}, {'force': True}])

    def test_wechat_message_targets_selected_release_and_building(self):
        sender = WeChat({**self.config, 'WX_MINIPROGRAM_STATE': 'trial'})
        sender.get_token = Mock(return_value='fake-token')
        session = Mock(post=Mock(return_value=Mock(json=Mock(return_value={'errcode': 0}))))
        with patch('laundry.wechat.http_session', return_value=session):
            sender.send(self.task())
        body = session.post.call_args.kwargs['json']
        self.assertEqual(body['miniprogram_state'], 'trial')
        self.assertEqual(body['page'], 'pages/index/index?id=27131')
        self.assertEqual(WeChat(self.config).miniprogram_state, 'formal')

    def test_legacy_migration_is_idempotent(self):
        path = Path(self.temp.name) / 'legacy.json'
        path.write_text(json.dumps({'user': [{'name': 'test', 'remind_ts': self.finish.timestamp() - 300,
                                            'hour': 12, 'minute': 0, 'early_min': 5}]}))
        self.assertEqual(self.store.import_legacy(path, 'template'), 1)
        self.assertEqual(self.store.import_legacy(path, 'template'), 0)


if __name__ == '__main__': unittest.main()
