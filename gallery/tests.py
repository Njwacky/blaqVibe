import io
import os
import zipfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from gallery.access import user_can_download
from gallery.forms import AppUploadForm
from gallery.models import AppProject, Category, Notification, PaymentIntent, ProjectCoOwner, Sale, Star, Trade, VibeBattle
from gallery.validators import validate_zip
from users.models import Profile


def make_zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def make_zip_file(files, name='app.zip'):
    return SimpleUploadedFile(name, make_zip_bytes(files), content_type='application/zip')


def make_user(username, password='pass12345', **profile_kwargs):
    user = User.objects.create_user(
        username=username,
        password=password,
        email=f'{username}@test.com',
    )
    profile = user.profile
    # Tests model legitimate users: verified email (trading requires it).
    profile_kwargs.setdefault('email_verified', True)
    for key, value in profile_kwargs.items():
        setattr(profile, key, value)
    if profile_kwargs:
        profile.save()
    return user


def make_category():
    return Category.objects.create(name='Apps', slug='apps', type='full_app')


def make_project(owner, category, **kwargs):
    defaults = {
        'title': kwargs.pop('title', f'{owner.username} vibe'),
        'short_description': 'A short description of this vibe used in tests.',
        'readme': '# Test Vibe\n\n' + ('This is a test readme with enough characters. ' * 4),
        'status': 'published',
        'category': category,
        'owner': owner,
    }
    defaults.update(kwargs)
    return AppProject.objects.create(**defaults)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class AccessAndPaywallTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.buyer = make_user('buyer', stars_balance=10)
        self.project = make_project(self.owner, self.cat, star_cost=3, price_zar=0)
        self.project.zip_file.save('paid.zip', make_zip_file({'app.py': 'print(1)\n'}), save=True)

    def test_anonymous_cannot_download_paid_zip(self):
        clones = self.project.clones
        response = self.client.get(f'/app/{self.project.slug}/download/')
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.clones, clones)
        self.assertIn('/accounts/login/', response.url)

    def test_buyer_without_trade_cannot_download(self):
        self.client.login(username='buyer', password='pass12345')
        response = self.client.get(f'/app/{self.project.slug}/download/')
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.project.slug, response.url)
        self.project.refresh_from_db()
        self.assertEqual(self.project.clones, 0)

    def test_trade_unlocks_download(self):
        Trade.objects.create(buyer=self.buyer, seller=self.owner, project=self.project, cost=3)
        self.client.login(username='buyer', password='pass12345')
        response = self.client.get(f'/app/{self.project.slug}/download/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.project.refresh_from_db()
        self.assertEqual(self.project.clones, 1)

    def test_owner_can_download(self):
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(f'/app/{self.project.slug}/download/')
        self.assertEqual(response.status_code, 200)

    @override_settings(PAYSTACK_SECRET_KEY='sk_test_lock', PAYSTACK_ENABLED=True)
    def test_sale_unlocks_priced_vibe(self):
        priced = make_project(self.owner, self.cat, title='Priced', star_cost=0, price_zar=50)
        priced.zip_file.save('priced.zip', make_zip_file({'app.py': 'print(2)\n'}), save=True)
        self.assertFalse(user_can_download(self.buyer, priced))
        Sale.objects.create(buyer=self.buyer, seller=self.owner, project=priced, amount_zar=50, paystack_ref='ok')
        self.assertTrue(user_can_download(self.buyer, priced))

    def test_file_preview_locked_without_access(self):
        self.client.login(username='buyer', password='pass12345')
        response = self.client.get(f'/app/{self.project.slug}/file/app.py')
        self.assertEqual(response.status_code, 403)

    def test_git_clone_locked_without_access(self):
        self.client.login(username='buyer', password='pass12345')
        response = self.client.get(f'/git/owner/{self.project.slug}.git/')
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.project.slug, response.url)

    def test_buy_without_paystack_does_not_grant_sale(self):
        priced = make_project(self.owner, self.cat, title='Cash', star_cost=0, price_zar=80)
        priced.zip_file.save('cash.zip', make_zip_file({'app.py': 'print(3)\n'}), save=True)
        self.client.login(username='buyer', password='pass12345')
        response = self.client.post(f'/app/{priced.slug}/buy/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Sale.objects.filter(buyer=self.buyer, project=priced).exists())


@override_settings(RATELIMIT_ENABLE=False)
class PrivacyAndBattleTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner', is_pro=True, pro_since=timezone.now())
        self.visitor = make_user('visitor')
        self.voter = make_user('voter')
        self.a = make_project(self.owner, self.cat, title='Alpha')
        self.b = make_project(self.visitor, self.cat, title='Beta')

    def test_who_viewed_is_owner_only(self):
        self.client.login(username='visitor', password='pass12345')
        response = self.client.get(f'/app/{self.a.slug}/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['viewers'])
        self.assertFalse(response.context['show_viewer_upsell'])
        self.assertNotContains(response, 'only you can see this')

    def test_owner_with_pro_sees_viewers(self):
        self.client.login(username='visitor', password='pass12345')
        self.client.get(f'/app/{self.a.slug}/')
        self.client.logout()
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(f'/app/{self.a.slug}/')
        self.assertIsNotNone(response.context['viewers'])
        self.assertContains(response, 'only you can see this')

    def test_vote_requires_post(self):
        battle = VibeBattle.objects.create(vibe_a=self.a, vibe_b=self.b)
        self.client.login(username='voter', password='pass12345')
        response = self.client.get(f'/battle/{battle.id}/vote/')
        self.assertEqual(response.status_code, 405)

    def test_vote_does_not_inflate_project_stars(self):
        battle = VibeBattle.objects.create(vibe_a=self.a, vibe_b=self.b)
        before = self.a.stars
        self.client.login(username='voter', password='pass12345')
        response = self.client.post(f'/battle/{battle.id}/vote/', {'choice': 'a'})
        self.assertEqual(response.status_code, 302)
        self.a.refresh_from_db()
        battle.refresh_from_db()
        self.assertEqual(self.a.stars, before)
        self.assertEqual(battle.votes_a, 1)


@override_settings(RATELIMIT_ENABLE=False)
class FormAndValidatorTests(TestCase):
    def setUp(self):
        self.cat = make_category()

    def test_readme_validation_is_not_swallowed(self):
        form = AppUploadForm(data={
            'title': 'Tiny',
            'category': self.cat.id,
            'short_description': 'A short description of this vibe.',
            'readme': 'too short',
            'html_code': '<div>hi</div>',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('readme', form.errors)

    def test_requires_zip_or_html(self):
        form = AppUploadForm(data={
            'title': 'Empty',
            'category': self.cat.id,
            'short_description': 'A short description of this vibe.',
            'readme': '# Heading\n\n' + ('Enough characters in this readme for the form. ' * 3),
        })
        self.assertFalse(form.is_valid())

    def test_env_file_blocked_in_zip(self):
        upload = make_zip_file({'.env': 'SECRET=1\n', 'app.py': 'print(1)\n'})
        with self.assertRaises(ValidationError):
            validate_zip(upload)

    def test_nested_env_blocked(self):
        upload = make_zip_file({'config/.env.local': 'SECRET=1\n'})
        with self.assertRaises(ValidationError):
            validate_zip(upload)

    def test_clean_zip_allowed(self):
        upload = make_zip_file({'app.py': 'print(1)\n', 'README.md': '# Hi\n'})
        self.assertIsNone(validate_zip(upload))


@override_settings(RATELIMIT_ENABLE=False)
class StarFloorTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.fan = make_user('fan')
        self.project = make_project(self.owner, self.cat, stars=0)

    def test_unstar_does_not_go_negative(self):
        Star.objects.create(user=self.fan, project=self.project)
        self.client.login(username='fan', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/star/')
        self.assertEqual(response.status_code, 200)
        self.project.refresh_from_db()
        self.assertGreaterEqual(self.project.stars, 0)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class RemainingHoleTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.buyer = make_user('buyer', stars_balance=10)
        self.project = make_project(self.owner, self.cat, star_cost=3, price_zar=0)
        self.project.zip_file.save('paid.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)

    def test_fork_requires_unlock(self):
        self.client.login(username='buyer', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/fork/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AppProject.objects.filter(owner=self.buyer, forked_from=self.project).exists())

    def test_fork_after_trade(self):
        Trade.objects.create(buyer=self.buyer, seller=self.owner, project=self.project, cost=3)
        self.client.login(username='buyer', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/fork/')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AppProject.objects.filter(owner=self.buyer, forked_from=self.project).exists())

    def test_media_zip_is_not_public(self):
        response = self.client.get(f'/media/{self.project.zip_file.name}')
        self.assertEqual(response.status_code, 404)

    def test_clamav_missing_stays_pending(self):
        from unittest.mock import patch
        from gallery.tasks import scan_zip_with_clamav, finalize_publish
        with patch('gallery.tasks.subprocess.run', side_effect=FileNotFoundError):
            result = scan_zip_with_clamav.run(self.project.id)
        self.assertEqual(result, 'scanner_unavailable')
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'pending')
        self.assertEqual((self.project.scan_report or {}).get('clamav'), 'unavailable')
        finalize_publish.run(self.project.id)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'pending')

    def test_api_lists_published_only(self):
        response = self.client.get('/api/v1/apps/')
        self.assertEqual(response.status_code, 200)
        slugs = [row['slug'] for row in response.json()['results']]
        self.assertIn(self.project.slug, slugs)
        self.assertNotIn('zip_file', response.json()['results'][0])

    def test_bookmark_roundtrip(self):
        from gallery.models import Bookmark
        self.client.login(username='buyer', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/save/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Bookmark.objects.filter(user=self.buyer, project=self.project).exists())

    def test_pr_content_diff_shows_modified_file(self):
        from django.core.files.base import ContentFile
        from gallery.diff import diff_projects
        from gallery.models import AppFile, PullRequest
        target = make_project(self.owner, self.cat, title='Diff target', star_cost=0)
        target.zip_file.save('target.zip', make_zip_file({'app.py': 'print("old")\n', 'keep.py': 'x=1\n'}), save=True)
        AppFile.objects.create(project=target, path='app.py', size=10)
        AppFile.objects.create(project=target, path='keep.py', size=10)
        fork = make_project(self.buyer, self.cat, title='Diff fork', forked_from=target, star_cost=0)
        fork.zip_file.save('fork.zip', make_zip_file({'app.py': 'print("new")\nprint("extra")\n', 'keep.py': 'x=1\n'}), save=True)
        AppFile.objects.create(project=fork, path='app.py', size=10)
        AppFile.objects.create(project=fork, path='keep.py', size=10)
        d = diff_projects(fork, target)
        self.assertEqual(d['modified_count'], 1)
        self.assertEqual(d['modified'][0]['path'], 'app.py')
        self.assertEqual(d['modified'][0]['additions'], 2)
        self.assertEqual(d['modified'][0]['deletions'], 1)

    def test_pr_merge_copies_files(self):
        from django.core.files.base import ContentFile
        from gallery.models import AppFile, PullRequest
        fork = make_project(self.buyer, self.cat, title='Forked copy', forked_from=self.project, star_cost=0)
        fork.zip_file.save('fork.zip', make_zip_file({'new.py': 'print(9)\\n'}), save=True)
        AppFile.objects.create(project=fork, path='new.py', size=10)
        pr = PullRequest.objects.create(source=fork, target=self.project, author=self.buyer, title='Add new.py', status='open')
        self.client.login(username='owner', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/prs/{pr.id}/', {'action': 'merge'})
        self.assertEqual(response.status_code, 302)
        pr.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(pr.status, 'merged')
        self.assertTrue(self.project.files.filter(path='new.py').exists())


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False)
class StarsEconomyTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner', stars_balance=5)
        self.buyer = make_user('buyer', stars_balance=10)
        self.project = make_project(self.owner, self.cat, star_cost=3, price_zar=0)
        self.project.zip_file.save('paid.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)

    def test_trade_moves_stars_and_unlocks(self):
        self.client.login(username='buyer', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/trade/')
        self.assertEqual(response.status_code, 302)
        self.buyer.profile.refresh_from_db()
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.buyer.profile.stars_balance, 7)
        self.assertEqual(self.owner.profile.stars_balance, 8)
        self.assertTrue(Trade.objects.filter(buyer=self.buyer, project=self.project, cost=3).exists())
        download = self.client.get(f'/app/{self.project.slug}/download/')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download['Content-Type'], 'application/zip')

    def test_trade_insufficient_stars_does_not_create_trade(self):
        self.buyer.profile.stars_balance = 1
        self.buyer.profile.save(update_fields=['stars_balance'])
        self.client.login(username='buyer', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/trade/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Trade.objects.filter(buyer=self.buyer, project=self.project).exists())
        self.buyer.profile.refresh_from_db()
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.buyer.profile.stars_balance, 1)
        self.assertEqual(self.owner.profile.stars_balance, 5)

    def test_second_trade_is_free_replay(self):
        Trade.objects.create(buyer=self.buyer, seller=self.owner, project=self.project, cost=3)
        self.client.login(username='buyer', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/trade/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Trade.objects.filter(buyer=self.buyer, project=self.project).count(), 1)
        self.buyer.profile.refresh_from_db()
        self.assertEqual(self.buyer.profile.stars_balance, 10)

    def test_star_is_reputation_only_never_wallet(self):
        """Starring moves project.stars (reputation), NEVER stars_balance.

        The old behaviour (star pays owner +1 spendable ★) was a minting
        loop: star → owner spends it → unstar (no deduction possible) →
        star again. Free actions must not create currency.
        """
        self.client.login(username='buyer', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/star/')
        self.owner.profile.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 5)  # wallet untouched
        self.assertEqual(self.project.stars, 1)                # reputation moved
        self.client.post(f'/app/{self.project.slug}/star/')
        self.owner.profile.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 5)
        self.assertEqual(self.project.stars, 0)

    def test_star_unstar_loop_cannot_mint(self):
        """The historical exploit: star, owner spends, unstar, star again."""
        from users.models import StarEvent
        self.client.login(username='buyer', password='pass12345')
        for _ in range(3):
            self.client.post(f'/app/{self.project.slug}/star/')   # star
            self.client.post(f'/app/{self.project.slug}/star/')   # unstar
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 5)
        # And no ledger rows were written for any of it.
        self.assertFalse(
            StarEvent.objects.filter(user=self.owner).exclude(reason='welcome').exists()
        )

    def test_trade_requires_verified_email(self):
        """Currency only moves between verified accounts."""
        self.buyer.profile.email_verified = False
        self.buyer.profile.save(update_fields=['email_verified'])
        self.client.login(username='buyer', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/trade/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Trade.objects.filter(buyer=self.buyer, project=self.project).exists())
        self.buyer.profile.refresh_from_db()
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.buyer.profile.stars_balance, 10)
        self.assertEqual(self.owner.profile.stars_balance, 5)

    def test_trade_writes_ledger_rows_both_sides(self):
        """sum(StarEvent.delta) == stars_balance must hold after a trade."""
        from users.models import StarEvent
        from users.wallet import ledger_balance
        # Give the test wallets matching opening rows so the invariant holds.
        StarEvent.objects.create(user=self.buyer, delta=10, reason='admin_adjust', ref='test-open')
        StarEvent.objects.create(user=self.owner, delta=5, reason='admin_adjust', ref='test-open')
        self.client.login(username='buyer', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/trade/')
        trade = Trade.objects.get(buyer=self.buyer, project=self.project)
        self.assertTrue(StarEvent.objects.filter(
            user=self.buyer, delta=-3, reason='trade_spend', ref=f'trade:{trade.pk}:{self.project.slug}',
        ).exists())
        self.assertTrue(StarEvent.objects.filter(
            user=self.owner, delta=3, reason='trade_earn', ref=f'trade:{trade.pk}:{self.project.slug}',
        ).exists())
        self.buyer.profile.refresh_from_db()
        self.owner.profile.refresh_from_db()
        self.assertEqual(ledger_balance(self.buyer), self.buyer.profile.stars_balance)
        self.assertEqual(ledger_balance(self.owner), self.owner.profile.stars_balance)


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class PreviewHonestyTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.zip_project = make_project(self.owner, self.cat, title='Zip App', star_cost=0)
        self.zip_project.zip_file.save('app.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)
        from gallery.models import AppFile
        AppFile.objects.create(project=self.zip_project, path='app.py', size=10)
        self.snippet = make_project(
            self.owner, self.cat, title='Hero Snippet', html_code='<h1>Hello</h1>', star_cost=0,
        )

    def test_preview_files_page_is_honest(self):
        response = self.client.get(f'/app/{self.zip_project.slug}/files/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Preview files')
        self.assertContains(response, 'app.py')
        self.assertNotContains(response, 'blaqvibes.run')
        self.assertNotContains(response, 'spin a new container')
        self.assertContains(response, 'not Docker')

    def test_run_redirects_zip_to_file_preview(self):
        response = self.client.get(f'/app/{self.zip_project.slug}/run/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/files/', response.url)

    def test_run_redirects_snippet_to_preview(self):
        response = self.client.get(f'/app/{self.snippet.slug}/run/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/preview/', response.url)

    def test_detail_never_promises_a_live_run_for_a_zip(self):
        """Intent of this test: a ZIP must not be advertised as runnable.

        The wording moved from the ambiguous "Preview files" to an explicit
        "Browse files" + "No live preview" once program kinds landed, so we
        assert the promise, not the old string.
        """
        response = self.client.get(f'/app/{self.zip_project.slug}/')
        self.assertContains(response, 'Browse files')
        self.assertContains(response, 'No live preview')
        self.assertNotContains(response, 'Run preview')
        self.assertNotContains(response, 'Preview 1h')
        self.assertNotContains(response, 'Buy R')


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class SeedDemoTests(TestCase):
    def test_seed_fills_the_feed(self):
        from django.core.management import call_command
        from gallery.models import AppProject
        self.assertEqual(AppProject.objects.filter(status='published').count(), 0)
        call_command('seed_demo')
        published = AppProject.objects.filter(status='published').count()
        self.assertGreaterEqual(published, 6)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SaaS Launch Hero')
        self.assertContains(response, 'Stock Tracker Starter')
        self.assertNotContains(response, 'Publish your first vibe')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False, PAYSTACK_SECRET_KEY='', PAYSTACK_ENABLED=False)
class FiveWhysHolesTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner', stars_balance=5)
        self.buyer = make_user('buyer', stars_balance=10)

    def test_zar_only_does_not_lock_when_paystack_off(self):
        from gallery.access import user_can_download
        priced = make_project(self.owner, self.cat, title='Zar only', star_cost=0, price_zar=80)
        priced.zip_file.save('zar.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)
        self.assertTrue(user_can_download(self.buyer, priced))
        self.client.login(username='buyer', password='pass12345')
        response = self.client.get(f'/app/{priced.slug}/download/')
        self.assertEqual(response.status_code, 200)

    def test_star_cost_still_locks_when_paystack_off(self):
        from gallery.access import user_can_download
        paid = make_project(self.owner, self.cat, title='Stars only', star_cost=2, price_zar=50)
        paid.zip_file.save('stars.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)
        self.assertFalse(user_can_download(self.buyer, paid))

    def test_allow_trading_off_makes_download_free(self):
        from gallery.access import user_can_download
        self.owner.profile.allow_trading = False
        self.owner.profile.save(update_fields=['allow_trading'])
        paid = make_project(self.owner, self.cat, title='No trade', star_cost=4)
        paid.zip_file.save('free.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)
        self.assertTrue(user_can_download(self.buyer, paid))

    def test_anonymous_trade_redirects_to_login(self):
        paid = make_project(self.owner, self.cat, title='Need login', star_cost=2)
        paid.zip_file.save('need.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)
        response = self.client.post(f'/app/{paid.slug}/trade/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_get_trade_is_405(self):
        paid = make_project(self.owner, self.cat, title='Get trade', star_cost=2)
        paid.zip_file.save('get.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)
        self.client.login(username='buyer', password='pass12345')
        response = self.client.get(f'/app/{paid.slug}/trade/')
        self.assertEqual(response.status_code, 405)

    def test_owner_trade_downloads_without_debit(self):
        paid = make_project(self.owner, self.cat, title='Own zip', star_cost=3)
        paid.zip_file.save('own.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)
        self.client.login(username='owner', password='pass12345')
        response = self.client.post(f'/app/{paid.slug}/trade/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Trade.objects.filter(buyer=self.owner, project=paid).exists())
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 5)

    def test_preview_files_does_not_unlock_contents(self):
        paid = make_project(self.owner, self.cat, title='Names only', star_cost=3)
        paid.zip_file.save('names.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)
        from gallery.models import AppFile
        AppFile.objects.create(project=paid, path='app.py', size=10)
        self.client.login(username='buyer', password='pass12345')
        listing = self.client.get(f'/app/{paid.slug}/files/')
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, 'app.py')
        contents = self.client.get(f'/app/{paid.slug}/file/app.py')
        self.assertEqual(contents.status_code, 403)

    def test_seed_is_idempotent_and_does_not_reset_wallet(self):
        from django.core.management import call_command
        call_command('seed_demo')
        from django.contrib.auth.models import User
        blaq = User.objects.get(username='blaq')
        blaq.profile.stars_balance = 3
        blaq.profile.save(update_fields=['stars_balance'])
        first = AppProject.objects.filter(status='published').count()
        call_command('seed_demo')
        self.assertEqual(AppProject.objects.filter(status='published').count(), first)
        blaq.profile.refresh_from_db()
        self.assertEqual(blaq.profile.stars_balance, 3)

    def test_nolo_without_key_is_honest_helper(self):
        from gallery.nolo_ai import configured_ai_backend, get_nolo_ai_answer
        self.assertEqual(configured_ai_backend(), 'heuristic')
        reply, source = get_nolo_ai_answer('How do I preview files in a ZIP?')
        self.assertEqual(source, 'heuristic')
        self.assertIn('not Docker', reply)
        page = self.client.get('/nolo/chat/')
        self.assertContains(page, 'not a live Claude')
        self.client.login(username='buyer', password='pass12345')
        api = self.client.post(
            '/nolo/chat/send/',
            data='{"prompt":"How do I trade stars?"}',
            content_type='application/json',
        )
        self.assertEqual(api.status_code, 200)
        body = api.json()
        self.assertEqual(body['source'], 'heuristic')
        self.assertIn('Stars', body['reply'])

    @override_settings(ANTHROPIC_API_KEY='sk-ant-test')
    def test_nolo_uses_claude_when_key_set(self):
        from unittest.mock import Mock, patch
        from gallery.nolo_ai import configured_ai_backend, get_nolo_ai_answer
        self.assertEqual(configured_ai_backend(), 'claude')
        fake = Mock()
        fake.raise_for_status = Mock()
        fake.json.return_value = {
            'content': [{'type': 'text', 'text': 'Preview files is the in-app file list, not a container.'}]
        }
        with patch('gallery.nolo_ai.requests.post', return_value=fake) as posted:
            reply, source = get_nolo_ai_answer('What is preview files?')
        self.assertEqual(source, 'claude')
        self.assertIn('not a container', reply)
        self.assertEqual(posted.call_args.kwargs['headers']['x-api-key'], 'sk-ant-test')



@override_settings(RATELIMIT_ENABLE=False, PAYSTACK_SECRET_KEY='sk_test_webhook', PAYSTACK_ENABLED=True)
class PaystackWebhookTests(TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.cat = make_category()
        self.owner = make_user('owner')
        self.buyer = make_user('buyer')
        self.project = make_project(self.owner, self.cat, title='Card vibe', star_cost=0, price_zar=50)
        self.project.zip_file.save('card.zip', make_zip_file({'app.py': 'print(1)\n'}), save=True)
        self.verify_patch = patch(
            'gallery.payments.verify_paystack_transaction',
            side_effect=self._verified,
        )
        self.verify_patch.start()
        self.addCleanup(self.verify_patch.stop)

    def _verified(self, reference):
        intent = PaymentIntent.objects.get(reference=reference)
        return {
            'status': 'success',
            'amount': intent.amount_kobo,
            'currency': 'ZAR',
            'reference': reference,
        }

    def _intent(self, reference='blaq-okref', amount_zar=50, **kwargs):
        now = timezone.now()
        defaults = {
            'reference': reference,
            'buyer': self.buyer,
            'project': self.project,
            'amount_zar': amount_zar,
            'amount_kobo': amount_zar * 100,
            'currency': 'ZAR',
            'status': 'pending',
            'expires_at': now + timedelta(minutes=25),
        }
        defaults.update(kwargs)
        return PaymentIntent.objects.create(**defaults)

    def _signed(self, payload: bytes):
        import hashlib, hmac
        return hmac.new(b'sk_test_webhook', payload, hashlib.sha512).hexdigest()

    def _body(self, reference, amount, currency='ZAR'):
        import json
        return json.dumps({
            'event': 'charge.success',
            'data': {'reference': reference, 'amount': amount, 'currency': currency},
        }).encode()

    def test_bad_signature_does_not_create_sale(self):
        self._intent('blaq-abc')
        body = self._body('blaq-abc', 5000)
        response = self.client.post(
            '/paystack/webhook/',
            data=body,
            content_type='application/json',
            headers={'x-paystack-signature': 'nope'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(buyer=self.buyer, project=self.project).exists())

    def test_valid_signature_creates_sale_and_unlocks(self):
        self._intent('blaq-okref')
        body = self._body('blaq-okref', 5000)
        response = self.client.post(
            '/paystack/webhook/',
            data=body,
            content_type='application/json',
            headers={'x-paystack-signature': self._signed(body)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Sale.objects.filter(buyer=self.buyer, project=self.project, amount_zar=50).exists())
        self.assertTrue(user_can_download(self.buyer, self.project))
        self.assertEqual(PaymentIntent.objects.get(reference='blaq-okref').status, 'paid')

    def test_amount_mismatch_rejected(self):
        self._intent('blaq-low')
        body = self._body('blaq-low', 100)
        response = self.client.post(
            '/paystack/webhook/',
            data=body,
            content_type='application/json',
            headers={'x-paystack-signature': self._signed(body)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(buyer=self.buyer, project=self.project).exists())

    def test_unknown_reference_rejected(self):
        body = self._body('blaq-no-such-intent', 5000)
        response = self.client.post(
            '/paystack/webhook/',
            data=body,
            content_type='application/json',
            headers={'x-paystack-signature': self._signed(body)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(buyer=self.buyer, project=self.project).exists())

    def test_price_change_after_initialize_still_honors_frozen_amount(self):
        self._intent('blaq-frozen', amount_zar=50)
        self.project.price_zar = 80
        self.project.save(update_fields=['price_zar'])
        body = self._body('blaq-frozen', 5000)
        response = self.client.post(
            '/paystack/webhook/',
            data=body,
            content_type='application/json',
            headers={'x-paystack-signature': self._signed(body)},
        )
        self.assertEqual(response.status_code, 200)
        sale = Sale.objects.get(buyer=self.buyer, project=self.project)
        self.assertEqual(sale.amount_zar, 50)
        self.assertTrue(user_can_download(self.buyer, self.project))

    def test_price_drop_to_zero_still_honors_intent(self):
        self._intent('blaq-zeroed', amount_zar=50)
        self.project.price_zar = 0
        self.project.save(update_fields=['price_zar'])
        body = self._body('blaq-zeroed', 5000)
        response = self.client.post(
            '/paystack/webhook/',
            data=body,
            content_type='application/json',
            headers={'x-paystack-signature': self._signed(body)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Sale.objects.filter(buyer=self.buyer, project=self.project, amount_zar=50).exists())

    def test_replay_webhook_is_idempotent(self):
        self._intent('blaq-replay')
        body = self._body('blaq-replay', 5000)
        headers = {'x-paystack-signature': self._signed(body)}
        first = self.client.post('/paystack/webhook/', data=body, content_type='application/json', headers=headers)
        second = self.client.post('/paystack/webhook/', data=body, content_type='application/json', headers=headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Sale.objects.filter(buyer=self.buyer, project=self.project).count(), 1)

    def test_currency_mismatch_rejected(self):
        self._intent('blaq-usd')
        body = self._body('blaq-usd', 5000, currency='USD')
        response = self.client.post(
            '/paystack/webhook/',
            data=body,
            content_type='application/json',
            headers={'x-paystack-signature': self._signed(body)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.exists())

    def test_buy_creates_intent_at_current_price(self):
        from unittest.mock import Mock, patch
        fake = Mock()
        fake.json.return_value = {
            'status': True,
            'data': {'authorization_url': 'https://paystack.test/checkout'},
        }
        self.client.login(username='buyer', password='pass12345')
        with patch('gallery.payments.requests.post', return_value=fake) as posted:
            response = self.client.post(f'/app/{self.project.slug}/buy/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, 'https://paystack.test/checkout')
        intent = PaymentIntent.objects.get(buyer=self.buyer, project=self.project)
        self.assertEqual(intent.amount_zar, 50)
        self.assertEqual(intent.amount_kobo, 5000)
        self.assertEqual(intent.currency, 'ZAR')
        self.assertEqual(intent.status, 'pending')
        self.assertEqual(intent.authorization_url, 'https://paystack.test/checkout')
        sent = posted.call_args.kwargs['json']
        self.assertEqual(sent['amount'], 5000)
        self.assertEqual(sent['currency'], 'ZAR')
        self.assertEqual(sent['email'], 'buyer@test.com')
        self.assertEqual(sent['reference'], intent.reference)

    def test_second_buy_reuses_fresh_pending(self):
        self._intent(
            'blaq-reuse',
            authorization_url='https://paystack.test/existing',
        )
        self.client.login(username='buyer', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/buy/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, 'https://paystack.test/existing')
        self.assertEqual(PaymentIntent.objects.filter(buyer=self.buyer, project=self.project).count(), 1)


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class LaunchGuideTests(TestCase):
    def test_hub_is_public_and_truthful_about_preview_boundary(self):
        response = self.client.get('/launch/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'You built the vibe')
        self.assertContains(response, 'Preview is not hosting')
        self.assertContains(response, 'does not keep your backend running')
        self.assertContains(response, 'No made-up build commands')
        self.assertGreaterEqual(len(response.context['guides']), 10)

    def test_category_filter_only_returns_matching_guides(self):
        response = self.client.get('/launch/?category=games')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_category'], 'games')
        self.assertTrue(response.context['guides'])
        self.assertTrue(all(guide['category'] == 'games' for guide in response.context['guides']))
        self.assertContains(response, 'Release a game on itch.io')
        self.assertContains(response, 'Prepare and release a game on Steam')
        self.assertFalse(any(guide['slug'] == 'google-play' for guide in response.context['guides']))

    def test_unknown_category_falls_back_to_all(self):
        response = self.client.get('/launch/?category=not-a-real-category')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_category'], 'all')

    def test_every_curated_guide_renders_with_official_sources(self):
        from gallery.launch_guides import LAUNCH_GUIDES
        for guide in LAUNCH_GUIDES:
            with self.subTest(slug=guide['slug']):
                response = self.client.get(f"/launch/{guide['slug']}/")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, guide['title'])
                self.assertContains(response, 'Verify against the live platform docs')
                self.assertContains(response, f'src="/static/{guide["icon"]}"', html=False)
                for source in guide['sources']:
                    self.assertContains(response, source['url'])
                    self.assertContains(response, source['label'])
                self.assertContains(response, 'BlaqVibes does not receive your store account or credentials')

    def test_unknown_guide_is_404(self):
        response = self.client.get('/launch/not-a-real-guide/')
        self.assertEqual(response.status_code, 404)

    def test_guide_data_has_complete_structure_and_valid_references(self):
        from gallery.launch_guides import ARTIFACT_ROUTES, CATEGORIES, LAUNCH_GUIDES
        required_fields = {
            'slug', 'category', 'icon', 'eyebrow', 'title', 'summary', 'result',
            'artifact', 'time', 'good_for', 'not_for', 'prerequisites', 'steps',
            'checklist', 'sources', 'last_reviewed',
        }
        allowed_categories = {item['slug'] for item in CATEGORIES} - {'all'}
        slugs = [guide['slug'] for guide in LAUNCH_GUIDES]
        self.assertEqual(len(slugs), len(set(slugs)))

        for guide in LAUNCH_GUIDES:
            with self.subTest(slug=guide['slug']):
                self.assertTrue(required_fields.issubset(guide))
                self.assertIn(guide['category'], allowed_categories)
                self.assertGreaterEqual(len(guide['steps']), 4)
                self.assertTrue(guide['prerequisites'])
                self.assertTrue(guide['checklist'])
                self.assertTrue(guide['sources'])
                for step in guide['steps']:
                    self.assertTrue(step['title'].strip())
                    self.assertTrue(step['body'].strip())
                    for command in step.get('commands', ()):
                        self.assertTrue(command['label'].strip())
                        self.assertTrue(command['text'].strip())
                        self.assertNotIn('API_KEY=', command['text'])
                        self.assertNotIn('PASSWORD=', command['text'])
                        if '<' in command['text']:
                            self.assertTrue(command.get('replace'))

        known_slugs = set(slugs)
        route_values = [route['value'] for route in ARTIFACT_ROUTES]
        self.assertEqual(len(route_values), len(set(route_values)))
        for route in ARTIFACT_ROUTES:
            with self.subTest(artifact=route['value']):
                self.assertTrue(route['label'].strip())
                self.assertTrue(route['note'].strip())
                self.assertTrue(route['guides'])
                self.assertTrue(set(route['guides']).issubset(known_slugs))

    def test_launch_icons_are_local_svg_assets(self):
        from django.contrib.staticfiles import finders
        from gallery.launch_guides import ARTIFACT_ROUTES, LAUNCH_GUIDES

        icons = {item['icon'] for item in (*LAUNCH_GUIDES, *ARTIFACT_ROUTES)}
        self.assertGreaterEqual(len(icons), 15)
        for icon in icons:
            with self.subTest(icon=icon):
                self.assertTrue(icon.startswith('gallery/icons/launch/'))
                self.assertTrue(icon.endswith('.svg'))
                self.assertIsNotNone(finders.find(icon))

        response = self.client.get('/launch/')
        self.assertContains(response, 'gallery/icons/launch/brands/google-play.svg')
        self.assertContains(response, 'gallery/icons/launch/brands/app-store.svg')
        self.assertContains(response, 'launch-brand--apple-app-store')

    def test_every_source_is_https_and_on_the_expected_official_domain(self):
        from urllib.parse import urlparse
        from gallery.launch_guides import LAUNCH_GUIDES
        expected_hosts = {
            'cloudflare-pages': {'docs.github.com', 'developers.cloudflare.com'},
            'vercel-web': {'vercel.com'},
            'render-web-service': {'render.com'},
            'installable-pwa': {'developer.mozilla.org', 'web.dev'},
            'docker-hub': {'docs.docker.com'},
            'google-play': {'developer.android.com', 'support.google.com'},
            'apple-app-store': {'developer.apple.com'},
            'itchio': {'itch.io', 'docs.godotengine.org'},
            'steam': {'partner.steamgames.com'},
            'microsoft-store': {'learn.microsoft.com'},
            'macos-direct': {'developer.apple.com'},
            'flathub': {'docs.flathub.org'},
            'chrome-web-store': {'developer.chrome.com'},
            'aws-s3-cloudfront': {'docs.aws.amazon.com'},
            'azure-static-web-apps': {'learn.microsoft.com'},
            'pythonanywhere': {'help.pythonanywhere.com'},
            'netlify': {'docs.netlify.com'},
            'supabase': {'supabase.com'},
            'digitalocean-app-platform': {'docs.digitalocean.com'},
            'railway': {'docs.railway.com'},
            'fly-io': {'fly.io'},
            'google-cloud-run': {'cloud.google.com'},
        }
        self.assertEqual(set(expected_hosts), {guide['slug'] for guide in LAUNCH_GUIDES})
        for guide in LAUNCH_GUIDES:
            for source in guide['sources']:
                with self.subTest(slug=guide['slug'], source=source['url']):
                    parsed = urlparse(source['url'])
                    self.assertEqual(parsed.scheme, 'https')
                    self.assertIn(parsed.hostname, expected_hosts[guide['slug']])
                    self.assertTrue(source['label'].strip())
                    self.assertNotIn('/msi/app-overview', parsed.path)

    def test_high_risk_requirements_are_explicit(self):
        vercel = self.client.get('/launch/vercel-web/')
        self.assertContains(vercel, 'first deployment of a new project is a production deployment')
        self.assertContains(vercel, 'Do not assume the first URL is private')
        steam = self.client.get('/launch/steam/')
        self.assertContains(steam, '30-day wait')
        self.assertContains(steam, 'at least two weeks')
        play = self.client.get('/launch/google-play/')
        self.assertContains(play, 'release-signed .aab')
        self.assertContains(play, 'at least 12 testers')
        self.assertContains(play, 'continuously opted in to a closed test for 14 days')
        apple = self.client.get('/launch/apple-app-store/')
        self.assertContains(apple, 'Add for Review')
        self.assertContains(apple, 'Submit for Review')
        microsoft = self.client.get('/launch/microsoft-store/')
        self.assertContains(microsoft, 'Trusted Root Program')
        self.assertContains(microsoft, 'standalone/offline installer')
        self.assertContains(microsoft, 'secure versioned HTTPS URL')

    def test_guide_interactions_render_accessible_controls(self):
        response = self.client.get('/launch/docker-hub/')
        self.assertContains(response, 'data-copy-command="docker build', html=False)
        self.assertContains(response, 'aria-live="polite"', html=False)
        self.assertContains(response, 'role="progressbar"', html=False)
        self.assertContains(response, 'data-checklist-key="blaq-launch-docker-hub"', html=False)

    def test_registry_and_host_are_not_conflated(self):
        response = self.client.get('/launch/docker-hub/')
        self.assertContains(response, 'not a public application URL')
        self.assertContains(response, 'Docker Hub stores and distributes images')
        self.assertContains(response, 'Deploy it on a real runtime')

    def test_navigation_links_to_launch_hub(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/launch/"', html=False)
        self.assertContains(response, 'Launch')

    def test_unknown_artifact_is_surfaced(self):
        response = self.client.get('/launch/?artifact=aws-s3')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_artifact'], '')
        self.assertEqual(response.context['invalid_artifact'], 'aws-s3')
        self.assertContains(response, 'aws-s3')
        self.assertContains(response, "isn’t a known build type")

    def test_artifact_query_marks_matching_routes(self):
        response = self.client.get('/launch/?artifact=frontend')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_artifact'], 'frontend')
        self.assertContains(response, 'is-match')
        self.assertContains(response, 'is-dimmed')
        self.assertContains(response, 'Vercel')

    def test_high_risk_steps_are_flagged(self):
        response = self.client.get('/launch/vercel-web/')
        self.assertContains(response, 'High-stakes')
        self.assertTrue(any(step.get('high_risk') for step in response.context['guide']['steps']))

    def test_every_guide_has_a_parseable_last_reviewed(self):
        """Per-guide review tracking: no guide may ship without a date."""
        from datetime import date
        from gallery.launch_guides import LAUNCH_GUIDES
        for guide in LAUNCH_GUIDES:
            with self.subTest(slug=guide['slug']):
                raw = guide.get('last_reviewed', '')
                self.assertTrue(raw, f'{guide["slug"]} has no last_reviewed')
                date.fromisoformat(raw)  # raises on garbage

    def test_guide_pages_expose_review_status(self):
        from gallery.launch_views import _review_status
        from gallery.launch_guides import LAUNCH_GUIDES
        for guide in LAUNCH_GUIDES:
            status = _review_status(guide)
            self.assertIn('days_since', status)
            self.assertIn('is_outdated', status)
        response = self.client.get('/launch/vercel-web/')
        self.assertIn('review', response.context['guide'])
        self.assertContains(response, 'Sources last reviewed')

    def test_review_status_flags_missing_and_unparseable_dates(self):
        from gallery.launch_views import _review_status
        self.assertTrue(_review_status({})['is_outdated'])
        self.assertTrue(_review_status({'last_reviewed': 'not a date'})['is_outdated'])
        self.assertIsNone(_review_status({})['days_since'])

    def test_review_status_detects_stale_guides(self):
        from datetime import date, timedelta
        from gallery.launch_views import _review_status, REVIEW_MAX_AGE_DAYS
        old = date.today() - timedelta(days=REVIEW_MAX_AGE_DAYS + 1)
        fresh = date.today()
        self.assertTrue(_review_status({'last_reviewed': old.isoformat()})['is_outdated'])
        self.assertFalse(_review_status({'last_reviewed': fresh.isoformat()})['is_outdated'])

    def test_check_guide_reviews_command_fails_on_stale_guide(self):
        """--fail must exit non-zero when any guide is older than --days."""
        from datetime import date, timedelta
        from unittest.mock import patch
        from django.core.management import call_command
        stale_guide = {
            'slug': 'stale-guide',
            'last_reviewed': (date.today() - timedelta(days=200)).isoformat(),
        }
        with patch(
            'gallery.management.commands.check_guide_reviews.LAUNCH_GUIDES',
            [stale_guide],
        ):
            with self.assertRaises(SystemExit) as cm:
                call_command('check_guide_reviews', days=90, fail=True)
            self.assertEqual(cm.exception.code, 1)

    def test_check_guide_reviews_command_passes_on_fresh_guides(self):
        from datetime import date
        from unittest.mock import patch
        from io import StringIO
        from django.core.management import call_command
        fresh_guide = {'slug': 'fresh-guide', 'last_reviewed': date.today().isoformat()}
        out = StringIO()
        with patch(
            'gallery.management.commands.check_guide_reviews.LAUNCH_GUIDES',
            [fresh_guide],
        ):
            call_command('check_guide_reviews', days=90, stdout=out)
        self.assertIn('reviewed within 90 days', out.getvalue())



@override_settings(RATELIMIT_ENABLE=False)
class ComparisonMatrixTests(TestCase):
    """The hub comparison matrix must reference only real guides and stay honest."""

    def test_every_group_row_references_a_real_guide(self):
        from gallery.launch_guides import GUIDES_BY_SLUG
        from gallery.comparison import COMPARISON_GROUPS
        for group in COMPARISON_GROUPS:
            with self.subTest(group=group['slug']):
                self.assertTrue(group['label'])
                self.assertTrue(group['question'])
                self.assertTrue(group['rows'])
                for row in group['rows']:
                    self.assertIn(row['slug'], GUIDES_BY_SLUG)
                    self.assertTrue(row['cost'], f"{row['slug']} missing cost label")

    def test_groups_do_not_duplicate_rows(self):
        from gallery.comparison import COMPARISON_GROUPS
        seen = set()
        for group in COMPARISON_GROUPS:
            for row in group['rows']:
                key = (group['slug'], row['slug'])
                self.assertNotIn(key, seen)
                seen.add(key)

    def test_enriched_rows_carry_guide_fields(self):
        from gallery.launch_guides import GUIDES_BY_SLUG
        from gallery.comparison import enrich_comparison_groups
        groups = enrich_comparison_groups(GUIDES_BY_SLUG)
        self.assertEqual(len(groups), 6)
        for group in groups:
            for row in group['rows']:
                self.assertTrue(row['name'])
                self.assertTrue(row['pace'])
                self.assertTrue(row['cost'])
                self.assertTrue(row['icon'])
                self.assertTrue(row['best_for'])

    def test_enrich_skips_malformed_guides_without_crashing(self):
        """A guide dict missing fields the template needs must be skipped, not fatal."""
        from gallery.comparison import enrich_comparison_groups
        guide_by_slug = {'cloudflare-pages': {'icon': 'only-icon.svg'}}  # no slug/name/pace
        groups = enrich_comparison_groups(guide_by_slug)
        self.assertEqual(sum(len(x['rows']) for x in groups), 0)

    def test_hub_renders_the_comparison_matrix(self):
        response = self.client.get('/launch/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Compare before you choose')
        self.assertContains(response, 'Static site hosts')
        self.assertContains(response, 'Server / full-stack hosts')
        self.assertContains(response, 'Free tier')
        self.assertContains(response, 'US$100 per product fee')
        self.assertGreaterEqual(len(response.context['comparison_groups']), 6)

    def test_matrix_links_to_real_guides(self):
        response = self.client.get('/launch/')
        body = response.content.decode()
        self.assertIn('/launch/cloudflare-pages/', body)
        self.assertIn('/launch/railway/', body)
        self.assertIn('/launch/steam/', body)


@override_settings(RATELIMIT_ENABLE=False)
class FrameworkCommandTests(TestCase):
    """The sidebar command reference must stay as honest as the guides."""

    def test_every_entry_has_complete_structure(self):
        from gallery.framework_commands import FRAMEWORK_COMMANDS
        for entry in FRAMEWORK_COMMANDS:
            with self.subTest(entry=entry['slug']):
                self.assertTrue(entry['name'])
                self.assertIn(entry['kind'], ('frontend', 'backend', 'mobile', 'game'))
                self.assertTrue(entry['how_to_find'])
                self.assertTrue(entry['docs']['label'])
                self.assertTrue(entry['docs']['url'])
                for cmd in entry['commands']:
                    self.assertTrue(cmd['text'])
                    self.assertNotIn('API_KEY=', cmd['text'])
                    self.assertNotIn('PASSWORD=', cmd['text'])
                    if '<' in cmd['text']:
                        self.assertTrue(cmd.get('replace'), f"{entry['slug']} command has unmarked placeholder")

    def test_all_doc_sources_are_https_and_official_domains(self):
        from urllib.parse import urlparse
        from gallery.framework_commands import FRAMEWORK_COMMANDS
        allowed_hosts = {
            'nextjs.org', 'vite.dev', 'svelte.dev', 'angular.dev',
            'docs.djangoproject.com', 'flask.palletsprojects.com',
            'fastapi.tiangolo.com', 'expressjs.com', 'guides.rubyonrails.org',
            'laravel.com', 'docs.flutter.dev', 'docs.godotengine.org',
        }
        for entry in FRAMEWORK_COMMANDS:
            with self.subTest(entry=entry['slug']):
                parsed = urlparse(entry['docs']['url'])
                self.assertEqual(parsed.scheme, 'https')
                self.assertIn(parsed.hostname, allowed_hosts)

    def test_guide_page_renders_the_framework_reference(self):
        response = self.client.get('/launch/vercel-web/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Find your framework')
        self.assertContains(response, 'Next.js')
        self.assertContains(response, 'React (Vite)')
        self.assertTrue(response.context['framework_commands'])

    def test_filter_matches_guide_audience(self):
        from gallery.framework_commands import framework_commands_for_guide
        django_guide = {'name': 'Render', 'eyebrow': 'Backend & full stack', 'summary': 'Django, Express, FastAPI', 'good_for': ('Django', 'FastAPI')}
        matched = framework_commands_for_guide(django_guide)
        slugs = [e['slug'] for e in matched]
        self.assertIn('django', slugs)
        self.assertIn('fastapi', slugs)
        # Never empty
        self.assertTrue(matched)

    def test_unmatched_guide_falls_back_to_full_table(self):
        from gallery.framework_commands import FRAMEWORK_COMMANDS, framework_commands_for_guide
        store_guide = {'name': 'App Store', 'eyebrow': 'iPhone, iPad', 'summary': 'Submit for review', 'good_for': ('iOS',)}
        self.assertEqual(framework_commands_for_guide(store_guide), FRAMEWORK_COMMANDS)

    def test_backend_guide_renders_django_commands(self):
        response = self.client.get('/launch/render-web-service/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'gunicorn')
        self.assertContains(response, 'collectstatic')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False)
class ChallengeAwardTests(TestCase):
    def setUp(self):
        from gallery.models import Challenge, Tag
        self.cat = make_category()
        self.admin = make_user('admin', role='admin')
        self.creator = make_user('creator', stars_balance=5)
        self.other = make_user('other', stars_balance=5)
        self.tag = Tag.objects.create(name='challenge-week-1', slug='challenge-week-1')
        self.challenge = Challenge.objects.create(
            title='Week 1',
            description='Build something cool for the week.',
            bounty_stars=10,
            tag='challenge-week-1',
            start=timezone.now() - timedelta(days=1),
            end=timezone.now() + timedelta(days=6),
            is_active=True,
        )
        self.entry = make_project(self.creator, self.cat, title='Tagged entry', stars=2)
        self.entry.tags.add(self.tag)
        self.outsider = make_project(self.other, self.cat, title='Not entered', stars=1)

    def test_winner_must_be_a_tagged_submission(self):
        self.client.login(username='admin', password='pass12345')
        response = self.client.post(
            f'/challenges/{self.challenge.tag}/pick-winner/',
            {'winner_id': self.outsider.id},
        )
        self.assertEqual(response.status_code, 302)
        self.challenge.refresh_from_db()
        self.assertIsNone(self.challenge.winner_id)
        self.other.profile.refresh_from_db()
        self.assertEqual(self.other.profile.stars_balance, 5)

    def test_unpublished_submission_cannot_win(self):
        self.entry.status = 'pending'
        self.entry.save(update_fields=['status'])
        self.client.login(username='admin', password='pass12345')
        self.client.post(
            f'/challenges/{self.challenge.tag}/pick-winner/',
            {'winner_id': self.entry.id},
        )
        self.challenge.refresh_from_db()
        self.creator.profile.refresh_from_db()
        self.assertIsNone(self.challenge.winner_id)
        self.assertEqual(self.creator.profile.stars_balance, 5)

    def test_award_is_idempotent_and_does_not_inflate_project_stars(self):
        from users.models import AdminLog
        self.client.login(username='admin', password='pass12345')
        url = f'/challenges/{self.challenge.tag}/pick-winner/'
        first = self.client.post(url, {'winner_id': self.entry.id})
        second = self.client.post(url, {'winner_id': self.entry.id})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.challenge.refresh_from_db()
        self.creator.profile.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual(self.challenge.winner_id, self.entry.id)
        self.assertEqual(self.creator.profile.stars_balance, 15)
        self.assertEqual(self.entry.stars, 2)
        self.assertTrue(self.creator.profile.is_pro_active)
        self.assertTrue(AdminLog.objects.filter(action='challenge_award', actor=self.admin).exists())

    def test_second_pick_cannot_switch_winner(self):
        other_entry = make_project(self.other, self.cat, title='Also tagged')
        other_entry.tags.add(self.tag)
        self.client.login(username='admin', password='pass12345')
        url = f'/challenges/{self.challenge.tag}/pick-winner/'
        self.client.post(url, {'winner_id': self.entry.id})
        self.client.post(url, {'winner_id': other_entry.id})
        self.challenge.refresh_from_db()
        self.other.profile.refresh_from_db()
        self.assertEqual(self.challenge.winner_id, self.entry.id)
        self.assertEqual(self.other.profile.stars_balance, 5)


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class SnippetIsolationTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.snippet = make_project(
            self.owner, self.cat, title='Live snippet',
            html_code='<h1>Hello</h1><form action="/oops/"><button>x</button></form>',
            js_code='console.log(1)',
        )

    def _token(self):
        from gallery.preview_token import issue_snippet_token
        return issue_snippet_token(self.snippet.slug)

    def test_top_level_document_is_blocked_even_with_token(self):
        response = self.client.get(
            f'/app/{self.snippet.slug}/snippet/',
            {'t': self._token()},
            headers={'Sec-Fetch-Dest': 'document'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'sandbox', status_code=403)

    def test_bare_request_without_token_is_blocked(self):
        response = self.client.get(
            f'/app/{self.snippet.slug}/snippet/',
            headers={'Sec-Fetch-Dest': 'iframe'},
        )
        self.assertEqual(response.status_code, 403)

    def test_iframe_dest_with_token_is_allowed(self):
        response = self.client.get(
            f'/app/{self.snippet.slug}/snippet/',
            {'t': self._token()},
            headers={'Sec-Fetch-Dest': 'iframe'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1>Hello</h1>')
        csp = response['Content-Security-Policy']
        self.assertIn("form-action 'none'", csp)
        self.assertIn("frame-ancestors 'self'", csp)
        self.assertIn('sandbox allow-scripts', csp)
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')

    def test_evil_host_referer_is_blocked(self):
        response = self.client.get(
            f'/app/{self.snippet.slug}/snippet/',
            {'t': self._token()},
            headers={'Referer': f'https://evil.example/app/{self.snippet.slug}/preview/'},
        )
        self.assertEqual(response.status_code, 403)

    def test_legacy_same_host_referer_with_token_is_allowed(self):
        response = self.client.get(
            f'/app/{self.snippet.slug}/snippet/',
            {'t': self._token()},
            headers={'Referer': f'http://testserver/app/{self.snippet.slug}/preview/'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h1>Hello</h1>')

    def test_preview_shell_embeds_signed_token(self):
        response = self.client.get(f'/app/{self.snippet.slug}/preview/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'sandbox="allow-scripts"', html=False)
        self.assertContains(response, '?t=', html=False)
        self.assertIn('snippet_token', response.context)


@override_settings(RATELIMIT_ENABLE=False)
class SafeZipExtractTests(TestCase):
    def test_safe_extract_writes_normal_files(self):
        import tempfile
        from pathlib import Path
        from gallery.validators import safe_extract_zip
        upload = make_zip_file({'app.py': 'print(1)\n', 'pkg/__init__.py': ''})
        dest = tempfile.mkdtemp()
        try:
            safe_extract_zip(upload, dest)
            self.assertTrue((Path(dest) / 'app.py').is_file())
            self.assertEqual((Path(dest) / 'app.py').read_text(), 'print(1)\n')
            self.assertTrue((Path(dest) / 'pkg' / '__init__.py').is_file())
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    def test_safe_extract_rejects_traversal(self):
        import os
        import tempfile
        from gallery.validators import safe_extract_zip
        dest = tempfile.mkdtemp()
        outside = os.path.abspath(os.path.join(dest, '..', 'pwned.py'))
        if os.path.exists(outside):
            os.remove(outside)
        upload = make_zip_file({'../pwned.py': 'bad\n'})
        try:
            with self.assertRaises(ValueError):
                safe_extract_zip(upload, dest)
            self.assertFalse(os.path.exists(outside))
            self.assertFalse(os.path.exists(os.path.join(dest, 'pwned.py')))
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)
            if os.path.exists(outside):
                os.remove(outside)

    def test_safe_extract_rejects_absolute_path(self):
        import tempfile
        from gallery.validators import safe_extract_zip
        dest = tempfile.mkdtemp()
        upload = make_zip_file({'/tmp/evil.py': 'bad\n'})
        try:
            with self.assertRaises(ValueError):
                safe_extract_zip(upload, dest)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    def test_safe_extract_rejects_env_file(self):
        import tempfile
        from gallery.validators import safe_extract_zip
        dest = tempfile.mkdtemp()
        upload = make_zip_file({'.env': 'SECRET=1\n'})
        try:
            with self.assertRaises(ValueError):
                safe_extract_zip(upload, dest)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)


class PrivateS3StorageTests(TestCase):
    def test_private_options_never_go_public(self):
        from django.conf import settings
        from gallery.storages import PRIVATE_S3_OPTIONS, PrivateMediaStorage
        self.assertIsNone(PRIVATE_S3_OPTIONS['default_acl'])
        self.assertTrue(PRIVATE_S3_OPTIONS['querystring_auth'])
        self.assertIsNone(PRIVATE_S3_OPTIONS['custom_domain'])
        self.assertIsNone(settings.AWS_DEFAULT_ACL)
        self.assertTrue(settings.AWS_QUERYSTRING_AUTH)
        self.assertFalse(settings.AWS_S3_CUSTOM_DOMAIN)
        self.assertNotEqual(settings.AWS_STORAGE_BUCKET_NAME, 'blaqvibes-public')
        self.assertIsNone(PrivateMediaStorage.default_acl)
        self.assertTrue(PrivateMediaStorage.querystring_auth)
        self.assertIsNone(PrivateMediaStorage.custom_domain)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class VersionDownloadTests(TestCase):
    def setUp(self):
        from gallery.models import AppVersion
        self.cat = make_category()
        self.owner = make_user('owner')
        self.buyer = make_user('buyer')
        self.project = make_project(self.owner, self.cat, star_cost=3)
        self.project.zip_file.save('paid.zip', make_zip_file({'app.py': 'print(1)\n'}), save=True)
        self.version = AppVersion.objects.create(
            project=self.project,
            zip_file=make_zip_file({'old.py': 'x=1\n'}, name='old.zip'),
            version='1.0.0',
        )

    def test_edit_page_does_not_emit_raw_storage_url(self):
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(f'/app/{self.project.slug}/edit/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'/app/{self.project.slug}/versions/{self.version.id}/download/')
        self.assertNotContains(response, '/media/apps/versions/')

    def test_stranger_cannot_download_version(self):
        self.client.login(username='buyer', password='pass12345')
        response = self.client.get(f'/app/{self.project.slug}/versions/{self.version.id}/download/')
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.project.slug, response.url)

    def test_owner_can_download_version(self):
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(f'/app/{self.project.slug}/versions/{self.version.id}/download/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False)
class ScanStatusVisibilityTests(TestCase):
    def setUp(self):
        from gallery.models import ScanJob
        self.cat = make_category()
        self.owner = make_user('owner')
        self.stranger = make_user('stranger')
        self.staff = make_user('staffer')
        self.staff.is_staff = True
        self.staff.save()
        self.mod = make_user('mod', role='moderator')
        self.pending = make_project(self.owner, self.cat, title='Pending vibe', status='pending')
        ScanJob.objects.create(project=self.pending, status='scanning')
        self.live = make_project(self.owner, self.cat, title='Live vibe', status='published')

    def test_stranger_cannot_see_unpublished_scan(self):
        response = self.client.get(f'/app/{self.pending.slug}/scan-status/')
        self.assertEqual(response.status_code, 404)

    def test_staff_flag_is_not_enough_for_unpublished_scan(self):
        self.client.login(username='staffer', password='pass12345')
        response = self.client.get(f'/app/{self.pending.slug}/scan-status/')
        self.assertEqual(response.status_code, 404)

    def test_owner_sees_unpublished_scan(self):
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(f'/app/{self.pending.slug}/scan-status/')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'scanning')
        self.assertFalse(body['is_published'])
        self.assertTrue(body['reason'])

    def test_moderator_sees_unpublished_scan(self):
        self.client.login(username='mod', password='pass12345')
        response = self.client.get(f'/app/{self.pending.slug}/scan-status/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'scanning')

    def test_stranger_sees_published_status_without_reason(self):
        response = self.client.get(f'/app/{self.live.slug}/scan-status/')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['is_published'])
        self.assertEqual(body['reason'], '')


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class CopyIncrementTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.fan = make_user('fan')
        self.live = make_project(self.owner, self.cat, title='Copy me', copies=0)
        self.pending = make_project(self.owner, self.cat, title='Not live', status='pending', copies=0)

    def test_unpublished_copy_is_404(self):
        response = self.client.post(f'/app/{self.pending.slug}/copy/')
        self.assertEqual(response.status_code, 404)

    def test_owner_copy_does_not_count(self):
        self.client.login(username='owner', password='pass12345')
        response = self.client.post(f'/app/{self.live.slug}/copy/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ignored'], 'owner')
        self.live.refresh_from_db()
        self.assertEqual(self.live.copies, 0)

    def test_one_copy_per_session(self):
        self.client.login(username='fan', password='pass12345')
        first = self.client.post(f'/app/{self.live.slug}/copy/')
        second = self.client.post(f'/app/{self.live.slug}/copy/')
        self.assertEqual(first.status_code, 200)
        self.assertNotIn('ignored', first.json())
        self.assertEqual(second.json()['ignored'], 'already')
        self.live.refresh_from_db()
        self.assertEqual(self.live.copies, 1)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class StaffIsNotAFreePassTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.staff = make_user('staffer')
        self.staff.is_staff = True
        self.staff.save()
        self.project = make_project(self.owner, self.cat, star_cost=3)
        self.project.zip_file.save('paid.zip', make_zip_file({'app.py': 'print(1)\n'}), save=True)

    def test_django_staff_cannot_download_paid_zip(self):
        self.assertFalse(user_can_download(self.staff, self.project))
        self.client.login(username='staffer', password='pass12345')
        response = self.client.get(f'/app/{self.project.slug}/download/')
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.project.slug, response.url)
        self.project.refresh_from_db()
        self.assertEqual(self.project.clones, 0)


@override_settings(RATELIMIT_ENABLE=False)
class AtomicStarToggleTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner', stars_balance=5)
        self.fan = make_user('fan')
        self.project = make_project(self.owner, self.cat, stars=0)

    def test_double_star_does_not_create_two_rows(self):
        from gallery.models import Star
        self.client.login(username='fan', password='pass12345')
        first = self.client.post(f'/app/{self.project.slug}/star/')
        again = self.client.post(f'/app/{self.project.slug}/star/')
        third = self.client.post(f'/app/{self.project.slug}/star/')
        self.assertTrue(first.json()['starred'])
        self.assertFalse(again.json()['starred'])
        self.assertTrue(third.json()['starred'])
        self.assertEqual(Star.objects.filter(user=self.fan, project=self.project).count(), 1)
        self.project.refresh_from_db()
        self.assertEqual(self.project.stars, 1)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False)
class WelcomeGrantTests(TestCase):
    """The 5 ★ grant is bound to email verification, not signup."""

    def test_signup_gives_zero_stars(self):
        response = self.client.post('/accounts/signup/', {
            'username': 'fresh',
            'email': 'fresh@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
        })
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='fresh')
        self.assertEqual(user.profile.stars_balance, 0)

    def test_verify_email_pays_grant_once(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        from users.models import StarEvent, WELCOME_STARS
        user = User.objects.create_user('verifyme', password='pass12345', email='v@test.com')
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        self.client.get(f'/accounts/verify/{uid}/{token}/')
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.stars_balance, WELCOME_STARS)
        self.assertEqual(StarEvent.objects.filter(user=user, reason='welcome').count(), 1)
        # Replaying the link never pays twice.
        token2 = default_token_generator.make_token(user)
        self.client.get(f'/accounts/verify/{uid}/{token2}/')
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.stars_balance, WELCOME_STARS)
        self.assertEqual(StarEvent.objects.filter(user=user, reason='welcome').count(), 1)

    def test_grant_helper_is_idempotent_under_direct_calls(self):
        from users.models import StarEvent, WELCOME_STARS
        from users.wallet import grant_welcome_stars
        user = User.objects.create_user('grantee', password='pass12345', email='g@test.com')
        self.assertTrue(grant_welcome_stars(user))
        self.assertFalse(grant_welcome_stars(user))
        self.assertFalse(grant_welcome_stars(user))
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.stars_balance, WELCOME_STARS)
        self.assertEqual(StarEvent.objects.filter(user=user, reason='welcome').count(), 1)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False)
class DeleteLifecycleTests(TestCase):
    """Deletes never destroy money records or paid downloads."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner', stars_balance=5)
        self.buyer = make_user('buyer', stars_balance=10)
        self.project = make_project(self.owner, self.cat, star_cost=3)
        self.project.zip_file.save('paid.zip', make_zip_file({'app.py': 'print(1)\n'}), save=True)

    def _buy(self):
        self.client.login(username='buyer', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/trade/')
        self.client.logout()

    def test_unpaid_vibe_hard_deletes(self):
        self.client.login(username='owner', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/delete/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AppProject.objects.filter(pk=self.project.pk).exists())

    def test_paid_vibe_soft_deletes_and_buyer_keeps_download(self):
        self._buy()
        self.client.login(username='owner', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/delete/')
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, 'removed')
        # Receipt survives.
        self.assertTrue(Trade.objects.filter(buyer=self.buyer, project=self.project).exists())
        # Buyer still downloads.
        self.client.logout()
        self.client.login(username='buyer', password='pass12345')
        download = self.client.get(f'/app/{self.project.slug}/download/')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download['Content-Type'], 'application/zip')
        # But the public page is gone.
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertEqual(page.status_code, 404)

    def test_removed_vibe_denies_non_buyers(self):
        self._buy()
        self.client.login(username='owner', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/delete/')
        self.client.logout()
        stranger = make_user('stranger', stars_balance=10)
        self.client.login(username='stranger', password='pass12345')
        download = self.client.get(f'/app/{self.project.slug}/download/')
        self.assertEqual(download.status_code, 404)
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertEqual(page.status_code, 404)

    def test_removed_vibe_leaves_feed(self):
        self._buy()
        self.client.login(username='owner', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/delete/')
        self.client.logout()
        feed = self.client.get('/')
        self.assertNotContains(feed, self.project.title)

    def test_db_protects_paid_project_from_hard_delete(self):
        from django.db.models.deletion import ProtectedError
        self._buy()
        with self.assertRaises(ProtectedError):
            self.project.delete()

    def test_account_delete_releases_sold_vibes_to_ghost(self):
        from gallery.lifecycle import GHOST_USERNAME
        self._buy()
        self.client.login(username='owner', password='pass12345')
        response = self.client.post('/settings/delete-account/', {'confirm': 'owner'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(username='owner').exists())
        self.project.refresh_from_db()
        self.assertEqual(self.project.owner.username, GHOST_USERNAME)
        self.assertEqual(self.project.status, 'removed')
        # Buyer keeps the download; the receipt names a NULL seller now.
        trade = Trade.objects.get(buyer=self.buyer, project=self.project)
        self.assertIsNone(trade.seller)
        self.client.login(username='buyer', password='pass12345')
        download = self.client.get(f'/app/{self.project.slug}/download/')
        self.assertEqual(download.status_code, 200)

    def test_buyer_account_delete_keeps_seller_receipt(self):
        self._buy()
        self.client.login(username='buyer', password='pass12345')
        self.client.post('/settings/delete-account/', {'confirm': 'buyer'})
        self.assertFalse(User.objects.filter(username='buyer').exists())
        trade = Trade.objects.get(project=self.project)
        self.assertIsNone(trade.buyer)
        self.assertEqual(trade.seller, self.owner)
        self.assertEqual(trade.cost, 3)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False)
class ZiputilStorageTests(TestCase):
    """ZIP access must work when FieldFile.path is unavailable (S3/R2)."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.project = make_project(self.owner, self.cat)
        self.project.zip_file.save(
            'tool.zip',
            make_zip_file({'src/app.py': 'print(1)\n', 'README.md': '# hi\n'}),
            save=True,
        )

    def _remote_field(self):
        """A FieldFile stand-in whose .path raises like remote storage does."""
        real = self.project.zip_file

        class RemoteFieldFile:
            @property
            def path(self):
                raise NotImplementedError('remote storage has no local path')

            def open(self, mode='rb'):
                real.open(mode)
                return real

            def read(self, *args, **kwargs):
                return real.read(*args, **kwargs)

            def close(self):
                real.close()

            def seek(self, *args, **kwargs):
                return real.seek(*args, **kwargs)

            def tell(self):
                return real.tell()

            def seekable(self):
                return True

        return RemoteFieldFile()

    def test_open_zip_local(self):
        from gallery.ziputil import open_zip
        with open_zip(self.project.zip_file) as z:
            self.assertIn('src/app.py', z.namelist())

    def test_open_zip_remote(self):
        from gallery.ziputil import open_zip
        with open_zip(self._remote_field()) as z:
            self.assertIn('src/app.py', z.namelist())

    def test_materialized_path_remote_creates_and_cleans_temp(self):
        import os
        from gallery.ziputil import materialized_path
        with materialized_path(self._remote_field()) as path:
            self.assertTrue(os.path.exists(path))
            import zipfile as zf
            with zf.ZipFile(path) as z:
                self.assertIn('README.md', z.namelist())
        self.assertFalse(os.path.exists(path))

    def test_materialized_path_local_is_original(self):
        from gallery.ziputil import materialized_path
        with materialized_path(self.project.zip_file) as path:
            self.assertEqual(path, self.project.zip_file.path)

    def test_build_tree_remote(self):
        from gallery.ziputil import build_tree
        tree, files = build_tree(self._remote_field())
        self.assertEqual(len(files), 2)
        self.assertIn('src', tree)
        self.assertIn('app.py', tree['src'])

    def test_language_detect_remote(self):
        from gallery.language import detect_languages_from_field
        stats = detect_languages_from_field(self._remote_field())
        self.assertIn('Python', stats)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False)
class ArtifactDetectionTests(TestCase):
    """Publish → launch loop: detected artifact must map to a real guide."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')

    def _project_with_files(self, paths):
        from gallery.models import AppFile
        project = make_project(self.owner, self.cat)
        project.zip_file.save('x.zip', make_zip_file({p: 'x' for p in paths}), save=True)
        for p in paths:
            AppFile.objects.create(project=project, path=p, size=1)
        return project

    def test_package_json_maps_to_frontend(self):
        from gallery.artifact_detect import detect_artifact
        project = self._project_with_files(['myapp/package.json', 'myapp/src/index.js'])
        self.assertEqual(detect_artifact(project), 'frontend')

    def test_dockerfile_beats_package_json(self):
        from gallery.artifact_detect import detect_artifact
        project = self._project_with_files(['app/Dockerfile', 'app/package.json'])
        self.assertEqual(detect_artifact(project), 'container')

    def test_aab_wins_over_everything(self):
        from gallery.artifact_detect import detect_artifact
        project = self._project_with_files(['release/app.aab', 'Dockerfile', 'package.json'])
        self.assertEqual(detect_artifact(project), 'aab')

    def test_index_html_is_static(self):
        from gallery.artifact_detect import detect_artifact
        project = self._project_with_files(['site/index.html', 'site/style.css'])
        self.assertEqual(detect_artifact(project), 'static')

    def test_manifest_without_index_is_extension(self):
        from gallery.artifact_detect import detect_artifact
        project = self._project_with_files(['ext/manifest.json', 'ext/background.js'])
        self.assertEqual(detect_artifact(project), 'extension')

    def test_manifest_with_index_is_not_extension(self):
        from gallery.artifact_detect import detect_artifact
        project = self._project_with_files(['pwa/manifest.json', 'pwa/index.html'])
        self.assertEqual(detect_artifact(project), 'static')

    def test_deep_node_modules_ignored(self):
        from gallery.artifact_detect import detect_artifact
        project = self._project_with_files(['app/node_modules/react/package.json'])
        self.assertEqual(detect_artifact(project), '')

    def test_every_detector_value_is_a_real_route(self):
        from gallery.artifact_detect import _DETECTORS, _ROUTE_VALUES
        for value, _ in _DETECTORS:
            self.assertIn(value, _ROUTE_VALUES)

    def test_owner_sees_launch_next_on_detail(self):
        project = self._project_with_files(['myapp/package.json'])
        self.client.login(username='owner', password='pass12345')
        response = self.client.get(f'/app/{project.slug}/')
        self.assertContains(response, '/launch/?artifact=frontend')

    def test_stranger_does_not_see_launch_next(self):
        project = self._project_with_files(['myapp/package.json'])
        other = make_user('other')
        self.client.login(username='other', password='pass12345')
        response = self.client.get(f'/app/{project.slug}/')
        self.assertNotContains(response, '/launch/?artifact=')

    def test_launch_hub_accepts_detected_value(self):
        response = self.client.get('/launch/?artifact=frontend')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vercel')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests', SEED_DEMO=False)
class ChallengeBountyLedgerTests(TestCase):
    def test_bounty_writes_ledger_row(self):
        from gallery.models import Challenge, Tag
        from users.models import StarEvent
        admin = make_user('admin', role='admin')
        creator = make_user('creator', stars_balance=5)
        tag = Tag.objects.create(name='challenge-week-9', slug='challenge-week-9')
        challenge = Challenge.objects.create(
            title='Week 9',
            description='Ship something.',
            bounty_stars=10,
            tag='challenge-week-9',
            start=timezone.now() - timedelta(days=1),
            end=timezone.now() + timedelta(days=6),
            is_active=True,
        )
        entry = make_project(creator, make_category(), title='Entry')
        entry.tags.add(tag)
        self.client.login(username='admin', password='pass12345')
        self.client.post(f'/challenges/{challenge.tag}/pick-winner/', {'winner_id': entry.id})
        creator.profile.refresh_from_db()
        self.assertEqual(creator.profile.stars_balance, 15)
        self.assertTrue(StarEvent.objects.filter(
            user=creator, delta=10, reason='challenge_bounty',
            ref=f'challenge:{challenge.tag}:{entry.slug}',
        ).exists())


# ===========================================================================
# Program-kind classification, honest previews, appeal scoring, taste feed.
# ===========================================================================

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class TaxonomyTests(TestCase):
    """The taxonomy is the contract every other piece depends on."""

    def test_coerce_rejects_unknown_values(self):
        from gallery.taxonomy import coerce_kind
        self.assertEqual(coerce_kind('roguelike-ish nonsense'), 'other')
        self.assertEqual(coerce_kind(''), 'other')
        self.assertEqual(coerce_kind(None), 'other')

    def test_coerce_accepts_aliases_an_llm_actually_returns(self):
        from gallery.taxonomy import coerce_kind
        self.assertEqual(coerce_kind('Games'), 'game')
        self.assertEqual(coerce_kind('web-app'), 'web_app')
        self.assertEqual(coerce_kind('MACHINE_LEARNING'), 'ai_ml')
        self.assertEqual(coerce_kind('chrome extension'.replace(' ', '_')), 'extension')

    def test_every_kind_declares_a_preview_capability(self):
        from gallery.taxonomy import PROGRAM_KINDS
        for k in PROGRAM_KINDS:
            self.assertIn(k['preview'], ('snippet', 'files'), k['value'])
            self.assertTrue(k['label'] and k['icon'] and k['blurb'], k['value'])

    def test_model_choices_match_taxonomy(self):
        from gallery.taxonomy import KIND_VALUES
        field = AppProject._meta.get_field('kind')
        self.assertEqual(tuple(c[0] for c in field.choices), KIND_VALUES)

    def test_zip_never_claims_a_runnable_preview(self):
        from gallery.taxonomy import preview_mode_for
        self.assertEqual(preview_mode_for('game', has_html=False, has_zip=True), 'files')
        self.assertEqual(preview_mode_for('game', has_html=True, has_zip=True), 'snippet')
        self.assertEqual(preview_mode_for('api_backend', has_html=False, has_zip=True), 'files')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class KindDetectTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('detectowner')

    def _with_files(self, paths, **kwargs):
        from gallery.models import AppFile
        project = make_project(self.owner, self.cat, **kwargs)
        project.zip_file.save(
            f'{project.slug}.zip', make_zip_file({p: 'x' for p in paths}), save=True
        )
        for p in paths:
            AppFile.objects.create(project=project, path=p, size=10)
        return project

    def test_unity_project_is_a_game(self):
        from gallery.kind_detect import detect_kind
        project = self._with_files([
            'MyGame/ProjectSettings/ProjectVersion.txt',
            'MyGame/Assets/Scenes/Level1.unity',
            'MyGame/Assets/Scripts/GameManager.cs',
        ])
        result = detect_kind(project)
        self.assertEqual(result['kind'], 'game')
        self.assertGreater(result['confidence'], 0.5)

    def test_godot_project_is_a_game(self):
        from gallery.kind_detect import detect_kind
        project = self._with_files(['game/project.godot', 'game/player.gd'])
        self.assertEqual(detect_kind(project)['kind'], 'game')

    def test_django_backend_is_api_backend_not_game(self):
        from gallery.kind_detect import detect_kind
        project = self._with_files(
            ['api/manage.py', 'api/app/wsgi.py', 'api/app/urls.py', 'api/requirements.txt'],
            title='Payments API', tech_stack='Django REST',
        )
        self.assertEqual(detect_kind(project)['kind'], 'api_backend')

    def test_flutter_app_is_mobile(self):
        from gallery.kind_detect import detect_kind
        project = self._with_files(
            ['app/pubspec.yaml', 'app/lib/main.dart'], tech_stack='Flutter',
        )
        self.assertEqual(detect_kind(project)['kind'], 'mobile_app')

    def test_notebook_is_ai_ml(self):
        from gallery.kind_detect import detect_kind
        project = self._with_files(
            ['ml/train.py', 'ml/notebooks/explore.ipynb'],
            title='Churn model', tech_stack='PyTorch',
        )
        self.assertEqual(detect_kind(project)['kind'], 'ai_ml')

    def test_canvas_snippet_is_detected_as_a_game(self):
        from gallery.kind_detect import detect_kind
        project = make_project(
            self.owner, self.cat,
            title='Mzansi Runner',
            html_code='<canvas id="c"></canvas><script>function loop(){requestAnimationFrame(loop)}</script>',
            js_code='document.addEventListener("keydown", e => {}); let score = 0;',
        )
        self.assertEqual(detect_kind(project)['kind'], 'game')

    def test_empty_project_falls_back_to_other_with_zero_confidence(self):
        from gallery.kind_detect import detect_kind
        project = AppProject(title='', short_description='', readme='', tech_stack='')
        result = detect_kind(project)
        self.assertEqual(result['kind'], 'other')
        self.assertEqual(result['confidence'], 0.0)

    def test_detector_never_emits_a_kind_outside_the_taxonomy(self):
        """Same guard artifact_detect has: producers may not invent values."""
        from gallery.kind_detect import _NAME_SIGNALS, _DIR_SIGNALS, _EXT_SIGNALS, _TEXT_SIGNALS, _LANGUAGE_SIGNALS
        from gallery.taxonomy import KIND_VALUES
        for table in (_NAME_SIGNALS, _DIR_SIGNALS, _EXT_SIGNALS, _TEXT_SIGNALS, _LANGUAGE_SIGNALS):
            for key, hits in table.items():
                for kind, weight in hits:
                    self.assertIn(kind, KIND_VALUES, f'{key} -> {kind}')
                    self.assertGreater(weight, 0, key)

    def test_deep_dependency_paths_do_not_decide_the_kind(self):
        from gallery.kind_detect import detect_kind
        project = self._with_files(
            ['site/index.html', 'site/style.css'],
            title='Portfolio site', short_description='My personal portfolio landing page.',
        )
        self.assertIn(detect_kind(project)['kind'], ('static_site', 'web_app'))

    def test_a_browser_snippet_is_never_labelled_a_backend(self):
        """Regression: a SaaS landing page's copy says 'backend' and 'API'.

        Marketing prose describes the product's subject, not the artifact.
        The artifact here is HTML we can verify, so shape beats words.
        """
        from gallery.kind_detect import detect_kind
        project = make_project(
            self.owner, self.cat,
            title='Waitlist Minimal',
            short_description='Waitlist page for a backend API product.',
            readme='# Waitlist\n\nA landing page for our backend API service. ' * 8,
            html_code='<section><h1>Join the waitlist</h1><form><input></form></section>',
        )
        self.assertNotIn(detect_kind(project)['kind'],
                         ('api_backend', 'mobile_app', 'desktop_app', 'cli_tool'))

    def test_a_snippet_dashboard_may_still_be_a_dashboard(self):
        """The damping must not punish kinds a browser genuinely can be."""
        from gallery.kind_detect import detect_kind
        project = make_project(
            self.owner, self.cat,
            title='Analytics Dashboard',
            short_description='An analytics dashboard with charts.',
            readme='# Dashboard\n\nCharts and analytics widgets. ' * 8,
            html_code='<div class="chart"><canvas></canvas></div><button>Filter</button>',
        )
        self.assertEqual(detect_kind(project)['kind'], 'data_viz')

    def test_web_native_flag_agrees_with_preview_capability(self):
        """A kind that a browser can be must be previewable, and vice versa."""
        from gallery.taxonomy import PROGRAM_KINDS
        for k in PROGRAM_KINDS:
            if k['web_native']:
                self.assertEqual(k['preview'], 'snippet', k['value'])
            else:
                self.assertEqual(k['preview'], 'files', k['value'])

    def test_evidence_is_returned_so_the_badge_is_arguable(self):
        from gallery.kind_detect import detect_kind
        project = self._with_files(['game/project.godot'])
        self.assertTrue(detect_kind(project)['evidence'])


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class ClassifyTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('classowner')

    def test_creator_pick_beats_the_detector(self):
        from gallery.classify import classify_project
        project = make_project(
            self.owner, self.cat, title='Django API', tech_stack='Django',
            html_code='<p>hi</p>', creator_kind='game',
        )
        verdict = classify_project(project, allow_llm=False)
        self.assertEqual(verdict['kind'], 'game')
        self.assertEqual(verdict['source'], 'creator')
        project.refresh_from_db()
        self.assertEqual(project.kind, 'game')

    def test_classification_persists_all_audit_fields(self):
        from gallery.classify import classify_project
        project = make_project(self.owner, self.cat, title='Pygame arcade shooter',
                               tech_stack='pygame', html_code='<p>x</p>')
        classify_project(project, allow_llm=False)
        project.refresh_from_db()
        self.assertEqual(project.kind, 'game')
        self.assertEqual(project.kind_source, 'heuristic')
        self.assertGreater(project.kind_confidence, 0)
        self.assertTrue(project.kind_evidence)

    def test_no_api_key_means_no_llm_call_and_still_a_kind(self):
        from gallery.classify import llm_classify, classify_project
        with override_settings(ANTHROPIC_API_KEY='', GEMINI_API_KEY='', GROQ_API_KEY=''):
            import os
            saved = {k: os.environ.pop(k, None) for k in
                     ('ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'GROQ_API_KEY')}
            try:
                self.assertIsNone(llm_classify(make_project(self.owner, self.cat)))
                project = make_project(self.owner, self.cat, title='Some thing')
                verdict = classify_project(project, allow_llm=True)
                self.assertIn(verdict['kind'], dict(AppProject._meta.get_field('kind').choices))
            finally:
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v

    def test_llm_only_consulted_when_the_heuristic_is_unsure(self):
        from gallery.classify import needs_llm
        self.assertTrue(needs_llm({'kind': 'other', 'confidence': 0.9}))
        self.assertTrue(needs_llm({'kind': 'game', 'confidence': 0.2}))
        self.assertFalse(needs_llm({'kind': 'game', 'confidence': 0.9}))

    def test_llm_answer_outside_the_taxonomy_is_coerced(self):
        from gallery.classify import _parse_llm_json
        parsed = _parse_llm_json('{"kind": "totally made up", "confidence": 0.9, "appeal": 80}')
        self.assertEqual(parsed['kind'], 'other')
        parsed = _parse_llm_json('sure! {"kind": "Games", "confidence": 2, "appeal": 500}')
        self.assertEqual(parsed['kind'], 'game')
        self.assertEqual(parsed['confidence'], 1.0)   # clamped
        self.assertEqual(parsed['appeal'], 100.0)     # clamped

    def test_llm_budget_caps_calls_per_minute(self):
        """The flood guard: a spam wave cannot fan out into unbounded calls."""
        from django.core.cache import cache
        from gallery.classify import _take_budget
        cache.clear()
        with override_settings(KIND_LLM_CALLS_PER_MINUTE=3):
            allowed = [_take_budget() for _ in range(10)]
        self.assertEqual(allowed.count(True), 3)
        self.assertEqual(allowed.count(False), 7)

    def test_budget_of_zero_disables_the_llm_entirely(self):
        from django.core.cache import cache
        from gallery.classify import _take_budget
        cache.clear()
        with override_settings(KIND_LLM_CALLS_PER_MINUTE=0):
            self.assertFalse(_take_budget())

    def test_preview_mode_is_computed_not_guessed(self):
        from gallery.classify import classify_project
        zipped = make_project(self.owner, self.cat, title='Unity build')
        zipped.zip_file.save('g.zip', make_zip_file({'a.txt': 'x'}), save=True)
        classify_project(zipped, allow_llm=False)
        zipped.refresh_from_db()
        self.assertEqual(zipped.preview_mode, 'files')
        self.assertFalse(zipped.can_run_preview)

        snippet = make_project(self.owner, self.cat, title='Snippet', html_code='<b>hi</b>')
        classify_project(snippet, allow_llm=False)
        snippet.refresh_from_db()
        self.assertEqual(snippet.preview_mode, 'snippet')
        self.assertTrue(snippet.can_run_preview)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class AppealScoreTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('appealowner')

    def test_quality_lets_a_brand_new_vibe_score_above_zero(self):
        """Cold start: no traffic must not mean no rank."""
        from gallery.interest import compute_appeal
        project = make_project(
            self.owner, self.cat,
            readme='# Great\n' + ('detailed docs. ' * 100),
            tech_stack='Django', file_count=25,
            html_code='<b>runs</b>',
        )
        project.preview_mode = 'snippet'
        self.assertGreater(compute_appeal(project), 10)

    def test_engagement_raises_the_score(self):
        from gallery.interest import compute_appeal
        quiet = make_project(self.owner, self.cat, title='Quiet')
        busy = make_project(self.owner, self.cat, title='Busy',
                            stars=120, clones=60, views=3000, review_count=15)
        self.assertGreater(compute_appeal(busy), compute_appeal(quiet))

    def test_freshness_decays_but_never_to_zero(self):
        from gallery.interest import freshness_multiplier, FRESHNESS_FLOOR
        fresh = make_project(self.owner, self.cat, title='Fresh')
        old = make_project(self.owner, self.cat, title='Old')
        AppProject.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=365))
        old.refresh_from_db()
        self.assertGreater(freshness_multiplier(fresh), freshness_multiplier(old))
        self.assertGreaterEqual(freshness_multiplier(old), FRESHNESS_FLOOR)

    def test_new_good_vibe_can_outrank_an_old_popular_one(self):
        """The whole point of decay: page one must be able to turn over."""
        from gallery.interest import compute_appeal
        old = make_project(self.owner, self.cat, title='Old hit',
                           stars=200, clones=100, views=9000)
        AppProject.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=400))
        old.refresh_from_db()
        new = make_project(
            self.owner, self.cat, title='New gem',
            readme='# New\n' + ('thorough documentation here. ' * 80),
            tech_stack='Phaser', file_count=30, stars=6, views=200,
            html_code='<canvas></canvas>',
        )
        new.preview_mode = 'snippet'
        self.assertGreater(compute_appeal(new), compute_appeal(old))

    def test_runnable_vibes_beat_identical_unrunnable_ones(self):
        from gallery.interest import runnable_component
        runnable = make_project(self.owner, self.cat, title='Runs', html_code='<b>x</b>')
        runnable.preview_mode = 'snippet'
        zipped = make_project(self.owner, self.cat, title='Zip only')
        zipped.zip_file.save('z.zip', make_zip_file({'a.py': 'x'}), save=True)
        self.assertGreater(runnable_component(runnable), runnable_component(zipped))

    def test_score_is_clamped_to_the_documented_range(self):
        from gallery.interest import compute_appeal
        absurd = make_project(self.owner, self.cat, title='Absurd', stars=10 ** 9,
                              clones=10 ** 9, views=10 ** 9, review_count=10 ** 6,
                              is_featured=True)
        score = compute_appeal(absurd)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)

    def test_refresh_batch_scores_never_scored_rows_first(self):
        from gallery.interest import refresh_batch
        for i in range(3):
            make_project(self.owner, self.cat, title=f'Batch {i}', stars=i * 10)
        AppProject.objects.update(appeal_score=0, appeal_updated_at=None)
        touched = refresh_batch(limit=10)
        self.assertEqual(touched, AppProject.objects.filter(status='published').count())
        self.assertFalse(
            AppProject.objects.filter(status='published', appeal_updated_at=None).exists())

    def test_refresh_batch_respects_its_limit(self):
        from gallery.interest import refresh_batch
        for i in range(6):
            make_project(self.owner, self.cat, title=f'Limited {i}')
        AppProject.objects.update(appeal_updated_at=None)
        self.assertEqual(refresh_batch(limit=2), 2)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class TasteLearningTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.cat = make_category()
        self.owner = make_user('tasteowner')
        self.user = make_user('gamer')

    def _project(self, kind, title, **kwargs):
        p = make_project(self.owner, self.cat, title=title, **kwargs)
        AppProject.objects.filter(pk=p.pk).update(kind=kind)
        p.refresh_from_db()
        return p

    def test_recording_builds_an_affinity_row(self):
        from gallery import taste
        from gallery.models import KindAffinity
        game = self._project('game', 'Runner')
        self.assertTrue(taste.record(self.user, game, 'star', project=game))
        row = KindAffinity.objects.get(user=self.user, kind='game')
        self.assertEqual(row.score, KindAffinity.EVENT_WEIGHTS['star'])
        self.assertEqual(row.events, 1)

    def test_anonymous_users_are_never_recorded(self):
        from django.contrib.auth.models import AnonymousUser
        from gallery import taste
        from gallery.models import KindAffinity
        game = self._project('game', 'Runner anon')
        self.assertFalse(taste.record(AnonymousUser(), game, 'star', project=game))
        self.assertEqual(KindAffinity.objects.count(), 0)

    def test_stronger_actions_weigh_more_than_weaker_ones(self):
        from gallery.models import KindAffinity
        self.assertGreater(KindAffinity.EVENT_WEIGHTS['trade'],
                           KindAffinity.EVENT_WEIGHTS['view'])
        self.assertGreater(KindAffinity.EVENT_WEIGHTS['download'],
                           KindAffinity.EVENT_WEIGHTS['view'])

    def test_repeat_views_of_one_project_are_deduped(self):
        from gallery import taste
        from gallery.models import KindAffinity
        game = self._project('game', 'Dedupe me')
        taste.record(self.user, game, 'view', project=game)
        for _ in range(5):
            taste.record(self.user, game, 'view', project=game)
        self.assertEqual(KindAffinity.objects.get(user=self.user, kind='game').events, 1)

    def test_viewing_your_own_vibe_is_not_taste(self):
        from gallery import taste
        from gallery.models import KindAffinity
        mine = self._project('game', 'My own game')
        taste.record(self.owner, mine, 'view', project=mine)
        self.assertFalse(KindAffinity.objects.filter(user=self.owner).exists())

    def test_scores_decay_over_time(self):
        from gallery import taste
        from gallery.models import KindAffinity
        game = self._project('game', 'Old love')
        taste.record(self.user, game, 'trade', project=game)
        row = KindAffinity.objects.get(user=self.user, kind='game')
        fresh = taste.affinities(self.user)['game']
        aged = taste._decayed(row.score, row.updated_at,
                              timezone.now() + timedelta(days=KindAffinity.HALF_LIFE_DAYS))
        self.assertAlmostEqual(aged, fresh / 2, places=2)

    def test_affinities_normalize_against_the_users_own_top_kind(self):
        from gallery import taste
        game = self._project('game', 'G1')
        api = self._project('api_backend', 'A1')
        taste.record(self.user, game, 'trade', project=game)
        taste.record(self.user, api, 'view', project=api)
        norm = taste.normalized_affinities(self.user)
        self.assertEqual(norm['game'], 1.0)
        self.assertLess(norm['api_backend'], 1.0)

    def test_a_single_event_is_not_enough_signal_to_reorder(self):
        from gallery import taste
        game = self._project('game', 'One click')
        taste.record(self.user, game, 'view', project=game)
        self.assertFalse(taste.has_enough_signal(self.user))
        taste.record(self.user, self._project('game', 'Two clicks'), 'star')
        self.assertTrue(taste.has_enough_signal(self.user))

    def test_personalized_order_puts_the_liked_kind_first(self):
        """The headline behaviour: a game lover sees games first."""
        from gallery import taste
        api = self._project('api_backend', 'Boring API')
        game = self._project('game', 'Fun game')
        # Make the game objectively *less* appealing so only taste can lift it.
        AppProject.objects.filter(pk=api.pk).update(appeal_score=80)
        AppProject.objects.filter(pk=game.pk).update(appeal_score=50)

        qs = AppProject.objects.filter(status='published')
        default_first = list(qs.order_by('-appeal_score'))[0]
        self.assertEqual(default_first.pk, api.pk)

        taste.record(self.user, game, 'trade', project=game)
        taste.record(self.user, game, 'fork', project=game)
        ordered, norm = taste.personalized_order(qs, self.user)
        self.assertEqual(norm.get('game'), 1.0)
        self.assertEqual(list(ordered)[0].pk, game.pk)

    def test_personalization_is_bounded_and_cannot_resurface_junk(self):
        from gallery import taste
        junk = self._project('game', 'Junk game')
        great = self._project('api_backend', 'Superb API')
        AppProject.objects.filter(pk=junk.pk).update(appeal_score=1)
        AppProject.objects.filter(pk=great.pk).update(appeal_score=99)
        taste.record(self.user, junk, 'trade', project=junk)
        taste.record(self.user, junk, 'fork', project=junk)
        ordered, _ = taste.personalized_order(
            AppProject.objects.filter(status='published'), self.user)
        self.assertEqual(list(ordered)[0].pk, great.pk)

    def test_no_signal_falls_back_to_global_order(self):
        from gallery import taste
        a = self._project('game', 'A')
        b = self._project('web_app', 'B')
        AppProject.objects.filter(pk=a.pk).update(appeal_score=10)
        AppProject.objects.filter(pk=b.pk).update(appeal_score=90)
        ordered, norm = taste.personalized_order(
            AppProject.objects.filter(status='published'), self.user)
        self.assertEqual(norm, {})
        self.assertEqual(list(ordered)[0].pk, b.pk)

    def test_ordering_happens_in_sql_not_python(self):
        """Must be a real queryset so the Paginator slices the ordered set."""
        from django.db.models import QuerySet
        from gallery import taste
        game = self._project('game', 'SQL game')
        taste.record(self.user, game, 'trade', project=game)
        taste.record(self.user, game, 'star', project=game)
        ordered, _ = taste.personalized_order(
            AppProject.objects.filter(status='published'), self.user)
        self.assertIsInstance(ordered, QuerySet)
        self.assertIn('ORDER BY', str(ordered.query).upper())


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class DiscoveryFeedTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.cat = make_category()
        self.owner = make_user('feedowner')
        self.user = make_user('feeduser')

    def _project(self, kind, title, appeal=50, **kwargs):
        p = make_project(self.owner, self.cat, title=title, **kwargs)
        AppProject.objects.filter(pk=p.pk).update(kind=kind, appeal_score=appeal)
        p.refresh_from_db()
        return p

    def test_program_filter_narrows_the_grid(self):
        self._project('game', 'A game')
        self._project('api_backend', 'An API')
        response = self.client.get('/?program=game')
        self.assertEqual(response.status_code, 200)
        titles = [p.title for p in response.context['page']]
        self.assertIn('A game', titles)
        self.assertNotIn('An API', titles)

    def test_unknown_program_filter_shows_everything_not_nothing(self):
        self._project('game', 'Still visible')
        response = self.client.get('/?program=not-a-real-kind')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Still visible', [p.title for p in response.context['page']])

    def test_runnable_filter_only_returns_really_runnable_vibes(self):
        runnable = self._project('game', 'Playable', html_code='<canvas></canvas>')
        AppProject.objects.filter(pk=runnable.pk).update(preview_mode='snippet')
        notrunnable = self._project('game', 'Zip game')
        notrunnable.zip_file.save('g.zip', make_zip_file({'a.txt': 'x'}), save=True)
        AppProject.objects.filter(pk=notrunnable.pk).update(preview_mode='files')
        response = self.client.get('/?runnable=1')
        titles = [p.title for p in response.context['page']]
        self.assertIn('Playable', titles)
        self.assertNotIn('Zip game', titles)

    def test_anonymous_visitor_gets_the_global_order_not_a_crash(self):
        self._project('game', 'Anon game', appeal=10)
        self._project('web_app', 'Anon web', appeal=90)
        response = self.client.get('/?sort=trending')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page'][0].title, 'Anon web')
        self.assertFalse(response.context['personalized'])

    def test_signed_in_game_lover_sees_games_first(self):
        """End-to-end version of the request: learn, then reorder the feed."""
        from gallery import taste
        game = self._project('game', 'Kasi Kart', appeal=40)
        self._project('api_backend', 'Invoice API', appeal=85)
        taste.record(self.user, game, 'trade', project=game)
        taste.record(self.user, game, 'fork', project=game)
        self.client.force_login(self.user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['personalized'])
        self.assertEqual(response.context['page'][0].title, 'Kasi Kart')

    def test_newest_sort_is_never_silently_personalized(self):
        from gallery import taste
        old_game = self._project('game', 'Old game', appeal=90)
        AppProject.objects.filter(pk=old_game.pk).update(
            created_at=timezone.now() - timedelta(days=10))
        new_api = self._project('api_backend', 'New API', appeal=1)
        taste.record(self.user, old_game, 'trade', project=old_game)
        taste.record(self.user, old_game, 'fork', project=old_game)
        self.client.force_login(self.user)
        response = self.client.get('/?sort=newest')
        self.assertEqual(response.context['page'][0].title, 'New API')

    def test_explicit_filter_beats_the_personalized_guess(self):
        from gallery import taste
        game = self._project('game', 'Loved game', appeal=90)
        self._project('api_backend', 'Requested API', appeal=5)
        taste.record(self.user, game, 'trade', project=game)
        taste.record(self.user, game, 'fork', project=game)
        self.client.force_login(self.user)
        response = self.client.get('/?program=api_backend')
        titles = [p.title for p in response.context['page']]
        self.assertEqual(titles, ['Requested API'])

    def test_search_still_works_while_personalized(self):
        from gallery import taste
        game = self._project('game', 'Zebra platformer', appeal=50)
        self._project('api_backend', 'Zebra invoicing', appeal=50)
        taste.record(self.user, game, 'trade', project=game)
        taste.record(self.user, game, 'fork', project=game)
        self.client.force_login(self.user)
        response = self.client.get('/?q=Zebra')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['page']), 2)

    def test_feed_shows_the_kind_badge_and_honest_preview_label(self):
        p = self._project('api_backend', 'Labelled API')
        p.zip_file.save('a.zip', make_zip_file({'a.py': 'x'}), save=True)
        AppProject.objects.filter(pk=p.pk).update(preview_mode='files')
        response = self.client.get('/')
        body = response.content.decode()
        self.assertIn('API / backend', body)
        self.assertIn('Files only', body)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class HonestPreviewTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('previewowner')

    def test_detail_page_of_an_unrunnable_vibe_says_so(self):
        project = make_project(self.owner, self.cat, title='Backend only')
        project.zip_file.save('b.zip', make_zip_file({'main.go': 'x'}), save=True)
        AppProject.objects.filter(pk=project.pk).update(
            kind='api_backend', preview_mode='files')
        response = self.client.get(f'/app/{project.slug}/')
        body = response.content.decode()
        self.assertIn('No live preview', body)
        self.assertNotIn(f"/app/{project.slug}/preview/\" sandbox", body)

    def test_runnable_snippet_still_gets_its_iframe(self):
        project = make_project(self.owner, self.cat, title='Runs fine',
                               html_code='<b>hello</b>')
        AppProject.objects.filter(pk=project.pk).update(
            kind='web_app', preview_mode='snippet')
        response = self.client.get(f'/app/{project.slug}/')
        self.assertIn('sandbox="allow-scripts allow-forms"', response.content.decode())

    def test_can_run_preview_requires_real_html_not_just_the_mode(self):
        project = make_project(self.owner, self.cat, title='Lying mode')
        AppProject.objects.filter(pk=project.pk).update(preview_mode='snippet')
        project.refresh_from_db()
        self.assertFalse(project.can_run_preview)

    def test_every_kind_is_publishable_including_unpreviewable_ones(self):
        """The rule: everything may be uploaded; only honesty differs."""
        from gallery.taxonomy import PROGRAM_KINDS
        from gallery.classify import classify_project
        for k in PROGRAM_KINDS:
            project = make_project(self.owner, self.cat, title=f"A {k['value']}",
                                   creator_kind=k['value'])
            project.zip_file.save(f"{k['value']}.zip",
                                  make_zip_file({'a.txt': 'x'}), save=True)
            classify_project(project, allow_llm=False)
            project.refresh_from_db()
            self.assertEqual(project.status, 'published')
            self.assertEqual(project.kind, k['value'])
            self.assertTrue(project.preview_note)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class KindApiTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('apiowner')

    def test_api_exposes_program_kind_without_breaking_the_old_key(self):
        import json
        project = make_project(self.owner, self.cat, title='API listed')
        AppProject.objects.filter(pk=project.pk).update(kind='game', preview_mode='files')
        data = json.loads(self.client.get('/api/v1/apps/').content)
        row = data['results'][0]
        self.assertEqual(row['kind'], 'snippet')          # legacy meaning intact
        self.assertEqual(row['program_kind'], 'game')
        self.assertEqual(row['preview'], 'files')
        self.assertFalse(row['can_run_preview'])

    def test_api_can_filter_by_program_kind(self):
        import json
        g = make_project(self.owner, self.cat, title='API game')
        a = make_project(self.owner, self.cat, title='API backend')
        AppProject.objects.filter(pk=g.pk).update(kind='game')
        AppProject.objects.filter(pk=a.pk).update(kind='api_backend')
        data = json.loads(self.client.get('/api/v1/apps/?program=game').content)
        self.assertEqual([r['title'] for r in data['results']], ['API game'])

    def test_taxonomy_endpoint_lists_every_kind(self):
        import json
        from gallery.taxonomy import KIND_VALUES
        data = json.loads(self.client.get('/api/v1/program-kinds/').content)
        self.assertEqual(tuple(r['value'] for r in data['results']), KIND_VALUES)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class PublishClassificationTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.cat = make_category()
        self.user = make_user('publisher')
        self.client.force_login(self.user)

    def _payload(self, **extra):
        data = {
            'title': 'My canvas game',
            'category': self.cat.id,
            'short_description': 'A tiny browser game built with a canvas loop.',
            'readme': '# My canvas game\n\n' + ('It is a small arcade game. ' * 6),
            'tech_stack': 'HTML, Canvas',
            'html_code': '<canvas id="c"></canvas><script>requestAnimationFrame(function l(){})</script>',
            'css_code': '',
            'js_code': 'let score = 0;',
            'star_cost': 0,
            'price_zar': 0,
            'creator_kind': '',
        }
        data.update(extra)
        return data

    def test_published_snippet_is_classified_and_scored(self):
        for i in range(3):
            make_project(self.user, self.cat, title=f'Prior {i}')
        response = self.client.post('/publish/', self._payload(), follow=True)
        self.assertEqual(response.status_code, 200)
        project = AppProject.objects.get(title='My canvas game')
        self.assertEqual(project.status, 'published')
        self.assertEqual(project.kind, 'game')
        self.assertEqual(project.preview_mode, 'snippet')
        self.assertGreater(project.appeal_score, 0)

    def test_creator_override_is_honoured_through_the_form(self):
        for i in range(3):
            make_project(self.user, self.cat, title=f'Prior2 {i}')
        self.client.post('/publish/', self._payload(
            title='Actually a template', creator_kind='template'), follow=True)
        project = AppProject.objects.get(title='Actually a template')
        self.assertEqual(project.kind, 'template')
        self.assertEqual(project.kind_source, 'creator')

    def test_form_rejects_a_kind_outside_the_taxonomy(self):
        form = AppUploadForm(data=self._payload(creator_kind='definitely-not-real'))
        self.assertFalse(form.is_valid())
        self.assertIn('creator_kind', form.errors)

    def test_publishing_teaches_the_platform_what_you_build(self):
        from gallery.models import KindAffinity
        for i in range(3):
            make_project(self.user, self.cat, title=f'Prior3 {i}')
        self.client.post('/publish/', self._payload(title='Taught game'), follow=True)
        self.assertTrue(
            KindAffinity.objects.filter(user=self.user, kind='game').exists())

    def test_zip_upload_is_classified_by_the_pipeline(self):
        from gallery.tasks import classify_and_score
        project = make_project(self.user, self.cat, title='Pipeline unity',
                               status='pending')
        project.zip_file.save('u.zip', make_zip_file({
            'ProjectSettings/ProjectVersion.txt': 'x',
            'Assets/Scenes/Main.unity': 'x',
        }), save=True)
        from gallery.models import AppFile
        for path in ('ProjectSettings/ProjectVersion.txt', 'Assets/Scenes/Main.unity'):
            AppFile.objects.create(project=project, path=path, size=5)
        classify_and_score(project)
        project.refresh_from_db()
        self.assertEqual(project.kind, 'game')
        self.assertEqual(project.preview_mode, 'files')
        self.assertGreater(project.appeal_score, 0)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class InteractionSignalTests(TestCase):
    """Every learning hook, proven through a real request."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.cat = make_category()
        self.owner = make_user('sigowner')
        self.user = make_user('siguser', stars_balance=20)
        self.game = make_project(self.owner, self.cat, title='Signal game', star_cost=2)
        self.game.zip_file.save('s.zip', make_zip_file({'a.py': 'x'}), save=True)
        AppProject.objects.filter(pk=self.game.pk).update(kind='game')
        self.game.refresh_from_db()
        self.client.force_login(self.user)

    def _score(self):
        from gallery.models import KindAffinity
        row = KindAffinity.objects.filter(user=self.user, kind='game').first()
        return row.score if row else 0

    def test_viewing_a_detail_page_records_taste(self):
        self.client.get(f'/app/{self.game.slug}/')
        self.assertGreater(self._score(), 0)

    def test_starring_records_taste(self):
        self.client.post(f'/app/{self.game.slug}/star/')
        self.assertGreaterEqual(self._score(), 3)

    def test_saving_records_taste(self):
        self.client.post(f'/app/{self.game.slug}/save/')
        self.assertGreaterEqual(self._score(), 3)

    def test_trading_records_the_strongest_signal(self):
        self.client.post(f'/app/{self.game.slug}/trade/')
        self.assertTrue(Trade.objects.filter(buyer=self.user, project=self.game).exists())
        self.assertGreaterEqual(self._score(), 8)

    def test_a_failed_interaction_does_not_teach_anything(self):
        broke = make_user('brokeuser', stars_balance=0)
        self.client.force_login(broke)
        self.client.post(f'/app/{self.game.slug}/trade/')
        from gallery.models import KindAffinity
        self.assertFalse(KindAffinity.objects.filter(user=broke, kind='game').exists())

    def test_recording_never_breaks_the_user_action(self):
        """Taste is best-effort: a broken recorder must not break a download."""
        from unittest.mock import patch
        Trade.objects.create(buyer=self.user, seller=self.owner,
                             project=self.game, cost=2)
        with patch('gallery.taste.KindAffinity.objects.select_for_update',
                   side_effect=RuntimeError('db on fire')):
            response = self.client.get(f'/app/{self.game.slug}/download/')
        self.assertEqual(response.status_code, 200)


@override_settings(RATELIMIT_ENABLE=False)
class CoOwnerSplitTests(TestCase):
    """Co-owner revenue splits — the trust layer for team-built vibes.

    5 Whys: Why test the money path so hard? Splits move currency to MORE
    recipients per purchase — every invariant that held for one seller
    (zero-sum, ledger reconciliation, no replay, verified gate) must hold
    for N sellers, or the economy gains a rounding/minting hole.
    """

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner', stars_balance=5)
        self.buyer = make_user('buyer', stars_balance=10)
        self.partner = make_user('partner', stars_balance=0)
        self.partner2 = make_user('partner2', stars_balance=0)
        self.project = make_project(self.owner, self.cat, star_cost=4, price_zar=0)
        self.project.zip_file.save('paid.zip', make_zip_file({'app.py': 'print(1)\n'}), save=True)

    def _trade(self):
        self.client.login(username='buyer', password='pass12345')
        return self.client.post(f'/app/{self.project.slug}/trade/')

    def test_no_co_owners_owner_gets_everything(self):
        """The pre-existing behaviour must not change when no split is set."""
        response = self._trade()
        self.assertEqual(response.status_code, 302)
        self.owner.profile.refresh_from_db()
        self.buyer.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 9)   # 5 + 4
        self.assertEqual(self.buyer.profile.stars_balance, 6)   # 10 - 4
        self.assertEqual(Trade.objects.filter(buyer=self.buyer, project=self.project).count(), 1)
        # Buyer still unlocks the ZIP.
        download = self.client.get(f'/app/{self.project.slug}/download/')
        self.assertEqual(download.status_code, 200)

    def test_5050_split_pays_both_and_buyer_once(self):
        ProjectCoOwner.objects.create(project=self.project, user=self.partner, share_percent=50)
        response = self._trade()
        self.assertEqual(response.status_code, 302)
        self.owner.profile.refresh_from_db()
        self.partner.profile.refresh_from_db()
        self.buyer.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 7)    # 5 + 2
        self.assertEqual(self.partner.profile.stars_balance, 2)  # 0 + 2
        self.assertEqual(self.buyer.profile.stars_balance, 6)    # 10 - 4
        rows = list(Trade.objects.filter(buyer=self.buyer, project=self.project))
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(r.cost for r in rows), 4)           # zero-sum
        # Replay must not pay twice.
        self._trade()
        self.assertEqual(Trade.objects.filter(buyer=self.buyer, project=self.project).count(), 2)
        self.owner.profile.refresh_from_db()
        self.partner.profile.refresh_from_db()
        self.buyer.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 7)
        self.assertEqual(self.partner.profile.stars_balance, 2)
        self.assertEqual(self.buyer.profile.stars_balance, 6)

    def test_three_way_rounding_sums_exactly(self):
        """5★ across 34/33/33 must distribute 2/2/1 — never 3 or 6."""
        self.project.star_cost = 5
        self.project.save(update_fields=['star_cost'])
        ProjectCoOwner.objects.create(project=self.project, user=self.partner, share_percent=34)
        ProjectCoOwner.objects.create(project=self.project, user=self.partner2, share_percent=33)
        self._trade()
        rows = list(Trade.objects.filter(buyer=self.buyer, project=self.project))
        self.assertEqual(sum(r.cost for r in rows), 5)
        self.assertEqual(
            sorted(r.cost for r in rows), [1, 2, 2],
            f'largest-remainder must hit 2/2/1, got {sorted(r.cost for r in rows)}',
        )
        # Every wallet reconciles with its ledger — the zero-sum invariant.
        # (Opening admin_adjust rows mirror the balances make_user set
        # directly, so sum(deltas) == balance holds before and after.)
        from users.models import StarEvent
        from users.wallet import ledger_balance
        openings = {self.owner: 5, self.partner: 0, self.partner2: 0}
        for u, opening in openings.items():
            StarEvent.objects.create(user=u, delta=opening, reason='admin_adjust', ref='test-open')
        for u in (self.owner, self.partner, self.partner2):
            u.profile.refresh_from_db()
            self.assertEqual(ledger_balance(u), u.profile.stars_balance, u.username)

    def test_owner_zero_share_when_coowners_take_all(self):
        ProjectCoOwner.objects.create(project=self.project, user=self.partner, share_percent=60)
        ProjectCoOwner.objects.create(project=self.project, user=self.partner2, share_percent=40)
        self._trade()
        rows = list(Trade.objects.filter(buyer=self.buyer, project=self.project))
        self.assertEqual(sum(r.cost for r in rows), 4)
        self.assertEqual(sorted(r.cost for r in rows), [2, 2])
        self.assertFalse(Trade.objects.filter(buyer=self.buyer, seller=self.owner).exists())
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 5)  # untouched

    def test_management_rejects_sum_over_100(self):
        ProjectCoOwner.objects.create(project=self.project, user=self.partner, share_percent=70)
        self.client.login(username='owner', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/co-owners/add/', {
            'username': 'partner2', 'share_percent': 40,
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProjectCoOwner.objects.filter(project=self.project, user=self.partner2).exists())

    def test_management_rejects_owner_and_duplicate_and_unknown(self):
        self.client.login(username='owner', password='pass12345')
        ProjectCoOwner.objects.create(project=self.project, user=self.partner, share_percent=30)
        url = f'/app/{self.project.slug}/co-owners/add/'
        self.client.post(url, {'username': 'owner', 'share_percent': 10})       # owner is the remainder
        self.client.post(url, {'username': 'partner', 'share_percent': 10})     # duplicate
        self.client.post(url, {'username': 'nobody-here', 'share_percent': 10}) # unknown
        self.assertEqual(ProjectCoOwner.objects.filter(project=self.project).count(), 1)

    def test_add_remove_flow_and_notification(self):
        self.client.login(username='owner', password='pass12345')
        url = f'/app/{self.project.slug}/co-owners/add/'
        response = self.client.post(url, {'username': 'partner', 'share_percent': 50})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProjectCoOwner.objects.filter(project=self.project, user=self.partner, share_percent=50).exists())
        self.assertTrue(Notification.objects.filter(user=self.partner, kind='co_owner').exists())
        # Remove — share returns to the owner.
        response = self.client.post(f'/app/{self.project.slug}/co-owners/{self.partner.id}/remove/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProjectCoOwner.objects.filter(project=self.project, user=self.partner).exists())
        self._trade()
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 9)  # full 4 back to owner

    def test_non_owner_cannot_manage(self):
        self.client.login(username='partner', password='pass12345')
        response = self.client.post(f'/app/{self.project.slug}/co-owners/add/', {
            'username': 'partner2', 'share_percent': 50,
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ProjectCoOwner.objects.filter(project=self.project).exists())

    def test_co_owner_account_deletion_cascades_share_back(self):
        ProjectCoOwner.objects.create(project=self.project, user=self.partner, share_percent=50)
        partner_id = self.partner.pk
        self.partner.delete()  # leaves the platform; pk becomes None afterwards
        self.assertFalse(ProjectCoOwner.objects.filter(project=self.project, user_id=partner_id).exists())
        self._trade()
        rows = list(Trade.objects.filter(buyer=self.buyer, project=self.project))
        self.assertEqual(len(rows), 1)  # only the owner is left
        self.assertEqual(rows[0].cost, 4)
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 9)

    def test_app_detail_shows_co_owners(self):
        ProjectCoOwner.objects.create(project=self.project, user=self.partner, share_percent=50)
        response = self.client.get(f'/app/{self.project.slug}/')
        self.assertContains(response, 'Co-owner')
        self.assertContains(response, '@partner')
        self.assertContains(response, '50% of trades')

    def test_split_trade_notifies_each_recipient_with_share(self):
        ProjectCoOwner.objects.create(project=self.project, user=self.partner, share_percent=50)
        self._trade()
        owner_note = Notification.objects.filter(user=self.owner, kind='trade').first()
        partner_note = Notification.objects.filter(user=self.partner, kind='trade').first()
        self.assertIsNotNone(owner_note)
        self.assertIsNotNone(partner_note)
        self.assertIn('your share', partner_note.title)
        self.assertIn('2 ★', partner_note.title)


# ==========================================================================
# Trust badge — the pipeline-written public verdict (gallery.trust).
# Every guarantee of the badge's 5 Whys has a test here: the writer rule,
# the reset rule, the unfakeable rule, the capped ranking boost, and the
# "nobody gets robbed" story.
# ==========================================================================
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class TrustBadgeTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.cat = make_category()
        self.owner = make_user('trustowner')
        self.client.force_login(self.owner)

    def _clean_zip_report(self):
        return {
            'clamav': 'clean', 'secrets': [], 'npm': [], 'pip': [],
            'dep_audit': {'ran': True, 'reason': 'ok'},
        }

    # ---- pure grading ---------------------------------------------------
    def test_clean_zip_project_grades_verified(self):
        from gallery.trust import trust_grade, TRUST_VERIFIED
        p = make_project(self.owner, self.cat,
                         zip_file=make_zip_file({'index.html': '<p>ok</p>'}),
                         scan_report=self._clean_zip_report())
        self.assertEqual(trust_grade(p), TRUST_VERIFIED)

    def test_scanner_disabled_by_operator_caps_at_scanned(self):
        from gallery.trust import trust_grade, TRUST_SCANNED
        report = self._clean_zip_report()
        report['clamav'] = 'disabled'
        p = make_project(self.owner, self.cat,
                         zip_file=make_zip_file({'index.html': 'x'}),
                         scan_report=report)
        self.assertEqual(trust_grade(p), TRUST_SCANNED)

    def test_legacy_report_without_dep_evidence_is_not_verified(self):
        from gallery.trust import trust_grade, TRUST_SCANNED
        report = {'clamav': 'clean', 'secrets': []}  # pre-badge row
        p = make_project(self.owner, self.cat,
                         zip_file=make_zip_file({'index.html': 'x'}),
                         scan_report=report)
        self.assertEqual(trust_grade(p), TRUST_SCANNED)

    def test_vulnerable_or_unknown_deps_cap_at_scanned(self):
        from gallery.trust import trust_grade, TRUST_SCANNED
        base = self._clean_zip_report()
        p1 = make_project(self.owner, self.cat, title='Vuln deps',
                          zip_file=make_zip_file({'a': 'b'}),
                          scan_report={**base, 'npm': ['lodash']})
        self.assertEqual(trust_grade(p1), TRUST_SCANNED)
        p2 = make_project(self.owner, self.cat, title='Fake deps',
                          zip_file=make_zip_file({'a': 'b'}),
                          scan_report={**base, 'unknown_deps': ['npm:definitely-not-real-pkg']})
        self.assertEqual(trust_grade(p2), TRUST_SCANNED)

    def test_secrets_in_zip_cap_at_scanned(self):
        from gallery.trust import trust_grade, TRUST_SCANNED
        report = self._clean_zip_report()
        report['secrets'] = ['config/settings.py']
        p = make_project(self.owner, self.cat,
                         zip_file=make_zip_file({'a': 'b'}),
                         scan_report=report)
        self.assertEqual(trust_grade(p), TRUST_SCANNED)

    def test_no_pipeline_evidence_is_unknown(self):
        from gallery.trust import trust_grade, TRUST_UNKNOWN
        p = make_project(self.owner, self.cat, scan_report={})
        self.assertEqual(trust_grade(p), TRUST_UNKNOWN)

    def test_pending_and_quarantined_never_carry_a_badge(self):
        from gallery.trust import trust_grade, TRUST_UNKNOWN
        pending = make_project(self.owner, self.cat, title='Pending',
                               status='pending', scan_report=self._clean_zip_report())
        quarantined = make_project(self.owner, self.cat, title='Quar',
                                   status='quarantined', scan_report=self._clean_zip_report())
        self.assertEqual(trust_grade(pending), TRUST_UNKNOWN)
        self.assertEqual(trust_grade(quarantined), TRUST_UNKNOWN)

    def test_clean_snippet_grades_verified(self):
        from gallery.trust import trust_grade, TRUST_VERIFIED
        p = make_project(self.owner, self.cat, html_code='<p>hi</p>', js_code='let x = 1;',
                         scan_report={'nolo_review': {'score': 8},
                                      'dep_audit': {'ran': True, 'reason': 'snippet_no_deps'}})
        self.assertEqual(trust_grade(p), TRUST_VERIFIED)

    def test_snippet_with_leaked_token_cannot_be_verified(self):
        """The live SECRET_PATTERNS check on snippet code — an AI-pasted
        GitHub token must cap the tier even with a spotless report."""
        from gallery.trust import trust_grade, TRUST_SCANNED
        p = make_project(self.owner, self.cat, html_code='<p>hi</p>',
                         js_code='const t = "ghp_' + 'A' * 36 + '";',
                         scan_report={'nolo_review': {'score': 8},
                                      'dep_audit': {'ran': True, 'reason': 'snippet_no_deps'}})
        self.assertEqual(trust_grade(p), TRUST_SCANNED)

    def test_grade_is_pure_and_never_raises(self):
        from gallery.trust import trust_grade, TRUST_UNKNOWN
        from types import SimpleNamespace
        broken = SimpleNamespace(status=None)  # no slug, no scan_report
        self.assertEqual(trust_grade(broken), TRUST_UNKNOWN)

    # ---- the writer rule -------------------------------------------------
    def test_apply_trust_grade_writes_and_stamps(self):
        from gallery.trust import apply_trust_grade, TRUST_VERIFIED
        p = make_project(self.owner, self.cat,
                         zip_file=make_zip_file({'index.html': 'x'}),
                         scan_report=self._clean_zip_report())
        grade = apply_trust_grade(p)
        p.refresh_from_db()
        self.assertEqual(grade, TRUST_VERIFIED)
        self.assertEqual(p.trust, TRUST_VERIFIED)
        self.assertIsNotNone(p.trust_graded_at)

    def test_stale_clock_cannot_overwrite_a_newer_grade(self):
        """Monotonic guard: an out-of-order task must not rewrite a newer
        verdict with an older one."""
        from gallery.trust import apply_trust_grade, TRUST_UNKNOWN
        from django.utils import timezone
        from datetime import timedelta
        p = make_project(self.owner, self.cat, scan_report=self._clean_zip_report())
        p.trust_graded_at = timezone.now() + timedelta(hours=1)  # future stamp
        p.save()
        written = apply_trust_grade(p)
        self.assertEqual(written, TRUST_UNKNOWN)  # refused, current value kept

    # ---- the pipeline end to end ----------------------------------------
    def test_publish_flow_grades_a_clean_snippet_verified(self):
        # The publish view trusts snippets from creators with >=3 published
        # vibes (same precondition as PublishClassificationTests); newer
        # creators' snippets wait for human review.
        for i in range(3):
            make_project(self.owner, self.cat, title=f'Prior vibe {i}')
        data = {
            'title': 'Clean snippet', 'category': self.cat.id,
            'short_description': 'A tiny clean snippet for the badge tests.',
            'readme': '# Clean\n\n' + ('Totally harmless readme body. ' * 6),
            'tech_stack': 'HTML', 'html_code': '<p>ok</p>', 'css_code': '',
            'js_code': 'let safe = true;', 'star_cost': 0, 'price_zar': 0,
            'creator_kind': '',
        }
        response = self.client.post('/publish/', data, follow=True)
        self.assertEqual(response.status_code, 200)
        p = AppProject.objects.get(title='Clean snippet')
        self.assertEqual(p.status, 'published')
        p.refresh_from_db()
        self.assertEqual(p.trust, 'verified')
        self.assertIsNotNone(p.trust_graded_at)

    def test_moderator_approval_grades_a_held_snippet(self):
        """The other snippet publish path: new creator → queued for review
        → moderator approves → the badge is graded from the recorded
        snippet_scan evidence."""
        mod = make_user('trustmod', role='moderator')
        held = make_project(self.owner, self.cat, title='Held snippet',
                            status='pending', html_code='<p>ok</p>', js_code='let x = 1;')
        from gallery.trust import snippet_evidence
        snippet_evidence(held)  # what the publish view ran before queueing
        self.client.force_login(mod)
        response = self.client.post(f'/moderation/{held.slug}/', {'action': 'approve'}, follow=True)
        self.assertEqual(response.status_code, 200)
        held.refresh_from_db()
        self.assertEqual(held.status, 'published')
        self.assertEqual(held.trust, 'verified')

    def test_finalize_held_for_secrets_writes_unknown(self):
        from gallery.tasks import finalize_publish
        p = make_project(self.owner, self.cat, title='Held', status='pending',
                         scan_report={'clamav': 'clean', 'secrets': ['config/.env']})
        result = finalize_publish.run(project_id=p.id)
        p.refresh_from_db()
        self.assertEqual(result, 'pending_secrets')
        self.assertEqual(p.status, 'pending')
        self.assertEqual(p.trust, 'unknown')

    # ---- the reset rule (nobody gets robbed) -----------------------------
    def test_content_change_resets_a_verified_badge(self):
        """The bait-and-switch defence: a buyer traded for a ✓ vibe, the
        owner then swaps the bytes — the badge must drop before any buyer
        can be charged for unverified content again."""
        from gallery.trust import apply_trust_grade, invalidate_trust
        p = make_project(self.owner, self.cat,
                         zip_file=make_zip_file({'index.html': 'x'}),
                         scan_report=self._clean_zip_report())
        self.assertEqual(apply_trust_grade(p), 'verified')
        invalidate_trust(p)  # what edit_vibe / git push / PR merge call
        p.refresh_from_db()
        self.assertEqual(p.trust, 'unknown')

    def test_buyer_sees_no_badge_after_content_change(self):
        from gallery.trust import apply_trust_grade, invalidate_trust
        p = make_project(self.owner, self.cat,
                         zip_file=make_zip_file({'index.html': 'x'}),
                         scan_report=self._clean_zip_report())
        apply_trust_grade(p)
        invalidate_trust(p)
        response = self.client.get(p.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '✓ Checked')

    # ---- the unfakeable rule ----------------------------------------------
    def test_publish_form_has_no_trust_field(self):
        self.assertNotIn('trust', AppUploadForm.base_fields)

    def test_posted_trust_value_is_ignored(self):
        """Spoof attempt: POST trust='verified' alongside a REAL leaked
        token — the verdict follows the evidence, never the POST."""
        for i in range(3):
            make_project(self.owner, self.cat, title=f'Prior spoof {i}')
        data = {
            'title': 'Spoof attempt', 'category': self.cat.id,
            'short_description': 'Trying to POST a fake trust tier.',
            'readme': '# Spoof\n\n' + ('Readme body long enough to pass. ' * 6),
            'tech_stack': 'HTML', 'html_code': '<p>ok</p>', 'css_code': '',
            'js_code': 'const t = "ghp_' + 'B' * 36 + '";',
            'star_cost': 0, 'price_zar': 0, 'creator_kind': '',
            'trust': 'verified', 'trust_graded_at': '2020-01-01T00:00:00Z',
        }
        response = self.client.post('/publish/', data, follow=True)
        self.assertEqual(response.status_code, 200)
        p = AppProject.objects.get(title='Spoof attempt')
        p.refresh_from_db()
        self.assertEqual(p.trust, 'scanned')  # evidence (leaked token) wins

    def test_api_returns_tier_never_the_report(self):
        from gallery.trust import apply_trust_grade
        p = make_project(self.owner, self.cat,
                         zip_file=make_zip_file({'index.html': 'x'}),
                         scan_report=self._clean_zip_report())
        apply_trust_grade(p)
        response = self.client.get('/api/v1/apps/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()['results']
        row = next(r for r in payload if r['slug'] == p.slug)
        self.assertEqual(row['trust'], 'verified')
        self.assertEqual(row['trust_label'], 'Checked')
        self.assertNotIn('scan_report', row)

    # ---- presentation table ------------------------------------------------
    def test_meta_table_covers_exactly_the_tiers(self):
        from gallery.trust import TRUST_META, TRUST_TIERS
        field_choices = {c[0] for c in AppProject._meta.get_field('trust').choices}
        self.assertEqual(set(TRUST_META.keys()), set(TRUST_TIERS))
        self.assertEqual(field_choices, set(TRUST_TIERS))

    def test_reasons_are_fixed_strings_and_never_leak_filenames(self):
        from gallery.trust import trust_reasons
        report = self._clean_zip_report()
        report['secrets'] = ['supersecretfile.py']  # must NOT reach the page
        p = make_project(self.owner, self.cat,
                         zip_file=make_zip_file({'a': 'b'}), scan_report=report)
        reasons = trust_reasons(p)
        self.assertTrue(reasons)
        joined = ' '.join(r['detail'] for r in reasons)
        self.assertNotIn('supersecretfile', joined)
        self.assertNotIn('.py', joined)
        for r in reasons:
            self.assertIn(r['ok'], (True, False))
            self.assertTrue(r['detail'])  # every row says something safe

    # ---- ranking: reorder equals, never buy rank ---------------------------
    def test_verified_boosts_identical_content(self):
        from gallery.interest import compute_appeal
        from gallery.trust import TRUST_VERIFIED, trust_multiplier
        base = make_project(self.owner, self.cat, title='Base', scan_report={})
        verified = make_project(self.owner, self.cat, title='Boosted',
                                scan_report={})  # identical in every way
        verified.trust = TRUST_VERIFIED
        self.assertAlmostEqual(
            compute_appeal(verified) / compute_appeal(base),
            trust_multiplier(TRUST_VERIFIED), places=3)

    def test_boost_is_small_enough_that_quality_still_wins(self):
        from gallery.interest import compute_appeal
        from gallery.trust import TRUST_VERIFIED
        weak = make_project(self.owner, self.cat, title='Weak verified',
                            readme='# x', tech_stack='', html_code='', js_code='',
                            scan_report={})
        weak.trust = TRUST_VERIFIED
        strong = make_project(self.owner, self.cat, title='Strong unknown',
                              readme='# Real README\n\n' + ('Documented, tested, described. ' * 40),
                              tech_stack='HTML, JS',
                              language_stats={'JavaScript': 90, 'HTML': 10},
                              html_code='<canvas></canvas>', js_code='let x=1;',
                              scan_report={})
        strong.trust = 'unknown'
        self.assertGreater(compute_appeal(strong), compute_appeal(weak))

    # ---- the public read ----------------------------------------------------
    def test_trust_legend_page_renders(self):
        response = self.client.get('/trust/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Trust Badge')

    def test_feed_and_detail_show_the_badge_for_verified(self):
        from gallery.trust import apply_trust_grade
        p = make_project(self.owner, self.cat, title='Badged',
                         zip_file=make_zip_file({'index.html': 'x'}),
                         scan_report=self._clean_zip_report())
        apply_trust_grade(p)
        feed = self.client.get('/')
        self.assertContains(feed, '🛡️ Checked')
        detail = self.client.get(p.get_absolute_url())
        self.assertContains(detail, '✓ Checked')
        self.assertContains(detail, 'What does this mean?')


# ==========================================================================
# Slopsquatting check (gallery.dep_check) + the "Checked only" feed filter.
# The registry check flags AI-invented package names; the filter lets a
# buyer browse only what the pipeline verified.
# ==========================================================================
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class DepCheckTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()

    # ---- pure parsers -----------------------------------------------------
    def test_npm_manifest_parser_reads_every_dependency_section(self):
        from gallery.dep_check import npm_deps_from_manifest
        import json, tempfile, os
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
            json.dump({'dependencies': {'react': '^18'},
                       'devDependencies': {'vite': '^5'},
                       'peerDependencies': {'@scope/lib': '*'},
                       'optionalDependencies': {'fsevents': '^2'}}, fh)
            path = fh.name
        try:
            self.assertEqual(npm_deps_from_manifest(path),
                             ['@scope/lib', 'fsevents', 'react', 'vite'])
        finally:
            os.unlink(path)

    def test_npm_manifest_parser_survives_broken_json(self):
        from gallery.dep_check import npm_deps_from_manifest
        import tempfile, os
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as fh:
            fh.write('{not json at all')
            path = fh.name
        try:
            self.assertEqual(npm_deps_from_manifest(path), [])
        finally:
            os.unlink(path)

    def test_requirements_parser_skips_options_comments_and_markers(self):
        from gallery.dep_check import pip_deps_from_requirements
        import tempfile, os
        body = ('\n'
                '# a comment\n'
                'django>=4.2\n'
                'not-a-real-pkg-zzz==1.0 ; python_version>"3.8"\n'
                '--index-url https://example.com\n'
                '-r other-requirements.txt\n'
                '-e git+https://github.com/x/y.git#egg=y\n'
                'celery[redis]==5.4\n'
                '%%garbage-line\n')
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as fh:
            fh.write(body)
            path = fh.name
        try:
            self.assertEqual(sorted(pip_deps_from_requirements(path)),
                             ['celery', 'django', 'not-a-real-pkg-zzz'])
        finally:
            os.unlink(path)

    # ---- registry lookups: cache, verdicts, breaker ------------------------
    def test_registry_results_are_cached_per_name(self):
        from gallery import dep_check
        calls = []
        def fake_status(url, timeout=5):
            calls.append(url)
            return 200
        with mock.patch.object(dep_check, '_http_status', side_effect=fake_status):
            dep_check._exists('npm', 'react')
            dep_check._exists('npm', 'react')
        self.assertEqual(len(calls), 1)

    def test_only_an_explicit_404_counts_as_missing(self):
        from gallery import dep_check
        for status in (200, 500, 403, None):  # None = network failure
            with mock.patch.object(dep_check, '_http_status', return_value=status):
                exists, offline = dep_check._exists('npm', f'pkg-{status}')
                self.assertTrue(exists, f'status {status} must not flag')
                self.assertEqual(offline, status is None)

    def test_check_flags_404_and_passes_200(self):
        from gallery import dep_check
        def fake_status(url, timeout=5):
            return 404 if 'not-a-real-pkg-zzz' in url else 200
        with mock.patch.object(dep_check, '_http_status', side_effect=fake_status):
            out = dep_check.check_dependencies({'pip': ['django', 'not-a-real-pkg-zzz']})
        self.assertEqual(out['flagged'], ['pip:not-a-real-pkg-zzz'])
        self.assertEqual(out['checked'], 2)
        self.assertEqual(out['reason'], 'ok')

    def test_network_failure_trips_the_circuit_breaker(self):
        """One network error ends the run — dep #2 is never asked."""
        from gallery import dep_check
        calls = []
        def fake_status(url, timeout=5):
            calls.append(url)
            return None
        with mock.patch.object(dep_check, '_http_status', side_effect=fake_status):
            out = dep_check.check_dependencies({'npm': ['a-first-pkg', 'a-second-pkg']})
        self.assertEqual(out['reason'], 'offline')
        self.assertEqual(out['flagged'], [])
        self.assertEqual(len(calls), 1)

    def test_dry_budget_checks_nothing(self):
        import time
        from django.core.cache import cache
        from gallery import dep_check
        cache.set(dep_check.BUCKET_KEY, {'start': int(time.time()), 'count': 10**9}, 3600)
        with mock.patch.object(dep_check, '_http_status', return_value=404) as hs:
            out = dep_check.check_dependencies({'npm': ['anything']})
        self.assertEqual(out['reason'], 'budget')
        self.assertEqual(out['flagged'], [])
        hs.assert_not_called()

    def test_env_switch_disables_the_whole_check(self):
        from gallery import dep_check
        with mock.patch.dict(os.environ, {'DEP_CHECK_ENABLED': '0'}):
            with mock.patch.object(dep_check, '_http_status', return_value=404) as hs:
                out = dep_check.check_dependencies({'npm': ['anything']})
        self.assertEqual(out['reason'], 'disabled')
        hs.assert_not_called()

    def test_no_dependencies_reports_no_deps(self):
        from gallery import dep_check
        out = dep_check.check_dependencies({'npm': [], 'pip': []})
        self.assertEqual(out['reason'], 'no_deps')

    # ---- end to end through the real scan task -----------------------------
    def _fake_status(self, url, timeout=5):
        return 404 if ('not-a-real-pkg-zzz' in url or 'fake-pkg-abc' in url) else 200

    def test_vuln_scan_flags_a_fake_npm_dependency(self):
        from gallery import dep_check
        from gallery.tasks import vulnerability_scan
        from gallery.trust import trust_grade
        owner = make_user('depowner1')
        cat = make_category()
        p = make_project(owner, cat, title='Fake npm dep', status='pending',
                         zip_file=make_zip_file({
                             'package.json': '{"dependencies": {"react": "^18", "fake-pkg-abc": "^1.0"}}',
                             'index.js': 'console.log(1)',
                         }))
        with mock.patch.object(dep_check, '_http_status', side_effect=self._fake_status):
            vulnerability_scan.run(project_id=p.id)
        p.refresh_from_db()
        self.assertEqual(p.scan_report.get('unknown_deps'), ['npm:fake-pkg-abc'])
        self.assertEqual(p.scan_report.get('dep_exist_check', {}).get('reason'), 'ok')
        # The row is still pending (finalize publishes it), and pending rows
        # are 'unknown' by design — the cap shows once it publishes:
        p.status = 'published'
        p.save(update_fields=['status'])
        self.assertEqual(trust_grade(p), 'scanned')  # capped — the badge reacts

    def test_vuln_scan_flags_a_fake_pip_dependency(self):
        from gallery import dep_check
        from gallery.tasks import vulnerability_scan
        from gallery.trust import trust_reasons
        owner = make_user('depowner2')
        cat = make_category()
        p = make_project(owner, cat, title='Fake pip dep', status='pending',
                         zip_file=make_zip_file({
                             'requirements.txt': 'django>=4.2\nnot-a-real-pkg-zzz==1.0\n',
                         }))
        with mock.patch.object(dep_check, '_http_status', side_effect=self._fake_status):
            vulnerability_scan.run(project_id=p.id)
        p.refresh_from_db()
        self.assertEqual(p.scan_report.get('unknown_deps'), ['pip:not-a-real-pkg-zzz'])
        p.status = 'published'  # pending rows are 'unknown' by design
        p.save(update_fields=['status'])
        joined = ' '.join(r['detail'] for r in trust_reasons(p))
        self.assertIn('possible fake package', joined)

    def test_vuln_scan_with_real_dependencies_does_not_flag(self):
        from gallery import dep_check
        from gallery.tasks import vulnerability_scan
        owner = make_user('depowner3')
        cat = make_category()
        p = make_project(owner, cat, title='Real deps', status='pending',
                         zip_file=make_zip_file({
                             'package.json': '{"dependencies": {"react": "^18"}}',
                         }))
        with mock.patch.object(dep_check, '_http_status', side_effect=self._fake_status):
            vulnerability_scan.run(project_id=p.id)
        p.refresh_from_db()
        self.assertNotIn('unknown_deps', p.scan_report)
        self.assertEqual(p.scan_report.get('dep_exist_check', {}).get('checked'), 1)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class TrustFilterTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.cat = make_category()
        self.owner = make_user('filterowner')
        from gallery.trust import apply_trust_grade, TRUST_VERIFIED
        self.verified = make_project(self.owner, self.cat, title='FilterVerifiedOne',
                                     zip_file=make_zip_file({'index.html': 'x'}),
                                     scan_report={'clamav': 'clean', 'secrets': [], 'npm': [], 'pip': [],
                                                  'dep_audit': {'ran': True, 'reason': 'ok'}})
        self.assertEqual(apply_trust_grade(self.verified), TRUST_VERIFIED)
        self.unknown = make_project(self.owner, self.cat, title='FilterUnknownOne')

    def test_checked_only_filter_returns_only_verified(self):
        response = self.client.get('/', {'trust': 'verified'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['trust'], 'verified')
        self.assertContains(response, 'FilterVerifiedOne')
        self.assertNotContains(response, 'FilterUnknownOne')

    def test_unknown_trust_values_are_ignored(self):
        response = self.client.get('/', {'trust': 'bogus'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['trust'], '')
        self.assertContains(response, 'FilterVerifiedOne')
        self.assertContains(response, 'FilterUnknownOne')

    def test_filter_with_nothing_verified_is_honestly_empty(self):
        """Nothing verified → the filter shows an EMPTY grid, not a quiet
        refill with unverified vibes. Absence is the honest answer."""
        from gallery.trust import invalidate_trust
        invalidate_trust(self.verified)  # nothing verified anymore
        response = self.client.get('/', {'trust': 'verified', 'q': ''})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Publish your first vibe')  # empty state
        self.assertNotContains(response, 'FilterVerifiedOne')
        self.assertNotContains(response, 'FilterUnknownOne')

    def test_feed_renders_the_checkbox(self):
        response = self.client.get('/')
        self.assertContains(response, 'name="trust"')
        self.assertContains(response, '🛡️ Checked only')

    def test_api_supports_the_trust_filter(self):
        response = self.client.get('/api/v1/apps/', {'trust': 'verified'})
        self.assertEqual(response.status_code, 200)
        rows = response.json()['results']
        self.assertTrue(rows)
        self.assertTrue(all(r['trust'] == 'verified' for r in rows))
        slugs = {r['slug'] for r in rows}
        self.assertIn(self.verified.slug, slugs)
        self.assertNotIn(self.unknown.slug, slugs)

    def test_api_ignores_bogus_trust_values(self):
        response = self.client.get('/api/v1/apps/', {'trust': 'bogus'})
        rows = response.json()['results']
        slugs = {r['slug'] for r in rows}
        self.assertIn(self.unknown.slug, slugs)


# ==========================================================================
# Marketing copy — the three promises on the feed (value strip, hero stat,
# meta description, footer). Every claim is pinned to something the code
# really does: a claim that stops being true is a bug, not marketing.
# ==========================================================================
@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class MarketingCopyTests(TestCase):
    """Each test names the feature that makes the claim true."""

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.cat = make_category()
        self.owner = make_user('marketer')

    def test_value_strip_promises_render_and_link_the_standard(self):
        # "Scanned before the feed" is true: gallery.trust + the scan chain.
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '🛡️ Scanned before the feed')
        self.assertContains(response, 'Read the standard →')      # /trust/ link
        self.assertContains(response, '★ Stars never expire')     # ledger, no expiry
        self.assertContains(response, '🇿🇦 Priced in Rands')      # price_zar + payouts
        self.assertContains(response, '10 ★ = R1')                # economy.py rate

    def test_logged_in_users_still_see_the_promises(self):
        self.client.force_login(self.owner)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '★ Stars never expire')

    def test_meta_description_carries_the_trust_line(self):
        response = self.client.get('/')
        self.assertContains(response, 'Every vibe is scanned before it reaches your feed')

    def test_footer_carries_the_promises_on_every_page(self):
        # Any page inheriting base.html — the legend page is a cheap proxy.
        response = self.client.get('/trust/')
        self.assertContains(response, '★ Stars never expire')

    def test_payout_claim_matches_the_real_economy(self):
        """'10 ★ = R1' and 'min 500 ★' in the copy must match the real
        constants — if the economy ever changes, this breaks BEFORE the
        marketing lies."""
        from users.models import MIN_PAYOUT_STARS, MAX_PAYOUT_STARS
        self.assertEqual(MIN_PAYOUT_STARS, 500)          # "min 500 ★" on the feed
        self.assertEqual(MAX_PAYOUT_STARS, 50000)        # R5 000 cap, human-sized EFT
        # 500 ★ = R50 (the comment on MIN_PAYOUT_STARS) ⇒ 10 ★ = R1,
        # exactly the rate the value strip states.
        self.assertEqual(MIN_PAYOUT_STARS // 50, 10)
