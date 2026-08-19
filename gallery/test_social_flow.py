"""End-to-end social signup through the REAL allauth machinery.

This is not a mock of the flow: it drives the actual URL routes
(/accounts/social/github/login/ → GitHub → /login/callback/ → signup
decision), the real session/state handling, the real provider
extraction (login → username), the real allauth SignupForm, and our
adapters. Only the two network hops to github.com are faked — the
OAuth token exchange and the profile fetch — because GitHub is outside
this codebase.

What this proves that unit tests cannot:
  * the routes promised by docs/specs/SOCIAL_AUTH.md actually resolve
    and run,
  * a dirty provider handle is refused auto-signup and routed to the
    signup form, where the real form rejects it again,
  * a clean provider handle signs up in one step and the handle lands
    on profile.github,
  * a dirty GitHub handle is never copied onto the public profile.
"""
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings

from gallery.tests import make_user

GITHUB_SETTINGS = {
    'github': {
        'APP': {'client_id': 'test-client', 'secret': 'test-secret', 'key': ''},
        'SCOPE': ['read:user', 'user:email'],
    },
}


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f'HTTP {self.status_code}')


class FakeSession:
    """Returns canned GitHub API responses per URL."""

    def __init__(self, profile):
        self.profile = profile

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, **kwargs):
        if url.endswith('/user'):
            return FakeResponse(self.profile)
        # /user/emails — GitHub documents a 404 here; allauth handles it.
        return FakeResponse([], status_code=404)


def _github_mocks(test_case, profile):
    """Patch only the network: the token exchange and the API session."""
    from unittest import mock
    from users.adapters import BlaqSocialAccountAdapter

    token_patch = mock.patch(
        'allauth.socialaccount.providers.oauth2.client.OAuth2Client.get_access_token',
        return_value={'access_token': 'fake-oauth-token', 'token_type': 'bearer'},
    )
    token_patch.start()
    test_case.addCleanup(token_patch.stop)

    session_patch = mock.patch.object(
        BlaqSocialAccountAdapter, 'get_requests_session',
        return_value=FakeSession(profile),
    )
    session_patch.start()
    test_case.addCleanup(session_patch.stop)


@override_settings(
    SOCIALACCOUNT_PROVIDERS=GITHUB_SETTINGS,
    RATELIMIT_ENABLE=False,
    SEED_DEMO=False,
)
class EndToEndGitHubSignupTests(TestCase):
    LOGIN_URL = '/accounts/social/github/login/?process=login'

    @staticmethod
    def _reload_urlconf():
        """The root urlconf mounts provider routes from the live
        SOCIALACCOUNT_PROVIDERS at import time; reload it so this class's
        override (and its removal) take effect."""
        import importlib
        from django.urls import clear_url_caches
        import blaqvibes.urls
        importlib.reload(blaqvibes.urls)
        clear_url_caches()

    def setUp(self):
        super().setUp()
        # Override active → github routes mounted.
        self._reload_urlconf()

    def tearDown(self):
        # Leave the urlconf the way a deployment without keys sees it.
        with override_settings(SOCIALACCOUNT_PROVIDERS={}):
            self._reload_urlconf()
        super().tearDown()

    def _start_oauth(self, profile):
        """POST the login button, follow nothing — return the authorize
        redirect's `state` (kept in the client session, like a browser)."""
        _github_mocks(self, profile)
        response = self.client.post(self.LOGIN_URL)
        self.assertEqual(response.status_code, 302)
        location = response['Location']
        self.assertIn('github.com/login/oauth/authorize', location)
        state = parse_qs(urlparse(location).query)['state'][0]
        return state

    def _callback(self, state):
        return self.client.get(
            f'/accounts/social/github/login/callback/?code=fake-code&state={state}'
        )

    def test_dirty_handle_is_refused_auto_signup_and_routed_to_form(self):
        profile = {'id': 777, 'login': 'fuckyou', 'name': 'Attacker',
                   'email': 'dirty@github.test'}
        state = self._start_oauth(profile)
        response = self._callback(state)
        # Auto-signup refused by BlaqSocialAccountAdapter.is_auto_signup_allowed
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].endswith('/accounts/social/signup/'))
        from django.contrib.auth.models import User
        self.assertFalse(User.objects.filter(username='fuckyou').exists())

        # The real signup form: the dirty handle is rejected on it too.
        signup_page = self.client.get('/accounts/social/signup/')
        self.assertEqual(signup_page.status_code, 200)
        response = self.client.post('/accounts/social/signup/', {
            'username': 'fuckyou', 'email': 'dirty@github.test',
        })
        self.assertEqual(response.status_code, 200)  # re-renders with error
        self.assertFalse(User.objects.filter(username='fuckyou').exists())

        # A clean handle finishes the signup.
        response = self.client.post('/accounts/social/signup/', {
            'username': 'reformed_dev', 'email': 'dirty@github.test',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        from django.contrib.auth.models import User
        from allauth.socialaccount.models import SocialAccount
        user = User.objects.get(username='reformed_dev')
        self.assertTrue(SocialAccount.objects.filter(user=user, provider='github').exists())
        user.profile.refresh_from_db()
        # The dirty GitHub handle is never copied onto the public profile.
        self.assertEqual(user.profile.github, '')
        self.assertTrue(user.profile.email_verified)

    def test_clean_handle_auto_signs_up_and_keeps_the_handle(self):
        profile = {'id': 778, 'login': 'octo_dev', 'name': 'Octo Dev',
                   'email': 'octo@github.test'}
        state = self._start_oauth(profile)
        response = self._callback(state)
        # Auto signup: straight in, no form stop.
        self.assertEqual(response.status_code, 302)
        self.assertFalse(response['Location'].endswith('/accounts/social/signup/'))
        from django.contrib.auth.models import User
        user = User.objects.get(username='octo_dev')
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.github, 'octo_dev')
        self.assertTrue(user.profile.email_verified)

    def test_existing_member_connects_instead_of_duplicating(self):
        member = make_user('member_x')
        member.email = 'member@github.test'
        member.save(update_fields=['email'])
        profile = {'id': 779, 'login': 'member_x_gh', 'name': 'Member X',
                   'email': 'member@github.test'}
        state = self._start_oauth(profile)
        self._callback(state)
        from django.contrib.auth.models import User
        # No second account for the same mailbox.
        self.assertEqual(User.objects.filter(email='member@github.test').count(), 1)

    def test_social_base_routes_always_resolve(self):
        """Signup/connections/error pages exist regardless of provider
        config — they host the form the gate routes people to."""
        from django.urls import resolve
        self.assertEqual(resolve('/accounts/social/signup/').view_name, 'socialaccount_signup')
        self.assertEqual(resolve('/accounts/social/login/error/').view_name, 'socialaccount_login_error')
        self.assertEqual(resolve('/accounts/social/login/cancelled/').view_name, 'socialaccount_login_cancelled')
