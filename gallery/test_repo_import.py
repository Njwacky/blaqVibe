"""GitHub import — the new-user demo path.

Every network call is patched. The one place real bytes are used is
RealGitHubArchiveTests, which runs the archive GitHub actually serves for this
repository through the real validator: that archive contains scripts/ci.sh, and
`.sh` is blocked, so it is the case that proves the normalizer is doing
something and not just passing bytes through.
"""
import io
import zipfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from gallery import repo_import
from gallery.repo_import import (
    DEMO_REPO_URL,
    RepoImportError,
    build_import,
    codeload_url,
    normalize_github_zip,
    parse_github_url,
    readme_for,
    suggest_category,
)
from gallery.validators import validate_zip


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def uploaded(data, name='app.zip'):
    return SimpleUploadedFile(name, data, content_type='application/zip')


def fake_response(body, status_code=200, headers=None):
    response = mock.Mock()
    response.status_code = status_code
    response.headers = headers or {}
    response.iter_content = mock.Mock(return_value=iter([body]))
    return response


def make_user(username='newbie'):
    return User.objects.create_user(
        username=username, password='pass12345', email=f'{username}@test.com',
    )


# A tree shaped like GitHub's: one wrapper folder, a blocked executable, a
# blocked build folder, a .env that must go and a .env.example that must stay.
GITHUB_SHAPED = {
    'myrepo-main/index.html': '<h1>hi</h1>',
    'myrepo-main/app.py': 'print("hi")\n',
    'myrepo-main/scripts/run.sh': '#!/bin/sh\n',
    'myrepo-main/node_modules/left-pad/index.js': 'module.exports = 1\n',
    'myrepo-main/.env': 'SECRET=hunter2\n',
    'myrepo-main/.env.example': 'SECRET=\n',
}


