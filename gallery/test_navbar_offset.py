"""Regression tests for the global navbar/content-overlap fix.

Root cause (Sep 2026): the mobile header was ``position: fixed`` with a
``body{padding-top:64px}`` compensation written for the old single-row bar.
When the bar grew a second pill row (~111px tall), ~47px of every page's
first heading/hero sat hidden behind the nav on phones.

The architecture this module pins:

* Mobile nav is ``position: sticky`` (in normal flow) — it reserves exactly
  its own height, so content can never start underneath it and no body
  padding compensation exists to drift out of sync.
* One token — ``--bv-nav-h``, measured live by blaqvibes.js — feeds every
  sticky/anchored/full-height offset (filter bar, :target, battle + publish
  shells, launch anchors). No stylesheet hard-codes the nav height.
* Desktop keeps the fixed left rail + ``body{padding-left}`` (a vertical
  rail cannot cover the top of the page).
"""
import re
from pathlib import Path

from django.test import TestCase, override_settings

from .tests import make_category, make_project, make_user

STATIC = Path(__file__).resolve().parent.parent / 'static' / 'gallery'


def css(name):
    """Stylesheet text with /* … */ comments stripped (comments legitimately
    name the old values they replaced — only live rules count)."""
    return re.sub(r'/\*.*?\*/', '', (STATIC / 'css' / name).read_text(), flags=re.S)


def mobile_block(body):
    """The `@media (max-width: 900px)` block that owns the mobile nav."""
    start = body.index('@media (max-width: 900px)')
    depth = 0
    for i, ch in enumerate(body[start:]):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return body[start:start + i + 1]
    raise AssertionError('unbalanced braces in blaqvibes.css')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class NavbarOffsetTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.user = make_user('offsetter')

    # -- Architecture: sticky in-flow bar, zero compensation ----------------

    def test_mobile_nav_is_sticky_not_fixed(self):
        block = mobile_block(css('blaqvibes.css'))
        nav_rule = re.search(r'\.nav\s*\{([^}]*)\}', block).group(1)
        self.assertIn('position: sticky', nav_rule)
        self.assertNotIn('position: fixed', nav_rule)

    def test_no_body_padding_compensation_for_the_nav(self):
        # A sticky in-flow bar reserves its own height; any body padding-top
        # "fix" would double-space every page the moment the bar's real
        # height differs from the hard-coded value (the original bug).
        self.assertNotRegex(css('blaqvibes.css'), r'body\s*\{\s*padding-top')

    def test_nav_height_token_exists_with_mobile_fallback(self):
        base = css('blaqvibes.css')
        self.assertIn('--bv-nav-h: 0px', base)  # desktop: left rail, no top bar
        self.assertRegex(mobile_block(base), r'--bv-nav-h:\s*\d+px')  # no-JS fallback

    def test_filter_bar_stick_point_derives_from_the_token(self):
        rule = re.search(r'\.filter-bar\s*\{([^}]*)\}', css('blaqvibes.css')).group(1)
        self.assertIn('top: calc(var(--bv-nav-h, 0px) + 12px)', rule)
        self.assertIn('scroll-margin-top: calc(var(--bv-nav-h, 0px) + 12px)', rule)

    def test_feed_css_does_not_set_a_competing_stick_point(self):
        rule = re.search(r'\.filter-bar\{([^}]*)\}', css('feed.css')).group(1)
        self.assertNotRegex(rule, r'(^|;)\s*top:')
        self.assertNotRegex(rule, r'(^|;)\s*position:')

    def test_no_stylesheet_hard_codes_the_old_64px_nav(self):
        for name in ('blaqvibes.css', 'feed.css', 'battle.css', 'publish.css', 'launch.css'):
            with self.subTest(sheet=name):
                body = css(name)
                self.assertNotRegex(body, r'(top|padding-top|scroll-margin-top):\s*64px')
                self.assertNotRegex(body, r'-\s*64px\)')

    def test_full_height_shells_subtract_the_measured_bar(self):
        for name in ('battle.css', 'publish.css'):
            self.assertIn('var(--bv-nav-h, 112px)', css(name))

    def test_js_measures_the_bar_and_clears_it_on_desktop(self):
        js = (STATIC / 'js' / 'blaqvibes.js').read_text()
        self.assertIn('--bv-nav-h', js)
        self.assertIn('ResizeObserver', js)
        self.assertIn("querySelector('.nav')", js)
        self.assertIn('offsetHeight', js)

    def test_no_important_in_nav_offset_rules(self):
        for name in ('blaqvibes.css', 'feed.css', 'battle.css', 'publish.css'):
            for match in re.finditer(r'--bv-nav-h[^;]*;', css(name)):
                self.assertNotIn('!important', match.group(0))

    # -- Behaviour: nav precedes content on every key page -------------------

    def _nav_precedes_main(self, url):
        body = self.client.get(url).content.decode()
        nav_at = body.index('<nav class="nav"')
        main_at = body.index('<main id="main">')
        self.assertLess(nav_at, main_at, f'{url}: nav must precede main in the DOM')
        return body

    def test_key_pages_render_with_nav_above_content_anonymous(self):
        for url in ('/', '/discover/', '/build/', '/challenges/', '/skills/',
                    '/launch/', '/battle/', '/accounts/login/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
                self._nav_precedes_main(url)

    def test_key_pages_render_with_nav_above_content_logged_in(self):
        self.client.login(username='offsetter', password='pass12345')
        project = make_project(self.user, self.cat, status='published')
        for url in ('/', '/discover/', '/build/', '/my-vibes/', '/settings/',
                    '/publish/', project.get_absolute_url(),
                    f'/u/{self.user.username}/'):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertLess(body.index('<nav class="nav"'), body.index('<main id="main">'))
