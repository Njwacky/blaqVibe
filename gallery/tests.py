import io
import zipfile
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from gallery.access import user_can_download
from gallery.forms import AppUploadForm
from gallery.models import AppProject, Category, PaymentIntent, Sale, Star, Trade, VibeBattle
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

    def test_detail_uses_preview_files_label(self):
        response = self.client.get(f'/app/{self.zip_project.slug}/')
        self.assertContains(response, 'Preview files')
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
            'checklist', 'sources',
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
