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
try:
    import fcntl
except ImportError:
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None
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
        if fcntl:
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if fcntl and self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
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

class PushRejected(Exception):
    """A push whose content the site refused to adopt.

    Carries the sentence the git client should see. Raised instead of caught
    silently: a push whose post-processing failed used to be swallowed, which
    left the ref moved, the ZIP unchanged and the vibe `published` — the clone
    endpoint then served bytes no scan had ever looked at.
    """


def _push_limit_bytes() -> int:
    # Read per call: `GIT_MAX_PUSH_MB` is an operator knob, and a module-level
    # constant would freeze whatever the test process happened to import first.
    try:
        return max(1, int(os.getenv('GIT_MAX_PUSH_MB', str(MAX_PUSH_MB)))) * 1024 * 1024
    except (TypeError, ValueError):
        return MAX_PUSH_MB * 1024 * 1024


class _BoundedBodyStream:
    """Read-only view of the request body that refuses to pass a size cap.

    5 Whys: why wrap the stream instead of trusting Content-Length?
    1. Why does it matter? The cap was `if request.META.get('CONTENT_LENGTH')`,
       so a chunked push — which is what `git push` really sends — had no
       Content-Length at all and skipped the check entirely. An unbounded pack
       lands on the worker's disk and in the object store.
    2. Why not reject chunked encoding? Then `git push` stops working; the cap
       has to apply to the bytes, not to the framing.
    3. Why return short reads instead of raising? dulwich's pack reader treats a
       truncation as a corrupt stream and fails the receive-pack, so refs are
       never updated. A raised exception mid-protocol can leave a half-written
       state the caller has to guess about.
    4. Why expose `.oversize`? So the view can answer 413 with a sentence the
       human git client actually shows, instead of a generic protocol error.
    5. Why delegate everything else? The request object is also iterated and
       asked for `readline` by different WSGI consumers; a wrapper that only
       implements `read` would break one of them.
    """

    def __init__(self, inner, limit):
        self._inner = inner
        self._limit = int(limit)
        self._seen = 0
        self.oversize = False

    @property
    def consumed(self) -> int:
        return self._seen

    def _refuse_more(self):
        """Called at the cap: distinguish 'body ended here' from 'body cut here'.

        Why read another byte at all? Without the probe, a push of exactly
        MAX_PUSH_MB would be reported as oversized, and a cap that rejects
        legitimate traffic gets disabled by the next engineer to hit it.
        """
        try:
            if self._inner.read(1):
                self.oversize = True
        except Exception:
            self.oversize = True

    def read(self, size=-1, *args, **kwargs):
        room = self._limit - self._seen
        if room <= 0:
            self._refuse_more()
            return b''
        if size is None or size < 0:
            chunk = self._inner.read() or b''
            if len(chunk) > room:
                self.oversize = True
                chunk = chunk[:room]      # hand over nothing past the cap
        else:
            chunk = self._inner.read(min(size, room)) or b''
            if size > room and len(chunk) >= room:
                self._refuse_more()
        self._seen += len(chunk)
        return chunk

    def readline(self, *args, **kwargs):
        room = self._limit - self._seen
        if room <= 0:
            self._refuse_more()
            return b''
        line = self._inner.readline(*args, **kwargs) or b''
        if len(line) > room:
            self.oversize = True
            line = line[:room]
        self._seen += len(line)
        return line

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def __getattr__(self, item):
        # seek/tell/raw passthroughs the WSGI server may probe for. Counting
        # only happens in read/readline, so a passthrough cannot smuggle bytes
        # past the cap unnoticed: dulwich's pack reader uses read().
        return getattr(self._inner, item)


