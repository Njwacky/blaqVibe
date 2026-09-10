"""Footer contact administration and production-only footer behaviour.

The footer contact list is data, not three fixed fields: an operator can add
as many methods as the company has (two support mailboxes, a WhatsApp number,
X, GitHub), reorder them, or hide one without deleting it. These tests pin the
behaviour a visitor sees in the footer and the safety of the links it renders.
"""
from django.test import TestCase, override_settings
from django.urls import reverse

from gallery.tests import make_user

from .footer_contacts import MAX_FOOTER_CONTACTS
from .models import AdminLog, FooterContact


def management_form(initial, total):
    return {
        'form-TOTAL_FORMS': str(total),
        'form-INITIAL_FORMS': str(initial),
        'form-MIN_NUM_FORMS': '0',
        'form-MAX_NUM_FORMS': '1000',
    }


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-footer-contacts', SEED_DEMO=False)
class FooterContactsAdminTests(TestCase):
    def setUp(self):
        self.admin = make_user('footeradmin', role='admin')
        self.superadmin = make_user('footersuperadmin', role='superadmin')
        self.member = make_user('footermember')
        self.url = reverse('footer_contacts')
        # The 0023 migration seeds an installation's existing footer details
        # (or the shipped defaults) as rows; tests start from an empty list.
        FooterContact.objects.all().delete()

    def _post_rows(self, new_rows=(), delete=()):
        """POST the editor: every saved row, then the given new/removed ones."""
        existing = list(FooterContact.objects.all())
        data = management_form(len(existing), len(existing) + len(new_rows))
        for index, contact in enumerate(existing):
            data.update({
                f'form-{index}-id': str(contact.pk),
                f'form-{index}-kind': contact.kind,
                f'form-{index}-value': contact.value,
                f'form-{index}-label': contact.label,
                f'form-{index}-position': str(contact.position),
            })
            if contact.is_active:
                data[f'form-{index}-is_active'] = 'on'
            if contact.pk in delete:
                data[f'form-{index}-DELETE'] = 'on'
        for offset, row in enumerate(new_rows):
            index = len(existing) + offset
            data.update({
                f'form-{index}-id': '',
                f'form-{index}-kind': row.get('kind', 'email'),
                f'form-{index}-value': row.get('value', ''),
                f'form-{index}-label': row.get('label', ''),
                f'form-{index}-position': str(row.get('position', 0)),
            })
            if row.get('is_active', True):
                data[f'form-{index}-is_active'] = 'on'
        return self.client.post(self.url, data)

    def _footer(self):
        return self.client.get(reverse('feed')).content.decode()

    def test_admin_and_superadmin_can_open_editor(self):
        for user in (self.admin, self.superadmin):
            self.client.force_login(user)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Footer contacts')
            self.assertContains(response, 'Add another method')
            self.assertContains(response, 'WhatsApp')
            self.client.logout()

    def test_anonymous_is_redirected_and_member_is_forbidden(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_admin_can_add_two_emails_a_whatsapp_number_and_x(self):
        self.client.force_login(self.admin)
        response = self._post_rows(new_rows=[
            {'kind': 'email', 'value': 'Support@Example.COM ', 'position': 0},
            {'kind': 'email', 'value': 'billing@example.com', 'position': 10},
            {'kind': 'whatsapp', 'value': '082 555 0100', 'position': 20},
            {'kind': 'twitter', 'value': '@blaqvibes', 'position': 30},
            {'kind': 'github', 'value': 'Njwacky', 'label': 'GitHub @Njwacky', 'position': 40},
        ])
        self.assertRedirects(response, self.url)
        self.assertEqual(FooterContact.objects.count(), 5)

        footer = self._footer()
        self.assertIn('mailto:support@example.com', footer)
        self.assertIn('mailto:billing@example.com', footer)
        self.assertIn('href="https://wa.me/27825550100"', footer)
        self.assertIn('href="https://x.com/blaqvibes"', footer)
        self.assertIn('GitHub @Njwacky', footer)

    def test_whatsapp_and_phone_numbers_are_normalised(self):
        self.client.force_login(self.admin)
        self._post_rows(new_rows=[
            {'kind': 'whatsapp', 'value': '082 555 0100'},
            {'kind': 'phone', 'value': '+1 (555) 010-0199'},
        ])
        whatsapp = FooterContact.objects.get(kind='whatsapp')
        phone = FooterContact.objects.get(kind='phone')
        self.assertEqual(whatsapp.value, '+27825550100')
        self.assertEqual(whatsapp.href, 'https://wa.me/27825550100')
        self.assertEqual(phone.value, '+15550100199')
        self.assertEqual(phone.href, 'tel:+15550100199')

    def test_twitter_handle_or_full_url_becomes_an_x_link(self):
        self.client.force_login(self.admin)
        self._post_rows(new_rows=[
            {'kind': 'twitter', 'value': 'https://twitter.com/blaqvibes'},
        ])
        contact = FooterContact.objects.get(kind='twitter')
        self.assertEqual(contact.value, 'blaqvibes')
        self.assertEqual(contact.href, 'https://x.com/blaqvibes')
        self.assertIn('href="https://x.com/blaqvibes"', self._footer())

    def test_a_pasted_foreign_url_is_rejected_for_a_handle_kind(self):
        self.client.force_login(self.admin)
        response = self._post_rows(new_rows=[
            {'kind': 'twitter', 'value': 'https://facebook.com/blaqvibes'},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'That link is not X (Twitter).')
        self.assertFalse(FooterContact.objects.exists())
        self.assertFalse(AdminLog.objects.filter(action='update_footer_contacts').exists())

    def test_javascript_url_is_rejected_and_nothing_is_saved(self):
        self.client.force_login(self.admin)
        response = self._post_rows(new_rows=[
            {'kind': 'website', 'value': 'javascript:alert(1)'},
        ])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Use a complete http:// or https:// URL.')
        self.assertFalse(FooterContact.objects.exists())

    def test_a_row_written_outside_a_form_can_never_ship_a_javascript_link(self):
        # Defence in depth: even a row that skipped validation (a shell, a bad
        # import, a future bug) renders no link rather than a dangerous one.
        FooterContact.objects.create(kind='website', value='javascript:alert(1)', position=0)
        self.assertNotIn('javascript:', self._footer())

    def test_spare_rows_are_ignored_the_way_a_browser_submits_them(self):
        # A browser posts every rendered row, including the two spare ones —
        # with their suggested Order number and Show ticked. Those rows must
        # not become contacts, and must not block the save either.
        self.client.force_login(self.admin)
        data = {
            'form-TOTAL_FORMS': '3', 'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0', 'form-MAX_NUM_FORMS': '1000',
            'form-0-id': '', 'form-0-kind': 'whatsapp', 'form-0-value': '082 555 0100',
            'form-0-label': '', 'form-0-position': '10', 'form-0-is_active': 'on',
            'form-1-id': '', 'form-1-kind': 'email', 'form-1-value': '',
            'form-1-label': '', 'form-1-position': '20', 'form-1-is_active': 'on',
            'form-2-id': '', 'form-2-kind': 'email', 'form-2-value': '',
            'form-2-label': '', 'form-2-position': '30', 'form-2-is_active': 'on',
        }
        response = self.client.post(self.url, data)
        self.assertRedirects(response, self.url)
        self.assertEqual(FooterContact.objects.count(), 1)
        self.assertEqual(FooterContact.objects.get().value, '+27825550100')

    def test_blank_extra_rows_and_valueless_rows_create_nothing(self):
        self.client.force_login(self.admin)
        self._post_rows(new_rows=[
            {'kind': 'email', 'value': '   '},   # typed nothing useful
            {'kind': 'email', 'value': ''},      # untouched extra row
        ])
        self.assertEqual(FooterContact.objects.count(), 0)
        self.assertFalse(AdminLog.objects.filter(action='update_footer_contacts').exists())

    def test_removing_a_contact_hides_it_from_the_footer(self):
        contact = FooterContact.objects.create(kind='email', value='old@example.com', position=0)
        self.client.force_login(self.admin)
        response = self._post_rows(delete=[contact.pk])
        self.assertRedirects(response, self.url)
        self.assertFalse(FooterContact.objects.exists())
        self.assertNotIn('mailto:old@example.com', self._footer())

    def test_hidden_contact_is_not_shown_but_is_kept(self):
        FooterContact.objects.create(kind='email', value='sales@example.com', position=0, is_active=False)
        footer = self._footer()
        self.assertNotIn('sales@example.com', footer)
        self.assertTrue(FooterContact.objects.filter(value='sales@example.com').exists())

    def test_position_sets_the_order_of_the_footer_links(self):
        FooterContact.objects.create(kind='email', value='last@example.com', position=30)
        FooterContact.objects.create(kind='whatsapp', value='+27825550100', position=10)
        FooterContact.objects.create(kind='twitter', value='blaqvibes', position=20)
        footer = self._footer()
        self.assertLess(footer.index('wa.me/27825550100'), footer.index('x.com/blaqvibes'))
        self.assertLess(footer.index('x.com/blaqvibes'), footer.index('mailto:last@example.com'))

    def test_display_label_overrides_the_raw_value(self):
        FooterContact.objects.create(
            kind='whatsapp', value='+27825550100', label='WhatsApp the team', position=0,
        )
        footer = self._footer()
        self.assertIn('WhatsApp the team', footer)
        self.assertNotIn('+27825550100', footer)

    def test_url_kinds_show_a_short_domain_not_the_whole_link(self):
        FooterContact.objects.create(
            kind='telegram', value='https://t.me/blaqvibes', position=0, label='',
        )
        footer = self._footer()
        self.assertIn('href="https://t.me/blaqvibes"', footer)
        self.assertIn('t.me/blaqvibes', footer)

    def test_audit_log_records_kinds_added_updated_and_removed(self):
        keep = FooterContact.objects.create(kind='email', value='keep@example.com', position=0)
        gone = FooterContact.objects.create(kind='github', value='Njwacky', position=10)
        self.client.force_login(self.admin)

        # Update `keep`, remove `gone`, add a WhatsApp number.
        existing = list(FooterContact.objects.all())
        data = management_form(len(existing), len(existing) + 1)
        for index, contact in enumerate(existing):
            value = 'changed@example.com' if contact.pk == keep.pk else contact.value
            data.update({
                f'form-{index}-id': str(contact.pk),
                f'form-{index}-kind': contact.kind,
                f'form-{index}-value': value,
                f'form-{index}-label': '',
                f'form-{index}-position': str(contact.position),
                f'form-{index}-is_active': 'on',
            })
            if contact.pk == gone.pk:
                data[f'form-{index}-DELETE'] = 'on'
        data.update({
            f'form-{len(existing)}-id': '',
            f'form-{len(existing)}-kind': 'whatsapp',
            f'form-{len(existing)}-value': '082 555 0100',
            f'form-{len(existing)}-label': '',
            f'form-{len(existing)}-position': '20',
            f'form-{len(existing)}-is_active': 'on',
        })
        self.client.post(self.url, data)

        log = AdminLog.objects.get(actor=self.admin, action='update_footer_contacts')
        self.assertIn('added: whatsapp', log.target)
        self.assertIn('updated: email', log.target)
        self.assertIn('removed: github', log.target)
        self.assertNotIn('changed@example.com', log.target)

    def test_footer_shows_only_nolo_when_there_are_no_contacts(self):
        footer = self._footer()
        self.assertIn('Ask Nolo (AI help)', footer)
        self.assertNotIn('admin@blaqvibes.co.za', footer)

    def test_more_than_the_maximum_number_of_methods_is_refused(self):
        self.client.force_login(self.admin)
        rows = [
            {'kind': 'email', 'value': f'team{index}@example.com', 'position': index * 10}
            for index in range(MAX_FOOTER_CONTACTS + 1)
        ]
        response = self._post_rows(new_rows=rows)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'Keep the footer to {MAX_FOOTER_CONTACTS} contact methods')
        self.assertEqual(FooterContact.objects.count(), 0)

    def test_an_edit_is_visible_on_the_very_next_page_load(self):
        # The footer list is not cached: the default cache is per-process and
        # production runs several workers, so a cached list would keep serving
        # the old number on the workers that did not handle the edit.
        self.client.force_login(self.admin)
        self._post_rows(new_rows=[{'kind': 'whatsapp', 'value': '082 555 0100', 'position': 0}])
        self.assertIn('wa.me/27825550100', self._footer())

        existing = list(FooterContact.objects.all())
        data = management_form(len(existing), len(existing))
        for index, contact in enumerate(existing):
            data.update({
                f'form-{index}-id': str(contact.pk),
                f'form-{index}-kind': contact.kind,
                f'form-{index}-value': '082 555 0199',
                f'form-{index}-label': '',
                f'form-{index}-position': '0',
                f'form-{index}-is_active': 'on',
            })
        self.client.post(self.url, data)

        footer = self._footer()
        self.assertIn('wa.me/27825550199', footer)
        self.assertNotIn('wa.me/27825550100', footer)


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
