from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from users.forms import SignUpForm
from users.models import AdminLog, Follow, Payout, Profile, StarEvent
from gallery.models import AppProject, Category, Notification


@override_settings(RATELIMIT_ENABLE=False)
class AuthAndProTests(TestCase):
    def test_signup_requires_email(self):
        form = SignUpForm(data={
            'username': 'newbie',
            'email': '',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('email', form.errors)

    def test_signup_creates_user_with_email(self):
        response = self.client.post('/accounts/signup/', {
            'username': 'newbie',
            'email': 'newbie@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='newbie')
        self.assertEqual(user.email, 'newbie@test.com')
        self.assertTrue(hasattr(user, 'profile'))

    def test_pro_trial_expires(self):
        user = User.objects.create_user('prouser', password='pass12345', email='p@test.com')
        profile = user.profile
        profile.is_pro = True
        profile.pro_since = timezone.now() - timedelta(days=8)
        profile.pro_until = timezone.now() - timedelta(days=1)
        profile.save()
        self.assertFalse(profile.is_pro_active)

    def test_pro_trial_is_seven_days_and_one_shot(self):
        user = User.objects.create_user('trial', password='pass12345', email='t@test.com')
        self.client.login(username='trial', password='pass12345')
        response = self.client.post('/pro/activate/')
        self.assertEqual(response.status_code, 302)
        profile = Profile.objects.get(user=user)
        self.assertTrue(profile.is_pro_active)
        self.assertIsNotNone(profile.pro_until)
        self.assertGreater(profile.pro_until, timezone.now() + timedelta(days=6))

        profile.pro_until = timezone.now() - timedelta(minutes=1)
        profile.save(update_fields=['pro_until'])
        response = self.client.post('/pro/activate/')
        self.assertEqual(response.status_code, 302)
        profile.refresh_from_db()
        self.assertFalse(profile.is_pro_active)

    def test_verify_email_marks_profile(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes
        user = User.objects.create_user('mailer', password='pass12345', email='m@test.com')
        self.assertFalse(user.profile.email_verified)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        response = self.client.get(f'/accounts/verify/{uid}/{token}/')
        self.assertEqual(response.status_code, 302)
        user.profile.refresh_from_db()
        self.assertTrue(user.profile.email_verified)

    def test_login_hides_social_when_unconfigured(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Continue with Google')

    @override_settings(GOOGLE_CLIENT_ID='test-google-id.apps.googleusercontent.com', GOOGLE_CLIENT_SECRET='secret')
    def test_login_shows_google_when_configured(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Continue with Google')
        self.assertContains(response, '/accounts/social/google/login/')

    def test_delete_account_requires_username(self):
        user = User.objects.create_user('goner', password='pass12345', email='g@test.com')
        self.client.login(username='goner', password='pass12345')
        response = self.client.post('/settings/delete-account/', {'confirm': 'wrong'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(username='goner').exists())
        response = self.client.post('/settings/delete-account/', {'confirm': 'goner'})
        self.assertFalse(User.objects.filter(username='goner').exists())

    def test_earnings_shows_real_star_totals(self):
        from gallery.models import AppProject, Category, Trade
        owner = User.objects.create_user('seller', password='pass12345', email='s@test.com')
        buyer = User.objects.create_user('buyer', password='pass12345', email='b@test.com')
        cat = Category.objects.create(name='Apps', slug='apps', type='full_app')
        project = AppProject.objects.create(
            owner=owner,
            title='Priced vibe',
            category=cat,
            short_description='A short description of this vibe used in tests.',
            readme='# Test Vibe\n\n' + ('This is a test readme with enough characters. ' * 4),
            status='published',
            star_cost=3,
        )
        Trade.objects.create(buyer=buyer, seller=owner, project=project, cost=3)
        self.client.login(username='seller', password='pass12345')
        response = self.client.get('/payout/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Earnings')
        self.assertContains(response, '3 ★')
        self.assertContains(response, 'stars are the complete money path')
        self.assertNotContains(response, 'YOUR PAYOUT (85%)')


@override_settings(RATELIMIT_ENABLE=False)
class ProfileAndFollowTests(TestCase):
    """The whole profile + follow surface — the community backbone.

    5 Whys: Why test this surface at all? Every discovery flow on the site
    funnels through it: feed -> creator name -> profile -> follow. It had
    ZERO tests while trading/payments had many; the follow economy cannot be
    trusted if "click creator name" is the only untested hop in the chain.
    """

    def _make_user(self, username, **kw):
        return User.objects.create_user(username, password='pass12345', email=f'{username}@test.com', **kw)

    def _make_project(self, owner, title, status='published', stars=0):
        cat = Category.objects.create(name='Apps', slug=f'cat-{title}-{owner.id}', type='full_app')
        return AppProject.objects.create(
            owner=owner,
            title=title,
            category=cat,
            short_description='A short description of this vibe used in tests.',
            readme='# Test Vibe\n\n' + ('This is a test readme with enough characters. ' * 4),
            status=status,
            stars=stars,
        )

    # --- Profile page ---

    def test_profile_page_renders_for_anonymous(self):
        owner = self._make_user('maker')
        response = self.client.get('/u/maker/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '@maker')
        self.assertContains(response, 'VIBES')
        self.assertContains(response, 'FOLLOWERS')
        self.assertContains(response, 'Joined')

    def test_profile_404_for_unknown_user(self):
        response = self.client.get('/u/no-such-user/')
        self.assertEqual(response.status_code, 404)

    def test_profile_shows_rank_and_star_stats(self):
        owner = self._make_user('ranker')
        self._make_project(owner, 'Star magnet', stars=12)  # 10 => Silver
        response = self.client.get('/u/ranker/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Silver')       # rank pill
        self.assertContains(response, '12 ★')          # stars earned stat

    def test_own_profile_shows_pending_vibes(self):
        owner = self._make_user('builder')
        self._make_project(owner, 'Live one', status='published')
        self._make_project(owner, 'Waiting one', status='pending')
        self.client.login(username='builder', password='pass12345')
        response = self.client.get('/u/builder/')
        self.assertContains(response, 'Live one')
        self.assertContains(response, 'Waiting one')
        self.assertContains(response, 'Queued')

    def test_other_profile_hides_pending_vibes(self):
        owner = self._make_user('secretive')
        self._make_project(owner, 'Public one', status='published')
        self._make_project(owner, 'Hidden one', status='pending')
        response = self.client.get('/u/secretive/')
        self.assertContains(response, 'Public one')
        self.assertNotContains(response, 'Hidden one')

    # --- Creator-name links from discovery surfaces ---

    def test_feed_links_creator_names_to_profiles(self):
        owner = self._make_user('feedstar')
        self._make_project(owner, 'Feed visible vibe')
        response = self.client.get('/')
        self.assertContains(response, '/u/feedstar/')

    def test_app_detail_links_publisher_to_profile(self):
        owner = self._make_user('publisher')
        project = self._make_project(owner, 'Detail page vibe')
        response = self.client.get(project.get_absolute_url())
        self.assertContains(response, '/u/publisher/')

    # --- Follow ---

    def test_follow_toggle_and_unfollow(self):
        fan = self._make_user('fan')
        star = self._make_user('starlet')
        self.client.login(username='fan', password='pass12345')
        response = self.client.post('/u/starlet/follow/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['following'])
        self.assertEqual(response.json()['followers'], 1)
        self.assertTrue(Follow.objects.filter(follower=fan, following=star).exists())

        response = self.client.post('/u/starlet/follow/')  # toggle off
        self.assertFalse(response.json()['following'])
        self.assertEqual(response.json()['followers'], 0)
        self.assertFalse(Follow.objects.filter(follower=fan, following=star).exists())

    def test_cannot_follow_self(self):
        self._make_user('loner')
        self.client.login(username='loner', password='pass12345')
        response = self.client.post('/u/loner/follow/')
        self.assertEqual(response.status_code, 400)

    def test_follow_requires_login(self):
        self._make_user('target')
        response = self.client.post('/u/target/follow/')
        self.assertEqual(response.status_code, 302)  # redirect to login
        self.assertIn('/accounts/login', response.url)

    def test_follow_creates_notification(self):
        self._make_user('fan')
        star = self._make_user('starlet')
        self.client.login(username='fan', password='pass12345')
        self.client.post('/u/starlet/follow/')
        self.assertTrue(
            Notification.objects.filter(user=star, kind='follow').exists()
        )

    @override_settings(
        RATELIMIT_ENABLE=True,
        RATELIMIT_USE_CACHE='ratelimit',
        # 5 Whys: Why pin the cache here? The rate-limit cache follows
        # REDIS_URL from .env at settings-import time. A test that silently
        # depends on whether .env exists (Redis up or down) would pass on
        # one machine and crash on another — a hermetic test pins locmem.
        CACHES={
            'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'test-default'},
            'ratelimit': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'test-ratelimit'},
        },
    )
    def test_follow_is_rate_limited(self):
        # 5 Whys: Why assert 403, not 429? This codebase's ratelimit pattern
        # (django-ratelimit 4.x, block=True) raises Ratelimited, which Django
        # maps through handler403 to the site's friendly safe_403 page — the
        # same behaviour publish/comments/battles have. Follow must not be
        # the odd one out.
        self._make_user('bot')
        self._make_user('t1')
        self._make_user('t2')
        self.client.login(username='bot', password='pass12345')
        last = None
        for i in range(31):
            target = 't1' if i % 2 == 0 else 't2'
            last = self.client.post(f'/u/{target}/follow/')
        self.assertEqual(last.status_code, 403)

    # --- Tabs ---

    def test_followers_tab_lists_followers(self):
        fan = self._make_user('fan')
        star = self._make_user('starlet')
        Follow.objects.create(follower=fan, following=star)
        response = self.client.get('/u/starlet/?tab=followers')
        self.assertContains(response, 'fan')
        self.assertContains(response, 'Follow')  # card button for the fan

    def test_following_tab_lists_following(self):
        fan = self._make_user('fan')
        star = self._make_user('starlet')
        Follow.objects.create(follower=fan, following=star)
        response = self.client.get('/u/fan/?tab=following')
        self.assertContains(response, 'starlet')

    def test_stars_tab_orders_by_recent_star(self):
        from gallery.models import Star
        user = self._make_user('stargiver')
        p_new = self._make_project(user, 'Starred recently')
        p_old = self._make_project(user, 'Starred long ago')
        Star.objects.create(user=user, project=p_old)
        Star.objects.create(user=user, project=p_new)  # newest first
        response = self.client.get('/u/stargiver/?tab=stars')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertLess(content.index('Starred recently'), content.index('Starred long ago'))

    def test_unknown_tab_falls_back_to_vibes(self):
        owner = self._make_user('safefallback')
        self._make_project(owner, 'Fallback vibe')
        response = self.client.get('/u/safefallback/?tab=hack')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fallback vibe')


@override_settings(RATELIMIT_ENABLE=False)
class TipTests(TestCase):
    """Star tipping — the gratitude money path.

    5 Whys: Why test tips like trades? Tips move spendable currency —
    the same blast radius as a Trade. The wallet must be provably
    zero-sum, ledgered, and gated, or the economy's "no minting" rule
    is just a comment.
    """

    def _make_user(self, username, verified=True):
        user = User.objects.create_user(username, password='pass12345', email=f'{username}@test.com')
        if verified:
            user.profile.email_verified = True
            user.profile.save(update_fields=['email_verified'])
            from users.wallet import grant_welcome_stars
            grant_welcome_stars(user)  # real wallet path -> 5★
        return user

    # --- Wallet moves ---

    def test_tip_moves_stars_and_writes_ledger(self):
        from gallery.models import Notification
        from users.models import StarEvent, Tip
        sender = self._make_user('tipster')
        recipient = self._make_user('tippee')
        self.client.login(username='tipster', password='pass12345')
        response = self.client.post('/u/tippee/tip/', {'amount': '3', 'message': 'Love your games!'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['balance'], 2)  # 5 - 3

        sender.profile.refresh_from_db()
        recipient.profile.refresh_from_db()
        self.assertEqual(sender.profile.stars_balance, 2)
        self.assertEqual(recipient.profile.stars_balance, 8)  # 5 + 3

        tip = Tip.objects.get()
        self.assertEqual(tip.sender, sender)
        self.assertEqual(tip.recipient, recipient)
        self.assertEqual(tip.amount, 3)
        self.assertEqual(tip.message, 'Love your games!')

        # Ledger: one spend, one earn, same ref — zero-sum.
        spend = StarEvent.objects.get(user=sender, reason='tip_spend')
        earn = StarEvent.objects.get(user=recipient, reason='tip_earn')
        self.assertEqual(spend.delta, -3)
        self.assertEqual(earn.delta, 3)
        self.assertEqual(spend.ref, f'tip:{tip.pk}')
        self.assertEqual(earn.ref, f'tip:{tip.pk}')
        # Wallet and ledger reconcile — the discipline the ledger exists for.
        self.assertTrue(recipient.profile.stars_balance == 8)
        from users.wallet import ledger_balance
        self.assertEqual(ledger_balance(recipient), 8)

        # Notification to the recipient.
        self.assertTrue(Notification.objects.filter(user=recipient, kind='tip').exists())

    def test_tip_rejects_insufficient_balance(self):
        self._make_user('broke')
        self._make_user('rich')
        # Burn the 5★ grant down to 1★ via a ledgered admin-style adjust.
        from users.models import StarEvent
        from django.db.models import F
        user = User.objects.get(username='broke')
        from users.models import Profile
        Profile.objects.filter(user=user).update(stars_balance=F('stars_balance') - 4)
        StarEvent.objects.create(user=user, delta=-4, reason='admin_adjust', ref='test')
        self.client.login(username='broke', password='pass12345')
        response = self.client.post('/u/rich/tip/', {'amount': '3'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('not enough', response.json()['error'].lower())

    def test_tip_rejects_self(self):
        self._make_user('loner')
        self.client.login(username='loner', password='pass12345')
        response = self.client.post('/u/loner/tip/', {'amount': '1'})
        self.assertEqual(response.status_code, 400)

    def test_tip_rejects_bad_amounts(self):
        self._make_user('picker')
        self._make_user('receivy')
        self.client.login(username='picker', password='pass12345')
        for bad in ('0', '-1', '1001', 'abc'):
            response = self.client.post('/u/receivy/tip/', {'amount': bad})
            self.assertEqual(response.status_code, 400, f'amount={bad}')

    def test_tip_requires_verified_email(self):
        sender = self._make_user('unverified', verified=False)
        self._make_user('tippee2')
        self.client.login(username='unverified', password='pass12345')
        response = self.client.post('/u/tippee2/tip/', {'amount': '1'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.json()['error'].lower())
        sender.profile.refresh_from_db()
        self.assertEqual(sender.profile.stars_balance, 0)  # nothing moved

    def test_tip_requires_login(self):
        self._make_user('target3')
        response = self.client.post('/u/target3/tip/', {'amount': '1'})
        self.assertEqual(response.status_code, 302)

    def test_tip_message_is_sanitized(self):
        # bleach tags=[] strip=True removes tag MARKUP but hoists inner text
        # (same policy as Profile.bio). What must never survive is a live
        # element or a javascript: URL — Django's template autoescape then
        # renders any leftover text inert.
        self._make_user('sani')
        self._make_user('receivy2')
        self.client.login(username='sani', password='pass12345')
        response = self.client.post('/u/receivy2/tip/', {'amount': '1', 'message': '<a href="javascript:alert(1)">click</a><script>x</script>Hi'})
        self.assertEqual(response.status_code, 200)
        from users.models import Tip
        stored = Tip.objects.get().message
        self.assertNotIn('<a', stored)
        self.assertNotIn('<script', stored)
        self.assertNotIn('javascript:', stored)
        self.assertIn('Hi', stored)  # hoisted text is fine — it renders escaped
        # The profile page must not contain the live XSS vector either
        # (the site's own <script> tags are fine — only the injection must
        # be absent).
        page = self.client.get('/u/receivy2/')
        self.assertNotContains(page, 'javascript:')

    @override_settings(
        RATELIMIT_ENABLE=True,
        RATELIMIT_USE_CACHE='ratelimit',
        CACHES={
            'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'tip-default'},
            'ratelimit': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'tip-ratelimit'},
        },
    )
    def test_tip_is_rate_limited(self):
        self._make_user('tipbot')
        for i in range(3):
            self._make_user(f'victim{i}')
        self.client.login(username='tipbot', password='pass12345')
        last = None
        for i in range(21):
            last = self.client.post(f'/u/victim{i % 3}/tip/', {'amount': '1'})
        self.assertEqual(last.status_code, 403)

    # --- UI surfaces ---

    def test_profile_shows_recent_tips(self):
        self._make_user('giver')
        creator = self._make_user('creat0r')
        self.client.login(username='giver', password='pass12345')
        self.client.post('/u/creat0r/tip/', {'amount': '2', 'message': 'dope'})
        response = self.client.get('/u/creat0r/')
        self.assertContains(response, 'Recent tips')
        self.assertContains(response, '+2★')
        self.assertContains(response, 'dope')

    def test_payout_dashboard_shows_tips(self):
        self._make_user('giver2')
        self._make_user('boss')
        self.client.login(username='giver2', password='pass12345')
        self.client.post('/u/boss/tip/', {'amount': '4'})
        self.client.login(username='boss', password='pass12345')
        response = self.client.get('/payout/')
        self.assertContains(response, 'Tips received')
        self.assertContains(response, '+4 ★')


class ChartTests(TestCase):
    """Earnings-page charts — real ledger data, honest empty states.

    5 Whys: Why test SVG markup? The charts are the one place the template
    renders Python-built HTML with |safe. The generators only emit dates
    and integers (no user text), but a regression that let user content
    into the SVG would be an XSS hole — so the tests pin what the chart
    CAN and CANNOT contain, and what the empty state says.
    """

    def _user_with_balance(self, username, balance):
        user = User.objects.create_user(username, password='pass12345', email=f'{username}@test.com')
        user.profile.email_verified = True
        user.profile.stars_balance = balance
        user.profile.save(update_fields=['email_verified', 'stars_balance'])
        return user

    def _ledger(self, user, delta, reason, days_ago=0):
        ev = StarEvent.objects.create(user=user, delta=delta, reason=reason, ref='test')
        StarEvent.objects.filter(pk=ev.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )

    def test_activity_chart_renders_earned_and_spent_bars(self):
        user = self._user_with_balance('chartstar', 6)
        self._ledger(user, 3, 'tip_earn', days_ago=0)      # earned today
        self._ledger(user, -2, 'trade_spend', days_ago=1)  # spent yesterday
        self.client.login(username='chartstar', password='pass12345')
        response = self.client.get('/payout/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<svg')
        self.assertContains(response, '#10B981')  # earned bar fill
        self.assertContains(response, '#EF4444')  # spent bar fill
        self.assertContains(response, '>earned<')
        self.assertContains(response, '>spent<')
        self.assertContains(response, 'Stars earned vs spent, last 14 days')

    def test_activity_chart_empty_state_when_no_events(self):
        user = self._user_with_balance('nomo', 0)
        self.client.login(username='nomo', password='pass12345')
        response = self.client.get('/payout/')
        self.assertContains(response, 'No wallet activity in the last 14 days')
        self.assertNotContains(response, 'Stars earned vs spent')

    def test_charts_ignore_events_outside_window(self):
        user = self._user_with_balance('oldie', 0)
        self._ledger(user, 9, 'tip_earn', days_ago=20)  # outside 14-day window
        self.client.login(username='oldie', password='pass12345')
        response = self.client.get('/payout/')
        self.assertContains(response, 'No wallet activity in the last 14 days')

    def test_balance_chart_draws_flat_real_balance(self):
        user = self._user_with_balance('steady', 5)
        self.client.login(username='steady', password='pass12345')
        response = self.client.get('/payout/')
        self.assertContains(response, '<polyline')       # the line exists
        self.assertContains(response, 'Wallet balance, last 14 days')
        self.assertContains(response, '5★')              # end-dot label = real balance

    def test_balance_chart_empty_state_when_nothing_real(self):
        user = self._user_with_balance('zero', 0)
        self.client.login(username='zero', password='pass12345')
        response = self.client.get('/payout/')
        self.assertContains(response, 'Balance history is empty')
        self.assertNotContains(response, '<polyline')

    def test_chart_never_contains_user_text(self):
        """User text must never reach the chart markup unescaped.

        The SVG is built only from dates and integers, so an XSS payload
        placed in a ledger ref must only ever appear ESCAPED (in the
        ledger list, where Django autoescapes) — never as live markup.
        The site's own <script> tags are fine; the injection is not.
        """
        user = self._user_with_balance('safechart', 5)
        ev = StarEvent.objects.create(
            user=user, delta=1, reason='tip_earn',
            ref='<script>alert(1)</script>',
        )
        StarEvent.objects.filter(pk=ev.pk).update(created_at=timezone.now())
        self.client.login(username='safechart', password='pass12345')
        response = self.client.get('/payout/')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertNotContains(response, 'javascript:')
        # The payload still exists in the DB — it was just rendered escaped.
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')


class AppDetailTipTests(TestCase):
    """The ⭐ Tip widget on app-detail pages — same backend, new surface.

    5 Whys: Why test the widget, not just the endpoint? The endpoint is
    already covered by TipTests; these tests pin the DISCOVERY surface —
    that a visitor on a vibe page can find the tip button, that the owner
    never sees it on their own vibe, and that anonymous visitors get the
    login path instead.
    """

    def setUp(self):
        self.owner = User.objects.create_user('creator', password='pass12345', email='creator@test.com')
        self.owner.profile.email_verified = True
        self.owner.profile.save(update_fields=['email_verified'])
        self.fan = User.objects.create_user('fanofapps', password='pass12345', email='fanofapps@test.com')
        self.fan.profile.email_verified = True
        self.fan.profile.stars_balance = 5
        self.fan.profile.save(update_fields=['email_verified', 'stars_balance'])
        cat = Category.objects.create(name='Apps', slug='tiptest-apps', type='full_app')
        self.project = AppProject.objects.create(
            owner=self.owner, title='Tip target vibe', category=cat,
            short_description='A vibe to tip for.',
            readme='# Tip\n\n' + ('Tippable vibe. ' * 20),
            status='published', star_cost=2,
        )

    def test_visitor_sees_tip_button_and_panel(self):
        self.client.login(username='fanofapps', password='pass12345')
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, '⭐ Tip')
        self.assertContains(response, f'data-username="{self.owner.username}"')
        self.assertContains(response, 'id="tip-panel"')
        self.assertContains(response, 'Your balance: 5 ★')

    def test_owner_never_sees_tip_button_on_own_vibe(self):
        self.client.login(username='creator', password='pass12345')
        response = self.client.get(self.project.get_absolute_url())
        self.assertNotContains(response, '⭐ Tip')
        self.assertNotContains(response, 'id="tip-panel"')

    def test_anonymous_gets_login_link(self):
        response = self.client.get(self.project.get_absolute_url())
        self.assertContains(response, '⭐ Tip')
        self.assertContains(response, '/accounts/login/')
        self.assertNotContains(response, 'id="tip-panel"')

    def test_tipping_from_app_detail_pays_the_owner(self):
        """The button's data-username is the owner — a tip lands in their
        wallet, not the vibe's (vibes have no wallet; people do)."""
        self.client.login(username='fanofapps', password='pass12345')
        response = self.client.post('/u/creator/tip/', {'amount': '2', 'message': 'from the vibe page'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 2)
        self.fan.profile.refresh_from_db()
        self.assertEqual(self.fan.profile.stars_balance, 3)


@override_settings(RATELIMIT_ENABLE=False)
class PayoutTests(TestCase):
    """Cash-outs — the star→ZAR exit of the money path.

    The rules under test (users/payouts.py): stars are HELD at request,
    one open payout per user, rejection refunds as a new ledger row, and
    only a human decision flips paid — a transfer reference never does.
    """

    def setUp(self):
        self.creator = User.objects.create_user('cashout', password='pass12345', email='cash@test.com')
        profile = self.creator.profile
        profile.email_verified = True
        profile.stars_balance = 1000
        profile.save(update_fields=['email_verified', 'stars_balance'])
        # Seed the wallet through the ledger, not just the integer — the
        # balance must always be explainable by StarEvent rows
        # (users.wallet.wallet_reconciles). 'backfill' is the honest
        # reason for pre-existing balances.
        StarEvent.objects.create(user=self.creator, delta=1000, reason='backfill', ref='test-setup')
        self.admin = User.objects.create_user('moneyadmin', password='pass12345', email='m@test.com')
        self.admin.profile.role = 'admin'
        self.admin.profile.save(update_fields=['role'])

    def _request(self, amount='500', bank='Capitec', account='1234567890', holder='Cash Out'):
        return self.client.post('/payout/request/', {
            'amount_stars': amount,
            'bank_name': bank,
            'account_number': account,
            'holder_name': holder,
        })

    def test_request_requires_login(self):
        response = self._request()
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_request_below_minimum_is_refused(self):
        self.client.login(username='cashout', password='pass12345')
        self._request(amount='100')
        self.creator.profile.refresh_from_db()
        self.assertEqual(self.creator.profile.stars_balance, 1000)
        self.assertFalse(Payout.objects.exists())

    def test_request_needs_verified_email(self):
        self.creator.profile.email_verified = False
        self.creator.profile.save(update_fields=['email_verified'])
        self.client.login(username='cashout', password='pass12345')
        self._request()
        self.creator.profile.refresh_from_db()
        self.assertEqual(self.creator.profile.stars_balance, 1000)
        self.assertFalse(Payout.objects.exists())

    def test_request_needs_whole_zar_multiples(self):
        self.client.login(username='cashout', password='pass12345')
        self._request(amount='505')
        self.assertFalse(Payout.objects.exists())

    def test_request_holds_stars_and_writes_ledger(self):
        from users.wallet import wallet_reconciles
        self.client.login(username='cashout', password='pass12345')
        response = self._request()
        self.assertEqual(response.status_code, 302)
        payout = Payout.objects.get(user=self.creator)
        self.assertEqual(payout.status, 'requested')
        self.assertEqual(payout.amount_stars, 500)
        self.assertEqual(payout.amount_zar, 50)
        self.creator.profile.refresh_from_db()
        self.assertEqual(self.creator.profile.stars_balance, 500)
        hold = StarEvent.objects.filter(user=self.creator, reason='payout_hold').get()
        self.assertEqual(hold.delta, -500)
        self.assertEqual(hold.ref, f'payout:{payout.pk}')
        self.assertTrue(wallet_reconciles(self.creator))
        self.assertTrue(
            Notification.objects.filter(user=self.creator, kind='payout').exists()
        )

    def test_second_request_blocked_while_one_open(self):
        self.client.login(username='cashout', password='pass12345')
        self._request()
        self._request(amount='500')
        self.assertEqual(Payout.objects.filter(user=self.creator).count(), 1)
        self.creator.profile.refresh_from_db()
        self.assertEqual(self.creator.profile.stars_balance, 500)

    def test_reject_refunds_stars(self):
        from users.wallet import wallet_reconciles
        self.client.login(username='cashout', password='pass12345')
        self._request()
        payout = Payout.objects.get(user=self.creator)
        self.client.login(username='moneyadmin', password='pass12345')
        response = self.client.post(f'/admin/payouts/{payout.pk}/decide/', {
            'action': 'reject', 'note': 'account number typo',
        })
        self.assertEqual(response.status_code, 302)
        payout.refresh_from_db()
        self.assertEqual(payout.status, 'rejected')
        self.assertEqual(payout.admin_note, 'account number typo')
        self.creator.profile.refresh_from_db()
        self.assertEqual(self.creator.profile.stars_balance, 1000)
        refund = StarEvent.objects.get(user=self.creator, reason='payout_refund')
        self.assertEqual(refund.delta, 500)
        self.assertTrue(wallet_reconciles(self.creator))
        self.assertTrue(AdminLog.objects.filter(action='payout_reject').exists())

    def test_pay_marks_paid_without_touching_balance(self):
        self.client.login(username='cashout', password='pass12345')
        self._request()
        payout = Payout.objects.get(user=self.creator)
        self.client.login(username='moneyadmin', password='pass12345')
        self.client.post(f'/admin/payouts/{payout.pk}/decide/', {
            'action': 'pay', 'note': 'EFT 8842',
        })
        payout.refresh_from_db()
        self.assertEqual(payout.status, 'paid')
        self.assertEqual(payout.reviewed_by, self.admin)
        self.creator.profile.refresh_from_db()
        self.assertEqual(self.creator.profile.stars_balance, 500)  # hold stands, no mint
        self.assertTrue(AdminLog.objects.filter(action='payout_pay').exists())

    def test_double_decide_is_refused(self):
        self.client.login(username='cashout', password='pass12345')
        self._request()
        payout = Payout.objects.get(user=self.creator)
        self.client.login(username='moneyadmin', password='pass12345')
        self.client.post(f'/admin/payouts/{payout.pk}/decide/', {'action': 'pay'})
        # A second click (double-submit, stale tab) must not refund a paid row.
        from users.payouts import PayoutError, decide_payout
        with self.assertRaises(PayoutError):
            decide_payout(self.admin, payout.pk, 'reject')
        payout.refresh_from_db()
        self.assertEqual(payout.status, 'paid')
        self.creator.profile.refresh_from_db()
        self.assertEqual(self.creator.profile.stars_balance, 500)

    def test_non_admin_cannot_open_queue(self):
        self.client.login(username='cashout', password='pass12345')
        response = self.client.get('/admin/payouts/')
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/admin/payouts/1/decide/', {'action': 'pay'})
        self.assertEqual(response.status_code, 403)

    def test_dashboard_shows_cashout_panel(self):
        self.client.login(username='cashout', password='pass12345')
        response = self.client.get('/payout/')
        self.assertContains(response, 'Cash out stars')
        self.assertContains(response, '500')  # minimum in the form label

    def test_admin_queue_lists_open_payout(self):
        self.client.login(username='cashout', password='pass12345')
        self._request()
        self.client.login(username='moneyadmin', password='pass12345')
        response = self.client.get('/admin/payouts/')
        self.assertContains(response, '500 ★ → R50')
        self.assertContains(response, '1234567890')  # money admins see full digits


class PaystackTransferTests(TestCase):
    """initiate_payout_transfer — real API shape, mocked wire.

    Never pretends: a successful initiation records the transfer code and
    leaves the payout 'requested'; only a human flips it to 'paid'.
    """

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self._payload

    def setUp(self):
        self.creator = User.objects.create_user('payee', password='pass12345', email='p@test.com')
        self.payout = Payout.objects.create(
            user=self.creator, amount_stars=500, amount_zar=50,
            bank_name='Capitec Bank', account_number='1234567890', holder_name='Payee Name',
        )

    def test_unconfigured_paystack_refuses_transfer(self):
        from gallery.payments import PaymentError, initiate_payout_transfer
        with self.assertRaises(PaymentError):
            initiate_payout_transfer(self.payout)

    @override_settings(PAYSTACK_SECRET_KEY='sk_test_123')
    def test_transfer_records_reference_and_stays_requested(self):
        from gallery.payments import initiate_payout_transfer
        banks = self.FakeResponse({'status': True, 'data': [
            {'name': 'First National Bank', 'code': '250655'},
            {'name': 'Capitec Bank', 'code': '470010'},
        ]})
        recipient = self.FakeResponse({'status': True, 'data': {'recipient_code': 'RCP_1'}})
        transfer = self.FakeResponse({'status': True, 'data': {'transfer_code': 'TRF_99', 'status': 'pending'}})
        with patch('gallery.payments.requests.get', return_value=banks), \
             patch('gallery.payments.requests.post', side_effect=[recipient, transfer]) as post:
            code = initiate_payout_transfer(self.payout)
        self.assertEqual(code, 'TRF_99')
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.provider_ref, 'TRF_99')
        self.assertEqual(self.payout.status, 'requested')  # transfer ≠ payment
        sent = [call.kwargs['json'] for call in post.call_args_list]
        self.assertEqual(sent[1]['recipient'], 'RCP_1')
        self.assertEqual(sent[1]['amount'], 5000)  # R50 in cents

    @override_settings(PAYSTACK_SECRET_KEY='sk_test_123')
    def test_unknown_bank_is_refused_not_guessed(self):
        from gallery.payments import PaymentError, initiate_payout_transfer
        banks = self.FakeResponse({'status': True, 'data': [
            {'name': 'First National Bank', 'code': '250655'},
        ]})
        with patch('gallery.payments.requests.get', return_value=banks):
            with self.assertRaises(PaymentError):
                initiate_payout_transfer(self.payout)
