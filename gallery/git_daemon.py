"""Real git smart-HTTP daemon — clone AND push, backed by Dulwich.

5 Whys:
1. Why Dulwich and not a `git clone` STRING? A copy-paste string is a
   promise the server never keeps. Dulwich speaks the smart-HTTP protocol
   (upload-pack / receive-pack) in pure Python, so `git clone` and
   `git push` actually work against `/git/<user>/<slug>.git`.
2. Why build bare repos from the stored ZIP instead of storing repos?
   The ZIP is the single source of truth — every scan, download, version
   and fork reads it. Deriving the repo keeps one copy, and the repo cache
   is disposable: it is rebuilt from the ZIP (or the version chain) whenever
   the stored bytes change.
3. Why must push re-enter the scan queue? The whole site's promise is
   "every app is checked". A pushed commit is new code — it becomes the
   project's current ZIP, the project goes back to `pending`, and the exact
   same ClamAV → secrets → vuln → publish chain runs before it goes live
   again. No scan bypass via git.
4. Why Basic auth + git tokens, not session cookies? The git client does
   not carry browser cookies. Passwords work for users who have one, and a
   revocable per-user token (hashed at rest) covers social-login users.
   Push REQUIRES Basic auth even from a logged-in browser session, so a
   cross-site POST can never write refs (no CSRF surface on writes).
5. Why a lock + marker for the repo cache? gunicorn runs several workers;
   without a lock two requests could rebuild the same repo concurrently.
   The marker file records which stored ZIP the repo was built from, so
   rebuilds only happen when the bytes actually changed — an edit/upload
   invalidates, a description tweak does not.

Honest limits, documented:
- Pushed history lives on the repo cache on local disk. If the cache is
  ever rebuilt from stored ZIPs (server reimage, cache eviction), history
  is reconstructed as one snapshot commit per stored version — content is
  complete, intermediate commit graphs are not. Push again to add history.
- The cache lives under MEDIA_ROOT/git_repos (override GIT_REPO_ROOT), so
  it is per-instance disk, not object storage.
"""
import base64
import fcntl
import hashlib
import hmac
import io
import logging
import os
import shutil
import stat
import tempfile
import time
import zipfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import User
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from .models import AppProject

from dulwich.errors import NotGitRepository
from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import Repo
from dulwich.server import Backend
from dulwich.web import HTTPGitApplication

logger = logging.getLogger(__name__)

def git_root() -> Path:
    # Lazily resolved so override_settings(MEDIA_ROOT=...) in tests and
    # runtime env changes always take effect (a module-level constant
    # would freeze the first value).
    return Path(os.getenv('GIT_REPO_ROOT', str(Path(settings.MEDIA_ROOT) / 'git_repos')))
PLATFORM_IDENTITY = b'BlaqVibes Upload <noreply@blaqvibes.co.za>'
DEFAULT_BRANCH = b'refs/heads/main'
MAX_PUSH_MB = int(os.getenv('GIT_MAX_PUSH_MB', '200'))
REALM = 'BlaqVibes Git'
MAX_FILES_PER_EXPORT = 20000  # same spirit as validators.MAX_FILES


# --- model-level helpers ------------------------------------------------------

def record_clone(project, user, source, ip=''):
    """One append-only CloneEvent + the `clones` counter bump.

    `source` is 'git' (real clone/fetch over the daemon) or 'zip' (the
    ZIP download path). Git clones are throttled to one event per user
    (or per IP hash for anonymous) per project per hour, because a client
    re-fetching a pack mid-interruption must not mint a clone per retry;
    ZIP downloads are not throttled — each served ZIP really is a clone.
    The `clones` counter and the event log stay in lockstep: the counter
    only moves when an event row is written, so the admin chart can never
    contradict the number shown on the vibe card.
    """
    from django.db.models import F
    from .models import AppProject, CloneEvent
    if source == 'git':
        hour_ago = timezone.now() - timedelta(hours=1)
        if user is not None:
            exists = CloneEvent.objects.filter(
                project=project, user=user, source='git', created_at__gte=hour_ago,
            ).exists()
        else:
            ip_hash = _ip_hash(ip)
            exists = CloneEvent.objects.filter(
                project=project, user=None, source='git', ip_hash=ip_hash, created_at__gte=hour_ago,
            ).exists()
        if exists:
            return None
    row = CloneEvent.objects.create(
        project=project,
        user=user,
        source=source,
        ip_hash=_ip_hash(ip) if user is None else '',
    )
    AppProject.objects.filter(pk=project.pk).update(clones=F('clones') + 1)
    return row


