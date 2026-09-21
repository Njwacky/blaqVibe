"""PUBLISH IDEMPOTENCY — one submission, one feed row.

The "double in feed" bug: publishing the same form twice (double-tap on a
slow mobile connection, an XHR retry after a lost response, a back-button
resubmit) used to create two identical published projects — "My App" and
"My App-1" both sitting on the feed. The form now carries a per-render
idempotency token, and the publish path resolves a repeat POST of the same
body to the project the first submit created.
"""
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from gallery.models import AppProject, Category

User = get_user_model()


def _publish_payload(token=None, title='Dupe Check'):
    data = {
        'title': title,
        'short_description': 'An app built to prove publishing does not double.',
        'build_method': 'human',
        'html_code': '<main><h1>Dupe check</h1></main>',
        'css_code': '',
        'js_code': '',
        'readme': '# Dupe Check\n\nA readme long enough to pass the minimum-length gate without thinking about it.',
        'tech_stack': 'HTML',
        'star_cost': '0',
        'price_zar': '0',
    }
    if token:
        data['publish_token'] = token
    return data


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class PublishIdempotencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dupey', password='x12345678')
        self.user.set_password('x12345678')
        self.user.save()
        Category.objects.get_or_create(slug='tools', defaults={'name': 'Tools'})
        self.client.login(username='dupey', password='x12345678')

    def _post(self, url, payload):
        return self.client.post(url, payload)

    def test_same_token_twice_creates_one_project(self):
        """Double-POST with the same token → one row, second lands on the SAME success page."""
        token = 'a' * 32
        r1 = self._post('/publish/', _publish_payload(token))
        self.assertEqual(r1.status_code, 302)
        slug1 = r1.url.rstrip('/').rsplit('/', 1)[-1]
        r2 = self._post('/publish/', _publish_payload(token))
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(r2.url, r1.url, 'retry must resolve to the original project, not a new slug')
        self.assertEqual(AppProject.objects.filter(owner=self.user, title='Dupe Check').count(), 1)
        self.assertEqual(AppProject.objects.get(owner=self.user).publish_token, token)

    def test_studio_double_post_same_token_creates_one_project(self):
        """The studio drawer posts through the same path — same guarantee."""
        token = 'b' * 32
        r1 = self._post('/studio/', _publish_payload(token))
        self.assertEqual(r1.status_code, 302)
        r2 = self._post('/studio/', _publish_payload(token))
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(r2.url, r1.url)
        self.assertEqual(AppProject.objects.filter(owner=self.user).count(), 1)

    def test_double_post_without_token_is_caught_by_the_short_window(self):
        """No token at all (JS off): identical title within 2 minutes → no duplicate."""
        r1 = self._post('/publish/', _publish_payload())
        self.assertEqual(r1.status_code, 302)
        r2 = self._post('/publish/', _publish_payload())
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(r2.url, r1.url)
        self.assertEqual(AppProject.objects.filter(owner=self.user).count(), 1)

    def test_same_title_after_the_window_is_a_deliberate_publish(self):
        """A republish with the same title AFTER the twin window is honoured, not swallowed."""
        from django.utils import timezone
        from datetime import timedelta
        r1 = self._post('/publish/', _publish_payload())
        self.assertEqual(r1.status_code, 302)
        AppProject.objects.update(created_at=timezone.now() - timedelta(minutes=5))
        r2 = self._post('/publish/', _publish_payload())
        self.assertEqual(r2.status_code, 302)
        self.assertNotEqual(r2.url, r1.url, 'a deliberate re-publish after 5 minutes gets its own row')
        self.assertEqual(AppProject.objects.filter(owner=self.user).count(), 2)

    def test_a_fresh_form_renders_a_fresh_token(self):
        """Every unbound render carries a usable idempotency token in the HTML."""
        r1 = self.client.get('/publish/')
        r2 = self.client.get('/publish/')
        import re
        def token(resp):
            m = re.search(rb'name="publish_token"[^>]*value="([0-9a-f]+)"', resp.content)
            return m.group(1) if m else None
        t1, t2 = token(r1), token(r2)
        self.assertTrue(t1 and t2, 'rendered forms must embed a publish_token')
        self.assertNotEqual(t1, t2, 'two renders are two different intended submissions')
        # A token from render 1 still publishes fine.
        r = self._post('/publish/', _publish_payload(token=t1.decode()))
        self.assertEqual(r.status_code, 302)

    def test_malformed_token_is_ignored_not_stored(self):
        r = self._post('/publish/', _publish_payload(token='../etc/passwd!!'))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(AppProject.objects.get(owner=self.user).publish_token, '')
