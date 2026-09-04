"""Tests for the starter gallery + in-browser Studio (feature 2).

The Studio is an on-ramp for a beginner with nothing built: pick a starter,
edit it live client-side, publish through the ONE publish path. These tests
pin the two promises:
  * the gallery and studio load starters honestly (data, public, blank option);
  * publishing from Studio flows through the real publish path, so the result
    is a normal snippet AppProject with the user's edits — no shortcut around
    scan/classify/trust.
"""
from django.conf import settings
from django.test import TestCase, override_settings

from gallery.models import AppProject
from gallery.starters import STARTERS, STARTERS_BY_SLUG, get_starter

from .tests import make_category, make_user


class StarterDataTests(TestCase):
    def test_every_starter_is_self_contained_and_complete(self):
        for s in STARTERS:
            self.assertTrue(s['slug'])
            self.assertTrue(s['name'])
            self.assertTrue(s['html'].strip(), s['slug'])
            self.assertGreaterEqual(len(s['readme'].strip()), 100, s['slug'])
            self.assertIn('# ', s['readme'], s['slug'])

    def test_get_starter_returns_none_for_unknown(self):
        self.assertIsNone(get_starter('nope'))
        self.assertIsNone(get_starter(''))

    def test_slugs_are_unique(self):
        slugs = [s['slug'] for s in STARTERS]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertEqual(len(STARTERS_BY_SLUG), len(STARTERS))


class StarterGalleryViewTests(TestCase):
    def test_gallery_is_public_and_lists_starters(self):
        resp = self.client.get('/start/')
        self.assertEqual(resp.status_code, 200)
        for s in STARTERS:
            self.assertContains(resp, s['name'])
        self.assertContains(resp, 'Blank canvas')


