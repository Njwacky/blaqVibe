"""OAuth sign-in — GitHub, Facebook and Google.

The provider handshake is stubbed at exactly one seam: the adapter's
``complete_login``, which is the method that would talk to github.com /
graph.facebook.com. Everything after it — state validation, the email/username
resolution, our adapters, the Profile sync, the star grant — is the real code
path a user goes through.
"""
from unittest.mock import patch

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from users.models import Profile, StarEvent, WELCOME_STARS

def _provider_settings(*slugs):
    """Build SOCIALACCOUNT_PROVIDERS the way settings.py does, for `slugs`."""
    from django.conf import settings as dj_settings

    out = {}
    for slug in slugs:
        cfg = dj_settings.SOCIAL_PROVIDER_CREDENTIALS[slug]
        out[slug] = {
            'APP': {'client_id': f'{slug}-id', 'secret': f'{slug}-secret', 'key': ''},
            **cfg['settings'],
        }
    return out

GITHUB_ON = dict(
    GITHUB_CLIENT_ID='github-id',
    GITHUB_CLIENT_SECRET='github-secret',
    SOCIALACCOUNT_PROVIDERS=_provider_settings('github'),
    RATELIMIT_ENABLE=False,
)
FACEBOOK_ON = dict(
    FACEBOOK_CLIENT_ID='facebook-id',
    FACEBOOK_CLIENT_SECRET='facebook-secret',
    SOCIALACCOUNT_PROVIDERS=_provider_settings('facebook'),
    RATELIMIT_ENABLE=False,
)
ALL_ON = dict(
    GOOGLE_CLIENT_ID='google-id',
    GOOGLE_CLIENT_SECRET='google-secret',
    GITHUB_CLIENT_ID='github-id',
    GITHUB_CLIENT_SECRET='github-secret',
    FACEBOOK_CLIENT_ID='facebook-id',
    FACEBOOK_CLIENT_SECRET='facebook-secret',
    SOCIALACCOUNT_PROVIDERS=_provider_settings('google', 'github', 'facebook'),
    RATELIMIT_ENABLE=False,
)

def _sign_in(client, slug, adapter_cls, provider_cls, profile_data, process='login'):
    """Drive a full OAuth round-trip with only the provider HTTP call faked."""
    start = client.post(f'/accounts/social/{slug}/login/?process={process}')
    assert start.status_code == 302, start.status_code
    state = start['Location'].split('state=')[1].split('&')[0]

    def fake_complete_login(self, request, app, token, **kwargs):
        return provider_cls(request, app).sociallogin_from_response(request, profile_data)

    with patch(
        'allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token',
        return_value={'access_token': 'stub-token'},
    ), patch.object(adapter_cls, 'complete_login', new=fake_complete_login):
        return client.get(
            f'/accounts/social/{slug}/login/callback/?code=stub-code&state={state}'
        )

def github_sign_in(client, data, process='login'):
    from allauth.socialaccount.providers.github.provider import GitHubProvider
    from allauth.socialaccount.providers.github.views import GitHubOAuth2Adapter

    return _sign_in(client, 'github', GitHubOAuth2Adapter, GitHubProvider, data, process)

def facebook_sign_in(client, data, process='login'):
    from allauth.socialaccount.providers.facebook.provider import FacebookProvider
    from allauth.socialaccount.providers.facebook.views import FacebookOAuth2Adapter

    return _sign_in(client, 'facebook', FacebookOAuth2Adapter, FacebookProvider, data, process)

def github_profile(uid=4242, login='octocat', email='octocat@example.com', verified=True):
    data = {'id': uid, 'login': login, 'name': 'Octo Cat', 'email': email}
    if email:
        data['emails'] = [{'email': email, 'primary': True, 'verified': verified}]
    return data

def facebook_profile(uid='99001', name='Thabo Mokoena', email='thabo@example.com'):
    data = {'id': uid, 'name': name, 'first_name': 'Thabo', 'last_name': 'Mokoena'}
    if email:
        data['email'] = email
    return data

