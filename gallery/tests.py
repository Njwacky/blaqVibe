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