def _export_head_zip(repo, tree_id, zf, prefix='', budget=None):
    """Stream a git tree into a ZIP (ZipFile w). Refuses hostile names and oversize trees.

    5 Whys: why is `budget` an argument defaulting to None instead of `count=[0]`?
    1. Why look at it at all? A mutable default is built ONCE, at import, and
       then shared by every call for the life of the process.
    2. What did that do here? `MAX_FILES_PER_EXPORT` became a per-WORKER budget
       across all projects and all pushes. Once spent, every later push raised
       'repo too large to export'.
    3. Why is that a security bug and not just a bug? The raise landed in
       `_after_push`'s blanket `except Exception`, so refs had already moved
       while no ZIP, no scan and no trust reset happened.
    4. Why keep the counter at all? It bounds a recursive walk over
       attacker-supplied git objects; that job still needs doing.
    5. Why count bytes as well as files? 20 000 files is not a limit when each
       may be 50 MB; the pair (files, bytes) is the actual bound.
    """
    # [files, uncompressed bytes, byte ceiling]. The ceiling is read ONCE per
    # push, so a 20 000-file tree costs one getenv instead of 20 000, and every
    # entry of one export is measured against the same limit.
    budget = [0, 0, _push_limit_bytes()] if budget is None else budget
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
        if budget[0] >= MAX_FILES_PER_EXPORT:
            raise PushRejected(f'too many files (limit {MAX_FILES_PER_EXPORT} per push)')
        if stat.S_ISDIR(entry.mode):
            budget[0] += 1
            _export_head_zip(repo, entry.sha, zf, path + '/', budget)
            continue
        data = repo.get_object(entry.sha).data
        budget[0] += 1
        budget[1] += len(data)
        if budget[1] > budget[2]:
            raise PushRejected('pushed tree is too large to publish here')
        # A symlink becomes a regular file holding the link text: git mode bits
        # must never survive into the ZIP that safe_extract_zip later walks.
        zf.writestr(path, data)


def _pushed_zip_to_project_zip(repo, commit):
    """(zip_bytes) for a commit tree, validated like any upload.

    Why validate here too? `git push` writes the project's current ZIP directly,
    so skipping `validate_zip` let a push carry what an upload cannot: more than
    the file cap, a single file over the per-file size limit, `.env`, `id_rsa`,
    `.npmrc`, blocked extensions. Same bytes, same rules — one gate, both doors.
    """
    from django.core.files.base import ContentFile
    from .validators import validate_zip

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        _export_head_zip(repo, commit.tree, zf)
    zip_bytes = buf.getvalue()
    upload = ContentFile(zip_bytes, name='git-push.zip')
    try:
        validate_zip(upload)
    except Exception as exc:
        # django.core.exceptions.ValidationError carries the useful sentence.
        messages = getattr(exc, 'messages', None)
        detail = (messages[0] if messages else str(exc)) or 'invalid content'
        raise PushRejected(f'pushed content rejected by the upload checks: {detail}') from exc
    return zip_bytes


def _repo_head(project):
    """Current branch tip of the cache, or None. Best-effort by design: a
    missing branch (fresh clone-less repo) is a legal pre-push state."""
    try:
        repo = Repo(str(_repo_dir(project)))
        try:
            return repo.refs[DEFAULT_BRANCH]
        finally:
            repo.close()
    except Exception:
        return None


def _restore_head(project, old_head):
    """Roll the cache's branch back so an unadopted push can never be cloned."""
    try:
        repo = Repo(str(_repo_dir(project)))
        try:
            if old_head:
                repo.refs[DEFAULT_BRANCH] = old_head
            else:
                # No branch to go back to: drop the ref. If the installed dulwich
                # refuses this call we land in the except below, which deletes the
                # whole cache — a heavier but strictly safer outcome.
                try:
                    del repo.refs[DEFAULT_BRANCH]
                except KeyError:
                    pass
        finally:
            repo.close()
    except Exception:
        logger.exception('could not roll back git ref for %s — dropping the cache', project.slug)
        # If we cannot undo the ref, the cache is unvouchable: delete it so the
        # next request rebuilds from the stored, scanned ZIPs.
        _discard_repo_cache(project)


def _discard_repo_cache(project):
    """Delete a repo cache we can no longer vouch for.

    Why deletion rather than a flag? `ensure_repo` rebuilds from the stored
    (scanned) ZIP chain whenever the marker is missing, so removing the
    directory + marker is both the fix and the thing no later code path can
    forget. Pushed history is documented as disposable for exactly this reason.
    """
    try:
        with _RepoLock(project):
            shutil.rmtree(_repo_dir(project), ignore_errors=True)
            for stale in git_root().glob(f'{project.slug}.git.*'):
                try:
                    if stale.is_dir():
                        shutil.rmtree(stale, ignore_errors=True)
                    else:
                        stale.unlink()
                except OSError:
                    pass
            try:
                _meta_file(project).unlink(missing_ok=True)
            except OSError:
                pass
    except Exception:
        logger.exception('repo cache discard failed %s', project.slug)