class SocialButtonTests(TestCase):
    """A button appears only when the handshake behind it can complete."""

    def test_no_buttons_without_credentials(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Continue with Google')
        self.assertNotContains(response, 'Continue with GitHub')
        self.assertNotContains(response, 'Continue with Facebook')

    @override_settings(**ALL_ON)
    def test_all_three_buttons_when_configured(self):
        response = self.client.get('/accounts/login/')
        for label in ('Google', 'GitHub', 'Facebook'):
            self.assertContains(response, f'Continue with {label}')
        for slug in ('google', 'github', 'facebook'):
            self.assertContains(response, f'/accounts/social/{slug}/login/?process=login')

    @override_settings(**ALL_ON)
    def test_buttons_appear_on_signup_page_too(self):
        response = self.client.get('/accounts/signup/')
        self.assertContains(response, 'Continue with GitHub')
        self.assertContains(response, 'Continue with Facebook')

    @override_settings(
        GITHUB_CLIENT_ID='github-id',
        GITHUB_CLIENT_SECRET='',
        SOCIALACCOUNT_PROVIDERS={},
        RATELIMIT_ENABLE=False,
    )
    def test_client_id_without_secret_shows_no_button(self):
        """Half-configured is not configured: the token exchange would fail."""
        response = self.client.get('/accounts/login/')
        self.assertNotContains(response, 'Continue with GitHub')

    @override_settings(**ALL_ON)
    def test_button_action_matches_the_real_route(self):
        """The hardcoded action in the template must be the mounted URL."""
        for slug in ('google', 'github', 'facebook'):
            self.assertEqual(
                reverse(f'{slug}_login'), f'/accounts/social/{slug}/login/'
            )

    def test_login_route_404s_when_provider_is_off(self):
        response = self.client.post('/accounts/social/github/login/?process=login')
        self.assertEqual(response.status_code, 404)

    @override_settings(**ALL_ON)
    def test_handshake_does_not_start_on_a_get(self):
        """A GET must not redirect to the provider: only a CSRF-checked POST."""
        response = self.client.get('/accounts/social/github/login/?process=login')
        self.assertNotEqual(response.status_code, 302)

@override_settings(**GITHUB_ON)
class GitHubSignInTests(TestCase):
    def test_new_user_is_created_and_signed_in(self):
        response = github_sign_in(self.client, github_profile())
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='octocat')
        self.assertEqual(user.email, 'octocat@example.com')
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))

    def test_github_handle_lands_on_the_profile(self):
        github_sign_in(self.client, github_profile(login='nolo-dev'))
        profile = Profile.objects.get(user__username='nolo-dev')
        self.assertEqual(profile.github, 'nolo-dev')

    def test_verified_provider_email_pays_the_welcome_grant_once(self):
        github_sign_in(self.client, github_profile())
        user = User.objects.get(username='octocat')
        self.assertTrue(user.profile.email_verified)
        self.assertEqual(user.profile.stars_balance, WELCOME_STARS)

        # Sign out and back in: the grant is ledger-idempotent.
        self.client.logout()
        github_sign_in(self.client, github_profile())
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.stars_balance, WELCOME_STARS)
        self.assertEqual(StarEvent.objects.filter(user=user, reason='welcome').count(), 1)

    def test_unverified_provider_email_does_not_verify_the_account(self):
        github_sign_in(self.client, github_profile(verified=False))
        profile = Profile.objects.get(user__username='octocat')
        self.assertFalse(profile.email_verified)
        self.assertEqual(profile.stars_balance, 0)

    def test_same_email_connects_to_the_existing_account(self):
        existing = User.objects.create_user(
            'og-builder', password='correcthorse1', email='octocat@example.com'
        )
        github_sign_in(self.client, github_profile())
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(
            SocialAccount.objects.get(provider='github').user_id, existing.pk
        )
        self.assertEqual(self.client.session['_auth_user_id'], str(existing.pk))

    def test_connecting_backfills_the_existing_profile(self):
        """The connect path never calls save_user — the signal must cover it."""
        existing = User.objects.create_user(
            'og-builder', password='correcthorse1', email='octocat@example.com'
        )
        github_sign_in(self.client, github_profile(login='octocat'))
        existing.profile.refresh_from_db()
        self.assertEqual(existing.profile.github, 'octocat')
        self.assertTrue(existing.profile.email_verified)
        self.assertEqual(existing.profile.stars_balance, WELCOME_STARS)

    def test_a_hand_typed_github_handle_is_not_overwritten(self):
        existing = User.objects.create_user(
            'og-builder', password='correcthorse1', email='octocat@example.com'
        )
        existing.profile.github = 'my-other-handle'
        existing.profile.save(update_fields=['github'])
        github_sign_in(self.client, github_profile(login='octocat'))
        existing.profile.refresh_from_db()
        self.assertEqual(existing.profile.github, 'my-other-handle')

    def test_returning_user_does_not_get_a_second_account(self):
        github_sign_in(self.client, github_profile())
        self.client.logout()
        github_sign_in(self.client, github_profile())
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(SocialAccount.objects.count(), 1)

    def test_taken_username_gets_a_suffix_not_a_collision(self):
        User.objects.create_user('octocat', password='correcthorse1', email='other@example.com')
        github_sign_in(self.client, github_profile())
        new_user = User.objects.get(email='octocat@example.com')
        self.assertNotEqual(new_user.username, 'octocat')
        self.assertEqual(User.objects.count(), 2)

    def test_reserved_username_is_never_taken_by_a_provider_handle(self):
        """'admin' from GitHub must not become @admin here."""
        github_sign_in(self.client, github_profile(login='admin', email='admin@example.com'))
        user = User.objects.get(email='admin@example.com')
        self.assertNotEqual(user.username.lower(), 'admin')

    def test_profane_provider_handle_does_not_become_a_username(self):
        github_sign_in(self.client, github_profile(login='fuck', email='rude@example.com'))
        user = User.objects.get(email='rude@example.com')
        self.assertNotIn('fuck', user.username.lower())

    def test_private_github_email_falls_back_to_the_signup_form(self):
        """GitHub returns email: null when the address is private."""
        response = github_sign_in(self.client, github_profile(email=None))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/accounts/social/signup/'))
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(self.client.get(response['Location']).status_code, 200)

    def test_the_fallback_signup_form_creates_the_account(self):
        github_sign_in(self.client, github_profile(email=None))
        response = self.client.post(
            '/accounts/social/signup/',
            {'email': 'chosen@example.com', 'username': 'chosen-name'},
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='chosen-name')
        self.assertEqual(user.email, 'chosen@example.com')
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))

    def test_no_access_token_is_stored(self):
        """We authenticate with the provider; we do not act on the user's behalf."""
        from allauth.socialaccount.models import SocialToken

        github_sign_in(self.client, github_profile())
        self.assertEqual(SocialToken.objects.count(), 0)

    def test_a_replayed_callback_without_state_is_rejected(self):
        response = self.client.get(
            '/accounts/social/github/login/callback/?code=stolen-code&state=forged'
        )
        self.assertNotEqual(response.status_code, 302)
        self.assertEqual(User.objects.count(), 0)

    def test_provider_denial_renders_the_error_page(self):
        self.client.post('/accounts/social/github/login/?process=login')
        response = self.client.get(
            '/accounts/social/github/login/callback/?error=access_denied&state=x'
        )
        self.assertEqual(response.status_code, 401)
        self.assertContains(
            response, "Couldn't finish that sign-in", status_code=401
        )
        self.assertEqual(User.objects.count(), 0)

