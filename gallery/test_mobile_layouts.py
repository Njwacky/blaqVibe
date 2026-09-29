"""Responsive profile/approval templates must retain their real form hooks.

Django tests pin the markup, permissions and scoped CSS contracts. Actual
layout/touch-target sizes also need browser checks at phone and desktop widths.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from users.models import ProfileLink

from .tests import make_category, make_project, make_user


class FormMarkup(HTMLParser):
    """Collect form controls without depending on attribute order/whitespace."""

    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.labels = set()
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = {'attrs': attrs, 'controls': []}
            self.forms.append(self.current)
        elif tag in ('input', 'select', 'button', 'textarea') and self.current is not None:
            self.current['controls'].append(attrs)
        elif tag == 'label' and 'for' in attrs:
            self.labels.add(attrs['for'])

    def handle_endtag(self, tag):
        if tag == 'form':
            self.current = None

    def form_with_class(self, name):
        return next(form for form in self.forms if name in form['attrs'].get('class', '').split())


@override_settings(RATELIMIT_ENABLE=False)
class MobileProfileTemplateTests(TestCase):
    def setUp(self):
        self.user = make_user('mobile-editor_' + 'long_name_' * 8, password=None)
        self.client.force_login(self.user)
        self.url = reverse('edit_profile')

    def test_scoped_styles_load_in_head_after_shared_form_styles(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        head = response.content.decode().split('</head>')[0]
        self.assertLess(head.index('forms.css'), head.index('edit-profile.css'))
        self.assertContains(response, 'class="profile-edit"')

    def test_all_editors_keep_post_actions_and_csrf_tokens(self):
        markup = FormMarkup(self.client.get(self.url).content.decode())
        main = markup.form_with_class('profile-edit__form')
        self.assertEqual(main['attrs']['method'], 'post')
        self.assertEqual(main['attrs']['enctype'], 'multipart/form-data')
        names = {control.get('name') for control in main['controls']}
        for name in ('bio', 'location', 'website', 'github', 'twitter', 'canvas_url', 'avatar',
                     'links-TOTAL_FORMS', 'links-INITIAL_FORMS', 'links-MAX_NUM_FORMS'):
            self.assertIn(name, names)
        rename = markup.form_with_class('profile-edit__rename-form')
        style = markup.form_with_class('profile-edit__style-form')
        self.assertEqual(rename['attrs']['action'], reverse('rename_username'))
        self.assertEqual(style['attrs']['action'], reverse('set_name_style'))
        for form in (main, rename, style):
            self.assertEqual(form['attrs']['method'], 'post')
            self.assertIn('csrfmiddlewaretoken', {control.get('name') for control in form['controls']})

    def test_profile_and_style_fields_have_associated_labels(self):
        markup = FormMarkup(self.client.get(self.url).content.decode())
        for field in ('bio', 'location', 'website', 'github', 'twitter', 'canvas_url', 'avatar',
                      'new_username', 'name_persona', 'name_font', 'name_color', 'name_size', 'name_fx'):
            self.assertIn(f'id_{field}', markup.labels)

    def test_existing_and_dynamic_website_rows_keep_the_same_hooks(self):
        ProfileLink.objects.create(
            profile=self.user.profile, label='Moved portfolio', url='https://old.example.com',
            status='moved', moved_to='https://new.example.com',
        )
        response = self.client.get(self.url)
        for hook in ('link-rows', 'link-empty-template', 'link-form-management', 'add-link-btn',
                     'data-link-row', 'data-link-status', 'data-link-moved-to-wrap', 'data-link-remove'):
            self.assertContains(response, hook)
        for name in ('links-0-label', 'links-0-url', 'links-0-status', 'links-0-moved_to', 'links-0-DELETE',
                     'links-__prefix__-label', 'links-__prefix__-url', 'links-__prefix__-status'):
            self.assertContains(response, f'name="{name}"')
        self.assertContains(response, 'https://new.example.com')
        self.assertContains(response, 'id="name-style-preview"')
        self.assertContains(response, 'id="name-style-maps"')

    def test_missing_management_data_shows_formset_error_without_saving_profile(self):
        response = self.client.post(self.url, {'bio': 'This must not save without the website formset.'})
        self.assertEqual(response.status_code, 200)
        errors = response.context['link_formset'].non_form_errors()
        self.assertTrue(errors)
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, escape(errors[0]))
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.bio, '')


@override_settings(RATELIMIT_ENABLE=False)
class MobileModerationTemplateTests(TestCase):
    def setUp(self):
        self.moderator = make_user('mobile-mod', password=None, role='moderator')
        self.owner = make_user('builder_' + 'long_name_' * 8, password=None)
        self.category = make_category()
        self.client.force_login(self.moderator)
        self.url = reverse('moderation_queue')

    def test_pending_and_quarantined_cards_keep_all_decisions_and_csrf(self):
        pending = make_project(self.owner, self.category, status='pending', title='Pending mobile review')
        quarantined = make_project(
            self.owner, self.category, status='quarantined', title='Quarantined mobile review',
        )
        response = self.client.get(self.url)
        self.assertContains(response, 'moderation-queue.css')
        self.assertContains(response, 'class="moderation-page"')
        markup = FormMarkup(response.content.decode())
        forms = {form['attrs'].get('action'): form for form in markup.forms}
        for project, actions in ((pending, {'approve', 'reject'}), (quarantined, {'approve', 'delete'})):
            form = forms[reverse('moderation_action', args=[project.slug])]
            self.assertEqual(form['attrs']['method'], 'post')
            self.assertIn('csrfmiddlewaretoken', {control.get('name') for control in form['controls']})
            self.assertEqual(
                {control['value'] for control in form['controls'] if control.get('name') == 'action'},
                actions,
            )
        self.assertContains(response, 'js-confirm-delete')
        for url in (reverse('reports_queue'), reverse('appeals_queue'), reverse('scan_status', args=[pending.slug])):
            self.assertContains(response, f'href="{url}"')

    def test_long_titles_descriptions_and_scan_reports_are_not_discarded(self):
        title = 'An approval title with a long unbroken segment ' + 'x' * 120
        description = 'A complete description to review on a phone. ' * 4
        report = {'secrets': ['directory/' + 'filename' * 80 + '.py', '<script>alert(1)</script>']}
        make_project(self.owner, self.category, status='pending', title=title, short_description=description)
        make_project(self.owner, self.category, status='quarantined', title='Scan report', scan_report=report)
        response = self.client.get(self.url)
        self.assertContains(response, title)
        self.assertContains(response, description)
        self.assertContains(response, '<details class="moderation-scan-report">')
        self.assertContains(response, '<summary>Scan report</summary>')
        self.assertContains(response, escape(str(report)))
        self.assertNotContains(response, '<script>alert(1)</script>')

    def test_empty_queues_still_have_clear_messages(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'No pending vibes — queue is clean.')
        self.assertContains(response, 'No quarantined vibes.')

    def test_responsive_queue_is_still_staff_only(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response.url)


class MobileLayoutStyleTests(SimpleTestCase):
    @staticmethod
    def css(name):
        path = Path(settings.BASE_DIR, 'static', 'gallery', 'css', name)
        return re.sub(r'/\*.*?\*/', '', path.read_text(), flags=re.S)

    def test_page_styles_fix_intrinsic_overflow_instead_of_clipping_it(self):
        for name in ('edit-profile.css', 'moderation-queue.css'):
            with self.subTest(stylesheet=name):
                body = self.css(name)
                self.assertIn('minmax(0, 1fr)', body)
                self.assertIn('min-width: 0', body)
                self.assertIn('overflow-wrap: anywhere', body)
                self.assertNotRegex(body, r'overflow(?:-x)?\s*:\s*(hidden|clip)')

    def test_profile_has_mobile_input_sizes_and_touch_safe_remove_buttons(self):
        body = self.css('edit-profile.css')
        self.assertIn('font-size: 16px', body)
        self.assertRegex(body, r'\.profile-edit \.link-remove\s*\{[^}]*width: 44px;[^}]*height: 44px;')
        self.assertIn('(hover: hover) and (pointer: fine)', body)
        # Live preview updates replace className, so wrapping must use its ID.
        self.assertRegex(body, r'#name-style-preview\s*\{[^}]*white-space: normal;[^}]*overflow-wrap: anywhere;')

    def test_approvals_stack_actions_and_wrap_expanded_reports(self):
        body = self.css('moderation-queue.css')
        self.assertRegex(body, r'\.moderation-actions\s*\{[^}]*grid-template-columns: minmax\(0, 1fr\);')
        self.assertRegex(body, r'\.moderation-scan-report pre\s*\{[^}]*white-space: pre-wrap;[^}]*overflow-wrap: anywhere;')
        self.assertIn('min-height: 44px', body)
        self.assertIn('@media (min-width: 960px)', body)
