"""Footer contact administration and production-only footer behaviour."""
from django.test import TestCase, override_settings
from django.urls import reverse

from gallery.tests import make_user

from .models import AdminLog, SiteSettings


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-footer-contacts', SEED_DEMO=False)
class FooterContactsAdminTests(TestCase):
    def setUp(self):
        self.admin = make_user('footeradmin', role='admin')
        self.superadmin = make_user('footersuperadmin', role='superadmin')
        self.member = make_user('footermember')
        self.url = reverse('footer_contacts')

    def test_admin_and_superadmin_can_open_editor(self):
        for user in (self.admin, self.superadmin):
            self.client.force_login(user)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Footer contacts')
            self.assertContains(response, 'Support email address')
            self.client.logout()

    def test_anonymous_is_redirected_and_member_is_forbidden(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_admin_can_update_public_footer_contacts_and_creates_audit_log(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {
            'footer_contact_email': 'Support@Example.COM ',
            'footer_github_url': 'https://github.com/example-org',
            'footer_github_label': 'GitHub @example-org',
        })
        self.assertRedirects(response, self.url)

        site = SiteSettings.get()
        self.assertEqual(site.footer_contact_email, 'support@example.com')
        self.assertEqual(site.footer_github_url, 'https://github.com/example-org')
        self.assertEqual(site.footer_github_label, 'GitHub @example-org')
        self.assertTrue(AdminLog.objects.filter(
            actor=self.admin,
            action='update_footer_contacts',
            target__contains='contact_email',
        ).exists())

        footer = self.client.get(reverse('login')).content.decode()
        self.assertIn('mailto:support@example.com', footer)
        self.assertIn('href="https://github.com/example-org"', footer)
        self.assertIn('GitHub @example-org', footer)

    def test_empty_contact_values_hide_only_the_optional_public_links(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {
            'footer_contact_email': '',
            'footer_github_url': '',
            'footer_github_label': '',
        })
        self.assertRedirects(response, self.url)

        footer = self.client.get(reverse('login')).content.decode()
        self.assertNotIn('mailto:admin@blaqvibes.co.za', footer)
        self.assertNotIn('href="https://github.com/Njwacky"', footer)
        self.assertIn('Ask Nolo (AI help)', footer)

    def test_invalid_non_web_contact_url_does_not_change_saved_value(self):
        before = SiteSettings.get().footer_github_url
        self.client.force_login(self.admin)
        response = self.client.post(self.url, {
            'footer_contact_email': 'support@example.com',
            'footer_github_url': 'ftp://github.com/example-org',
            'footer_github_label': 'GitHub @example-org',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Use a complete http:// or https:// URL.')
        self.assertEqual(SiteSettings.get().footer_github_url, before)
        self.assertFalse(AdminLog.objects.filter(action='update_footer_contacts').exists())


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-footer-production', SEED_DEMO=False)
class FooterHostingNoticeTests(TestCase):
    def test_hosting_notice_is_not_rendered_in_production(self):
        with self.settings(DEBUG=False, LOCAL_DEV=False, PREVIEW=False):
            response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Previews are not production hosting.')

    def test_hosting_notice_is_rendered_only_for_local_or_preview_environments(self):
        with self.settings(DEBUG=False, LOCAL_DEV=False, PREVIEW=True):
            response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Previews are not production hosting.')