@override_settings(**FACEBOOK_ON)
class FacebookSignInTests(TestCase):
    def test_new_user_is_created_and_signed_in(self):
        response = facebook_sign_in(self.client, facebook_profile())
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email='thabo@example.com')
        self.assertEqual(self.client.session['_auth_user_id'], str(user.pk))

    def test_facebook_email_is_treated_as_verified(self):
        facebook_sign_in(self.client, facebook_profile())
        user = User.objects.get(email='thabo@example.com')
        self.assertTrue(user.profile.email_verified)
        self.assertEqual(user.profile.stars_balance, WELCOME_STARS)

    def test_same_email_connects_to_the_existing_account(self):
        existing = User.objects.create_user(
            'thabo-og', password='correcthorse1', email='thabo@example.com'
        )
        facebook_sign_in(self.client, facebook_profile())
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(
            SocialAccount.objects.get(provider='facebook').user_id, existing.pk
        )

    def test_missing_email_falls_back_to_the_signup_form(self):
        """A Facebook account with no confirmed address returns no email."""
        response = facebook_sign_in(self.client, facebook_profile(email=None))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].startswith('/accounts/social/signup/'))
        self.assertEqual(User.objects.count(), 0)

    def test_graph_api_version_is_pinned_to_a_supported_one(self):
        """allauth defaults to v19.0, which Meta retired on 2026-05-21.

        allauth reads this at import time, so the pin has to be in the
        credentials table settings.py builds SOCIALACCOUNT_PROVIDERS from —
        not injected later.
        """
        from django.conf import settings as dj_settings

        version = dj_settings.SOCIAL_PROVIDER_CREDENTIALS['facebook']['settings']['VERSION']
        self.assertGreaterEqual(int(version.lstrip('v').split('.')[0]), 21)

    def test_authorize_url_uses_the_pinned_version(self):
        """The pin has to reach the URL allauth actually sends the user to."""
        import importlib

        from allauth.socialaccount.providers.facebook import constants

        # constants.py snapshots SOCIALACCOUNT_PROVIDERS at import, so reload
        # it under the override to see what a real boot with these credentials
        # would have produced.
        reloaded = importlib.reload(constants)
        try:
            self.assertEqual(reloaded.GRAPH_API_VERSION, 'v25.0')
            self.assertEqual(reloaded.GRAPH_API_URL, 'https://graph.facebook.com/v25.0')
        finally:
            importlib.reload(constants)

    def test_no_facebook_button_without_credentials(self):
        with override_settings(
            FACEBOOK_CLIENT_ID='', FACEBOOK_CLIENT_SECRET='', SOCIALACCOUNT_PROVIDERS={}
        ):
            response = self.client.get('/accounts/login/')
            self.assertNotContains(response, 'Continue with Facebook')

