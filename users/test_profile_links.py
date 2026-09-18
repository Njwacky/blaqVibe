"""Profile website links — every gate tested.

A builder's profile now carries ROWS of their own websites (ProfileLink),
not just the single legacy website field: each row is a display name + URL
+ a status light visitors read BEFORE they click —
  ● green   active            (link works, points at url)
  ● orange  under maintenance (link works, warns)
  ● grey    inactive          (announced, NOT clickable)
  ⇗ moved                     (chip points at moved_to, the new address)

This file mirrors the rules rather than the views: the moved/no-destination
rule and the status→href mapping live on ProfileLink, so testing the model
(plus a thin end-to-end pass through edit_profile and profile_view) means
the admin inline or any future editor inherits the same guarantees.
"""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from users.forms import ProfileLinkForm, ProfileLinkFormSet
from users.models import Profile, ProfileLink

def link_data(*rows, initial=0):
    """Build a formset POST payload the way the browser would."""
    data = {
        'links-TOTAL_FORMS': str(len(rows)),
        'links-INITIAL_FORMS': str(initial),
        'links-MIN_NUM_FORMS': '0',
        'links-MAX_NUM_FORMS': str(ProfileLink.MAX_LINKS),
    }
    for i, row in enumerate(rows):
        for key, value in row.items():
            data[f'links-{i}-{key}'] = value
    return data


@override_settings(RATELIMIT_ENABLE=False)
class ProfileLinkModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('builder', password='pass12345', email='b@test.com')
        self.profile = self.user.profile

    def _link(self, **kwargs):
        return ProfileLink(profile=self.profile, label='Portfolio', url='https://me.dev', **kwargs)

    def test_status_light_mapping(self):
        """Each status maps to its dot class — green, orange, grey, ⇗ violet."""
        self.assertEqual(self._link(status='active').dot_class, 'link-dot--active')
        self.assertEqual(self._link(status='maintenance').dot_class, 'link-dot--maintenance')
        self.assertEqual(self._link(status='inactive').dot_class, 'link-dot--inactive')
        self.assertEqual(
            self._link(status='moved', moved_to='https://new.dev').dot_class,
            'link-dot--moved',
        )

    def test_moved_chip_points_at_new_address(self):
        link = self._link(status='moved', moved_to='https://new.dev')
        link.clean()
        self.assertEqual(link.href, 'https://new.dev')
        self.assertTrue(link.is_moved)
        self.assertTrue(link.is_clickable)

    def test_non_moved_statuses_point_at_their_own_url(self):
        for status in ('active', 'maintenance', 'inactive'):
            self.assertEqual(self._link(status=status).href, 'https://me.dev')

    def test_moved_row_requires_new_address(self):
        link = self._link(status='moved', moved_to='')
        with self.assertRaises(Exception):
            link.clean()

    def test_moved_new_address_must_differ(self):
        link = self._link(status='moved', moved_to='https://me.dev')
        with self.assertRaises(Exception):
            link.clean()

    def test_leaving_moved_clears_stale_destination(self):
        """Flipping back to active must not keep a stale redirect around."""
        link = self._link(status='active', moved_to='https://leftover.dev')
        link.clean()
        self.assertEqual(link.moved_to, '')

    def test_inactive_is_not_clickable(self):
        self.assertFalse(self._link(status='inactive').is_clickable)
        for status in ('active', 'maintenance', 'moved'):
            self.assertTrue(self._link(status=status, moved_to='https://n.dev').is_clickable)

    def test_ordering_by_position(self):
        second = ProfileLink.objects.create(profile=self.profile, label='B', url='https://b.dev', position=2)
        first = ProfileLink.objects.create(profile=self.profile, label='A', url='https://a.dev', position=1)
        self.assertEqual(list(self.profile.links.all()), [first, second])


