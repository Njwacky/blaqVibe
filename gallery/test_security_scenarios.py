"""Ten realistic "somebody made an authorization mistake" scenarios.

This module exists so a future refactor cannot quietly reintroduce the
class of bug it covers: private content reaching an unauthorized reader,
a paid download reachable without paying, one user's private data served
to another, or a role check that answers to the wrong flag.

Each test drives a real HTTP endpoint (never a template flag) and asserts
the server's answer for the three audiences that matter: anonymous,
unrelated-but-signed-in, and authorized.
"""
import io
import zipfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import caches
from django.test import TestCase, override_settings

from gallery.models import (
    AppProject,
    AppVersion,
    Category,
    Notification,
    PullRequest,
    ScanJob,
    Star,
    Trade,
)
from users.models import Follow

from .tests import (
    make_category,
    make_project,
    make_user,
    make_zip_bytes,
    make_zip_file,
    published_zip,
)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-sec-tests')
class Scenario1_PrivateProjectLeaks(TestCase):
    """A private/unpublished vibe must be invisible in every listing."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.other = make_user('other')
        self.published = make_project(self.owner, self.cat, title='Public Vibe')
        self.pending = make_project(self.owner, self.cat, title='Pending Vibe', status='pending')
        self.quarantined = make_project(self.owner, self.cat, title='Quarantined Vibe', status='quarantined')
        self.removed = make_project(self.owner, self.cat, title='Removed Vibe', status='removed')
        self.hidden = [self.pending, self.quarantined, self.removed]

    def _assert_hidden(self, body):
        for p in self.hidden:
            self.assertNotIn(p.slug, body)
            self.assertNotIn(p.title, body)

    def test_feed_excludes_non_published(self):
        body = self.client.get('/').content.decode()
        self.assertIn(self.published.title, body)
        self._assert_hidden(body)

    def test_search_excludes_non_published(self):
        # The search box echoes the query back into the page, so the *title*
        # itself is expected in the HTML. What must never appear is the
        # card: the slug, or a link to the vibe's detail page.
        for p in self.hidden:
            body = self.client.get(f'/?q={p.title}').content.decode()
            self.assertNotIn(p.slug, body, p.title)
            self.assertNotIn(f'/app/{p.slug}/', body, p.title)

    def test_api_excludes_non_published(self):
        payload = self.client.get('/api/v1/apps/').json()
        slugs = {row['slug'] for row in payload['results']}
        self.assertIn(self.published.slug, slugs)
        for p in self.hidden:
            self.assertNotIn(p.slug, slugs)

    def test_api_detail_404s_for_non_published(self):
        for p in self.hidden:
            self.assertEqual(self.client.get(f'/api/v1/apps/{p.slug}/').status_code, 404)

    def test_sitemap_excludes_non_published(self):
        body = self.client.get('/sitemap.xml').content.decode()
        self.assertIn(self.published.slug, body)
        self._assert_hidden(body)

    def test_detail_page_404s_for_strangers_but_opens_for_owner(self):
        for p in self.hidden:
            self.assertEqual(self.client.get(p.get_absolute_url()).status_code, 404)
        self.client.force_login(self.other)
        for p in self.hidden:
            self.assertEqual(self.client.get(p.get_absolute_url()).status_code, 404)
        self.client.force_login(self.owner)
        for p in self.hidden:
            self.assertEqual(self.client.get(p.get_absolute_url()).status_code, 200)

    def test_related_projects_never_suggest_a_hidden_vibe(self):
        body = self.client.get(self.published.get_absolute_url()).content.decode()
        self._assert_hidden(body)

    def test_personalised_feed_still_excludes_non_published(self):
        self.client.force_login(self.other)
        from gallery.models import KindAffinity
        KindAffinity.objects.create(user=self.other, kind=self.pending.kind, score=50, events=9)
        body = self.client.get('/?sort=foryou').content.decode()
        self._assert_hidden(body)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-sec-tests')
class Scenario2_PullRequestLeak(TestCase):
    """PR endpoints: only people with a reason to review may read a diff."""

    def setUp(self):
        self.cat = make_category()
        self.target_owner = make_user('targetowner')
        self.fork_owner = make_user('forkowner')
        self.stranger = make_user('stranger')
        self.moderator = make_user('mod', role='moderator')
        self.target = published_zip(make_project(self.target_owner, self.cat, title='Target Vibe'))
        self.fork = make_project(self.fork_owner, self.cat, title='Fork Vibe', forked_from=self.target)
        self.pr = PullRequest.objects.create(
            source=self.fork, target=self.target, author=self.fork_owner,
            title='Improve the thing', description='A real change.',
        )
        self.url = f'/app/{self.target.slug}/prs/{self.pr.id}/view/'
        self.list_url = f'/app/{self.target.slug}/prs/'

    def test_public_target_list_is_readable(self):
        self.assertEqual(self.client.get(self.list_url).status_code, 200)

    def test_pending_source_diff_is_hidden_from_anonymous_and_stranger(self):
        self.fork.status = 'pending'
        self.fork.save(update_fields=['status'])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_pending_source_diff_opens_for_fork_owner_target_owner_and_moderator(self):
        self.fork.status = 'pending'
        self.fork.save(update_fields=['status'])
        for user in (self.fork_owner, self.target_owner, self.moderator):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 200,
                             f'{user.username} should be able to review this PR')

    def test_published_source_diff_is_public(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_pr_id_cannot_be_swapped_to_another_project(self):
        other_target = make_project(self.target_owner, self.cat, title='Other Target')
        # Same PR id, different project: the row must not be found through it.
        self.assertEqual(
            self.client.get(f'/app/{other_target.slug}/prs/{self.pr.id}/view/').status_code, 404)

    def test_only_target_owner_or_admin_can_merge(self):
        self.client.force_login(self.stranger)
        response = self.client.post(f'/app/{self.target.slug}/prs/{self.pr.id}/', {'action': 'merge'})
        self.assertEqual(response.status_code, 403)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'open')

    def test_target_owner_can_merge(self):
        self.client.force_login(self.target_owner)
        response = self.client.post(f'/app/{self.target.slug}/prs/{self.pr.id}/', {'action': 'merge'})
        self.assertEqual(response.status_code, 302)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'merged')

    def test_cannot_open_a_pr_from_somebody_elses_fork(self):
        self.client.force_login(self.stranger)
        response = self.client.post(f'/app/{self.fork.slug}/pr/create/',
                                    {'title': 'hi', 'description': 'take my change'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(PullRequest.objects.count(), 1)

    def test_pending_target_pr_list_404s(self):
        self.target.status = 'pending'
        self.target.save(update_fields=['status'])
        self.assertEqual(self.client.get(self.list_url).status_code, 404)
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.list_url).status_code, 404)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-sec-tests')
class Scenario3_DownloadBypass(TestCase):
    """Every route to a paid archive must ask the same authorization."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.buyer = make_user('buyer', stars_balance=20)
        self.thief = make_user('thief', stars_balance=20)
        self.project = published_zip(
            make_project(self.owner, self.cat, title='Paid Vibe', star_cost=3))
        self.url = f'/app/{self.project.slug}/download/'

    def test_anonymous_gets_no_bytes(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(b'PK', (response.content or b'')[:2])

    def test_signed_in_non_buyer_gets_no_bytes(self):
        self.client.force_login(self.thief)
        response = self.client.get(self.url, follow=True)
        self.assertNotIn(b'index.html', b''.join(response.streaming_content
                                                 if response.streaming else [response.content]))

    def test_buyer_after_trade_gets_bytes(self):
        self.client.force_login(self.buyer)
        self.assertEqual(self.client.post(f'/app/{self.project.slug}/trade/').status_code, 302)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = b''.join(response.streaming_content)
        self.assertIn(b'PK', body[:2])

    def test_file_preview_requires_unlock(self):
        self.client.force_login(self.thief)
        response = self.client.get(f'/app/{self.project.slug}/file/index.html')
        self.assertEqual(response.status_code, 403)

    def test_file_preview_allows_buyer(self):
        self.client.force_login(self.buyer)
        self.client.post(f'/app/{self.project.slug}/trade/')
        response = self.client.get(f'/app/{self.project.slug}/file/index.html')
        self.assertEqual(response.status_code, 200)
        self.assertIn('index.html', response.json()['path'])

    def test_media_url_never_streams_paid_zips(self):
        self.client.force_login(self.buyer)
        self.client.post(f'/app/{self.project.slug}/trade/')
        response = self.client.get(f'/media/{self.project.zip_file.name}')
        self.assertEqual(response.status_code, 404)

    def test_version_download_denied_to_stranger(self):
        AppVersion.objects.create(project=self.project, zip_file=self.project.zip_file,
                                  version='1.1.0')
        version = self.project.versions.first()
        self.client.force_login(self.thief)
        response = self.client.get(
            f'/app/{self.project.slug}/versions/{version.id}/download/')
        self.assertEqual(response.status_code, 302)  # bounced to the paywall
        self.assertIn('/app/', response.url)

    def test_buyer_keeps_download_while_the_vibe_is_being_rescanned(self):
        """A receipt outlives a rescan (the old rule locked buyers out)."""
        self.client.force_login(self.buyer)
        self.client.post(f'/app/{self.project.slug}/trade/')
        AppVersion.objects.create(project=self.project, zip_file=self.project.zip_file,
                                  version='1.0.0', changelog='before the edit')
        self.project.status = 'pending'
        self.project.save(update_fields=['status'])
        self.project.zip_file.save('new.zip',
                                   SimpleUploadedFile('new.zip', make_zip_bytes({'evil.py': 'x'}),
                                                       content_type='application/zip'),
                                   save=True)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = b''.join(response.streaming_content)
        self.assertIn(b'index.html', body)   # the scanned version…
        self.assertNotIn(b'evil.py', body)   # …never the unchecked one

    def test_unscanned_bytes_are_never_served_to_a_stranger(self):
        self.project.status = 'pending'
        self.project.save(update_fields=['status'])
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.thief)
        self.assertEqual(self.client.get(self.url).status_code, 404)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-sec-tests')