def _after_push(request, project, user, old_head=None):
    """Run AFTER a successful receive-pack: the pushed HEAD becomes the
    project's current ZIP and the vibe re-enters the scan queue.

    Mirrors edit_vibe exactly: old ZIP -> AppVersion snapshot, new ZIP on the
    project, status back to pending, ScanJob queued, scan chain fired.

    Fail-closed contract: anything that can go wrong is discovered BEFORE the
    refs are considered final, and any failure rolls the cache back so nothing
    unscanned is ever cloneable. Returns None when the push was adopted, or the
    message for the git client when it was refused.
    """
    from django.core.files.base import ContentFile
    from .models import AppVersion, ScanJob

    # 'adopted' is the line between the two failure stories below, so it is set
    # exactly where the database says the pushed bytes are now the project's.
    # 5 Whys: why distinguish at all? 1. Because `_after_push` has one giant
    # try/except and failures before and after that line mean opposite things.
    # 2. What does pre-adoption failure mean? Nothing changed on the project; the
    # ref moved, so the ref is what has to go back. 3. And post-adoption? The new
    # ZIP and `status='pending'` are committed, so rolling the ref forward-and-
    # back would make git and the site disagree about which bytes are live.
    # 4. Why not raise in the second case? A 400 would tell the author to push
    # again, creating a second version of content that is already published-pending.
    # 5. Why is the degraded-but-adopted case safe? The vibe is `pending` with a
    # queued ScanJob and a void trust badge, so nothing is live before it is read.
    adopted = False
    try:
        repo = Repo(str(_repo_dir(project)))
        try:
            head = repo.refs[DEFAULT_BRANCH]
            short = head.decode('ascii')[:8]
            commit = repo.get_object(head)
            # Same byte/entry caps and the same validators an upload runs
            # through — one gate, both doors.
            zip_bytes = _pushed_zip_to_project_zip(repo, commit)
        finally:
            repo.close()
        message = commit.message.decode('utf-8', 'replace').splitlines()
        message = (message[0] if message else '').strip()[:280] or f'git push {short}'

        with transaction_atomic():
            locked = _lock_project(project)
            snapshot = AppVersion.objects.create(
                project=locked,
                zip_file=locked.zip_file,
                version=f'1.{locked.versions.count() + 1}.0',
                changelog=f'pre-push snapshot (git {short})',
            )
            locked.zip_file = ContentFile(zip_bytes, name=f'{locked.slug}-{short}.zip')
            # New bytes via git push = the old trust verdict is void until the
            # pipeline re-scans (gallery.trust WHY 4). The reset rides the same
            # save: one write, no window where new bytes wear an old tick.
            try:
                from .trust import invalidate_trust
                invalidate_trust(locked, save=False)
            except Exception:
                logger.exception('trust invalidation failed for %s', locked.slug)
            locked.status = 'pending'
            locked.save(update_fields=['zip_file', 'status', 'trust', 'trust_graded_at'])
            job, _ = ScanJob.objects.get_or_create(project=locked)
            job.status = 'queued'
            job.save(update_fields=['status'])
            project.pk = locked.pk
        adopted = True

        # The pushed state is now what the project serves, so write the marker
        # immediately: leaving it stale makes the next clone rebuild the repo
        # from the ZIP and throw away the history we just adopted.
        _write_marker(project)

        # Preview file tree must reflect what is now current. A failure here is
        # cosmetic (a stale tree), never a reason to reject an adopted push.
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

        try:
            from .tasks import process_upload_pipeline
            process_upload_pipeline.delay(project.pk)
        except Exception:
            logger.exception('push scan queue trigger failed %s', project.slug)
            # The ScanJob row says 'queued', so the moderation queue still holds
            # the vibe at 'pending' — the state is fail-safe, only the timing is
            # late. Shout about it rather than letting it look like a live scan.
            report = project.scan_report or {}
            report['last_git_push']['scan_not_queued'] = True
            project.scan_report = report
            project.save(update_fields=['scan_report'])

        from .notify import notify
        notify(
            project.owner, 'git_push',
            f'@{user.username} pushed a new version via git',
            f'git-{short} queued for scan — we will tell you when it is live again.',
            project.get_absolute_url(),
        )
        return None
    except PushRejected as exc:
        logger.info('push refused for %s: %s', getattr(project, 'slug', '?'), exc)
        _restore_head(project, old_head)
        _notice_push_refused(
            project, user, f'@{getattr(user, "username", "?")} pushed a commit we could '
                           f'not publish: {exc}. The site still serves the previously '
                           'scanned version — fix it and push again.')
        return str(exc)
    except Exception as exc:
        logger.exception('post-push pipeline failed %s', getattr(project, 'slug', '?'))
        if adopted:
            # The push IS the live-but-unscanned version: keep the refs (they
            # match the stored ZIP) and let the queued ScanJob do its job. Say
            # nothing to the client — a 400 here would invite a duplicate push.
            return None
        _restore_head(project, old_head)
        _notice_push_refused(
            project, user, 'Your push reached git but the site could not process it, so it '
                           'was not published. The repo cache was reset: push again, or '
                           're-upload the ZIP from the edit page.')
        return f'server could not process the push ({exc.__class__.__name__})'


