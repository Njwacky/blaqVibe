"""Phase 5 — precise ship-readiness, never a security guarantee."""
from django.test import TestCase, override_settings

from gallery.ship_readiness import ship_readiness, tests_detected, vuln_count
from gallery.tests import make_category, make_project, make_user


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class ShipReadinessTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('shipowner')

    def test_detects_test_files_and_vuln_count(self):
        p = make_project(
            self.owner, self.cat, title='Ready Build',
            file_tree={'app.py': {}, 'tests': {'test_app.py': {}}, 'package.json': {}},
            scan_report={'clamav': 'clean', 'secrets': [], 'npm': ['lodash', 'minimist'], 'pip': ['oldpkg']},
            trust='scanned',
        )
        self.assertTrue(any('test_app' in path for path in tests_detected(p)))
        self.assertEqual(vuln_count(p), 3)
        ready = ship_readiness(p)
        self.assertTrue(ready['scan_completed'])
        self.assertEqual(ready['tests_detected'], 1)
        self.assertEqual(ready['vuln_count'], 3)
        self.assertIn('does not guarantee', ready['disclaimer'])

    def test_detail_renders_precise_checks_not_a_guarantee(self):
        p = make_project(
            self.owner, self.cat, title='Honest Scan',
            file_tree={'src': {'main.py': {}}, 'tests': {'test_main.py': {}}},
            scan_report={'clamav': 'clean', 'secrets': [], 'dep_audit': {'ran': True, 'reason': 'ok'}},
            trust='verified',
        )
        response = self.client.get(p.get_absolute_url())
        self.assertContains(response, 'SHIP READINESS')
        self.assertContains(response, 'Tests detected')
        self.assertContains(response, 'does not guarantee this app is secure')
        self.assertNotContains(response, 'BlaqVibes guarantees this is secure')

    def test_trust_legend_refuses_guarantee(self):
        response = self.client.get('/trust/')
        self.assertContains(response, 'does not guarantee this app is secure')
