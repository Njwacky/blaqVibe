"""SiteSettings — the singleton operator toggles and their cached copy.

`gallery/performance` caches the SiteSettings row (perf:site_settings:v1,
120 s) because the context processor reads it on every request. The row is
edited at runtime — the /settings/ toggle API and the settings form — so a
save has to invalidate the cached copy: otherwise a toggle reports success
while every page keeps serving the old flags until the TTL expires.
"""
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from gallery.performance import SITE_SETTINGS_CACHE_KEY
from gallery.tests import make_user

from .models import SiteSettings


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-site-settings')
class SiteSettingsCacheInvalidationTests(TestCase):
    def test_save_invalidates_the_cached_copy(self):
        cache.set(SITE_SETTINGS_CACHE_KEY, {'maintenance': False}, 120)
        site = SiteSettings.get()
        site.maintenance = True
        site.save()
        self.assertIsNone(
            cache.get(SITE_SETTINGS_CACHE_KEY),
            'SiteSettings.save() left the stale cached copy in place — the '
            'toggle API would report success while pages serve the old flags',
        )

    def test_toggle_endpoint_flips_the_row_and_the_cache_is_fresh_after(self):
        sa = make_user('togglesa', role='superadmin')
        self.client.force_login(sa)
        response = self.client.post(
            reverse('toggle_setting'), {'key': 'maintenance', 'value': 'true'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(SiteSettings.get().maintenance)
        self.assertIsNone(
            cache.get(SITE_SETTINGS_CACHE_KEY),
            'the toggle endpoint saved but the cached copy was not dropped',
        )

    def test_stays_a_singleton(self):
        SiteSettings.get().save()
        SiteSettings.get().save()
        self.assertEqual(SiteSettings.objects.count(), 1)