def _notice_push_refused(project, user, body):
    """Tell the owner a push did not become the live version.

    Why a separate helper? Both refusal paths must never be able to raise out of
    the push handling — a broken notification must not turn a refused push into a
    500 on a git client that has already given up on the response.
    """
    try:
        from .notify import notify
        notify(project.owner, 'git_push_rejected', 'git push not published', body,
               project.get_absolute_url())
    except Exception:
        logger.exception('push refusal notice failed %s', getattr(project, 'slug', '?'))


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


def _run_wsgi(request, project, rest, body=None):
    """Run dulwich's smart-HTTP app against a Django request and collect
    the response into a Django HttpResponse.

    `body` is the stream handed to the WSGI app as wsgi.input. It exists so a
    push can be fed through a size-capped view of the request instead of the raw
    request object (see _BoundedBodyStream): dulwich reads packs from wsgi.input,
    so the wrapper is the only place the byte count is honest.
    """
    env = {
        'REQUEST_METHOD': request.method,
        'PATH_INFO': f'/{project.slug}.git/{rest}' if rest else f'/{project.slug}.git/',
        'QUERY_STRING': request.META.get('QUERY_STRING', ''),
        'CONTENT_TYPE': request.META.get('CONTENT_TYPE', ''),
        'CONTENT_LENGTH': request.META.get('CONTENT_LENGTH', ''),
        'HTTP_TRANSFER_ENCODING': request.META.get('HTTP_TRANSFER_ENCODING', ''),
        'wsgi.input': request if body is None else body,
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

    # The push cap is enforced on BYTES, not on a header.
    # 5 Whys: why is the old `if request.META.get('CONTENT_LENGTH')` check not
    # enough? 1. Because a missing header skipped the check entirely. 2. Why is
    # it missing? `git push` uploads chunked, and chunked has no Content-Length.
    # 3. So what was capped? Only the pushes that no real client sends. 4. Why
    # keep the header check then? It rejects a declared-size push before we
    # buffer any of it. 5. Why is the stream wrapper the real fix? dulwich reads
    # the pack from wsgi.input, so counting there bounds every framing — and if
    # it truncates, receive-pack fails the ref update instead of storing it.
    limit = _push_limit_bytes()
    limit_mb = max(1, limit // (1024 * 1024))
    declared = request.META.get('CONTENT_LENGTH')
    if is_push and declared:
        try:
            if int(declared) > limit:
                return HttpResponse(f'Push too large (max {limit_mb} MB).',
                                    status=413, content_type='text/plain')
        except (TypeError, ValueError):
            pass  # unusable header: the byte-counting stream still enforces it

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

    # Remember the branch tip BEFORE the pack lands: if the site then refuses
    # the content, the ref has to go back exactly here, or the rejected bytes
    # stay cloneable from a cache the scan pipeline never saw.
    old_head = _repo_head(project) if is_push else None

    stream = _BoundedBodyStream(request, limit) if is_push else None
    code, resp = _run_wsgi(request, project, rest, body=stream)

    if stream is not None and stream.oversize:
        # We cut the body, so receive-pack aborted: no refs moved, nothing to undo.
        return HttpResponse(f'Push too large (max {limit_mb} MB).',
                            status=413, content_type='text/plain')

    if code == 200 and request.method == 'POST':
        if rest.rstrip('/') == 'git-upload-pack':
            record_clone(project, eff_user, 'git', request.META.get('REMOTE_ADDR', ''))
        elif is_push:
            refusal = _after_push(request, project, basic_user, old_head=old_head)
            if refusal:
                resp = HttpResponse(f'blaqVibes: push rejected - {refusal}\n',
                                    status=400, content_type='text/plain')
                resp.headers['X-BlaqVibes-Reason'] = refusal[:180]
    return resp
