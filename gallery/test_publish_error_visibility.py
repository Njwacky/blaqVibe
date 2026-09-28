"""Regression tests: a rejected publish must SHOW why it was rejected.

Root cause (Sep 2026): "I filled in all the files, pressed Publish, and it
just came back to the same page — like there was an error but nothing was
raised."

The error *was* raised. The server answered an invalid submission in place —
200 with the publish card re-rendered and its errors attached — and the XHR
in static/gallery/js/publish.js threw that answer away:

    xhr.onload = function () {
      if (xhr.responseURL) { window.location.href = xhr.responseURL; return; }
      ...
    };

`XMLHttpRequest.responseURL` is the FINAL url after redirects, so on success
(302 → /publish/done/slug/) it is a different page and navigating there is
right. On a rejection it is /publish/ — byte-identical to the page already on
screen. Assigning it back to window.location reloaded a brand-new, unbound
form: the reason for the rejection, everything the builder had typed, and the
chosen ZIP all vanished with nothing to explain any of it.

Three further gaps on the same path, pinned here:

* errors on fields the template renders no markup for (css_code, js_code,
  readme, tech_stack, category, creator_kind, star_cost, price_zar) failed the
  form with no message anywhere — even with JS off;
* the re-rendered picker is empty because a browser cannot put an uploaded
  file back into a form, and nothing said so;
* django-ratelimit's decorator defaults to block=True, so the publish view's
  own 429 branch was unreachable and the 6th upload in an hour answered with
  handler403's "You tried to access a page you shouldn't" — which the XHR then
  discarded as well.
"""
import re
import zipfile
from io import BytesIO
from pathlib import Path

from django.contrib.auth.models import User
from django.core.cache import caches
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

STATIC = Path(__file__).resolve().parent.parent / 'static' / 'gallery'


def zip_upload(name='app.zip'):
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('index.html', '<h1>it runs</h1>')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='application/zip')


class PublishPostMixin:
    """One POST helper: a complete, valid publish that each test then breaks."""

    def post(self, **over):
        data = {
            'title': 'Inventory Tracker',
            'short_description': 'A simple inventory system for small businesses.',
            'zip_file': zip_upload(),
            'build_method': 'human',
            'publish_token': 'a' * 32,
        }
        data.update(over)
        return self.client.post(reverse('publish'), data)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class PublishRejectionIsVisibleTests(PublishPostMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user('builder', password='pw12345!')
        self.client.force_login(self.user)

    def test_valid_publish_still_lands_on_its_own_page(self):
        """The success path the XHR navigates on: 302 to a different URL."""
        res = self.post()
        self.assertEqual(res.status_code, 302)
        self.assertIn('/publish/done/', res['Location'])
        self.assertNotEqual(res['Location'], reverse('publish'))

    def test_rejected_publish_answers_in_place_with_the_reason(self):
        """The response shape that used to be silently reloaded: 200, same
        URL, and the error actually in the body."""
        res = self.post(short_description='')
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.has_header('Location'))
        self.assertEqual(res.resolver_match.url_name, 'publish')
        body = res.content.decode()
        self.assertTrue('id="publish-errors"' in body, 'no error summary rendered')
        self.assertTrue('Tell people what you built' in body, 'no reason shown')
        # Announced to assistive tech, not just painted.
        self.assertTrue('role="alert"' in body, 'summary is not announced')
        # The text the builder typed survives the round trip.
        self.assertTrue('value="Inventory Tracker"' in body, 'typed text was dropped')

    def test_summary_lists_errors_for_fields_with_no_markup(self):
        """star_cost has no rendering of its own in publish.html; before the
        summary it failed the form with nothing on the page."""
        res = self.post(star_cost='99')
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        summary = re.search(r'id="publish-errors".*?(?=<form)', body, re.S)
        self.assertIsNotNone(summary, 'error summary missing')
        self.assertIn('Star cost', summary.group(0))
        self.assertIn('less than or equal to 5', summary.group(0))

    def test_lost_zip_is_explained(self):
        """A rejected submit comes back with an empty picker. Say so."""
        res = self.post(short_description='')
        body = res.content.decode()
        self.assertTrue('id="zip-lost-note"' in body, 'no note about the lost ZIP')
        self.assertTrue('pick it again' in body, 'note does not say what to do')

    def test_no_zip_note_for_a_snippet_publish(self):
        """Snippet builders never had a file to lose — do not nag them."""
        res = self.post(zip_file='', html_code='<main><h1>hi</h1></main>',
                        short_description='')
        self.assertEqual(res.status_code, 200)
        self.assertNotIn('id="zip-lost-note"', res.content.decode())

    def test_profanity_rejection_is_visible(self):
        res = self.post(short_description='fuck off everyone')
        self.assertEqual(res.status_code, 200)
        self.assertTrue('Please reword this' in res.content.decode(),
                        'public-language rejection was not shown')


