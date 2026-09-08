"""GitHub import — the shortest road from "I have nothing" to a published vibe.

GitHub already serves a ZIP of every public repository:

    https://codeload.github.com/<owner>/<repo>/zip/refs/heads/<ref>

So a new user does not need git, a build step or an editor to get their first
vibe onto the platform — they need one button. This module turns that URL into
an archive this platform will actually accept, and hands it to the SAME
publish pipeline every upload uses (validate → save → tree → scan queue →
trust). It never invents a second upload path.

Two things make the raw GitHub archive unusable as-is, and both are handled
here rather than left as a confusing form error:

  1. GitHub wraps everything in one folder (`blaqVibe-master/`), so the file
     tree shows a pointless extra level. The wrapper is stripped.
  2. `validate_zip` refuses executable extensions and credential paths —
     `scripts/ci.sh` alone is enough to reject the whole BlaqVibe repo. The
     rejected paths are dropped and REPORTED to the user, never hidden.

Security: the only host ever contacted is `codeload.github.com`, enforced by
rebuilding the URL from parsed owner/repo/ref rather than by pattern-matching
the caller's input. That makes SSRF structurally impossible — a URL that
parses produces a codeload URL, and a URL that does not parse is refused.
"""
import io
import logging
import zipfile

import requests
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import HttpResponse
from django_ratelimit.decorators import ratelimit

from .validators import MAX_ZIP_SIZE, blocked_reason

logger = logging.getLogger(__name__)

# The one host this module will ever speak to. Not configurable: there is no
# setting that could point the fetch at an internal address.
CODELOAD_HOST = 'codeload.github.com'
CODELOAD_SCHEME = 'https'

# A source archive is source. 60 MB covers any real repository while keeping a
# hostile repo from making the web worker hold an unbounded body. This is the
# wire cap; the validator's own 100 MB ZIP cap still applies afterwards.
MAX_DOWNLOAD_BYTES = 60 * 1024 * 1024
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 25

# Shown pre-filled on the import form. It is this repository, so the demo
# always has something real to import even on a fresh database.
DEMO_REPO_URL = 'https://github.com/Njwacky/blaqVibe'
DEMO_LABEL = 'BlaqVibe — this platform, open source'

# Slug/name pairs GitHub accepts. Checked before the fetch so a typo becomes a
# form message instead of a wasted round trip.
_NAME_CHARS = set(
    'abcdefghijklmnopqrstuvwxyz'
    'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    '0123456789-._'
)


class RepoImportError(Exception):
    """A problem the user can act on. Its message is safe to display."""