def _ip_hash(ip):
    if not ip:
        return ''
    return hashlib.sha256(ip.encode('utf-8', 'ignore')).hexdigest()


# --- repo cache ---------------------------------------------------------------

def _repo_dir(project) -> Path:
    return git_root() / f'{project.slug}.git'


def _meta_file(project) -> Path:
    return git_root() / f'{project.slug}.git.meta'


def _current_zip_key(project):
    """Identify the newest stored ZIP: (name, version_id)."""
    from .models import AppVersion
    latest = AppVersion.objects.filter(project=project).order_by('-created_at').first()
    if latest and latest.zip_file:
        return latest.zip_file.name, latest.pk
    if project.zip_file:
        return project.zip_file.name, 0
    return None, 0


def _marker_value(project):
    name, version_id = _current_zip_key(project)
    return f'{name or ""}|{version_id}'


def _write_marker(project):
    tmp = _meta_file(project).with_suffix('.meta.tmp')
    tmp.write_text(_marker_value(project), encoding='utf-8')
    os.replace(tmp, _meta_file(project))


class _RepoLock:
    """Inter-process lock so concurrent workers never rebuild/rename the
    same repo directory at once."""

    def __init__(self, project):
        self.lock_dir = git_root() / 'locks'
        self.path = self.lock_dir / f'{project.slug}.lock'
        self._fd = None

    def __enter__(self):
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def _tree_from_dir(repo, dirpath):
    """Build a git Tree from a directory, storing blobs/trees in repo.

    Symlinks are skipped — `validators.safe_extract_zip` never creates
    them, so any symlink here is a bug, and git does not need it.
    """
    entries = []
    for name in sorted(os.listdir(dirpath)):
        full = os.path.join(dirpath, name)
        st = os.lstat(full)
        if os.path.islink(full):
            logger.warning('skipping symlink in repo build: %s', full)
            continue
        if os.path.isdir(full):
            entries.append((0o40000, name.encode('utf-8', 'surrogateescape'), _tree_from_dir(repo, full)))
        elif os.path.isfile(full):
            with open(full, 'rb') as fh:
                blob = Blob.from_string(fh.read())
            repo.object_store.add_object(blob)
            mode = 0o100755 if (st.st_mode & 0o111) else 0o100644
            entries.append((mode, name.encode('utf-8', 'surrogateescape'), blob.id))
    tree = Tree()
    for mode, name, sha in sorted(entries, key=lambda e: e[1]):
        tree.add(name, mode, sha)
    repo.object_store.add_object(tree)
    return tree.id


def _commit_snapshot(repo, zip_field, message, parent=None):
    """Commit the contents of a stored ZIP into the bare repo."""
    from .validators import safe_extract_zip
    from .ziputil import materialized_path
    tmpdir = tempfile.mkdtemp(prefix='bv-repo-')
    try:
        with materialized_path(zip_field) as zip_path:
            safe_extract_zip(zip_path, tmpdir)
        tree_id = _tree_from_dir(repo, tmpdir)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    commit = Commit()
    commit.tree = tree_id
    commit.author = commit.committer = PLATFORM_IDENTITY
    now = int(time.time())
    commit.commit_time = commit.author_time = now
    commit.commit_timezone = commit.author_timezone = 0
    commit.encoding = b'UTF-8'
    commit.message = message.encode('utf-8', 'replace')
    commit.parents = [parent] if parent else []
    repo.object_store.add_object(commit)
    return commit.id