@override_settings(MEDIA_ROOT='/tmp/blaqvibes-tests')
class PublishRateLimitTests(PublishPostMixin, TestCase):
    """Rate limiting stays ON here — this is the test that needs it. The
    limit counter lives in the 'ratelimit' cache alias, which the test
    runner's default-cache flush does not reach, and user pks repeat across
    tests, so it is cleared explicitly."""

    def setUp(self):
        caches['ratelimit'].clear()
        self.user = User.objects.create_user('builder', password='pw12345!')
        self.client.force_login(self.user)

    def tearDown(self):
        caches['ratelimit'].clear()

    def test_sixth_publish_answers_429_with_a_reason_not_a_403(self):
        last = None
        for i in range(6):
            last = self.post(title=f'Vibe {i}', publish_token=f'{i}' * 32)
        self.assertEqual(last.status_code, 429)
        body = last.content.decode()
        self.assertTrue('5 publishes in an hour' in body, 'no reason for the limit')
        self.assertTrue('id="publish-form"' in body, 'not on the publish page any more')
        self.assertTrue('You tried to access a page you shouldn' not in body,
                        'fell through to the generic 403 page')

    def test_the_upload_limit_keeps_what_was_typed(self):
        """An unbound form here discarded everything the builder had just
        typed, while the message told them nothing was lost."""
        last = None
        for i in range(6):
            last = self.post(title=f'Vibe {i}', publish_token=f'{i}' * 32)
        self.assertEqual(last.status_code, 429)
        body = last.content.decode()
        self.assertTrue('value="Vibe 5"' in body, 'typed title was thrown away')
        self.assertTrue(
            'value="A simple inventory system for small businesses."' in body,
            'typed description was thrown away')
        self.assertTrue('Nothing was lost' not in body,
                        'the page still claims nothing was lost')

    def test_the_upload_limit_explains_the_empty_picker(self):
        """The refusal carries a valid form, so `form.errors` is empty — the
        note has to key off the refusal, not off validation."""
        last = None
        for i in range(6):
            last = self.post(title=f'Vibe {i}', publish_token=f'{i}' * 32)
        self.assertEqual(last.status_code, 429)
        body = last.content.decode()
        self.assertTrue('id="zip-lost-note"' in body, 'no note about the lost ZIP')
        self.assertTrue('id="publish-errors"' not in body,
                        'a valid form must not show an error summary')

    def test_the_first_five_still_publish(self):
        for i in range(5):
            res = self.post(title=f'Vibe {i}', publish_token=f'{i}' * 32)
            self.assertEqual(res.status_code, 302, f'publish {i} was blocked')


class PublishUploadScriptTests(TestCase):
    """The browser half of the fix. publish.js is plain DOM code with no
    build step, so the contract is pinned on its source: navigate only when
    the server actually moved us, otherwise put the server's page on screen
    and hand the builder their file back."""

    def setUp(self):
        self.js = (STATIC / 'js' / 'publish.js').read_text()

    def test_no_longer_navigates_blindly(self):
        self.assertNotIn('if (xhr.responseURL) { window.location.href', self.js)

    def test_navigates_only_when_the_server_moved_the_page(self):
        self.assertIn('const movedAway = xhr.responseURL && xhr.responseURL !== here', self.js)
        self.assertIn('if (movedAway) { window.location.href = xhr.responseURL; return; }', self.js)
        # `here` must be the page the form lives on, captured before the POST.
        self.assertIn('const here = window.location.href;', self.js)

    def test_rejected_answer_is_put_on_screen(self):
        self.assertIn('swapDocument(xhr.responseText, files)', self.js)

    def test_uploaded_zip_is_handed_back(self):
        """A re-rendered form cannot carry a File; the script still holds the
        object, so a 100MB archive is not picked twice."""
        self.assertIn('newInput.files = dt.files', self.js)
        self.assertIn("document.getElementById('zip-lost-note')", self.js)

    def test_a_broken_upload_is_reported_not_blanked(self):
        self.assertIn('xhr.status >= 400', self.js)
        self.assertIn('Nothing was published', self.js)
        self.assertIn('xhr.ontimeout', self.js)