def parse_github_url(raw):
    """(owner, repo, ref) from any URL a person is likely to paste.

    Accepts the browser URL, the codeload URL, `owner/repo` shorthand, and
    optional `.git` / trailing slash. Anything else raises RepoImportError —
    including anything whose host is not GitHub, which is what keeps the
    fetch pinned to codeload.
    """
    text = (raw or '').strip()
    if not text:
        raise RepoImportError('Paste a GitHub repository link first.')

    owner = repo = ref = ''

    if '://' in text or text.startswith('github.com') or text.startswith('codeload.github.com'):
        url = text if '://' in text else 'https://' + text
        try:
            from urllib.parse import urlsplit
            parts = urlsplit(url)
        except Exception:
            raise RepoImportError('That link does not look like a URL.')
        host = (parts.hostname or '').lower()
        if host not in ('github.com', 'www.github.com', 'codeload.github.com'):
            raise RepoImportError(
                'Only public GitHub repositories can be imported. '
                'Paste a github.com link — or download the ZIP and use the Publish page.'
            )
        segments = [s for s in parts.path.split('/') if s]
        if host == 'codeload.github.com':
            # /<owner>/<repo>/zip/refs/heads/<ref>  or  /<owner>/<repo>/zip/<ref>
            if len(segments) >= 3 and segments[2] == 'zip':
                owner, repo = segments[0], segments[1]
                rest = segments[3:]
                if rest[:2] == ['refs', 'heads']:
                    ref = '/'.join(rest[2:])
                elif rest:
                    ref = '/'.join(rest)
            else:
                raise RepoImportError('That codeload link is missing the repository name.')
        else:
            if len(segments) < 2:
                raise RepoImportError(
                    'That link has no repository in it. Use the form '
                    'github.com/username/repo.'
                )
            owner, repo = segments[0], segments[1]
            if repo.endswith('.git'):
                repo = repo[:-4]
            if len(segments) >= 4 and segments[2] in ('tree', 'blob', 'commits'):
                # /owner/repo/tree/<branch>/path/to/file. For tree/commits the
                # whole remainder is the ref (a branch may contain slashes, as
                # in feature/x); for blob the remainder after the branch is a
                # FILE path, so only the first segment can be the ref.
                if segments[2] == 'blob':
                    ref = segments[3]
                else:
                    ref = '/'.join(segments[3:])
            elif len(segments) >= 4 and segments[2] == 'archive':
                # /owner/repo/archive/refs/heads/main.zip
                rest = segments[3:]
                if rest[:2] == ['refs', 'heads']:
                    ref = '/'.join(rest[2:])
                elif rest:
                    ref = rest[-1]
    else:
        segments = [s for s in text.split('/') if s]
        if len(segments) < 2:
            raise RepoImportError('Use the form github.com/username/repo.')
        owner, repo = segments[0], segments[1]
        if repo.endswith('.git'):
            repo = repo[:-4]
        if len(segments) >= 3:
            ref = '/'.join(segments[2:])

    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        raise RepoImportError('Use the form github.com/username/repo.')
    # Only the /archive/<ref>.zip shape carries a file extension, so only there
    # is it safe to strip one — a real branch could legitimately end in .zip.
    if ref.endswith('.zip') and '/archive/' in text:
        ref = ref[:-4]
    for label, value in (('username', owner), ('repository', repo)):
        if len(value) > 100 or not set(value) <= _NAME_CHARS or value in ('.', '..'):
            raise RepoImportError(f'That does not look like a GitHub {label}: {value[:40]}')
    if ref and (len(ref) > 200 or not set(ref) <= _NAME_CHARS | {'/'}):
        raise RepoImportError(f'That does not look like a branch name: {ref[:40]}')
    # `..` is never a branch. The URL is rebuilt from parts and quote()d, so
    # this is belt-and-braces rather than the only thing standing between a
    # pasted link and a traversal in the request path — but refuse it here so
    # the answer is a clear form message instead of a GitHub 404.
    if ref and any(part in ('', '.', '..') for part in ref.split('/')):
        raise RepoImportError(f'That does not look like a branch name: {ref[:40]}')

    return owner, repo, ref or 'HEAD'


def codeload_url(owner, repo, ref='HEAD'):
    """The archive URL. Built, never derived — so the host cannot be injected."""
    from urllib.parse import quote
    return '%s://%s/%s/%s/zip/%s' % (
        CODELOAD_SCHEME, CODELOAD_HOST,
        quote(owner, safe=''), quote(repo, safe=''), quote(ref, safe='/'),
    )


def fetch_archive(url):
    """Download the archive, capped. Raises RepoImportError on any failure."""
    try:
        response = requests.get(
            url,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            stream=True,
            allow_redirects=False,
            headers={'User-Agent': 'BlaqVibes-RepoImport/1.0'},
        )
    except requests.RequestException as exc:
        logger.warning('repo import fetch failed url=%s err=%s', url, exc)
        raise RepoImportError('GitHub did not answer. Check your connection and try again.')

    if response.status_code == 404:
        raise RepoImportError(
            'GitHub has no archive at that address. Check the spelling, and make '
            'sure the repository is public.'
        )
    if response.status_code != 200:
        logger.warning('repo import unexpected status %s url=%s', response.status_code, url)
        raise RepoImportError(f'GitHub returned {response.status_code} for that archive.')

    length = response.headers.get('Content-Length')
    if length and length.isdigit() and int(length) > MAX_DOWNLOAD_BYTES:
        raise RepoImportError(
            f'That repository is {int(length) // (1024 * 1024)} MB zipped — over the '
            f'{MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB import cap. Remove build output '
            f'from the repo, or upload a trimmed ZIP through the Publish page.'
        )

    buf = io.BytesIO()
    total = 0
    try:
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise RepoImportError(
                    f'That repository is larger than the '
                    f'{MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB import cap.'
                )
            buf.write(chunk)
    except requests.RequestException as exc:
        logger.warning('repo import stream failed url=%s err=%s', url, exc)
        raise RepoImportError('The download broke half way through. Try again.')

    if total == 0:
        raise RepoImportError('GitHub sent an empty archive.')
    return buf.getvalue()