def ensure_repo(project):
    """Return a Repo for the project, building it from the stored ZIPs
    when missing or stale. Raises NotGitRepository when there is no ZIP."""
    if not project.zip_file:
        raise NotGitRepository(f'{project.slug}.git')
    with _RepoLock(project):
        meta_path = _meta_file(project)
        repo_path = _repo_dir(project)
        current = _marker_value(project)
        if meta_path.exists() and repo_path.is_dir():
            try:
                if meta_path.read_text(encoding='utf-8').strip() == current:
                    return Repo(str(repo_path))
            except OSError:
                pass
        # Rebuild: one snapshot commit per stored version, oldest first,
        # with the current ZIP as HEAD. This reconstructs upload/version
        # history (and pushed content) even on a fresh instance.
        from .models import AppVersion
        versions = list(AppVersion.objects.filter(project=project).order_by('created_at'))
        chain = [
            (f'v{v.version} — {v.changelog or "update"}', v.zip_file)
            for v in versions if v.zip_file
        ]
        current_label = f'v1.{len(versions) + 1}.0' if versions else 'v1.0.0'
        chain.append((f'{current_label} — current', project.zip_file))

        tmp_path = git_root() / f'{project.slug}.git.build.{os.getpid()}'
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)
        git_root().mkdir(parents=True, exist_ok=True)
        tmp_path.mkdir(parents=True)
        repo = Repo.init_bare(str(tmp_path), default_branch=b'main')
        try:
            cfg = repo.get_config()
            cfg.set((b'http',), b'receivepack', b'true')
            cfg.set((b'core',), b'bare', b'true')
            cfg.write_to_path()
        except Exception:
            logger.exception('repo config write failed %s', project.slug)
        head = None
        for message, zip_field in chain:
            try:
                head = _commit_snapshot(repo, zip_field, message, parent=head)
            except Exception as exc:
                shutil.rmtree(tmp_path, ignore_errors=True)
                logger.exception('repo snapshot commit failed %s', project.slug)
                raise NotGitRepository(f'{project.slug}.git') from exc
        repo.refs.add_if_new(DEFAULT_BRANCH, head)
        # Atomic swap: old repo stays reachable until the new one is fully built.
        stale = git_root() / f'{project.slug}.git.old.{os.getpid()}'
        if repo_path.exists():
            os.replace(repo_path, stale)
        os.replace(tmp_path, repo_path)
        if stale.exists():
            shutil.rmtree(stale, ignore_errors=True)
        _write_marker(project)
        return Repo(str(repo_path))


# --- auth ---------------------------------------------------------------------

def _basic_user(request):
    """Resolve Basic credentials to a User: password OR git token.

    Returns None when no/bad credentials were offered — the caller then
    decides between 401 (prompt) and anonymous access.
    """
    header = request.META.get('HTTP_AUTHORIZATION', '')
    if not header or not header.lower().startswith('basic '):
        return None
    try:
        raw = base64.b64decode(header.split(' ', 1)[1].strip(), validate=True)
        username_b, _, secret_b = raw.partition(b':')
        username = username_b.decode('utf-8', 'ignore')
        secret = secret_b.decode('utf-8', 'ignore')
    except Exception:
        return None
    if not username or not secret:
        return None
    user = User.objects.filter(username=username).first()
    if user is None:
        return None
    if not user.is_active:
        # Suspended accounts must not keep pushing through the daemon.
        return None
    if user.check_password(secret):
        return user
    try:
        token_hash = user.profile.git_token_hash
    except Exception:
        token_hash = ''
    if token_hash:
        digest = hashlib.sha256(secret.encode('utf-8', 'ignore')).hexdigest()
        if hmac.compare_digest(digest, token_hash):
            return user
    return None


def _push_allowed(user, project) -> bool:
    from .models import ProjectCoOwner
    if user.pk == project.owner_id:
        return True
    return ProjectCoOwner.objects.filter(project=project, user=user).exists()


def _auth_required(message):
    resp = HttpResponse(f'Authentication required to {message}.', status=401, content_type='text/plain')
    resp['WWW-Authenticate'] = f'Basic realm="{REALM}"'
    resp['Cache-Control'] = 'no-store'
    return resp


# --- push pipeline ------------------------------------------------------------

def _export_head_zip(repo, tree_id, prefix, zf, count=[0]):
    """Stream a git tree into a ZIP (ZipFile w). Refuses hostile names."""
    for entry in repo.get_object(tree_id).items():
        name = entry.path.decode('utf-8', 'replace')
        if name in ('', '.', '..') or '/' in name.replace('\\', '/'):
            logger.warning('skipping hostile tree entry: %r', name)
            continue
        if '\x00' in name or '\\' in name:
            logger.warning('skipping hostile tree entry: %r', name)
            continue
        path = f'{prefix}{name}'
        if '..' in path.split('/'):
            logger.warning('skipping hostile tree path: %r', path)
            continue
        if count[0] >= MAX_FILES_PER_EXPORT:
            raise ValueError('repo too large to export')
        count[0] += 1
        if stat.S_ISDIR(entry.mode):
            _export_head_zip(repo, entry.sha, path + '/', zf, count)
        elif stat.S_ISLNK(entry.mode):
            zf.writestr(path, repo.get_object(entry.sha).data)
        else:
            zf.writestr(path, repo.get_object(entry.sha).data)