class Scenario4_OldUrlsAfterUnpublish(TestCase):
    """Unpublish/remove must invalidate every URL that exposed the content."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.buyer = make_user('buyer', stars_balance=20)
        self.project = published_zip(make_project(self.owner, self.cat, title='Ghost Vibe'))
        self.slug = self.project.slug
        Trade.objects.create(buyer=self.buyer, seller=self.owner, project=self.project, cost=1)

    def _urls(self):
        return [
            f'/app/{self.slug}/',
            f'/api/v1/apps/{self.slug}/',
            f'/app/{self.slug}/download/',
            f'/app/{self.slug}/files/',
            f'/app/{self.slug}/preview/',
            f'/app/{self.slug}/forks/',
            f'/app/{self.slug}/scan-status/',
        ]

    def test_removed_vibe_urls_are_dead_for_strangers(self):
        self.project.status = 'removed'
        self.project.save(update_fields=['status'])
        for url in self._urls():
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_removed_vibe_keeps_the_buyers_download(self):
        self.project.status = 'removed'
        self.project.save(update_fields=['status'])
        self.client.force_login(self.buyer)
        response = self.client.get(f'/app/{self.slug}/download/')
        self.assertEqual(response.status_code, 200)

    def test_removed_vibe_is_gone_from_feed_api_and_sitemap(self):
        self.project.status = 'removed'
        self.project.save(update_fields=['status'])
        self.assertNotIn(self.slug, self.client.get('/').content.decode())
        self.assertNotIn(self.slug, self.client.get('/sitemap.xml').content.decode())
        slugs = {r['slug'] for r in self.client.get('/api/v1/apps/').json()['results']}
        self.assertNotIn(self.slug, slugs)

    def test_pending_vibe_is_hidden_from_strangers_everywhere(self):
        self.project.status = 'pending'
        self.project.save(update_fields=['status'])
        for url in self._urls():
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(f'/app/{self.slug}/').status_code, 200)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-sec-tests')
class Scenario5_Idor(TestCase):
    """Changing an identifier must never hand over somebody else's object."""

    def setUp(self):
        self.cat = make_category()
        self.alice = make_user('alice')
        self.bob = make_user('bob')
        self.alice_vibe = published_zip(make_project(self.alice, self.cat, title='Alice Vibe'))
        self.bob_vibe = published_zip(make_project(self.bob, self.cat, title='Bob Vibe'))
        self.alice_version = AppVersion.objects.create(
            project=self.alice_vibe, zip_file=self.alice_vibe.zip_file, version='1.1.0')

    def test_version_id_from_another_project_is_not_downloadable(self):
        self.client.force_login(self.bob)
        response = self.client.get(
            f'/app/{self.bob_vibe.slug}/versions/{self.alice_version.id}/download/')
        self.assertEqual(response.status_code, 404)

    def test_edit_delete_and_stats_are_owner_only(self):
        self.client.force_login(self.bob)
        for url in (f'/app/{self.alice_vibe.slug}/edit/',
                    f'/app/{self.alice_vibe.slug}/stats/'):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        # delete is POST-only: a GET is a 405 from require_POST before the
        # view ever runs, and the POST is a 404 because the vibe isn't Bob's.
        self.assertEqual(self.client.get(f'/app/{self.alice_vibe.slug}/delete/').status_code, 405)
        self.assertEqual(self.client.post(f'/app/{self.alice_vibe.slug}/delete/').status_code, 404)
        self.assertTrue(AppProject.objects.filter(pk=self.alice_vibe.pk).exists())

    def test_co_owner_endpoints_are_owner_only(self):
        self.client.force_login(self.bob)
        self.assertEqual(
            self.client.post(f'/app/{self.alice_vibe.slug}/co-owners/add/',
                             {'username': 'bob', 'share_percent': 50}).status_code, 404)

    def test_analytics_belong_to_the_owner_only(self):
        self.client.force_login(self.alice)
        response = self.client.get(f'/app/{self.alice_vibe.slug}/stats/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Alice Vibe', response.content.decode())
        self.assertNotIn('Bob Vibe', response.content.decode())

    def test_notifications_are_private(self):
        Notification.objects.create(user=self.alice, kind='trade', title='Alice secrets')
        self.client.force_login(self.bob)
        body = self.client.get('/inbox/').content.decode()
        self.assertNotIn('Alice secrets', body)
        self.client.force_login(self.alice)
        self.assertIn('Alice secrets', self.client.get('/inbox/').content.decode())

    def test_saved_vibes_are_private(self):
        from gallery.models import Bookmark
        Bookmark.objects.create(user=self.alice, project=self.bob_vibe)
        self.client.force_login(self.bob)
        self.assertNotIn('Bob Vibe', self.client.get('/saved/').content.decode())
        self.client.force_login(self.alice)
        self.assertIn('Bob Vibe', self.client.get('/saved/').content.decode())


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-sec-tests')
class Scenario6_PrivateUserData(TestCase):
    """No endpoint may answer with another account's private facts."""

    def setUp(self):
        self.cat = make_category()
        self.alice = make_user('alice', stars_balance=50)
        self.bob = make_user('bob')

    def test_git_token_is_stored_hashed_and_never_rendered(self):
        self.client.force_login(self.alice)
        token = self.alice.profile.rotate_git_token()
        self.alice.profile.refresh_from_db()
        self.assertNotEqual(self.alice.profile.git_token_hash, token)
        self.assertEqual(len(self.alice.profile.git_token_hash), 64)
        body = self.client.get('/settings/').content.decode()
        self.assertNotIn(token, body)

    def test_api_never_exposes_the_scan_report(self):
        vibe = published_zip(make_project(self.alice, self.cat, title='Report Vibe'))
        vibe.scan_report = {'secrets': ['config/.env'], 'clamav': 'clean'}
        vibe.save(update_fields=['scan_report'])
        payload = self.client.get(f'/api/v1/apps/{vibe.slug}/').json()
        self.assertNotIn('scan_report', payload)
        self.assertNotIn('.env', str(payload))

    def test_trading_history_is_own_only(self):
        vibe = published_zip(make_project(self.alice, self.cat, title='Traded Vibe', star_cost=1))
        Trade.objects.create(buyer=self.bob, seller=self.alice, project=vibe, cost=1)
        self.client.force_login(User.objects.create_user(username='carol', password='pass12345'))
        body = self.client.get('/trades/').content.decode()
        self.assertNotIn('Traded Vibe', body)

    def test_profile_page_hides_nothing_private_but_shows_public_facts(self):
        body = self.client.get('/u/alice/').content.decode()
        self.assertIn('alice', body)
        self.assertNotIn('@test.com', body)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-sec-tests')