def normalize_github_zip(raw_bytes):
    """Rewrite a GitHub source archive into one this platform accepts.

    Strips the single wrapper folder GitHub adds and drops the paths
    `validate_zip` would refuse, reporting each one. Symlinks are dropped for
    the same reason. Raises ValidationError when nothing usable is left, so the
    caller shows a real message instead of publishing an empty vibe.
    """
    try:
        source = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except zipfile.BadZipFile:
        raise ValidationError('GitHub did not return a readable ZIP.')

    with source:
        infos = source.infolist()
        if not infos:
            raise ValidationError('That archive has no files in it.')

        # GitHub wraps the tree in exactly one folder named after the repo and
        # ref. Drop that one level and nothing else: a repository that really
        # does hold a single top-level directory keeps it, because then more
        # than one entry shares the root.
        roots = {i.filename.replace('\\', '/').split('/')[0] for i in infos}
        prefix = ''
        if len(roots) == 1:
            candidate = next(iter(roots))
            if candidate and all(
                i.filename.replace('\\', '/').startswith(candidate + '/')
                for i in infos
                if not i.is_dir()
            ):
                prefix = candidate + '/'

        kept = []
        dropped = []
        for info in infos:
            name = info.filename.replace('\\', '/').lstrip('/')
            if prefix and name.startswith(prefix):
                name = name[len(prefix):]
            if not name:
                continue
            if info.is_dir():
                continue
            reason = blocked_reason(name)
            if reason is None and (info.external_attr >> 16) & 0o170000 == 0o120000:
                reason = 'symlinks are not allowed in a vibe'
            if reason is not None:
                dropped.append({'path': name, 'reason': reason})
                continue
            try:
                data = source.read(info)
            except Exception:
                logger.warning('repo import: unreadable entry %s', name)
                dropped.append({'path': name, 'reason': 'could not be read from the archive'})
                continue
            kept.append((name, data, info.date_time or (1980, 1, 1, 0, 0, 0)))

        if not kept:
            raise ValidationError(
                'Every file in that archive is one this platform refuses '
                '(build output, credentials or executables). Nothing left to publish.'
            )

        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as target:
            for name, data, date_time in kept:
                zip_info = zipfile.ZipInfo(filename=name, date_time=date_time)
                zip_info.external_attr = 0o644 << 16
                target.writestr(zip_info, data)

        return {
            'bytes': out.getvalue(),
            'files': [name for name, _, _ in kept],
            'file_count': len(kept),
            'uncompressed_bytes': sum(len(data) for _, data, _ in kept),
            'dropped': dropped,
            'wrapper': prefix[:-1] if prefix else '',
        }


def readme_for(files, source_zip_bytes, repo_label, repo_url):
    """The README the imported vibe publishes with.

    The repository's own README when it has one that satisfies the form
    (100 characters and a heading); otherwise an honest stub that says where
    the code came from. Never fabricated prose about what the app does.
    """
    candidates = ('README.md', 'readme.md', 'README.MD', 'Readme.md', 'README.rst', 'README.txt')
    try:
        with zipfile.ZipFile(io.BytesIO(source_zip_bytes)) as z:
            names = set(z.namelist())
            for candidate in candidates:
                if candidate not in names:
                    continue
                text = z.read(candidate).decode('utf-8', errors='replace')
                if len(text.strip()) >= 100 and '# ' in text:
                    return text.strip()
                break
    except Exception:
        logger.exception('repo import: README read failed for %s', repo_label)

    return (
        f'# {repo_label}\n\n'
        f'Imported into BlaqVibes straight from the public GitHub repository '
        f'[{repo_label}]({repo_url}).\n\n'
        f'This is the upstream code as published — {len(files)} files, nothing '
        f'rewritten. Read the repository for what it does and how to run it.\n'
    )


def demo_payload(repo_url):
    """(owner, repo, ref, codeload_url) for a user-supplied link, or the demo repo."""
    owner, repo, ref = parse_github_url(repo_url or DEMO_REPO_URL)
    return owner, repo, ref, codeload_url(owner, repo, ref)


def build_import(repo_url):
    """Fetch + normalize. Returns the dict the view turns into a form.

    Raises RepoImportError for anything the user can fix, ValidationError for
    an archive with nothing publishable in it.
    """
    owner, repo, ref, url = demo_payload(repo_url)
    raw = fetch_archive(url)
    result = normalize_github_zip(raw)
    result.update({
        'owner': owner,
        'repo': repo,
        'ref': ref,
        'source_url': url,
        'repo_url': f'https://github.com/{owner}/{repo}',
        'repo_label': f'{owner}/{repo}',
    })
    result['zip_file'] = SimpleUploadedFile(
        f'{repo}-from-github.zip', result['bytes'], content_type='application/zip',
    )
    return result