@override_settings(RATELIMIT_ENABLE=False)
class ProfileLinkFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('formuser', password='pass12345', email='f@test.com')
        self.profile = self.user.profile

    def _formset(self, *rows, **kwargs):
        return ProfileLinkFormSet(
            link_data(*rows, initial=kwargs.get('initial', 0)),
            queryset=self.profile.links.all(),
            prefix='links',
            profile=self.profile,
        )

    def test_add_as_many_links_as_you_want(self):
        fs = self._formset(
            {'label': 'Portfolio', 'url': 'https://p.dev', 'status': 'active', 'moved_to': ''},
            {'label': 'Blog', 'url': 'https://b.dev', 'status': 'maintenance', 'moved_to': ''},
            {'label': 'Old lab', 'url': 'https://old.dev', 'status': 'inactive', 'moved_to': ''},
            {'label': 'SaaS', 'url': 'https://s.dev', 'status': 'moved', 'moved_to': 'https://new.dev'},
        )
        self.assertTrue(fs.is_valid(), fs.errors)
        fs.save()
        self.assertEqual(self.profile.links.count(), 4)
        moved = self.profile.links.get(status='moved')
        self.assertEqual(moved.href, 'https://new.dev')

    def test_blank_extra_row_is_ignored(self):
        """The JS clones empty rows; a fully blank row saves nothing."""
        fs = self._formset({'label': '', 'url': '', 'status': 'active', 'moved_to': ''})
        self.assertTrue(fs.is_valid(), fs.errors)
        self.assertEqual(fs.save(), [])

    def test_labelled_row_without_url_is_refused(self):
        """A chip with no words on it, or words with no chip, both refuse."""
        fs = self._formset({'label': 'Portfolio', 'url': '', 'status': 'active', 'moved_to': ''})
        self.assertFalse(fs.is_valid())
        self.assertIn('url', fs.forms[0].errors)
        fs2 = self._formset({'label': '', 'url': 'https://x.dev', 'status': 'active', 'moved_to': ''})
        self.assertFalse(fs2.is_valid())
        self.assertIn('label', fs2.forms[0].errors)

    def test_label_passes_public_language_gate(self):
        fs = self._formset({'label': 'fuck you', 'url': 'https://x.dev', 'status': 'active', 'moved_to': ''})
        self.assertFalse(fs.is_valid())
        self.assertIn('label', fs.forms[0].errors)

    def test_javascript_url_is_refused_by_urlfield(self):
        form = ProfileLinkForm({'label': 'Evil', 'url': 'javascript:alert(1)', 'status': 'active', 'moved_to': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('url', form.errors)

    def test_more_than_twelve_links_is_refused(self):
        rows = [
            {'label': f'Site {i}', 'url': f'https://s{i}.dev', 'status': 'active', 'moved_to': ''}
            for i in range(ProfileLink.MAX_LINKS + 1)
        ]
        fs = self._formset(*rows)
        self.assertFalse(fs.is_valid())

    def test_new_rows_get_stamped_with_the_editing_profile(self):
        """save_new must land the row on the profile being edited — never NULL."""
        fs = self._formset({'label': 'Mine', 'url': 'https://m.dev', 'status': 'active', 'moved_to': ''})
        self.assertTrue(fs.is_valid(), fs.errors)
        fs.save()
        link = ProfileLink.objects.get(label='Mine')
        self.assertEqual(link.profile_id, self.profile.pk)


@override_settings(RATELIMIT_ENABLE=False)
class EditProfileLinksViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('viewuser', password='pass12345', email='v@test.com')
        self.client.login(username='viewuser', password='pass12345')
        self.url = reverse('edit_profile')
        self.profile = self.user.profile

    def _profile_post(self, **extra):
        data = {
            'bio': 'I build things',
            'location': 'Durban, ZA',
            'website': 'https://main.dev',
            'github': 'viewuser',
            'twitter': '',
            'canvas_url': '',
        }
        data.update(extra)
        return data

    def test_get_shows_links_editor_and_existing_rows(self):
        ProfileLink.objects.create(profile=self.profile, label='Blog', url='https://blog.dev')
        response = self.client.get(self.url)
        self.assertContains(response, 'Your websites')
        self.assertContains(response, 'link-empty-template')
        self.assertContains(response, 'links-0-label')
        self.assertContains(response, 'https://blog.dev')

    def test_profile_and_links_save_together(self):
        response = self.client.post(
            self.url,
            {
                **self._profile_post(),
                'links-TOTAL_FORMS': '1', 'links-INITIAL_FORMS': '0',
                'links-MIN_NUM_FORMS': '0', 'links-MAX_NUM_FORMS': '12',
                'links-0-label': 'Side project', 'links-0-url': 'https://side.dev',
                'links-0-status': 'active', 'links-0-moved_to': '',
            },
        )
        self.assertRedirects(response, reverse('profile_view', kwargs={'username': self.user.username}))
        self.assertEqual(self.profile.links.count(), 1)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.bio, 'I build things')

    def test_bad_link_row_saves_neither_bio_nor_links(self):
        """The page is one save: a refused row must not eat the bio edit."""
        response = self.client.post(
            self.url,
            {
                **self._profile_post(bio='should not save'),
                'links-TOTAL_FORMS': '1', 'links-INITIAL_FORMS': '0',
                'links-MIN_NUM_FORMS': '0', 'links-MAX_NUM_FORMS': '12',
                'links-0-label': 'Broken', 'links-0-url': '',
                'links-0-status': 'active', 'links-0-moved_to': '',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertNotEqual(self.profile.bio, 'should not save')
        self.assertEqual(self.profile.links.count(), 0)

    def test_delete_saved_row_via_hidden_checkbox(self):
        link = ProfileLink.objects.create(profile=self.profile, label='Old', url='https://old.dev')
        response = self.client.post(
            self.url,
            {
                **self._profile_post(),
                'links-TOTAL_FORMS': '1', 'links-INITIAL_FORMS': '1',
                'links-MIN_NUM_FORMS': '0', 'links-MAX_NUM_FORMS': '12',
                'links-0-id': str(link.pk), 'links-0-label': 'Old',
                'links-0-url': 'https://old.dev', 'links-0-status': 'active',
                'links-0-moved_to': '', 'links-0-DELETE': 'on',
            },
        )
        self.assertRedirects(response, reverse('profile_view', kwargs={'username': self.user.username}))
        self.assertFalse(ProfileLink.objects.filter(pk=link.pk).exists())

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)


@override_settings(RATELIMIT_ENABLE=False)
class ProfileShowsLinksWithStatusLightsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('public', password='pass12345', email='p@test.com')
        self.profile = self.user.profile
        self.url = reverse('profile_view', kwargs={'username': self.user.username})

    def test_visitor_sees_each_link_with_its_light(self):
        ProfileLink.objects.create(profile=self.profile, label='Portfolio', url='https://p.dev', status='active', position=1)
        ProfileLink.objects.create(profile=self.profile, label='Dashboard', url='https://d.dev', status='maintenance', position=2)
        ProfileLink.objects.create(profile=self.profile, label='Old lab', url='https://old.dev', status='inactive', position=3)
        ProfileLink.objects.create(profile=self.profile, label='Blog', url='https://old-blog.dev', status='moved', moved_to='https://new-blog.dev', position=4)

        response = self.client.get(self.url)
        html = response.content.decode()

        # Active: green dot chip pointing at its own URL.
        self.assertIn('link-dot--active', html)
        self.assertIn('href="https://p.dev"', html)
        # Maintenance: orange dot chip.
        self.assertIn('link-dot--maintenance', html)
        self.assertIn('href="https://d.dev"', html)
        # Moved: chip points at the NEW address, not the dead one.
        self.assertIn('link-moved-glyph', html)
        self.assertIn('href="https://new-blog.dev"', html)
        self.assertNotIn('href="https://old-blog.dev"', html)
        # Inactive: announced but NOT clickable.
        self.assertIn('link-dot--inactive', html)
        self.assertIn('Old lab — inactive', html)
        inactive_chunk = html.split('Old lab — inactive')[0][-400:]
        self.assertNotIn('<a class="profile-social-chip profile-link-chip profile-link--inactive"', inactive_chunk)

    def test_links_are_ordered_by_position(self):
        ProfileLink.objects.create(profile=self.profile, label='B', url='https://b.dev', position=2)
        ProfileLink.objects.create(profile=self.profile, label='A', url='https://a.dev', position=1)
        html = self.client.get(self.url).content.decode()
        self.assertLess(html.index('https://a.dev'), html.index('https://b.dev'))

    def test_no_links_no_section_rows(self):
        html = self.client.get(self.url).content.decode()
        self.assertNotIn('link-dot', html)


@override_settings(RATELIMIT_ENABLE=False)
class EditProfileShipsNoDevNotesTests(TestCase):
    """A note meant for the next developer must never become text a visitor reads.

    Django's ``{# #}`` is single-line only: a multi-line one is not a comment,
    so the browser gets it verbatim. The editor page is checked as served, and
    every template file is checked for the mistake itself.
    """

    def test_edit_profile_renders_no_literal_template_markup(self):
        User.objects.create_user('notetaker', password='pass12345', email='n@test.com')
        self.client.login(username='notetaker', password='pass12345')
        html = self.client.get(reverse('edit_profile')).content.decode()
        for markup in ('{#', '#}', '{%', '{{'):
            self.assertNotIn(markup, html)

    def test_no_template_file_carries_a_multi_line_hash_comment(self):
        offenders = []
        for path in Path(settings.BASE_DIR, 'templates').rglob('*.html'):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                index = 0
                while True:
                    index = line.find('{#', index)
                    if index == -1:
                        break
                    if '#}' not in line[index:]:
                        offenders.append(f'{path.relative_to(settings.BASE_DIR)}:{number}')
                        break
                    index += 2
        self.assertEqual(offenders, [])