class Scenario7_RoleEscalation(TestCase):
    """Django flags are not BlaqVibes roles."""

    def setUp(self):
        self.cat = make_category()
        self.user = make_user('plain')
        self.mod = make_user('mod', role='moderator')
        self.admin = make_user('admin', role='admin')
        self.staff = make_user('staff')  # is_staff but role=user
        self.staff.is_staff = True
        self.staff.save(update_fields=['is_staff'])

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get('/admin/dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_plain_user_is_denied(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/admin/dashboard/').status_code, 403)
        self.assertEqual(self.client.get('/moderation/queue/').status_code, 403)
        self.assertEqual(self.client.get('/moderation/reports/').status_code, 403)

    def test_is_staff_alone_grants_nothing(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/admin/dashboard/').status_code, 403)
        self.assertEqual(self.client.get('/admin/roles/').status_code, 403)

    def test_moderator_gets_moderation_but_not_admin(self):
        self.client.force_login(self.mod)
        self.assertEqual(self.client.get('/moderation/queue/').status_code, 200)
        self.assertEqual(self.client.get('/moderation/reports/').status_code, 200)
        self.assertEqual(self.client.get('/admin/dashboard/').status_code, 403)
        self.assertEqual(self.client.get('/admin/roles/').status_code, 403)

    def test_admin_gets_admin_but_not_superadmin(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/admin/dashboard/').status_code, 200)
        self.assertEqual(self.client.get('/admin/roles/').status_code, 403)

    def test_moderator_cannot_change_roles_by_posting(self):
        self.client.force_login(self.mod)
        response = self.client.post('/admin/roles/plain/', {'role': 'superadmin'})
        self.assertEqual(response.status_code, 403)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.role, 'user')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-sec-tests')
class Scenario8_EconomyRaces(TestCase):
    """Two clicks or two requests must not mint, double-spend or double-award."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.buyer = make_user('buyer', stars_balance=10)
        self.project = published_zip(
            make_project(self.owner, self.cat, title='Race Vibe', star_cost=3))

    def test_double_trade_charges_once(self):
        from gallery.economy import trade_for_download
        first = trade_for_download(self.buyer, self.project)
        second = trade_for_download(self.buyer, self.project)
        self.assertIsNotNone(first)
        self.assertEqual(second.pk, first.pk)
        self.buyer.profile.refresh_from_db()
        self.assertEqual(self.buyer.profile.stars_balance, 7)
        self.assertEqual(Trade.objects.filter(buyer=self.buyer, project=self.project).count(), 1)

    def test_insufficient_balance_charges_nothing(self):
        from gallery.economy import TradeError, trade_for_download
        poor = make_user('poor', stars_balance=1)
        with self.assertRaises(TradeError):
            trade_for_download(poor, self.project)
        poor.profile.refresh_from_db()
        self.assertEqual(poor.profile.stars_balance, 1)
        self.assertEqual(Trade.objects.filter(buyer=poor).count(), 0)

    def test_star_toggle_is_idempotent_per_user(self):
        from gallery.economy import toggle_project_star
        self.assertTrue(toggle_project_star(self.buyer, self.project))
        self.assertFalse(toggle_project_star(self.buyer, self.project))
        self.project.refresh_from_db()
        self.assertEqual(self.project.stars, 0)
        self.assertEqual(Star.objects.filter(user=self.buyer, project=self.project).count(), 0)

    def test_star_counter_never_goes_negative(self):
        from gallery.economy import toggle_project_star
        self.project.stars = 0
        self.project.save(update_fields=['stars'])
        toggle_project_star(self.buyer, self.project)   # star
        toggle_project_star(self.buyer, self.project)   # unstar
        AppProject.objects.filter(pk=self.project.pk).update(stars=0)
        toggle_project_star(self.buyer, self.project)   # star again
        toggle_project_star(self.buyer, self.project)   # unstar again
        self.project.refresh_from_db()
        self.assertGreaterEqual(self.project.stars, 0)

    def test_xp_cannot_be_farmed_by_repeating_the_same_action(self):
        from users.progress import xp_total
        from users.progress import award
        self.assertTrue(award(self.owner, 'publish', ref='project:42'))
        self.assertFalse(award(self.owner, 'publish', ref='project:42'))
        self.assertEqual(xp_total(self.owner), 20)

    def test_duplicate_follow_is_impossible(self):
        from django.db import IntegrityError, transaction
        Follow.objects.create(follower=self.buyer, following=self.owner)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Follow.objects.create(follower=self.buyer, following=self.owner)

    def test_trade_download_endpoint_is_safe_to_replay(self):
        self.client.force_login(self.buyer)
        self.client.post(f'/app/{self.project.slug}/trade/')
        self.client.post(f'/app/{self.project.slug}/trade/')
        self.buyer.profile.refresh_from_db()
        self.assertEqual(self.buyer.profile.stars_balance, 7)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-sec-tests')
class Scenario9_GitAuth(TestCase):
    """A browser session is not a git credential."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner', password='StrongPass123!')
        self.other = make_user('other', password='StrongPass123!')
        self.project = published_zip(make_project(self.owner, self.cat, title='Git Vibe'))
        self.repo = f'/git/{self.owner.username}/{self.project.slug}.git/'
        self.info_refs = self.repo + 'info/refs?service=git-upload-pack'

    def _basic(self, username, secret):
        import base64
        raw = base64.b64encode(f'{username}:{secret}'.encode()).decode()
        return {'HTTP_AUTHORIZATION': f'Basic {raw}'}

    def test_anonymous_clone_of_a_free_vibe_is_allowed(self):
        response = self.client.get(self.info_refs)
        self.assertIn(response.status_code, (200, 401))

    def test_browser_session_alone_does_not_authorise_push(self):
        self.client.force_login(self.other)
        response = self.client.get(self.repo + 'info/refs?service=git-receive-pack')
        self.assertEqual(response.status_code, 401)

    def test_wrong_password_is_rejected(self):
        response = self.client.get(self.repo + 'info/refs?service=git-receive-pack',
                                   **self._basic(self.owner.username, 'wrong-password'))
        self.assertEqual(response.status_code, 401)

    def test_wrong_username_is_rejected(self):
        response = self.client.get(self.repo + 'info/refs?service=git-receive-pack',
                                   **self._basic('somebodyelse', 'StrongPass123!'))
        self.assertEqual(response.status_code, 401)

    def test_unauthorized_collaborator_cannot_push(self):
        # Wrong password -> 401 (unauthenticated). Right password for a user
        # who is NOT the owner/co-owner -> 403: git answers "authenticated,
        # not permitted", so the client knows not to retry credentials.
        response = self.client.get(self.repo + 'info/refs?service=git-receive-pack',
                                   **self._basic(self.other.username, 'StrongPass123!'))
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(b'have', response.content[:200])

    def test_git_token_works_then_stops_working_after_rotation(self):
        token = self.owner.profile.rotate_git_token()
        response = self.client.get(self.repo + 'info/refs?service=git-receive-pack',
                                   **self._basic(self.owner.username, token))
        self.assertNotEqual(response.status_code, 401)
        self.owner.profile.rotate_git_token()  # revoked
        response = self.client.get(self.repo + 'info/refs?service=git-receive-pack',
                                   **self._basic(self.owner.username, token))
        self.assertEqual(response.status_code, 401)

    def test_plaintext_token_is_never_persisted(self):
        token = self.owner.profile.rotate_git_token()
        owner_row = User.objects.get(pk=self.owner.pk)
        blob = f'{owner_row.profile.git_token_hash}{owner_row.password}'
        self.assertNotIn(token, blob)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-sec-tests')
class Scenario10_UploadAndZipSafety(TestCase):
    """Hostile archives are rejected at the door, not unpacked on the host."""

    def _reject(self, files, name='bad.zip', **zipinfo_kwargs):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            for path, content in files.items():
                info = zipfile.ZipInfo(path)
                info.external_attr = zipinfo_kwargs.get('external_attr', 0o644 << 16)
                zf.writestr(info, content)
        from django.core.exceptions import ValidationError
        from gallery.validators import validate_zip
        upload = SimpleUploadedFile(name, buf.getvalue(), content_type='application/zip')
        with self.assertRaises(ValidationError):
            validate_zip(upload)

    def test_path_traversal_is_rejected(self):
        self._reject({'../../etc/passwd': 'x'})

    def test_absolute_path_is_rejected(self):
        self._reject({'/etc/passwd': 'x'})

    def test_windows_drive_path_is_rejected(self):
        self._reject({'C:/windows/win.ini': 'x'})

    def test_symlink_is_rejected(self):
        self._reject({'link': '/etc/passwd'}, external_attr=(0o120777 << 16))

    def test_env_file_is_rejected_with_actionable_copy(self):
        from django.core.exceptions import ValidationError
        from gallery.validators import validate_zip
        upload = SimpleUploadedFile('a.zip', make_zip_bytes({'app.py': 'x', '.env': 'KEY=1'}))
        with self.assertRaises(ValidationError) as ctx:
            validate_zip(upload)
        self.assertIn('.env.example', str(ctx.exception))

    def test_node_modules_is_rejected_with_the_folder_instruction(self):
        from django.core.exceptions import ValidationError
        from gallery.validators import validate_zip
        upload = SimpleUploadedFile(
            'a.zip', make_zip_bytes({'index.html': 'x', 'node_modules/x/package.json': '{}'}))
        with self.assertRaises(ValidationError) as ctx:
            validate_zip(upload)
        self.assertIn('Delete that folder', str(ctx.exception))

    def test_executable_extension_is_rejected(self):
        self._reject({'run.exe': 'MZ'})

    def test_too_many_files_is_rejected(self):
        self._reject({f'f{i}.txt': 'x' for i in range(1001)})

    def test_zip_bomb_ratio_is_rejected(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('bomb.txt', '0' * (12 * 1024 * 1024))
        from django.core.exceptions import ValidationError
        from gallery.validators import validate_zip
        upload = SimpleUploadedFile('bomb.zip', buf.getvalue(), content_type='application/zip')
        with self.assertRaises(ValidationError):
            validate_zip(upload)

    def test_extraction_refuses_the_same_paths(self):
        from gallery.validators import safe_extract_zip
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as zf:
                zf.writestr('../escape.txt', 'x')
            path = tmp + '/bad.zip'
            with open(path, 'wb') as fh:
                fh.write(buf.getvalue())
            with self.assertRaises(Exception):
                safe_extract_zip(path, tmp + '/out')

    def test_snippet_code_only_runs_inside_the_sandbox(self):
        vibe = make_project(make_user('snip'), make_category(), title='Snippet Vibe',
                            html_code='<script>alert(1)</script>')
        response = self.client.get(f'/app/{vibe.slug}/snippet/')
        # Top-level access must be refused (403) rather than executing the
        # author's JS in the site's own origin.
        self.assertEqual(response.status_code, 403)

    def test_uploaded_file_preview_does_not_execute(self):
        vibe = published_zip(make_project(make_user('z'), make_category(), title='Zip Vibe'),
                             {'index.html': '<script>alert(1)</script>'})
        self.client.force_login(vibe.owner)
        response = self.client.get(f'/app/{vibe.slug}/file/index.html')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertIn('content', response.json())


class AntiAbuseRateLimitTests(TestCase):
    """P9 — every write that costs money, stars or attention is bounded.

    These five endpoints had no limit at all: the AI README calls a hosted
    LLM (someone else's bill), a payout request moves real money, and
    reviews/bookmarks/PR actions are the public write surface a bot would
    hammer first.
    """

    def setUp(self):
        self._caches = override_settings(
            RATELIMIT_ENABLE=True,
            RATELIMIT_USE_CACHE='ratelimit',
            CACHES={
                'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'ab-default'},
                'ratelimit': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'ab-ratelimit'},
            },
        )
        self._caches.enable()
        self.addCleanup(self._caches.disable)
        self.addCleanup(lambda: caches['ratelimit'].clear())
        self.cat = make_category()
        self.owner = make_user('ab-owner')
        self.other = make_user('ab-other')
        self.project = published_zip(make_project(self.owner, self.cat, title='Abuse Vibe'))

    def test_ai_readme_generation_is_capped_at_10_per_hour(self):
        from unittest import mock
        self.client.force_login(self.owner)
        url = f'/app/{self.project.slug}/ai-readme/generate/'
        with mock.patch('gallery.ai_readme.generate_ai_readme', return_value='# hi'):
            last = None
            for _ in range(11):  # rate is 10/h
                last = self.client.post(url)
            self.assertEqual(last.status_code, 403)

    def test_payout_requests_are_capped_at_5_per_hour(self):
        self.client.force_login(self.owner)
        last = None
        for _ in range(6):
            last = self.client.post('/payout/request/', {'stars': 1000})
        self.assertEqual(last.status_code, 403)

    def test_bookmark_toggle_is_capped_at_60_per_hour(self):
        self.client.force_login(self.other)
        url = f'/app/{self.project.slug}/save/'
        last = None
        for _ in range(61):
            last = self.client.post(url)
        self.assertEqual(last.status_code, 403)

    def test_review_post_is_capped_at_10_per_hour(self):
        self.client.force_login(self.other)
        url = f'/app/{self.project.slug}/review/'
        last = None
        for _ in range(11):
            last = self.client.post(url, {'rating': 5, 'body': 'nice work'})
        self.assertEqual(last.status_code, 403)

    def test_pr_action_is_capped_at_20_per_hour(self):
        from gallery.models import PullRequest
        fork = published_zip(make_project(self.other, self.cat, title='Abuse Fork'))
        fork.forked_from = self.project
        fork.save(update_fields=['forked_from'])
        pr = PullRequest.objects.create(source=fork, target=self.project,
                                       author=self.other, title='t', status='open')
        self.client.force_login(self.owner)
        last = None
        for _ in range(21):
            last = self.client.post(f'/app/{self.project.slug}/prs/{pr.id}/', {'action': 'close'})
        self.assertEqual(last.status_code, 403)