def _after_push(request, project, user):
    """Run AFTER a successful receive-pack: the pushed HEAD becomes the
    project's current ZIP and the vibe re-enters the scan queue.

    Mirrors edit_vibe exactly: old ZIP -> AppVersion snapshot, new ZIP on
    the project, status back to pending, ScanJob queued, scan chain fired.
    """
    try:
        from django.core.files.base import ContentFile
        from .models import AppVersion, ScanJob
        repo = Repo(str(_repo_dir(project)))
        head = repo.refs[DEFAULT_BRANCH]
        short = head.decode('ascii')[:8]
        commit = repo.get_object(head)
        message = commit.message.decode('utf-8', 'replace').splitlines()
        message = (message[0] if message else '').strip()[:280] or f'git push {short}'

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            _export_head_zip(repo, commit.tree, '', zf)
        zip_bytes = buf.getvalue()

        with transaction_atomic():
            locked = _lock_project(project)
            snapshot = AppVersion.objects.create(
                project=locked,
                zip_file=locked.zip_file,
                version=f'1.{locked.versions.count() + 1}.0',
                changelog=f'pre-push snapshot (git {short})',
            )
            locked.zip_file = ContentFile(zip_bytes, name=f'{locked.slug}-{short}.zip')
            locked.status = 'pending'
            locked.save(update_fields=['zip_file', 'status'])
            job, _ = ScanJob.objects.get_or_create(project=locked)
            job.status = 'queued'
            job.save(update_fields=['status'])

        # Preview file tree must reflect what is now current.
        try:
            from .ziputil import build_tree
            from .models import AppFile
            project.refresh_from_db()
            tree, files = build_tree(project.zip_file)
            project.file_tree = tree
            project.file_count = len(files)
            project.save(update_fields=['file_tree', 'file_count'])
            project.files.all().delete()
            for f in files[:2000]:
                AppFile.objects.create(project=project, path=f['path'], size=f['size'])
        except Exception:
            logger.exception('push tree rebuild failed %s', project.slug)

        # Record what pushed, backend-only blob (no schema churn).
        report = project.scan_report or {}
        report['last_git_push'] = {
            'sha': head.decode('ascii'),
            'message': message,
            'by': user.username,
        }
        project.scan_report = report
        project.save(update_fields=['scan_report'])

        # Keep the repo cache authoritative — marker now matches the
        # pushed state so no later request rebuilds the history away.
        _write_marker(project)

        from .notify import notify
        notify(
            project.owner, 'git_push',
            f'@{user.username} pushed a new version via git',
            f'git-{short} queued for scan — we will tell you when it is live again.',
            project.get_absolute_url(),
        )
        try:
            from .tasks import process_upload_pipeline
            process_upload_pipeline.delay(project.pk)
        except Exception:
            logger.exception('push scan queue trigger failed %s', project.slug)
    except Exception:
        # The push itself succeeded (git refs are on disk); the site-side
        # pipeline must never turn a 200 into a 500 for the client.
        logger.exception('post-push pipeline failed %s', getattr(project, 'slug', '?'))


def transaction_atomic():
    from django.db import transaction
    return transaction.atomic()


def _lock_project(project):
    from .models import AppProject
    return AppProject.objects.select_for_update().get(pk=project.pk)


# --- WSGI bridge --------------------------------------------------------------

class _ProjectBackend(Backend):
    def __init__(self, project):
        self.project = project

    def open_repository(self, path):
        # dulwich passes the URL prefix (e.g. '/slug.git'); we already
        # resolved and gated the project, so just hand back its repo.
        return ensure_repo(self.project)