class ParseGithubUrlTests(TestCase):
    """Whatever a person pastes, only GitHub survives — and only as parts."""

    def test_accepts_the_shapes_people_actually_paste(self):
        cases = {
            'https://github.com/Njwacky/blaqVibe': ('Njwacky', 'blaqVibe', 'HEAD'),
            'https://github.com/Njwacky/blaqVibe/': ('Njwacky', 'blaqVibe', 'HEAD'),
            'http://github.com/Njwacky/blaqVibe': ('Njwacky', 'blaqVibe', 'HEAD'),
            'github.com/Njwacky/blaqVibe': ('Njwacky', 'blaqVibe', 'HEAD'),
            'Njwacky/blaqVibe': ('Njwacky', 'blaqVibe', 'HEAD'),
            'https://github.com/Njwacky/blaqVibe.git': ('Njwacky', 'blaqVibe', 'HEAD'),
            'https://www.github.com/Njwacky/blaqVibe': ('Njwacky', 'blaqVibe', 'HEAD'),
            'https://github.com/a/b/tree/main': ('a', 'b', 'main'),
            'https://github.com/a/b/tree/feature/x': ('a', 'b', 'feature/x'),
            'https://github.com/a/b/blob/main/src/app.py': ('a', 'b', 'main'),
            'https://github.com/a/b/archive/refs/heads/main.zip': ('a', 'b', 'main'),
            'https://codeload.github.com/a/b/zip/refs/heads/main': ('a', 'b', 'main'),
            'https://codeload.github.com/a/b/zip/main': ('a', 'b', 'main'),
            '  https://github.com/a/b  ': ('a', 'b', 'HEAD'),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_github_url(raw), expected)

    def test_non_github_hosts_are_refused(self):
        """The SSRF guard: the host is checked, and the URL is rebuilt anyway."""
        for raw in (
            'https://gitlab.com/a/b',
            'https://evil.com/a/b',
            'http://169.254.169.254/latest/meta-data',
            'http://localhost:8000/a/b',
            'https://github.com.evil.com/a/b',
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(RepoImportError):
                    parse_github_url(raw)

    def test_malformed_links_are_refused_with_a_usable_message(self):
        for raw in ('', '   ', 'github.com/onlyowner', 'https://github.com/', 'not a link'):
            with self.subTest(raw=raw):
                with self.assertRaises(RepoImportError):
                    parse_github_url(raw)

    def test_traversal_is_refused(self):
        for raw in ('https://github.com/a/b/tree/../../etc', 'https://github.com/a/b/tree/a/../b'):
            with self.subTest(raw=raw):
                with self.assertRaises(RepoImportError):
                    parse_github_url(raw)

    def test_bad_characters_are_refused(self):
        # A query string or fragment is NOT a bad character: urlsplit drops it,
        # so `github.com/a/b?x=1` correctly means repo a/b. Only characters that
        # cannot appear in a GitHub slug are refused.
        for raw in ('https://github.com/a b/c', 'https://github.com/a/b!', 'https://github.com/é/é'):
            with self.subTest(raw=raw):
                with self.assertRaises(RepoImportError):
                    parse_github_url(raw)

    def test_a_query_string_or_fragment_is_ignored_not_refused(self):
        self.assertEqual(parse_github_url('https://github.com/a/b?x=1'), ('a', 'b', 'HEAD'))
        self.assertEqual(parse_github_url('https://github.com/a/b#c'), ('a', 'b', 'HEAD'))

    def test_codeload_url_is_always_the_codeload_host(self):
        """Built from parts, never derived from the input string."""
        url = codeload_url('Njwacky', 'blaqVibe', 'master')
        self.assertEqual(url, 'https://codeload.github.com/Njwacky/blaqVibe/zip/master')
        # Even a hostile repo name cannot move the host.
        owner, repo, ref = parse_github_url('https://github.com/a/b')
        self.assertTrue(codeload_url(owner, repo, ref).startswith('https://codeload.github.com/'))


class FetchArchiveTests(TestCase):
    def test_happy_path_returns_the_bytes(self):
        payload = zip_bytes({'a.txt': 'a'})
        with mock.patch('gallery.repo_import.requests.get',
                        return_value=fake_response(payload)) as get:
            self.assertEqual(repo_import.fetch_archive('https://codeload.github.com/a/b/zip/HEAD'), payload)
        # stream + a timeout + no redirects: a hung GitHub must not hang a worker.
        _, kwargs = get.call_args
        self.assertTrue(kwargs['stream'])
        self.assertFalse(kwargs['allow_redirects'])
        self.assertEqual(kwargs['timeout'], (10, 25))

    def test_404_says_the_repo_is_not_there(self):
        with mock.patch('gallery.repo_import.requests.get',
                        return_value=fake_response(b'', status_code=404)):
            with self.assertRaises(RepoImportError) as ctx:
                repo_import.fetch_archive('https://codeload.github.com/a/b/zip/HEAD')
        self.assertIn('no archive', str(ctx.exception))

    def test_other_status_is_reported_not_raised(self):
        with mock.patch('gallery.repo_import.requests.get',
                        return_value=fake_response(b'', status_code=500)):
            with self.assertRaises(RepoImportError) as ctx:
                repo_import.fetch_archive('https://codeload.github.com/a/b/zip/HEAD')
        self.assertIn('500', str(ctx.exception))

    def test_network_failure_becomes_a_plain_message(self):
        import requests
        with mock.patch('gallery.repo_import.requests.get',
                        side_effect=requests.ConnectionError('boom')):
            with self.assertRaises(RepoImportError) as ctx:
                repo_import.fetch_archive('https://codeload.github.com/a/b/zip/HEAD')
        self.assertIn('GitHub did not answer', str(ctx.exception))

    def test_oversized_content_length_is_refused_before_downloading(self):
        headers = {'Content-Length': str(repo_import.MAX_DOWNLOAD_BYTES + 1)}
        with mock.patch('gallery.repo_import.requests.get',
                        return_value=fake_response(b'', headers=headers)):
            with self.assertRaises(RepoImportError) as ctx:
                repo_import.fetch_archive('https://codeload.github.com/a/b/zip/HEAD')
        self.assertIn('import cap', str(ctx.exception))

    def test_a_liar_about_content_length_is_still_capped_while_streaming(self):
        """No Content-Length, or a false one, must not make the cap optional."""
        chunk = b'x' * (1024 * 1024)
        response = mock.Mock()
        response.status_code = 200
        response.headers = {}
        response.iter_content = mock.Mock(return_value=iter([chunk] * 200))
        with mock.patch('gallery.repo_import.requests.get', return_value=response):
            with self.assertRaises(RepoImportError) as ctx:
                repo_import.fetch_archive('https://codeload.github.com/a/b/zip/HEAD')
        self.assertIn('larger than', str(ctx.exception))


class NormalizeGithubZipTests(TestCase):
    def test_wrapper_folder_is_stripped(self):
        result = normalize_github_zip(zip_bytes({
            'myrepo-main/index.html': '<h1>hi</h1>',
            'myrepo-main/app.py': 'print(1)\n',
        }))
        self.assertEqual(result['wrapper'], 'myrepo-main')
        self.assertEqual(sorted(result['files']), ['app.py', 'index.html'])

    def test_a_lone_top_level_directory_that_is_not_a_wrapper_is_kept(self):
        """Only strip when EVERY entry sits under the one folder.

        A repo whose real content is `src/...` plus a root README has two
        roots, so nothing is stripped and the tree is not mangled.
        """
        result = normalize_github_zip(zip_bytes({
            'repo-main/README.md': '# x\n',
            'repo-main/src/app.py': 'print(1)\n',
        }))
        self.assertEqual(result['wrapper'], 'repo-main')
        self.assertIn('src/app.py', result['files'])

    def test_blocked_paths_are_dropped_and_reported(self):
        result = normalize_github_zip(zip_bytes(GITHUB_SHAPED))
        dropped = {item['path'] for item in result['dropped']}
        self.assertEqual(dropped, {'scripts/run.sh', 'node_modules/left-pad/index.js', '.env'})
        self.assertEqual(sorted(result['files']), ['.env.example', 'app.py', 'index.html'])
        self.assertEqual(result['file_count'], 3)

    def test_env_example_survives_but_env_does_not(self):
        result = normalize_github_zip(zip_bytes(GITHUB_SHAPED))
        self.assertIn('.env.example', result['files'])
        self.assertNotIn('.env', result['files'])

    def test_every_drop_carries_a_reason_the_user_can_act_on(self):
        result = normalize_github_zip(zip_bytes(GITHUB_SHAPED))
        for item in result['dropped']:
            with self.subTest(path=item['path']):
                self.assertTrue(item['reason'])

    def test_output_passes_the_real_upload_validator(self):
        """The whole point: what comes out is uploadable."""
        result = normalize_github_zip(zip_bytes(GITHUB_SHAPED))
        validate_zip(uploaded(result['bytes'], 'repo.zip'))  # must not raise

    def test_the_raw_github_shape_would_have_been_refused(self):
        """Control for the test above — the normalizer is load-bearing."""
        with self.assertRaises(ValidationError):
            validate_zip(uploaded(zip_bytes(GITHUB_SHAPED), 'repo.zip'))

    def test_directory_entries_are_skipped_not_published_as_files(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('repo-main/', '')
            zf.writestr('repo-main/index.html', '<h1>hi</h1>')
        result = normalize_github_zip(buf.getvalue())
        self.assertEqual(result['files'], ['index.html'])

    def test_symlinks_are_dropped(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            info = zipfile.ZipInfo('repo-main/link')
            info.external_attr = 0o120777 << 16  # symlink
            zf.writestr(info, '/etc/passwd')
            zf.writestr('repo-main/index.html', '<h1>hi</h1>')
        result = normalize_github_zip(buf.getvalue())
        self.assertEqual(result['files'], ['index.html'])
        self.assertEqual(result['dropped'][0]['path'], 'link')

    def test_an_archive_of_nothing_but_blocked_paths_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            normalize_github_zip(zip_bytes({'repo-main/run.sh': '#!/bin/sh\n'}))
        self.assertIn('Nothing left', ' '.join(ctx.exception.messages))

    def test_corrupt_bytes_are_refused(self):
        with self.assertRaises(ValidationError):
            normalize_github_zip(b'this is not a zip')

    def test_no_entries_leaves_nothing_to_publish(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w'):
            pass
        with self.assertRaises(ValidationError):
            normalize_github_zip(buf.getvalue())


class ReadmeTests(TestCase):
    def test_the_repositorys_own_readme_is_used_when_it_qualifies(self):
        body = '# Real Repo\n\n' + ('x' * 200)
        data = zip_bytes({'README.md': body, 'index.html': '<h1>hi</h1>'})
        result = normalize_github_zip(data)
        self.assertEqual(readme_for(result['files'], result['bytes'], 'a/b', 'https://github.com/a/b'), body)

    def test_a_thin_readme_falls_back_to_an_honest_stub(self):
        data = zip_bytes({'README.md': '# tiny\n', 'index.html': '<h1>hi</h1>'})
        result = normalize_github_zip(data)
        text = readme_for(result['files'], result['bytes'], 'a/b', 'https://github.com/a/b')
        self.assertIn('# a/b', text)
        self.assertIn('https://github.com/a/b', text)
        # The form requires 100 chars and a heading; the stub must satisfy both.
        self.assertGreaterEqual(len(text.strip()), 100)
        self.assertIn('# ', text)

    def test_no_readme_at_all_still_produces_a_valid_one(self):
        data = zip_bytes({'index.html': '<h1>hi</h1>'})
        result = normalize_github_zip(data)
        text = readme_for(result['files'], result['bytes'], 'a/b', 'https://github.com/a/b')
        self.assertGreaterEqual(len(text.strip()), 100)
        self.assertIn('# ', text)


class BuildImportTests(TestCase):
    def test_builds_an_uploadable_file_and_keeps_the_provenance(self):
        payload = zip_bytes(GITHUB_SHAPED)
        with mock.patch('gallery.repo_import.requests.get',
                        return_value=fake_response(payload)):
            result = build_import('https://github.com/Njwacky/blaqVibe')
        self.assertEqual(result['repo_label'], 'Njwacky/blaqVibe')
        self.assertEqual(result['repo_url'], 'https://github.com/Njwacky/blaqVibe')
        self.assertEqual(result['source_url'], 'https://codeload.github.com/Njwacky/blaqVibe/zip/HEAD')
        self.assertEqual(result['zip_file'].name, 'blaqVibe-from-github.zip')
        validate_zip(result['zip_file'])  # must not raise

    def test_an_empty_repo_url_falls_back_to_the_demo_repo(self):
        payload = zip_bytes({'repo-main/index.html': '<h1>hi</h1>'})
        with mock.patch('gallery.repo_import.requests.get',
                        return_value=fake_response(payload)) as get:
            build_import('')
        owner, repo, _ = parse_github_url(DEMO_REPO_URL)
        self.assertIn(f'/{owner}/{repo}/zip/', get.call_args[0][0])


class SuggestCategoryTests(TestCase):
    def test_a_full_app_category_is_preferred(self):
        from gallery.models import Category
        Category.objects.create(slug='snippets', name='Snippets', type='snippet', order=1)
        full = Category.objects.create(slug='apps', name='Full Apps', type='full_app', order=2)
        self.assertEqual(suggest_category(['index.html']), full)

    def test_it_creates_a_category_when_the_table_is_somehow_empty(self):
        """Belt-and-braces only: post_migrate seeds the four base categories
        (gallery/apps.py), so this branch is not reachable on a normal boot.
        Mocked because a real empty Category table cannot be arranged here."""
        from gallery.models import Category
        # suggest_category chains .filter(...).order_by(...).first(), so the
        # mock has to model the chain — a bare Mock() would return a truthy
        # Mock and the fallback would never run.
        empty_qs = mock.Mock()
        empty_qs.first.return_value = None
        empty_qs.order_by.return_value = empty_qs
        manager = mock.Mock()
        manager.filter.return_value = empty_qs
        manager.order_by.return_value = empty_qs
        with mock.patch.object(Category, 'objects', manager):
            suggest_category(['index.html'])
        manager.create.assert_called_once_with(
            slug='full-app', name='Full App', type='full_app', order=1,
        )


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-repo-import-tests')
class ImportFromGithubViewTests(TestCase):
    """The view end to end, with the network patched."""

    def setUp(self):
        from gallery.models import Category
        Category.objects.get_or_create(slug='apps', defaults={'name': 'Full Apps', 'type': 'full_app'})
        self.user = make_user()
        self.client.force_login(self.user)
        self.patcher = mock.patch(
            'gallery.repo_import.requests.get',
            return_value=fake_response(zip_bytes(GITHUB_SHAPED)),
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def post(self, **extra):
        data = {'repo_url': 'https://github.com/Njwacky/blaqVibe'}
        data.update(extra)
        return self.client.post('/import/github/', data, follow=True)

    def test_anonymous_visitors_are_sent_to_login(self):
        self.client.logout()
        response = self.client.get('/import/github/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_get_renders_the_form_prefilled_with_the_demo_repo(self):
        response = self.client.get('/import/github/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, DEMO_REPO_URL)

    def test_import_creates_a_pending_vibe_with_the_files(self):
        from gallery.models import AppFile, AppProject, ScanJob
        response = self.post()
        project = AppProject.objects.get(owner=self.user)
        self.assertRedirects(response, project.get_absolute_url())
        self.assertEqual(project.status, 'pending')
        self.assertTrue(project.zip_file)
        self.assertEqual(project.file_count, 3)
        self.assertEqual(project.star_cost, 0)
        self.assertEqual(
            sorted(AppFile.objects.filter(project=project).values_list('path', flat=True)),
            ['.env.example', 'app.py', 'index.html'],
        )
        # Same pipeline as a hand upload: a scan job exists.
        self.assertTrue(ScanJob.objects.filter(project=project).exists())

    def test_title_defaults_to_the_repo_name(self):
        from gallery.models import AppProject
        self.post()
        self.assertEqual(AppProject.objects.get(owner=self.user).title, 'blaqVibe')

    def test_the_published_readme_satisfies_the_upload_form_rules(self):
        from gallery.models import AppProject
        self.post()
        readme = AppProject.objects.get(owner=self.user).readme
        self.assertGreaterEqual(len(readme.strip()), 100)
        self.assertIn('# ', readme)
        self.assertIn('https://github.com/Njwacky/blaqVibe', readme)

    def test_the_user_is_told_what_was_left_out(self):
        response = self.post()
        text = ' '.join(str(m.message) for m in response.context['messages'])
        self.assertIn('scripts/run.sh', text)
        self.assertIn('3 path(s) were left out', text)

    def test_a_non_github_link_is_refused_without_any_request(self):
        from gallery.models import AppProject
        with mock.patch('gallery.repo_import.requests.get') as get:
            response = self.post(repo_url='http://169.254.169.254/latest/meta-data')
        get.assert_not_called()
        self.assertEqual(AppProject.objects.count(), 0)
        text = ' '.join(str(m.message) for m in response.context['messages'])
        self.assertIn('public GitHub', text)

    def test_a_missing_repo_is_an_error_not_a_500(self):
        from gallery.models import AppProject
        with mock.patch('gallery.repo_import.requests.get',
                        return_value=fake_response(b'', status_code=404)):
            response = self.post()
        self.assertEqual(AppProject.objects.count(), 0)
        text = ' '.join(str(m.message) for m in response.context['messages'])
        self.assertIn('no archive', text)

    def test_an_unexpected_crash_is_reported_and_creates_nothing(self):
        from gallery.models import AppProject
        with mock.patch('gallery.repo_import.requests.get', side_effect=RuntimeError('disk on fire')):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(AppProject.objects.count(), 0)
        text = ' '.join(str(m.message) for m in response.context['messages'])
        self.assertIn('failed on our side', text)

    def test_an_over_long_title_is_refused_rather_than_silently_cut(self):
        from gallery.models import AppProject
        response = self.post(title='x' * 400)
        self.assertEqual(AppProject.objects.count(), 0)
        self.assertEqual(response.status_code, 200)
        self.assertIn('title', response.context['form'].errors)

    def test_a_custom_title_is_used(self):
        from gallery.models import AppProject
        self.post(title='My imported vibe')
        self.assertEqual(AppProject.objects.get(owner=self.user).title, 'My imported vibe')


@override_settings(RATELIMIT_ENABLE=True, RATELIMIT_USE_CACHE='default',
                   MEDIA_ROOT='/tmp/blaqvibes-repo-import-tests')
class ImportRateLimitTests(TestCase):
    def test_the_sixth_import_in_an_hour_is_refused(self):
        from gallery.models import Category, AppProject
        Category.objects.get_or_create(slug='apps', defaults={'name': 'Full Apps', 'type': 'full_app'})
        user = make_user('limited')
        self.client.force_login(user)
        with mock.patch('gallery.repo_import.requests.get',
                        return_value=fake_response(zip_bytes({'repo-main/index.html': '<h1>hi</h1>'}))):
            last = None
            for i in range(6):
                last = self.client.post('/import/github/', {'repo_url': 'Njwacky/blaqVibe', 'title': f'v{i}'})
        # django-ratelimit with block=True raises PermissionDenied, which this
        # project routes to handler403/safe_403 — so the ceiling surfaces as
        # 403, not 429. The view's own `request.limited` branch (429) is only
        # reachable when RATELIMIT_ENABLE is off but the flag is still set.
        self.assertEqual(last.status_code, 403)
        self.assertLessEqual(AppProject.objects.filter(owner=user).count(), 5)


class RealGitHubArchiveTests(TestCase):
    """GitHub's real archive shape for this repo, through the real validator.

    `gallery/fixtures/github_blaqvibe_master.zip` is a slice of the archive
    `codeload.github.com/Njwacky/blaqVibe/zip/refs/heads/master` actually
    serves: the `blaqVibe-master/` wrapper, the real `scripts/ci.sh` that makes
    the raw archive unuploadable, plus `manage.py`, `.env.example` and the
    README. Committed rather than downloaded so this runs in CI with no network.
    """

    FIXTURE = Path(__file__).parent / 'fixtures' / 'github_blaqvibe_master.zip'

    def raw(self):
        return self.FIXTURE.read_bytes()

    def test_the_raw_archive_is_refused_by_the_upload_validator(self):
        """The failure a new user hits today if they upload the GitHub ZIP."""
        with self.assertRaises(ValidationError) as ctx:
            validate_zip(uploaded(self.raw(), 'blaqVibe-master.zip'))
        message = ' '.join(ctx.exception.messages)
        self.assertIn('Blocked file type .sh', message)
        self.assertIn('blaqVibe-master/scripts/ci.sh', message)

    def test_the_normalized_archive_passes_the_upload_validator(self):
        result = normalize_github_zip(self.raw())
        validate_zip(uploaded(result['bytes'], 'blaqVibe.zip'))  # must not raise
        self.assertEqual(result['wrapper'], 'blaqVibe-master')
        self.assertEqual([item['path'] for item in result['dropped']], ['scripts/ci.sh'])
        self.assertEqual(
            sorted(result['files']),
            ['.env.example', 'README.md', 'manage.py'],
        )

    def test_the_fixture_readme_is_the_repositorys_real_one(self):
        result = normalize_github_zip(self.raw())
        text = readme_for(result['files'], result['bytes'],
                          'Njwacky/blaqVibe', 'https://github.com/Njwacky/blaqVibe')
        self.assertTrue(text.startswith('# '))
        self.assertNotIn('Imported into BlaqVibes', text)  # the stub was not used
