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
from gallery.models import AppProject, Category, Sale, Star, Trade, VibeBattle
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

    def test_star_awards_owner_one_star(self):
        self.client.login(username='buyer', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/star/')
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 6)
        self.client.post(f'/app/{self.project.slug}/star/')
        self.owner.profile.refresh_from_db()
        self.assertEqual(self.owner.profile.stars_balance, 5)


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
        self.cat = make_category()
        self.owner = make_user('owner')
        self.buyer = make_user('buyer')
        self.project = make_project(self.owner, self.cat, title='Card vibe', star_cost=0, price_zar=50)
        self.project.zip_file.save('card.zip', make_zip_file({'app.py': 'print(1)\\n'}), save=True)

    def _signed(self, payload: bytes):
        import hashlib, hmac
        return hmac.new(b'sk_test_webhook', payload, hashlib.sha512).hexdigest()

    def test_bad_signature_does_not_create_sale(self):
        import json
        body = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': f'blaq-{self.project.id}-{self.buyer.id}-abc',
                'amount': 5000,
            },
        }).encode()
        response = self.client.post(
            '/paystack/webhook/',
            data=body,
            content_type='application/json',
            headers={'x-paystack-signature': 'nope'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(buyer=self.buyer, project=self.project).exists())

    def test_valid_signature_creates_sale_and_unlocks(self):
        import json
        from gallery.access import user_can_download
        body = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': f'blaq-{self.project.id}-{self.buyer.id}-okref',
                'amount': 5000,
            },
        }).encode()
        response = self.client.post(
            '/paystack/webhook/',
            data=body,
            content_type='application/json',
            headers={'x-paystack-signature': self._signed(body)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Sale.objects.filter(buyer=self.buyer, project=self.project, amount_zar=50).exists())
        self.assertTrue(user_can_download(self.buyer, self.project))

    def test_amount_mismatch_rejected(self):
        import json
        body = json.dumps({
            'event': 'charge.success',
            'data': {
                'reference': f'blaq-{self.project.id}-{self.buyer.id}-low',
                'amount': 100,
            },
        }).encode()
        response = self.client.post(
            '/paystack/webhook/',
            data=body,
            content_type='application/json',
            headers={'x-paystack-signature': self._signed(body)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Sale.objects.filter(buyer=self.buyer, project=self.project).exists())


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