def _run_wsgi(request, project, rest):
    """Run dulwich's smart-HTTP app against a Django request and collect
    the response into a Django HttpResponse."""
    env = {
        'REQUEST_METHOD': request.method,
        'PATH_INFO': f'/{project.slug}.git/{rest}' if rest else f'/{project.slug}.git/',
        'QUERY_STRING': request.META.get('QUERY_STRING', ''),
        'CONTENT_TYPE': request.META.get('CONTENT_TYPE', ''),
        'CONTENT_LENGTH': request.META.get('CONTENT_LENGTH', ''),
        'HTTP_TRANSFER_ENCODING': request.META.get('HTTP_TRANSFER_ENCODING', ''),
        'wsgi.input': request,
        'wsgi.url_scheme': request.scheme,
        'SERVER_NAME': request.get_host().split(':')[0] or 'localhost',
        'SERVER_PORT': str(request.get_port() or 80),
        'REMOTE_ADDR': request.META.get('REMOTE_ADDR', ''),
    }
    captured = {'status': '200 OK', 'headers': []}
    body_parts = []

    def start_response(status, headers, exc_info=None):
        captured['status'] = status
        captured['headers'] = list(headers)
        return body_parts.append

    app = HTTPGitApplication(_ProjectBackend(project))
    out = app(env, start_response)
    # Iterate the WSGI iterable FIRST: the smart-protocol handlers stream
    # through start_response's write() callback during iteration, so any
    # join of body_parts must happen after the generator is consumed.
    out_body = b''.join(list(out))
    body = b''.join(body_parts) + out_body
    try:
        code = int(captured['status'].split(' ', 1)[0])
    except (ValueError, IndexError):
        code = 500
    resp = HttpResponse(body, status=code)
    for key, value in captured['headers']:
        if key.lower() == 'content-length':
            continue  # Django computes it; a stale value would truncate.
        resp.headers[key] = value
    return code, resp


def handle_git_request(request, username, slug, rest=''):
    """Entry point wired to /git/<username>/<slug>.git[/<rest>]."""
    if getattr(request, 'limited', False):
        return HttpResponse('Too many git requests from this network. Slow down.', status=429, content_type='text/plain')
    project = get_object_or_404(AppProject, slug=slug, owner__username=username)
    if not project.zip_file:
        raise Http404
    rest = rest or ''
    if request.method == 'POST' and rest.rstrip('/') not in ('git-upload-pack', 'git-receive-pack'):
        raise Http404

    service = request.GET.get('service', '')

    # A bare GET on the repo root is a HUMAN in a browser, not a git
    # client (git always starts at /info/refs or a service endpoint).
    # Keep the old browser UX: bounce to the vibe page, where the unlock
    # flow lives. Protocol requests get protocol answers (401/403) below.
    if request.method == 'GET' and not rest and not service:
        from .access import access_denied_message, user_can_download
        can_pull = user_can_download(request.user, project) or (
            request.user.is_authenticated and request.user.pk == project.owner_id
        )
        if not can_pull:
            messages.error(request, access_denied_message(request.user, project))
            if not request.user.is_authenticated:
                return redirect(f'{settings.LOGIN_URL}?next={request.path}')
        return redirect(project.get_absolute_url())

    is_push = service == 'git-receive-pack' or rest.rstrip('/') == 'git-receive-pack'

    if is_push and request.META.get('CONTENT_LENGTH'):
        try:
            if int(request.META['CONTENT_LENGTH']) > MAX_PUSH_MB * 1024 * 1024:
                return HttpResponse(f'Push too large (max {MAX_PUSH_MB} MB).', status=413, content_type='text/plain')
        except ValueError:
            pass

    basic_user = _basic_user(request)
    session_user = request.user if request.user.is_authenticated else None
    eff_user = basic_user or session_user

    if is_push:
        # Push is a WRITE: only Basic-authenticated owners/co-owners.
        # Sessions are deliberately ignored here (no cookie-based writes).
        if basic_user is None:
            return _auth_required('push to this repository')
        if project.status == 'removed':
            return HttpResponse('This vibe was removed — push rejected.', status=403, content_type='text/plain')
        if not _push_allowed(basic_user, project):
            return HttpResponse('Push denied — only the owner or a co-owner can push.', status=403, content_type='text/plain')
    else:
        from .access import access_denied_message, user_can_download
        allowed = user_can_download(eff_user, project) or (
            eff_user is not None and eff_user.pk == project.owner_id
        )
        if not allowed:
            if eff_user is None:
                return _auth_required('access this repository')
            return HttpResponse(access_denied_message(eff_user, project), status=403, content_type='text/plain')

    try:
        ensure_repo(project)
    except NotGitRepository:
        raise Http404

    code, resp = _run_wsgi(request, project, rest)

    if request.method == 'POST' and code == 200:
        if rest.rstrip('/') == 'git-upload-pack':
            record_clone(project, eff_user, 'git', request.META.get('REMOTE_ADDR', ''))
        elif rest.rstrip('/') == 'git-receive-pack':
            _after_push(request, project, basic_user)
    return resp