@override_settings(**ALL_ON)
class SocialConnectionManagementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'builder', password='correcthorse1', email='builder@example.com'
        )
        self.client.login(username='builder', password='correcthorse1')

    def test_settings_lists_connected_accounts(self):
        SocialAccount.objects.create(
            user=self.user, provider='github', uid='1', extra_data={'login': 'builder'}
        )
        response = self.client.get('/settings/')
        self.assertContains(response, 'Connected accounts')
        self.assertContains(response, 'GitHub')

    def test_settings_says_so_when_nothing_is_connected(self):
        response = self.client.get('/settings/')
        self.assertContains(response, 'No accounts connected yet')

    def test_connections_page_renders_with_our_layout(self):
        SocialAccount.objects.create(
            user=self.user, provider='github', uid='1', extra_data={'login': 'builder'}
        )
        response = self.client.get('/accounts/social/')
        self.assertContains(response, 'Disconnect selected')
        self.assertContains(response, 'process=connect')

    def test_a_provider_can_be_disconnected(self):
        account = SocialAccount.objects.create(
            user=self.user, provider='github', uid='1', extra_data={'login': 'builder'}
        )
        response = self.client.post('/accounts/social/', {'account': account.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SocialAccount.objects.count(), 0)

    def test_the_last_login_method_cannot_be_disconnected(self):
        """A social-only account must not be able to lock itself out."""
        self.user.set_unusable_password()
        self.user.save(update_fields=['password'])
        account = SocialAccount.objects.create(
            user=self.user, provider='github', uid='1', extra_data={'login': 'builder'}
        )
        self.client.force_login(self.user)
        self.client.post('/accounts/social/', {'account': account.pk})
        self.assertEqual(SocialAccount.objects.count(), 1)

    def test_settings_warns_a_password_less_account(self):
        self.user.set_unusable_password()
        self.user.save(update_fields=['password'])
        SocialAccount.objects.create(
            user=self.user, provider='github', uid='1', extra_data={'login': 'builder'}
        )
        self.client.force_login(self.user)
        response = self.client.get('/settings/')
        self.assertContains(response, 'only way into your account')

    def test_connecting_a_second_provider_keeps_one_account(self):
        github_sign_in(self.client, github_profile(email='other@example.com'), process='connect')
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(SocialAccount.objects.filter(user=self.user).count(), 1)

@override_settings(**ALL_ON)
class SocialAuthPageTests(TestCase):
    """The pages allauth renders must not 500 on a missing URL name."""

    def test_cancelled_page_renders(self):
        response = self.client.get('/accounts/social/login/cancelled/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sign-in cancelled')

    def test_error_page_renders(self):
        response = self.client.get('/accounts/social/login/error/')
        self.assertEqual(response.status_code, 401)

    def test_signup_page_without_a_pending_login_redirects_to_login(self):
        response = self.client.get('/accounts/social/signup/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/accounts/login/')

    def test_allauth_url_names_resolve_to_our_views(self):
        self.assertEqual(reverse('account_login'), '/accounts/login/')
        self.assertEqual(reverse('account_signup'), '/accounts/signup/')