class StudioViewTests(TestCase):
    def test_studio_loads_starter_into_editors(self):
        resp = self.client.get('/studio/hello-landing/')
        self.assertEqual(resp.status_code, 200)
        starter = get_starter('hello-landing')
        self.assertContains(resp, 'people vibing')
        self.assertEqual(resp.context['starter']['slug'], starter['slug'])

    def test_blank_studio_loads(self):
        resp = self.client.get('/studio/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['starter'])

    def test_unknown_starter_is_404(self):
        self.assertEqual(self.client.get('/studio/not-real/').status_code, 404)


class StudioPreviewLoginGateTests(TestCase):
    """Anonymous visitors can write; they cannot run a live preview.

    5 Whys — why these tests, not just a CSS assert?
    1. Why omit the iframe instead of hiding it? CSS hide is a fake gate;
       the HTML must not contain `#studio-frame` for anonymous GETs.
    2. Why still assert the three editors? "You can write" is the other
       half of the promise — a login wall on the whole Studio would fail it.
    3. Why assert the JS flag AND the missing element? Flipping
       `canPreview` in DevTools must not be enough; there is no iframe
       to assign srcdoc to.
    4. Why persist a draft in JS? Sign-in is a navigation; without
       sessionStorage the conversion deletes the work.
    5. Why honor `next` on signup? Dumping a new account on the feed
       looks like the code vanished.
    """

    def test_anonymous_can_write_but_has_no_preview_iframe(self):
        resp = self.client.get('/studio/hello-landing/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['can_preview'])
        self.assertContains(resp, 'id="ed-html"')
        self.assertContains(resp, 'id="ed-css"')
        self.assertContains(resp, 'id="ed-js"')
        self.assertContains(resp, 'people vibing')
        self.assertNotContains(resp, 'id="studio-frame"')
        self.assertNotContains(resp, 'sandbox="allow-scripts"')
        self.assertContains(resp, 'Sign in to run a live preview')
        self.assertContains(resp, 'canPreview: false')
        self.assertContains(resp, '/accounts/login/?next=/studio/hello-landing/')

    def test_blank_studio_same_gate(self):
        resp = self.client.get('/studio/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['can_preview'])
        self.assertContains(resp, 'id="ed-html"')
        self.assertNotContains(resp, 'id="studio-frame"')
        self.assertContains(resp, 'canPreview: false')

    def test_authenticated_gets_live_preview_iframe(self):
        user = make_user('studiopreview')
        self.client.force_login(user)
        resp = self.client.get('/studio/hello-landing/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['can_preview'])
        self.assertContains(resp, 'id="studio-frame"')
        self.assertContains(resp, 'sandbox="allow-scripts"')
        self.assertContains(resp, 'canPreview: true')
        self.assertContains(resp, 'Live preview · sandboxed')
        self.assertNotContains(resp, 'Sign in to run a live preview')

    def test_js_never_sets_srcdoc_without_canPreview_and_keeps_editors(self):
        js = (settings.BASE_DIR / 'static' / 'gallery' / 'js' / 'studio.js').read_text()
        self.assertIn('canPreview', js)
        self.assertIn('previewLive', js)
        self.assertIn('frame.srcdoc', js)
        self.assertIn('sessionStorage', js)
        self.assertIn('blaq-studio-draft', js)
        self.assertNotIn('if (!frame || !ed.html) return;', js)
        srcdoc_at = js.index('frame.srcdoc')
        gate_at = js.index('if (!previewLive) return;')
        self.assertLess(gate_at, srcdoc_at)

    def test_start_page_does_not_promise_anonymous_preview(self):
        resp = self.client.get('/start/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'edit it live with an instant preview')
        self.assertContains(resp, 'Sign in when you want to run the live preview')

    def test_login_form_keeps_studio_next(self):
        page = self.client.get('/accounts/login/?next=/studio/hello-landing/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="next"')
        self.assertContains(page, 'value="/studio/hello-landing/"')
        self.assertContains(page, '/accounts/signup/?next=/studio/hello-landing/')

    def test_signup_returns_to_studio_next(self):
        resp = self.client.post('/accounts/signup/?next=/studio/hello-landing/', {
            'username': 'studiofresh',
            'email': 'studiofresh@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
            'next': '/studio/hello-landing/',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, '/studio/hello-landing/')

    def test_signup_rejects_open_redirect(self):
        resp = self.client.post('/accounts/signup/', {
            'username': 'studioevil',
            'email': 'studioevil@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
            'next': 'https://evil.example/phish',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('evil.example', resp.url)

    def test_signup_rejects_protocol_relative_next(self):
        resp = self.client.post('/accounts/signup/', {
            'username': 'studioprotocol',
            'email': 'studioprotocol@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
            'next': '//evil.example/phish',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('evil.example', resp.url)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-studio-tests')
class StudioPublishTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.user = make_user('studiouser')
        self.client.force_login(self.user)

    def test_publish_from_studio_creates_snippet_project(self):
        resp = self.client.post('/studio/hello-landing/', {
            'title': 'My First Vibe',
            'category': self.cat.id,
            'short_description': 'A landing page I made in Studio.',
            'readme': '# My First Vibe\n\n' + ('Built in the BlaqVibes Studio from a starter. ' * 4),
            'tech_stack': 'HTML, CSS',
            'html_code': '<h1>Edited in studio</h1>',
            'css_code': 'h1{color:hotpink}',
            'js_code': 'console.log("mine")',
            'star_cost': 0,
            'price_zar': 0,
        })
        self.assertEqual(resp.status_code, 302)
        project = AppProject.objects.get(owner=self.user, title='My First Vibe')
        self.assertIn('Edited in studio', project.html_code)
        self.assertIn('hotpink', project.css_code)
        self.assertFalse(project.zip_file)
        self.assertEqual(project.owner, self.user)

    def test_publish_from_blank_studio(self):
        resp = self.client.post('/studio/', {
            'title': 'Blank Start Vibe',
            'category': self.cat.id,
            'short_description': 'Started from a blank canvas.',
            'readme': '# Blank Start Vibe\n\n' + ('Made from scratch in the Studio blank canvas. ' * 4),
            'tech_stack': 'HTML',
            'html_code': '<main>hello world</main>',
            'css_code': 'main{padding:20px}',
            'js_code': '',
            'star_cost': 0,
            'price_zar': 0,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(AppProject.objects.filter(owner=self.user, title='Blank Start Vibe').exists())

    def test_studio_publish_requires_login(self):
        self.client.logout()
        resp = self.client.post('/studio/hello-landing/', {
            'title': 'Should Not Publish',
            'category': self.cat.id,
            'short_description': 'x',
            'readme': '# x\n\n' + ('y ' * 60),
            'html_code': '<h1>x</h1>',
            'star_cost': 0,
            'price_zar': 0,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)
        self.assertFalse(AppProject.objects.filter(title='Should Not Publish').exists())


class StudioDrawerDismissTests(TestCase):
    """The publish drawer must appear only when asked for, and always close.

    Regression: `.studio-drawer { display: grid }` is an author-origin rule,
    and the browser's `display:none` for the `hidden` attribute lives in the
    UA stylesheet — author always beats UA. So the drawer rendered over the
    whole Studio page from the moment it loaded, and its × button looked
    broken because setting `drawer.hidden = true` changed nothing. Two
    invariants keep that from coming back:
      * the stylesheet keeps a `[hidden]` guard for every element studio.js
        toggles by setting `.hidden`;
      * the drawer markup ships `hidden` plus two obvious ways out (× and a
        Cancel button), and the page's JS closes on both and on Escape.
    """

    TOGGLED_BY_JS = ('.studio-drawer', '.studio-nolo', '.studio-code')

    @staticmethod
    def _css_rules(text):
        """Minimal selector -> declarations map. Enough for one flat file."""
        import re
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        return [
            ([s.strip() for s in m.group(1).split(',')], m.group(2))
            for m in re.finditer(r'([^{}]+)\{([^{}]*)\}', text)
        ]

    def test_display_rules_on_js_toggled_elements_have_a_hidden_guard(self):
        css = (settings.BASE_DIR / 'static' / 'gallery' / 'css' / 'studio.css').read_text()
        rules = self._css_rules(css)
        for selector in self.TOGGLED_BY_JS:
            sets_display = any(
                selector in sels and '[hidden]' not in s and 'display' in body
                for sels, body in rules for s in sels
            )
            if not sets_display:
                continue
            guarded = any(
                (selector + '[hidden]') in sels and 'display: none' in body.replace(' ', ' ')
                for sels, body in rules
            )
            self.assertTrue(
                guarded,
                f'{selector} sets display but has no `{selector}[hidden] {{ display: none }}` '
                f'guard, so it will ignore the hidden attribute and cover the page.',
            )

    def test_drawer_starts_hidden_with_two_ways_out(self):
        html = self.client.get('/studio/hello-landing/').content.decode()
        open_tag = html[html.index('id="studio-publish"'):].split('>')[0]
        self.assertIn('hidden', open_tag)
        self.assertIn('role="dialog"', open_tag)
        self.assertIn('aria-modal="true"', open_tag)
        self.assertEqual(html.count('data-close-drawer'), 2, html.count('data-close-drawer'))
        self.assertContains(self.client.get('/studio/hello-landing/'), 'Cancel — keep editing')

    def test_blank_studio_drawer_also_starts_hidden(self):
        html = self.client.get('/studio/').content.decode()
        open_tag = html[html.index('id="studio-publish"'):].split('>')[0]
        self.assertIn('hidden', open_tag)

    def test_studio_js_closes_the_drawer_on_cancel_and_escape(self):
        js = (settings.BASE_DIR / 'static' / 'gallery' / 'js' / 'studio.js').read_text()
        self.assertIn("querySelectorAll('[data-close-drawer]')", js)
        self.assertIn("'Escape'", js)
