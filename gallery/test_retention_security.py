"""Regression tests for the retention-layer fixes.

Two classes of fix are covered here, both found during the full product +
security + retention review:

1. Battle visibility — ``battle()`` / ``battle_history()`` / ``vote_battle()``
   rendered (or let a person vote on) a ``VibeBattle`` whose vibe had been
   re-queued, quarantined or removed. A battle is a view of two vibes; if
   either has gone non-public it must not be surfaced to a stranger (the
   same rule every other content read enforces via ``user_can_see_project``).

2. Notification controls — the inbox marked everything read on open but had
   no way to mark exactly one read, no explicit mark-all, and no owner-scope
   guarantee beyond the existing inbox query. Added
   ``notifications_mark_read`` / ``notifications_mark_all_read`` and pinned
   the ``unread_notifications`` context processor.

Each test drives a real HTTP endpoint (or the exact function the endpoint
calls) and asserts server-side behaviour for anonymous, unrelated-signed-in
and authorized audiences.
"""
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase, override_settings

from gallery.models import Notification, VibeBattle

from .tests import make_category, make_project, make_user

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-retention-tests')
class BattleVisibilityTests(TestCase):
    """A battle never leaks metadata about a vibe that went non-public."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('bat_owner')
        self.other = make_user('bat_other')
        self.stranger = make_user('bat_stranger')
        self.mod = make_user('bat_mod', role='moderator')
        self.a = make_project(self.owner, self.cat, title='Battle Alpha')
        self.b = make_project(self.other, self.cat, title='Battle Beta')
        self.battle = VibeBattle.objects.create(vibe_a=self.a, vibe_b=self.b)

    def _hide_b(self, status='removed'):
        self.b.status = status
        self.b.save(update_fields=['status'])

    def test_history_hides_a_removed_vibe_from_strangers(self):
        self._hide_b('removed')
        body = self.client.get('/battle/history/').content.decode()
        self.assertNotIn('Battle Beta', body)
        self.assertNotIn('@bat_other', body)
        self.assertNotIn('Battle Alpha', body)  # neither card may show
        self.client.force_login(self.stranger)
        body = self.client.get('/battle/history/').content.decode()
        self.assertNotIn('Battle Beta', body)

    def test_history_hides_a_pending_vibe_from_anonymous_tour(self):
        self._hide_b('pending')
        body = self.client.get('/battle/history/').content.decode()
        self.assertNotIn('Battle Beta', body)
        # Alpha alone must not be shown as a battle (a battle is two vibes).
        self.assertNotIn('Battle Alpha', body)

    def test_history_still_shows_when_both_vibes_are_public(self):
        body = self.client.get('/battle/history/').content.decode()
        self.assertIn('Battle Alpha', body)
        self.assertIn('Battle Beta', body)

    def test_owner_still_sees_their_own_removed_vibe_in_history(self):
        self._hide_b('removed')
        self.client.force_login(self.other)
        body = self.client.get('/battle/history/').content.decode()
        self.assertIn('Battle Beta', body)  # owner keeps visibility

    def test_moderator_can_review_a_battle_with_a_removed_vibe(self):
        self._hide_b('removed')
        self.client.force_login(self.mod)
        body = self.client.get('/battle/history/').content.decode()
        self.assertIn('Battle Beta', body)

    def test_battle_page_never_returns_a_hidden_battle(self):
        self._hide_b('removed')
        # A previously-voted battle must be excluded for a stranger too; the
        # direct route renders the current battle, so just assert no leak.
        body = self.client.get('/battle/').content.decode()
        self.assertNotIn('Battle Beta', body)
        self.assertNotIn('Battle Alpha', body)

    def test_cannot_vote_on_a_battle_with_a_removed_vibe(self):
        self._hide_b('removed')
        self.client.force_login(self.stranger)
        response = self.client.post(f'/battle/{self.battle.id}/vote/', {'choice': 'a'})
        self.assertEqual(response.status_code, 404)
        self.battle.refresh_from_db()
        self.assertEqual(self.battle.votes_a, 0)

    def test_can_still_vote_on_a_fully_public_battle(self):
        self.client.force_login(self.stranger)
        response = self.client.post(f'/battle/{self.battle.id}/vote/', {'choice': 'a'})
        self.assertEqual(response.status_code, 302)
        self.battle.refresh_from_db()
        self.assertEqual(self.battle.votes_a, 1)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-retention-tests')
class NotificationControlsTests(TestCase):
    def setUp(self):
        self.alice = make_user('nt_alice')
        self.bob = make_user('nt_bob')
        self.n1 = Notification.objects.create(user=self.alice, kind='trade', title='Alice note 1')
        self.n2 = Notification.objects.create(user=self.alice, kind='star', title='Alice note 2', is_read=True)
        self.bob_note = Notification.objects.create(user=self.bob, kind='follow', title='Bob secret')

    def test_mark_one_read_returns_unread_count(self):
        self.client.force_login(self.alice)
        response = self.client.post(f'/inbox/{self.n1.id}/read/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['unread'], 0)  # n1 just read; n2 already read
        self.n1.refresh_from_db()
        self.assertTrue(self.n1.is_read)

    def test_mark_one_read_is_owner_scoped(self):
        self.client.force_login(self.alice)
        response = self.client.post(f'/inbox/{self.bob_note.id}/read/')
        self.assertEqual(response.status_code, 404)
        self.bob_note.refresh_from_db()
        self.assertFalse(self.bob_note.is_read)

    def test_mark_one_read_is_idempotent(self):
        self.client.force_login(self.alice)
        self.client.post(f'/inbox/{self.n1.id}/read/')
        response = self.client.post(f'/inbox/{self.n1.id}/read/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['unread'], 0)

    def test_mark_all_read_is_scoped_to_the_user(self):
        self.client.force_login(self.alice)
        response = self.client.post('/inbox/read-all/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['unread'], 0)
        self.n1.refresh_from_db()
        self.assertTrue(self.n1.is_read)
        # Bob's row is untouched.
        self.bob_note.refresh_from_db()
        self.assertFalse(self.bob_note.is_read)

    def test_mark_all_read_requires_login(self):
        response = self.client.post('/inbox/read-all/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_mark_one_read_requires_login(self):
        response = self.client.post(f'/inbox/{self.n1.id}/read/')
        self.assertEqual(response.status_code, 302)

    def test_inbox_marks_all_read_on_open(self):
        self.client.force_login(self.alice)
        self.client.get('/inbox/')
        self.n1.refresh_from_db()
        self.assertTrue(self.n1.is_read)

    def test_unread_count_context_processor_counts_only_unread(self):
        from gallery.context_processors import extras
        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.alice
        self.assertEqual(extras(request)['unread_notifications'], 1)

    def test_unread_count_is_zero_for_anonymous(self):
        from gallery.context_processors import extras
        factory = RequestFactory()
        request = factory.get('/')
        request.user = AnonymousUser()
        self.assertEqual(extras(request)['unread_notifications'], 0)

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-retention-tests')
class StarEndpointGateTests(TestCase):
    """The star write must be a logged-in POST, like every other write.

    Before the gate an unauthenticated GET/POST reached
    ``toggle_project_star(request.user, ...)`` with ``AnonymousUser``.
    The FK to ``Star.user`` then failed on a NOT NULL constraint and the
    ``IntegrityError`` was swallowed as ``True`` — so an anonymous visitor
    was told they'd starred a vibe they never affected, and the ``if
    project.owner_id != request.user.id`` line that follows ran on a user
    with no id. It was harmless in practice (no row, no counter move, no
    notification) but it was a write endpoint answering unauthenticated,
    which is the exact shape a real account-takeover or spam avenue starts
    from. ``toggle_bookmark`` already had the gate; ``toggle_star`` should
    match.
    """

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('star_owner')
        self.fan = make_user('star_fan')
        self.project = make_project(self.owner, self.cat, title='Starred vibe')

    def test_anonymous_get_redirects_to_login(self):
        # login_required runs before require_POST, so an anonymous GET is a
        # login redirect (not a 405) — same as toggle_bookmark. The point is
        # it never reaches the wallet/star write.
        response = self.client.get(f'/app/{self.project.slug}/star/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_anonymous_post_redirects_to_login(self):
        response = self.client.post(f'/app/{self.project.slug}/star/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_logged_in_post_still_stars(self):
        self.client.force_login(self.fan)
        response = self.client.post(f'/app/{self.project.slug}/star/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['starred'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.stars, 1)
        from gallery.models import Star
        self.assertTrue(Star.objects.filter(user=self.fan, project=self.project).exists())

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-retention-tests')
class AdminTrustWritabilityTests(TestCase):
    """``AppProject.trust`` may be written only by the trust pipeline.
        The marketplace ranks on ``trust`` (verified / scanned / human-checked) —
        it is the moat. Only ``gallery/trust.py`` may set it. Letting a superuser
        hand-edit it in the Django admin would bypass the pipeline, so the field
        is read-only in ``AppProjectAdmin``.
    """

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('trust_owner')
        self.project = make_project(self.owner, self.cat, title='Trust Vibe', trust=42)

    def test_admin_renders_trust_as_readonly(self):
        admin = User.objects.create_superuser('trust_admin', 'trust@test.com', 'pass12345')
        self.client.force_login(admin)
        url = f'/blaq-admin-secure/gallery/appproject/{self.project.id}/change/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The field is present, but inside Django's readonly container — it
        # cannot be submitted as an editable value.
        self.assertIn('field-trust', body)
        self.assertIn('readonly', body)

    def test_admin_post_cannot_change_trust(self):
        # Craft a full admin change POST from the object's current values
        # (as the admin form would), but try to set trust to the "best"
        # value. Because trust is readonly the submitted value is ignored.
        admin = User.objects.create_superuser('trust_admin2', 'trust2@test.com', 'pass12345')
        self.client.force_login(admin)
        market = make_project(self.owner, self.cat, title='Market Vibe')
        market.trust = '10'
        market.save(update_fields=['trust'])
        url = f'/blaq-admin-secure/gallery/appproject/{market.id}/change/'
        post = {
            'title': market.title,
            'owner': market.owner_id,
            'category': market.category_id,
            'slug': market.slug,
            'status': market.status,
            'short_description': market.short_description or '',
            'readme': market.readme or '',
            'star_cost': market.star_cost,
            'price_zar': market.price_zar,
            'zip_file': '',
            'trust': 99,  # must be ignored
            '_save': 'Save',
        }
        self.client.post(url, post)
        market.refresh_from_db()
        self.assertEqual(market.trust, '10')
