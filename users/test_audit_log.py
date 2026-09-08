"""Audit Log page — layout and rendering regressions.

The Trading History column used to be cut off on phones: the two-column grid
was an inline `grid-template-columns:1.2fr .8fr` (which the global mobile
catch-all never matched) with `1fr` tracks whose floor is the longest
unbreakable word. A single long username or CamelCase project title widened the
grid to ~1200px, and because html/body clip horizontal overflow the right
column was simply unreachable. These tests pin the contract that fixes it:

* the page is class-driven (no inline grid the stylesheet cannot reach),
* every grid track is minmax(0, …) and children are min-width:0,
* long content is allowed to wrap anywhere,
* the columns stack at the mobile breakpoint,
* the trade card is labelled Buyer / Seller / Date rows, not one rigid line,
* nothing is truncated or hidden to make it fit,
* a trade whose buyer/seller account was deleted still renders (this used to
  raise NoReverseMatch, which the role decorator turned into a 403 page).
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings

from gallery.models import Trade
from gallery.tests import make_category, make_project, make_user

from .models import AdminLog

CSS_PATH = Path(settings.BASE_DIR) / 'static' / 'gallery' / 'css' / 'audit-log.css'

LONG_USER = 'very_long_username_for_testing_mobile_layout'
LONG_SELLER = 'another_very_long_username'
LONG_TITLE = 'AfricanCreativeTechnologyAndInnovationPlatformWebsiteConcept'
LONG_ACTION = 'ExtremelyLongAuditActionNameForTesting'
LONG_TARGET = ('@very_long_creator_username_that_would_normally_break_the_layout: '
               'user→moderator (someone.with.a.really.long.email@example-company.co.za)')


def _rule(css, selector):
    """Return the declaration block for an exact selector (first match)."""
    m = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', css)
    return m.group(1) if m else ''


def _media_block(css, query):
    m = re.search(r'@media\s*\(' + re.escape(query) + r'\)\s*\{(.*?)\n\}', css, re.S)
    return m.group(1) if m else ''


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-audit', SEED_DEMO=False)
class AuditLogPageTests(TestCase):
    def setUp(self):
        self.admin = make_user('auditadmin', role='admin')
        self.buyer = make_user(LONG_USER)
        self.seller = make_user(LONG_SELLER)
        self.cat = make_category()
        self.project = make_project(self.seller, self.cat, slug='long-title', title=LONG_TITLE)
        Trade.objects.create(buyer=self.buyer, seller=self.seller, project=self.project, cost=25)
        AdminLog.objects.create(actor=self.admin, action=LONG_ACTION, target=LONG_TARGET)
        self.client.force_login(self.admin)

    def page(self):
        resp = self.client.get('/admin/audit/')
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_page_uses_the_class_driven_layout_not_an_inline_grid(self):
        body = self.page()
        self.assertIn('gallery/css/audit-log.css', body)
        self.assertIn('class="audit-page"', body)
        self.assertIn('class="audit-layout"', body)
        self.assertEqual(body.count('class="audit-section"'), 2)
        # The offending inline grid must never come back on this page.
        main = body.split('<main id="main">', 1)[1].split('</main>', 1)[0]
        self.assertNotIn('grid-template-columns', main)
        self.assertNotIn('white-space:nowrap', main)
        self.assertNotIn('width:32px', main)  # no fixed inline widths left on the cards

    def test_long_data_is_rendered_in_full_not_truncated(self):
        body = self.page()
        for text in (LONG_USER, LONG_SELLER, LONG_TITLE, LONG_ACTION):
            self.assertIn(text, body)
        # target contains → and an email; both must be present after escaping.
        self.assertIn('user→moderator', body)
        self.assertIn('someone.with.a.really.long.email@example-company.co.za', body)

    def test_trade_card_is_labelled_rows_not_one_rigid_line(self):
        body = self.page()
        self.assertIn('<dl class="trade-entry-content">', body)
        for label in ('<dt>Buyer</dt>', '<dt>Seller</dt>', '<dt>Date</dt>'):
            self.assertIn(label, body)
        self.assertIn(f'href="/u/{LONG_USER}/"', body)
        self.assertIn(f'href="/u/{LONG_SELLER}/"', body)
        self.assertIn('<span class="project-name">' + LONG_TITLE + '</span>', body)
        self.assertIn('<span class="trade-cost">25 ★</span>', body)
        # The old "@buyer → @seller • date" single-line format is gone.
        self.assertNotRegex(body, r'</a>\s*→\s*<a')

    def test_sections_are_landmarks_with_headings_for_screen_readers(self):
        body = self.page()
        self.assertIn('aria-labelledby="audit-actions-heading"', body)
        self.assertIn('id="audit-actions-heading"', body)
        self.assertIn('aria-labelledby="audit-trades-heading"', body)
        self.assertIn('id="audit-trades-heading"', body)
        self.assertIn('<article class="audit-entry">', body)
        self.assertIn('<article class="trade-entry">', body)
        self.assertIn('<time datetime="', body)

    def test_view_my_trades_button_is_a_full_width_column_child(self):
        body = self.page()
        self.assertIn('class="btn-violet audit-cta"', body)
        self.assertIn('View My Trades', body)

    def test_empty_states_still_render(self):
        Trade.objects.all().delete()
        AdminLog.objects.all().delete()
        body = self.page()
        self.assertIn('No admin actions yet.', body)
        self.assertIn('No trades yet.', body)

    def test_trade_with_deleted_buyer_or_seller_still_renders(self):
        """Trade.buyer/seller are SET_NULL. The old template reversed
        profile_view with '' and crashed — the decorator then served a 403."""
        Trade.objects.create(buyer=None, seller=self.seller, project=self.project, cost=3)
        Trade.objects.create(buyer=self.buyer, seller=None, project=self.project, cost=4)
        AdminLog.objects.create(actor=None, action='repair_superadmin', target='@admin')
        body = self.page()
        self.assertGreaterEqual(body.count('deleted account'), 3)


class AuditLogStylesheetTests(TestCase):
    """The CSS contract that keeps the page inside a 320px viewport."""

    def setUp(self):
        self.css = CSS_PATH.read_text(encoding='utf-8')

    def test_desktop_grid_tracks_are_minmax_zero(self):
        layout = _rule(self.css, '.audit-layout')
        self.assertIn('display: grid', layout)
        self.assertRegex(layout, r'grid-template-columns:\s*minmax\(0,\s*1\.2fr\)\s+minmax\(0,\s*\.8fr\)')
        self.assertIn('min-width: 0', _rule(self.css, '.audit-layout > *'))

    def test_columns_stack_at_the_mobile_breakpoint(self):
        mobile = _media_block(self.css, 'max-width: 760px')
        self.assertTrue(mobile, 'expected a max-width: 760px media query')
        self.assertRegex(_rule(mobile, '.audit-layout'), r'grid-template-columns:\s*minmax\(0,\s*1fr\)')
        # Trade fields go label-over-value on phones.
        self.assertRegex(_rule(mobile, '.trade-entry-content'), r'grid-template-columns:\s*minmax\(0,\s*1fr\)')

    def test_cards_and_content_cannot_exceed_their_container(self):
        cards = _rule(self.css, '.audit-entry,\n.trade-entry,\n.audit-empty')
        self.assertIn('box-sizing: border-box', cards)
        self.assertIn('min-width: 0', cards)
        self.assertIn('max-width: 100%', cards)
        content = _rule(self.css, '.audit-entry-content,\n.trade-entry-content')
        self.assertIn('min-width: 0', content)
        self.assertIn('max-width: 100%', content)
        page = _rule(self.css, '.audit-page')
        self.assertIn('width: 100%', page)
        self.assertIn('box-sizing: border-box', page)

    def test_long_words_may_wrap_anywhere(self):
        self.assertIn('overflow-wrap: anywhere', _rule(self.css, '.audit-entry, .audit-entry *,\n.trade-entry, .trade-entry *'))
        self.assertIn('overflow-wrap: anywhere', _rule(self.css, '.username,\n.audit-target,\n.audit-action,\n.project-name'))

    def test_button_never_widens_the_column(self):
        cta = _rule(self.css, '.audit-section .audit-cta')
        self.assertIn('width: 100%', cta)
        self.assertIn('max-width: 100%', cta)
        self.assertIn('box-sizing: border-box', cta)

    def test_no_horizontal_scroll_workaround_and_no_fixed_mobile_widths(self):
        self.assertNotRegex(self.css, r'overflow-x:\s*(auto|scroll)')
        self.assertNotRegex(self.css, r'overflow:\s*(auto|scroll)')
        # Nothing may be hidden to make it fit.
        self.assertNotIn('text-overflow', self.css)
        self.assertNotRegex(self.css, r'white-space:\s*nowrap')
        # No fixed pixel widths other than the 32px avatar tile.
        widths = set(re.findall(r'(?<![-\w])(?:min-)?width:\s*(\d+)px', self.css))
        self.assertLessEqual(widths, {'32'}, widths)

    def test_theme_colours_are_variables_only(self):
        self.assertNotRegex(self.css, r'#[0-9a-fA-F]{3,8}\b')
        self.assertNotRegex(self.css, r'rgba?\(')
