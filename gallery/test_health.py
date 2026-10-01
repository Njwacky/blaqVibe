"""Tests for the ops probes: /healthz (liveness) and /readyz (readiness)."""
import json
from unittest.mock import patch


from django.test import TestCase, override_settings

from users.models import SiteSettings

@override_settings(RATELIMIT_ENABLE=False)
class HealthProbeTests(TestCase):

    def test_healthz_is_always_200_and_no_store(self):
        response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['Content-Type'], 'application/json')
        body = json.loads(response.content)
        self.assertEqual(body['status'], 'ok')
        self.assertEqual(body['probe'], 'liveness')

    def test_readyz_ok_when_db_database_answers(self):
        response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertEqual(body['status'], 'ok')
        self.assertTrue(body['checks']['database']['ok'])

    def test_readyz_503_and_ok_false_when_db_is_down(self):
        with patch('gallery.health.connection.cursor', side_effect=Exception('db gone')):
            response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 503)
        body = json.loads(response.content)
        self.assertEqual(body['status'], 'unavailable')
        self.assertFalse(body['checks']['database']['ok'])

    @override_settings(SCAN_QUEUE_BACKEND='celery', CELERY_TASK_ALWAYS_EAGER=False,
                       CELERY_BROKER_URL='redis://127.0.0.1:1/0')
    def test_readyz_reports_queue_down_without_failing_readiness(self):
        with patch('gallery.health._ping_redis', side_effect=Exception('redis down')) as ping:
            response = self.client.get('/readyz')
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.content)
        self.assertFalse(body['checks']['queue']['ok'])
        ping.assert_called_once_with('redis://127.0.0.1:1/0')

    def test_healthz_survives_maintenance_mode(self):
        SiteSettings.get().save()  # ensure row exists
        settings = SiteSettings.get()
        settings.maintenance = True
        settings.save()
        try:
            self.assertEqual(self.client.get('/healthz').status_code, 200)
            self.assertEqual(self.client.get('/readyz').status_code, 200)
            # But the actual site is walled off with 503.
            self.assertEqual(self.client.get('/').status_code, 503)
        finally:
            settings.maintenance = False
            settings.save()