def suggest_category(imported_files):
    """The Category an import should land in.

    Prefers a `full_app` category — an imported repository is never a snippet —
    and creates a minimal one on a database that has none, so the demo works on
    a fresh install instead of failing on a required field.
    """
    from .models import Category
    category = Category.objects.filter(type='full_app').order_by('order', 'id').first()
    if category is None:
        category = Category.objects.order_by('order', 'id').first()
    if category is None:
        category = Category.objects.create(
            slug='full-app', name='Full App', type='full_app', order=1,
        )
    return category


# --- the view ---------------------------------------------------------------
#
# One request does the whole thing: fetch, normalize, form-validate, save, and
# hand the project to the same post-save pipeline a normal ZIP upload uses.
# There is deliberately no "cache the archive, then POST it back" step — the
# default cache is per-process LocMemCache, so a token cached here would not be
# readable by the gunicorn worker that serves the next request. Doing the work
# in the request that asked for it is what makes the demo work on one worker
# and on three.

@login_required
@ratelimit(key='user', rate='5/h', method='POST')
def import_from_github(request):
    """Import a public GitHub repository as this user's first vibe."""
    from django.contrib import messages
    from django.core.exceptions import ValidationError
    from django.shortcuts import redirect, render

    from .forms import AppUploadForm
    from .views import register_zip_project
    from . import taste

    context = {
        'demo_repo_url': DEMO_REPO_URL,
        'demo_label': DEMO_LABEL,
    }

    if request.method != 'POST':
        return render(request, 'gallery/import_repo.html', context)

    if getattr(request, 'limited', False):
        return HttpResponse('Rate limit: 5 imports/hour', status=429)

    raw_url = (request.POST.get('repo_url') or '').strip()[:300]
    context['repo_url'] = raw_url

    try:
        imported = build_import(raw_url)
    except RepoImportError as exc:
        messages.error(request, str(exc))
        return render(request, 'gallery/import_repo.html', context)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return render(request, 'gallery/import_repo.html', context)
    except Exception:
        # Anything unexpected (storage, disk, a broken upstream archive) is
        # logged with its traceback and answered with a plain retry message —
        # the user never sees a stack trace and never gets a half-made vibe.
        logger.exception('repo import failed for %r', raw_url[:200])
        messages.error(request, 'That import failed on our side. Please try again.')
        return render(request, 'gallery/import_repo.html', context)

    # Not truncated: AppUploadForm's title field is max_length=200, so an
    # over-long title is refused with a readable field error instead of being
    # silently shortened behind the user's back.
    title = (request.POST.get('title') or '').strip() or imported['repo']
    form = AppUploadForm({
        'title': title,
        'category': suggest_category(imported['files']).pk,
        'short_description': f'Imported from {imported["repo_url"]} — the real repository, unedited.'[:260],
        'readme': readme_for(
            imported['files'], imported['bytes'],
            imported['repo_label'], imported['repo_url'],
        ),
        'tech_stack': '',
        # A copy of a public repository is free by definition: the same bytes
        # are one click away on GitHub, so charging stars for them would be a
        # paywall on nothing. Both price fields are required on the form even
        # though the model defaults them to 0.
        'star_cost': 0,
        'price_zar': 0,
    }, {'zip_file': imported['zip_file']})

    if not form.is_valid():
        # The normalized archive has already passed validate_zip, so what lands
        # here is a field problem (a title the text filter refused, a missing
        # category). Show it next to the form rather than as a banner.
        context['form'] = form
        return render(request, 'gallery/import_repo.html', context)

    project = form.save(commit=False)
    project.owner = request.user
    project.status = 'pending'  # Always pending first — must go through queue
    if not getattr(request.user.profile, 'allow_trading', True):
        project.star_cost = 0
    project.save()
    form.save_m2m()

    register_zip_project(project)
    messages.success(
        request,
        f'✅ Imported {imported["file_count"]} files from {imported["repo_label"]} — '
        f'“{project.title}” is in the scan queue. It goes live the moment the scan clears it.',
    )
    if imported['dropped']:
        names = ', '.join(item['path'] for item in imported['dropped'][:4])
        extra = '' if len(imported['dropped']) <= 4 else f" (+{len(imported['dropped']) - 4} more)"
        messages.warning(
            request,
            f'{len(imported["dropped"])} path(s) were left out because this platform refuses them: '
            f'{names}{extra}. Everything else came through unchanged.',
        )
    try:
        from users.progress import award
        award(project.owner, 'publish', ref=f'project:{project.pk}')
    except Exception:
        logger.exception('repo import xp failed %s', project.slug)
    try:
        taste.record(request.user, project, 'publish', project=project)
    except Exception:
        logger.exception('repo import taste record failed %s', project.slug)
    return redirect(project.get_absolute_url())

