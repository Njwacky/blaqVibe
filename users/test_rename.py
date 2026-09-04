"""PUBG-style rename rules + name styling — every gate tested.

This file mirrors the rules rather than the views: the money and identity
walls live in users.rename. Testing them directly (plus a thin end-to-end
pass through the views) means an admin tool or future API that reuses
rename_user inherits the same guarantees the tests prove.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from users.forms import NameStyleForm, RenameForm, SignUpForm
from users.models import (
    NAME_COLORS,
    NAME_FONTS,
    NAME_FX,
    NAME_PERSONAS,
    NAME_SIZES,
    Profile,
    RENAME_COOLDOWN_DAYS,
    RENAME_COST_STARS,
    RENAME_RESERVE_DAYS,
    STYLE_COST_STARS,
    StarEvent,
    UsernameHistory,
    compose_name_style,
    people_style_slugs,
)
from users.rename import (
    RESERVED_USERNAMES,
    RenameError,
    cooldown_remaining,
    rename_user,
    set_name_style,
)

def make_user(username, stars=0, pro=False, **profile_kwargs):
    """Create a user whose starting balance is LEDGERED.

    The balance isn't just set directly: wallet_reconciles() proves
    balance == sum of ledger rows, and a test wallet minted out of thin air
    would break the invariant these tests protect. Every star a test user
    holds arrives the way real ones do — as a ledger row.
    """
    user = User.objects.create_user(username, password='pass12345', email=f'{username}@test.com')
    profile = user.profile
    if stars:
        profile.stars_balance = stars
        profile.save(update_fields=['stars_balance'])
        StarEvent.objects.create(
            user=user, delta=stars, reason='backfill', ref=f'test-setup:{username}'
        )
    if pro:
        profile.is_pro = True
        profile.pro_since = timezone.now()
        profile.save(update_fields=['is_pro', 'pro_since'])
    for key, value in profile_kwargs.items():
        setattr(profile, key, value)
        profile.save(update_fields=[key])
    return user

def top_up(user, stars):
    """Add spendable stars the ledgered way (cooldown tests need a second card)."""
    profile = user.profile
    Profile.objects.filter(pk=profile.pk).update(stars_balance=profile.stars_balance + stars)
    StarEvent.objects.create(user=user, delta=stars, reason='backfill', ref='test-topup')

@override_settings(RATELIMIT_ENABLE=False)
class RenameRuleTests(TestCase):
    def setUp(self):
        self.user = make_user('coder', stars=RENAME_COST_STARS)

    # the happy paths

    def test_non_pro_pays_stars_and_burns_them(self):
        history = rename_user(self.user, 'coderprime')
        self.assertEqual(self.user.username, 'coderprime')
        self.assertEqual(history.method, 'stars')
        self.assertEqual(history.cost_stars, RENAME_COST_STARS)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.stars_balance, 0)
        burn = StarEvent.objects.filter(user=self.user, reason='rename_spend').get()
        self.assertEqual(burn.delta, -RENAME_COST_STARS)
        self.assertIn('coder', burn.ref)
        self.assertIn('coderprime', burn.ref)
        # Burned, not moved: no other account received anything.
        self.assertFalse(
            StarEvent.objects.exclude(user=self.user).filter(reason='trade_earn').exists()
        )

    def test_pro_gets_free_rename_card(self):
        self.user.profile.is_pro = True
        self.user.profile.pro_since = timezone.now()
        self.user.profile.save()
        history = rename_user(self.user, 'coderprime')
        self.assertEqual(history.method, 'pro')
        self.assertEqual(history.cost_stars, 0)
        self.assertFalse(StarEvent.objects.filter(reason='rename_spend').exists())

    def test_wallet_still_reconciles_after_rename(self):
        from users.wallet import ledger_balance
        rename_user(self.user, 'coderprime')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.stars_balance, ledger_balance(self.user))

    # the money wall

    def test_welcome_grant_cannot_afford_a_card(self):
        """5 ★ welcome < 100 ★ card — a throwaway farm renames nothing."""
        poor = make_user('poor', stars=5)
        with self.assertRaises(RenameError) as ctx:
            rename_user(poor, 'poorprime')
        self.assertIn('Pro', ctx.exception.message)
        self.assertEqual(poor.username, 'poor')
        poor.profile.refresh_from_db()
        self.assertEqual(poor.profile.stars_balance, 5)  # nothing charged
        self.assertFalse(StarEvent.objects.filter(user=poor, reason='rename_spend').exists())

    # the cooldown wall (PUBG rule)

    def test_cooldown_blocks_second_rename_even_with_stars_and_pro(self):
        rename_user(self.user, 'coderprime')
        self.user.profile.is_pro = True
        self.user.profile.stars_balance = 9999
        self.user.profile.save(update_fields=['is_pro', 'stars_balance'])
        with self.assertRaises(RenameError) as ctx:
            rename_user(self.user, 'coderx')
        self.assertIn('cooldown', ctx.exception.message.lower())
        self.assertEqual(self.user.username, 'coderprime')
        # Exactly one ledger burn — the blocked attempt charged nothing.
        self.assertEqual(
            StarEvent.objects.filter(user=self.user, reason='rename_spend').count(), 1
        )

    def test_cooldown_expires_after_thirty_days(self):
        rename_user(self.user, 'coderprime')
        Profile.objects.filter(user=self.user).update(
            last_rename_at=timezone.now() - timedelta(days=RENAME_COOLDOWN_DAYS + 1)
        )
        top_up(self.user, RENAME_COST_STARS)  # a second card, ledgered
        history = rename_user(self.user, 'coderx')  # own old name 'coder' also freed
        self.assertEqual(history.new_username, 'coderx')

    def test_cooldown_remaining_is_none_before_first_rename(self):
        self.assertIsNone(cooldown_remaining(self.user.profile))

    # impersonation walls

    def test_cannot_take_existing_name_case_insensitive(self):
        other = make_user('Nolo')
        with self.assertRaises(RenameError) as ctx:
            rename_user(self.user, 'nolo')
        self.assertIn('taken', ctx.exception.message)

    def test_reserved_words_blocked(self):
        for word in ('admin', 'support', 'blaqvibes', 'Nolo'.lower()):
            with self.assertRaises(RenameError, msg=word):
                rename_user(self.user, word)
        self.assertTrue(RESERVED_USERNAMES)  # the list itself is wired

    def test_old_username_reserved_for_others(self):
        rename_user(self.user, 'coderprime')
        stranger = make_user('stranger', stars=RENAME_COST_STARS)
        with self.assertRaises(RenameError) as ctx:
            rename_user(stranger, 'coder')
        self.assertIn('reserved', ctx.exception.message)
        self.assertEqual(stranger.username, 'stranger')
        self.assertEqual(
            StarEvent.objects.filter(user=stranger, reason='rename_spend').count(), 0
        )

    def test_reservation_expires_after_ninety_days(self):
        rename_user(self.user, 'coderprime')
        UsernameHistory.objects.update(
            created_at=timezone.now() - timedelta(days=RENAME_RESERVE_DAYS + 1)
        )
        stranger = make_user('stranger', stars=RENAME_COST_STARS)
        history = rename_user(stranger, 'coder')  # freed after the window
        self.assertEqual(history.new_username, 'coder')

    def test_owner_can_reclaim_own_old_name_after_cooldown(self):
        rename_user(self.user, 'coderprime')
        Profile.objects.filter(user=self.user).update(
            last_rename_at=timezone.now() - timedelta(days=RENAME_COOLDOWN_DAYS + 1)
        )
        top_up(self.user, RENAME_COST_STARS)
        rename_user(self.user, 'coder')  # own reservation exempts self
        self.assertEqual(self.user.username, 'coder')

    # validation walls

    def test_same_name_rejected(self):
        with self.assertRaises(RenameError):
            rename_user(self.user, 'coder')
        with self.assertRaises(RenameError):
            rename_user(self.user, 'CODER')

    def test_bad_charset_rejected(self):
        for bad in ('has space', 'no<script>', 'a' * 200, 'ab'):
            with self.assertRaises(RenameError, msg=bad):
                rename_user(self.user, bad)

    def test_profanity_rejected(self):
        with self.assertRaises(RenameError):
            rename_user(self.user, 'fucktool')

    def test_failed_rename_leaves_no_history_row(self):
        with self.assertRaises(RenameError):
            rename_user(self.user, 'admin')
        self.assertFalse(UsernameHistory.objects.exists())

    # link-rot fix

    def test_old_profile_url_redirects_to_new(self):
        rename_user(self.user, 'coderprime')
        response = self.client.get('/u/coder/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].endswith('/u/coderprime/'))

    def test_chained_rename_redirects_to_live_username(self):
        rename_user(self.user, 'coderprime')
        Profile.objects.filter(user=self.user).update(
            last_rename_at=timezone.now() - timedelta(days=RENAME_COOLDOWN_DAYS + 1)
        )
        top_up(self.user, RENAME_COST_STARS)
        rename_user(self.user, 'coderx')
        # The FIRST old name must land on the live username, not the stale B.
        response = self.client.get('/u/coder/')
        self.assertTrue(response['Location'].endswith('/u/coderx/'))

    def test_unknown_username_still_404s(self):
        self.assertEqual(self.client.get('/u/ghost-user/').status_code, 404)

    # the views end-to-end

    def test_rename_view_charges_and_redirects(self):
        self.client.login(username='coder', password='pass12345')
        response = self.client.post('/settings/rename/', {'new_username': 'coderprime'})
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'coderprime')

    def test_rename_view_rejects_invalid_form(self):
        self.client.login(username='coder', password='pass12345')
        response = self.client.post('/settings/rename/', {'new_username': 'bad name!'})
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'coder')

    def test_edit_profile_page_shows_identity_panels(self):
        """Identity editing lives on Edit Profile ("my profile"), not Settings."""
        self.client.login(username='coder', password='pass12345')
        response = self.client.get('/settings/profile/')
        self.assertContains(response, 'rename card')
        self.assertContains(response, 'Name style')
        self.assertContains(response, 'People-style')
        for label in ('Coder', 'Glamour', 'Charmer', 'Strict'):
            self.assertContains(response, label)

    def test_settings_page_no_longer_hosts_identity_panels(self):
        """Settings is toggles-only — and the card grid is gone everywhere:
        the people-style is one dropdown list with a live styles display."""
        self.client.login(username='coder', password='pass12345')
        response = self.client.get('/settings/')
        self.assertNotContains(response, 'People-style')
        self.assertNotContains(response, 'persona-card')
        self.assertNotContains(response, 'persona-grid')
        response = self.client.get('/settings/profile/')
        self.assertNotContains(response, 'persona-card')
        self.assertContains(response, 'name-style-preview')

    def test_signup_reserved_username_blocked(self):
        form = SignUpForm(data={
            'username': 'support',
            'email': 's@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)

@override_settings(RATELIMIT_ENABLE=False)
class NameStyleTests(TestCase):
    def setUp(self):
        self.user = make_user('styler', stars=STYLE_COST_STARS)

    def test_defaults_render_plain(self):
        profile = self.user.profile
        self.assertEqual(profile.name_style_css(), '')
        self.assertEqual(profile.name_style_classes(), '')

    def test_non_pro_pays_per_change(self):
        profile, changed = set_name_style(self.user, 'grotesk', 'gold', 'xl', 'shine')
        self.assertTrue(changed)
        profile.refresh_from_db()
        self.assertEqual(profile.stars_balance, 0)
        burn = StarEvent.objects.get(user=self.user, reason='style_spend')
        self.assertEqual(burn.delta, -STYLE_COST_STARS)

    def test_pro_styles_free(self):
        self.user.profile.is_pro = True
        self.user.profile.pro_since = timezone.now()
        self.user.profile.save()
        _, changed = set_name_style(self.user, 'mono', 'rainbow', 'lg', 'chroma')
        self.assertTrue(changed)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.stars_balance, STYLE_COST_STARS)  # untouched
        self.assertFalse(StarEvent.objects.filter(reason='style_spend').exists())

    def test_reset_to_default_is_free(self):
        set_name_style(self.user, 'grotesk', 'gold', 'xl', 'shine')  # paid
        self.user.profile.stars_balance = 0
        self.user.profile.save(update_fields=['stars_balance'])
        profile, changed = set_name_style(self.user, 'classic', 'default', 'md', 'none')
        self.assertTrue(changed)
        profile.refresh_from_db()
        self.assertEqual(profile.stars_balance, 0)  # free, even with 0 ★

    def test_same_style_is_free_no_op(self):
        set_name_style(self.user, 'grotesk', 'gold', 'lg', 'glow')
        self.user.profile.refresh_from_db()
        balance = self.user.profile.stars_balance
        _, changed = set_name_style(self.user, 'grotesk', 'gold', 'lg', 'glow')
        self.assertFalse(changed)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.stars_balance, balance)

    def test_welcome_grant_cannot_afford_styling(self):
        poor = make_user('poor2', stars=5)
        with self.assertRaises(RenameError):
            set_name_style(poor2 := poor, 'grotesk', 'gold', 'md', 'none')

    def test_unknown_slugs_coerce_to_safe_defaults(self):
        profile, _changed = set_name_style(
            self.user, 'papyrus', 'hotpink', 'xxl', 'matrix', persona='not-a-person',
        )
        # Unknown slugs are STORED as defaults — a tampered POST buys nothing.
        self.assertEqual(profile.name_font, 'classic')
        self.assertEqual(profile.name_color, 'default')
        self.assertEqual(profile.name_size, 'md')
        self.assertEqual(profile.name_fx, 'none')
        self.assertEqual(profile.name_persona, 'classic')

    def test_tampered_db_value_renders_plain(self):
        """The renderer must survive a bad slug, not echo it."""
        Profile.objects.filter(user=self.user).update(name_font='";color:red')
        profile = self.user.profile
        profile.refresh_from_db()
        self.assertEqual(profile.name_style_css(), '')
        self.assertEqual(profile.name_style_classes(), '')

    def test_rendered_css_only_contains_whitelisted_values(self):
        profile, _ = set_name_style(self.user, 'mono', 'cyan', 'lg', 'glow')
        css = profile.name_style_css()
        self.assertIn('JetBrains Mono', css)
        self.assertIn('#22d3ee', css)
        self.assertNotIn('url(', css)
        self.assertIn('name-size-lg', profile.name_style_classes())
        self.assertIn('namefx-glow', profile.name_style_classes())

    def test_style_form_rejects_off_whitelist_value(self):
        form = NameStyleForm(data={
            'name_font': 'comic-sans-custom',
            'name_color': 'default',
            'name_size': 'md',
            'name_fx': 'none',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('name_font', form.errors)

    def test_rename_form_rejects_reserved_charset(self):
        form = RenameForm(data={'new_username': 'not allowed!'})
        self.assertFalse(form.is_valid())

    def test_style_view_end_to_end(self):
        self.client.login(username='styler', password='pass12345')
        response = self.client.post('/settings/name-style/', {
            'name_font': 'grotesk',
            'name_color': 'rainbow',
            'name_size': 'xl',
            'name_fx': 'shine',
        })
        self.assertEqual(response.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.name_fx, 'shine')
        self.assertIn('namefx-rainbow', self.user.profile.name_style_classes())

    def test_styled_name_renders_on_profile_page(self):
        set_name_style(self.user, 'grotesk', 'rainbow', 'xl', 'shine')
        response = self.client.get('/u/styler/')
        self.assertContains(response, 'namefx-rainbow')
        self.assertContains(response, 'namefx-shine')
        self.assertContains(response, 'name-size-xl')

    def test_there_are_exactly_twenty_people_styles(self):
        slugs = people_style_slugs()
        self.assertEqual(len(slugs), 20)
        self.assertEqual(len(set(slugs)), 20)
        self.assertIn('classic', NAME_PERSONAS)
        self.assertNotIn('classic', slugs)
        for required in ('coder', 'glamour', 'charmer', 'strict'):
            self.assertIn(required, slugs)

    def test_every_people_style_recipe_is_on_whitelist(self):
        for slug, meta in NAME_PERSONAS.items():
            self.assertIn(meta['font'], NAME_FONTS, slug)
            self.assertIn(meta['color'], NAME_COLORS, slug)
            self.assertIn(meta['size'], NAME_SIZES, slug)
            self.assertIn(meta['fx'], NAME_FX, slug)
            self.assertTrue(meta['label'], slug)
            self.assertTrue(meta['blurb'], slug)
            if slug == 'classic':
                self.assertEqual(meta['cls'], '')
            else:
                self.assertEqual(meta['cls'], f'namepersona-{slug}')

    def test_coder_people_style_stores_recipe_and_class(self):
        profile, changed = set_name_style(
            self.user, 'classic', 'default', 'md', 'none', persona='coder',
        )
        self.assertTrue(changed)
        profile.refresh_from_db()
        self.assertEqual(profile.name_persona, 'coder')
        self.assertEqual(profile.name_font, 'mono')
        self.assertEqual(profile.name_color, 'cyan')
        self.assertEqual(profile.name_size, 'md')
        self.assertEqual(profile.name_fx, 'glow')
        self.assertIn('namepersona-coder', profile.name_style_classes())
        self.assertIn('JetBrains Mono', profile.name_style_css())
        self.assertIn('#22d3ee', profile.name_style_css())
        self.assertEqual(profile.stars_balance, 0)
        burn = StarEvent.objects.get(user=self.user, reason='style_spend')
        self.assertIn('coder', burn.ref)

    def test_js_off_people_style_post_applies_recipe(self):
        """No-JS: radio=glamour, dropdowns still default → store the recipe."""
        self.client.login(username='styler', password='pass12345')
        response = self.client.post('/settings/name-style/', {
            'name_persona': 'glamour',
            'name_font': 'classic',
            'name_color': 'default',
            'name_size': 'md',
            'name_fx': 'none',
        })
        self.assertEqual(response.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.name_persona, 'glamour')
        self.assertEqual(self.user.profile.name_font, 'serif')
        self.assertEqual(self.user.profile.name_color, 'gold')
        self.assertEqual(self.user.profile.name_fx, 'shine')
        self.assertIn('namepersona-glamour', self.user.profile.name_style_classes())

    def test_fine_tune_that_leaves_the_recipe_clears_persona(self):
        set_name_style(self.user, 'classic', 'default', 'md', 'none', persona='coder')
        self.user.profile.stars_balance = STYLE_COST_STARS
        self.user.profile.save(update_fields=['stars_balance'])
        StarEvent.objects.create(
            user=self.user, delta=STYLE_COST_STARS, reason='backfill', ref='test-retune',
        )
        profile, changed = set_name_style(
            self.user, 'grotesk', 'gold', 'xl', 'shine', persona='coder',
        )
        self.assertTrue(changed)
        profile.refresh_from_db()
        self.assertEqual(profile.name_persona, 'classic')
        self.assertEqual(profile.name_font, 'grotesk')
        self.assertEqual(profile.name_color, 'gold')
        self.assertNotIn('namepersona-coder', profile.name_style_classes())

    def test_matching_recipe_keeps_the_people_style(self):
        recipe = NAME_PERSONAS['charmer']
        profile, _ = set_name_style(
            self.user,
            recipe['font'],
            recipe['color'],
            recipe['size'],
            recipe['fx'],
            persona='charmer',
        )
        self.assertEqual(profile.name_persona, 'charmer')
        self.assertIn('namepersona-charmer', profile.name_style_classes())

    def test_unknown_persona_does_not_echo_into_html(self):
        Profile.objects.filter(user=self.user).update(
            name_persona='";onclick=alert(1)',
            name_font='";color:red',
        )
        profile = self.user.profile
        profile.refresh_from_db()
        self.assertEqual(profile.name_style_css(), '')
        self.assertEqual(profile.name_style_classes(), '')
        response = self.client.get('/u/styler/')
        self.assertNotContains(response, 'alert(1)')
        self.assertNotContains(response, 'not-a-person')
        self.assertNotContains(response, '";color:red')
        self.assertNotContains(response, 'namepersona-')

    def test_style_form_rejects_unknown_persona(self):
        form = NameStyleForm(data={
            'name_persona': 'not-a-person',
            'name_font': 'classic',
            'name_color': 'default',
            'name_size': 'md',
            'name_fx': 'none',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('name_persona', form.errors)

    def test_edit_profile_lists_all_twenty_people_styles(self):
        """The picker is ONE dropdown on Edit Profile; every people-style is
        an option (label + blurb), and the flourish classes reach the page
        through the json_script preview maps — not a card grid."""
        self.client.login(username='styler', password='pass12345')
        response = self.client.get('/settings/profile/')
        self.assertContains(response, 'Twenty people-styles')
        self.assertContains(response, '<select')
        self.assertContains(response, 'name_persona')
        self.assertNotContains(response, 'persona-card')
        for slug, meta in NAME_PERSONAS.items():
            self.assertContains(response, meta['label'])
        # Preview maps (styles display) carry every flourish class.
        for slug in people_style_slugs():
            self.assertContains(response, f'namepersona-{slug}')

    def test_people_style_renders_on_profile_page(self):
        set_name_style(self.user, 'classic', 'default', 'md', 'none', persona='strict')
        response = self.client.get('/u/styler/')
        self.assertContains(response, 'namepersona-strict')

    def test_compose_never_emits_user_text(self):
        packed = compose_name_style('papyrus', 'hotpink', 'xxl', 'matrix', 'xss')
        self.assertEqual(packed['name_persona'], 'classic')
        self.assertEqual(packed['css'], '')
        self.assertEqual(packed['classes'], '')
        self.assertNotIn('papyrus', packed['css'])
        self.assertNotIn('hotpink', packed['css'])
