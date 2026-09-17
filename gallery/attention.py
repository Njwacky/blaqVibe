"""Attention cases — duplicated builds and broken builds, decided on a clock.

THE PROBLEM THIS SOLVES
A builder uploads the same ZIP twice ("final", "final-v2"), or a build breaks
in a way the platform can PROVE (quarantined bytes, a failed scan, a preview
that points at a file that is not in the archive). Before this module both
facts were invisible: the duplicate sat on the feed confusing the people who
came to look at the work, and the broken build sat in the workshop looking
like progress. Neither is the platform's call to make silently, and neither is
the owner's call to make if nobody tells them.

THE 5 WHYS
1. Why not just delete the older copy?
   Because "older" is not "worse". The older copy may be the one three people
   paid stars for, the one that passed the scan, the one a remix credits as its
   parent. Deleting it would destroy receipts and orphan lineage — the two
   things this platform exists to protect.
2. Why not just leave both and let the feed sort it out?
   Because two identical cards in a feed is noise the VISITOR pays for, and the
   visitor is the person we are trying to convince that this builder can build.
3. Why ask the owner at all, instead of always choosing for them?
   Because only the owner knows which copy they meant. The platform can rank
   evidence; it cannot know intent. So the owner gets the first 7 days.
4. Why does the platform choose after 7 days?
   Because an unanswered question is still a cost being paid by every visitor,
   forever. A deadline with a stated default turns "ignored" into "decided".
5. Why is the default a PARK, and not a delete?
   Because a machine decision must be reversible. `lifecycle.park_project`
   takes the loser off the public site and keeps it restorable; the only hard
   delete is the owner's own FINAL DELETE click — or their 24 hours of silence
   after they have demonstrably opened that decision. Silence after seeing it
   is consent; silence without seeing it never is (that gets a longer backstop).

REMINDERS
An open case re-surfaces every `ATTENTION_REMINDER_MINUTES` (30 by default)
until it is answered. The reminder BUMPS the one pinned inbox row instead of
appending a new one — 336 nudges over 7 days would otherwise be 336 rows and an
unread badge nobody trusts. The banner on every page and the nav badge read the
same row.

WRITER RULE
This module is the only writer of AttentionCase / AttentionCandidate. Views,
Celery tasks and the management command call these functions, so the countdown,
the notification and the audit trail can never drift apart.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.utils import timezone

from .lifecycle import park_project, remove_project, restore_project
from .models import AppProject, AttentionCandidate, AttentionCase, Sale, Trade
from .notify import category_meta, notify, redeliver

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Tunables. Every one of these is quoted in user-visible copy, so each is read
# from settings with a module default — the sentence "you have 7 days" and the
# countdown that enforces it come from the same number.
# ----------------------------------------------------------------------
DEFAULT_DECISION_DAYS = 7
DEFAULT_REMINDER_MINUTES = 30
DEFAULT_FINAL_DELETE_HOURS = 24
DEFAULT_UNOPENED_BACKSTOP_DAYS = 7
DEFAULT_DUPLICATE_THRESHOLD = 70
DEFAULT_STUCK_HOURS = 48

def _int_setting(name, default, minimum=1):
    try:
        return max(minimum, int(getattr(settings, name, default)))
    except Exception:
        return default

def decision_days() -> int:
    return _int_setting('ATTENTION_DECISION_DAYS', DEFAULT_DECISION_DAYS)

def reminder_minutes() -> int:
    return _int_setting('ATTENTION_REMINDER_MINUTES', DEFAULT_REMINDER_MINUTES)

def final_delete_hours() -> int:
    return _int_setting('ATTENTION_FINAL_DELETE_HOURS', DEFAULT_FINAL_DELETE_HOURS)

def unopened_backstop_days() -> int:
    return _int_setting('ATTENTION_UNOPENED_BACKSTOP_DAYS', DEFAULT_UNOPENED_BACKSTOP_DAYS)

def duplicate_threshold() -> int:
    return _int_setting('ATTENTION_DUPLICATE_THRESHOLD', DEFAULT_DUPLICATE_THRESHOLD, minimum=1)

def stuck_hours() -> int:
    return _int_setting('ATTENTION_STUCK_HOURS', DEFAULT_STUCK_HOURS)

def enabled() -> bool:
    return bool(getattr(settings, 'ATTENTION_ENABLED', True))

# ----------------------------------------------------------------------
# Fingerprints — deterministic, storage-agnostic, no LLM, no network.
# ----------------------------------------------------------------------
_TITLE_NOISE = re.compile(r'[^0-9a-z]+')
_SLUG_SUFFIX = re.compile(r'-\d+$')

def norm_title(title: str) -> str:
    """'My  Dashboard!!' and 'my dashboard' are the same title."""
    return _TITLE_NOISE.sub(' ', (title or '').casefold()).strip()

def slug_base(slug: str) -> str:
    """'dashboard-2' → 'dashboard'. The suffix is what AppProject.save() adds
    when a slug collides — i.e. the platform's own fingerprint of "you already
    had one of these"."""
    return _SLUG_SUFFIX.sub('', (slug or '').casefold())

def tree_paths(project) -> list[str]:
    """Sorted file paths from the stored tree (or the AppFile rows).

    Handles both shapes the codebase writes: build_tree() nests directories as
    dicts with `None` leaves, while seed data uses `{path: {}}`. An EMPTY dict
    is therefore read as a file — a directory with no files in it never appears
    in a ZIP listing, so that reading is the one that cannot lose a path.
    """
    paths: list[str] = []

    def walk(node, prefix):
        if not isinstance(node, dict):
            return
        for name, child in node.items():
            here = f'{prefix}{name}'
            if isinstance(child, dict) and child:
                walk(child, here + '/')
            else:
                paths.append(here)

    tree = getattr(project, 'file_tree', None)
    if isinstance(tree, dict) and tree:
        walk(tree, '')
    if paths:
        return sorted(paths)
    try:
        rows = list(project.files.all()[:2000])
    except Exception:
        rows = []
    return sorted(r.path for r in rows if getattr(r, 'path', None))

def fingerprint(project) -> dict:
    """The comparable identity of one build. Cheap: reads stored columns only."""
    paths = tree_paths(project)
    code = '\n'.join([
        (project.html_code or ''),
        (project.css_code or ''),
        (project.js_code or ''),
    ]).strip()
    langs = project.language_stats if isinstance(project.language_stats, dict) else {}
    return {
        'paths': paths,
        'paths_hash': hashlib.sha256('\n'.join(paths).encode('utf-8')).hexdigest() if paths else '',
        'code_hash': hashlib.sha256(code.encode('utf-8')).hexdigest() if len(code) >= 40 else '',
        'title_norm': norm_title(project.title),
        'slug_base': slug_base(project.slug),
        'lang_key': ','.join(f'{k}:{int(v)}' for k, v in sorted(langs.items())) if langs else '',
    }

# ----------------------------------------------------------------------
# Duplicate scoring. Each signal carries points AND the sentence it earns, so
# the evidence a case was opened on is the same evidence the owner is shown.
# ----------------------------------------------------------------------
# The signal table. Points are listed here rather than inline in similarity()
# for one reason: the bucketing below PROVES it can skip pairs, and that proof is
# arithmetic on this table. A test (test_weak_signals_alone_cannot_reach_the_
# threshold) recomputes it, so raising a weak signal past the point where
# bucketing stays exact fails the suite instead of silently missing duplicates.
SIGNAL_POINTS = {
    'same_files': 45,        # identical archive: same names, two or more files
    'same_files_single': 20, # one shared path proves almost nothing
    'same_code': 45,         # byte-identical inline HTML/CSS/JS
    'same_title': 25,
    'same_slug': 10,         # 'dashboard' vs 'dashboard-2'
    'same_stack': 8,
}
# Signals strong enough that a pair MUST match one of them to be worth opening a
# case about. Everything else is corroboration.
STRONG_SIGNALS = ('same_files', 'same_code')
WEAK_SIGNAL_MAX = sum(
    points for key, points in SIGNAL_POINTS.items()
    if key not in STRONG_SIGNALS and key != 'same_files_single'
)

def similarity(a: dict, b: dict) -> tuple[int, list[dict]]:
    """(score 0-100, matched signals) for two fingerprints."""
    signals: list[dict] = []
    score = 0

    if a['paths_hash'] and a['paths_hash'] == b['paths_hash']:
        n = len(a['paths'])
        # One shared path proves almost nothing ('index.html' is everybody's
        # first file); two or more identical paths in identical order is the
        # archive itself.
        points = SIGNAL_POINTS['same_files'] if n >= 2 else SIGNAL_POINTS['same_files_single']
        score += points
        signals.append({
            'key': 'same_files', 'points': points,
            'label': f'Same {n} file{"s" if n != 1 else ""}, same names',
        })
    if a['code_hash'] and a['code_hash'] == b['code_hash']:
        score += SIGNAL_POINTS['same_code']
        signals.append({'key': 'same_code', 'points': SIGNAL_POINTS['same_code'],
                        'label': 'Byte-identical code'})
    if a['title_norm'] and a['title_norm'] == b['title_norm']:
        score += SIGNAL_POINTS['same_title']
        signals.append({'key': 'same_title', 'points': SIGNAL_POINTS['same_title'], 'label': 'Same title'})
    if a['slug_base'] and a['slug_base'] == b['slug_base']:
        score += SIGNAL_POINTS['same_slug']
        signals.append({'key': 'same_slug', 'points': SIGNAL_POINTS['same_slug'],
                        'label': 'Slug differs only by a "-2" suffix'})
    if a['lang_key'] and a['lang_key'] == b['lang_key']:
        score += SIGNAL_POINTS['same_stack']
        signals.append({'key': 'same_stack', 'points': SIGNAL_POINTS['same_stack'],
                        'label': 'Same languages, same proportions'})

    return min(score, 100), signals

def _is_legitimate_lineage(a: AppProject, b: AppProject) -> bool:
    """A remix is credit, not duplication (README: lineage is credit).

    Excluded: a fork of the other, and two forks of the same parent — both are
    the remix tree working as designed. Punishing them would teach builders
    that remixing gets their work deleted.
    """
    if a.forked_from_id and (a.forked_from_id == b.pk or a.forked_from_id == b.forked_from_id):
        return True
    if b.forked_from_id and b.forked_from_id == a.pk:
        return True
    return False

def pair_key_for(kind: str, user_id: int, *identifiers) -> str:
    """Stable idempotency key. UNIQUE on the row, so an hourly sweep can never
    open the same problem twice and a dismissed problem stays dismissed."""
    raw = ':'.join([kind, str(user_id), *[str(i) for i in identifiers]])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

# ----------------------------------------------------------------------
# Snapshots — what the owner is shown about each copy, including AFTER one of
# them has been deleted (the row survives, the FK goes null).
# ----------------------------------------------------------------------
def snapshot_project(project) -> dict:
    """Server-side facts only. Never user free-text beyond the title, which is
    already rendered escaped everywhere in this codebase."""
    trades = 0
    sales = 0
    try:
        trades = Trade.objects.filter(project=project).count()
        sales = Sale.objects.filter(project=project).count()
    except Exception:
        pass
    scan_status = ''
    try:
        scan_status = project.scan_job.status
    except Exception:
        scan_status = ''
    return {
        'title': project.title,
        'slug': project.slug,
        'status': project.status,
        'status_label': project.get_status_display(),
        # Human dates, formatted when the snapshot is taken. The attention page
        # has to keep showing them after the build is deleted, and an ISO string
        # is not something a person compares two copies with.
        'uploaded_on': _day(project.created_at),
        'updated_on': _day(project.updated_at),
        'published_on': _day(project.published_at),
        'trust': getattr(project, 'trust', ''),
        'uploaded_at': _iso(project.created_at),
        'updated_at': _iso(project.updated_at),
        'published_at': _iso(project.published_at),
        'kind': project.kind,
        'stars': project.stars,
        'views': project.views,
        'clones': project.clones,
        'review_count': project.review_count,
        'avg_rating': project.avg_rating,
        'file_count': project.file_count,
        'has_zip': bool(project.zip_file),
        'preview_mode': project.preview_mode,
        'is_remix': bool(project.forked_from_id),
        'star_cost': project.star_cost,
        'price_zar': project.price_zar,
        'trades': trades,
        'sales': sales,
        'scan_status': scan_status,
    }

def _iso(value) -> str:
    return value.isoformat() if value else ''

def _day(value) -> str:
    """'02 Jan 2026' in the server's display timezone, or '' — the shape the
    attention page prints next to each copy so the owner can tell old from new."""
    if not value:
        return ''
    try:
        return timezone.localtime(value).strftime('%d %b %Y')
    except Exception:
        return value.strftime('%d %b %Y')

def describe_age(project, now=None) -> str:
    """'uploaded 12 Mar 2026, last updated 3 days ago' — the sentence that lets
    an owner tell the old copy from the one they were still working on."""
    now = now or timezone.now()
    uploaded = timezone.localtime(project.created_at).strftime('%d %b %Y') if project.created_at else 'unknown date'
    if project.updated_at:
        from django.utils.timesince import timesince
        return f'uploaded {uploaded}, last updated {timesince(project.updated_at, now)} ago'
    return f'uploaded {uploaded}'

# ----------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------
def _candidate_projects(user):
    """One owner's builds, excluding the parked ones. A 'removed' build is
    already off the public site, so it cannot be half of a live duplicate."""
    return (
        AppProject.objects
        .filter(owner=user)
        .exclude(status='removed')
        .order_by('created_at')
    )

def detect_duplicates(user, threshold: int | None = None) -> list[AttentionCase]:
    """Open a case for every pair of THIS owner's builds that looks like the
    same build. Returns the cases created (never the ones that already exist).

    Same owner only, on purpose: two different people's similar projects is a
    copyright/plagiarism question that belongs to the report + moderation flow,
    where a human moderator decides — not to a timer that deletes somebody's
    work because a stranger uploaded similar bytes.
    """
    if not enabled():
        return []
    created: list[AttentionCase] = []
    for a, b, score, signals in duplicate_pairs(user, threshold=threshold):
        # Cluster, don't pair: a third identical upload joins the OPEN case for
        # that pair instead of minting a second case about the same mess. One
        # decision per problem is the whole point of this feature.
        existing = _open_case_for_project(user, 'duplicate', a) or _open_case_for_project(user, 'duplicate', b)
        if existing is not None:
            _add_candidate(existing, b if existing.subject_id == a.pk else a, score, signals)
            continue
        case = _create_duplicate_case(user, a, b, score, signals)
        if case is not None:
            created.append(case)
    return created


def duplicate_pairs(user, threshold: int | None = None):
    """Yield (a, b, score, signals) for every pair of ONE owner's builds that
    reaches the duplicate threshold.

    This is the whole of "what looks like the same build", in one place, and it
    is shared by the engine and by `manage.py attention_sweep --dry-run` so the
    report an operator reads cannot disagree with the sweep that follows it.
    Deliberately NOT case-aware: whether a pair already has a case, or should be
    folded into an open one, is the caller's decision — the engine folds (a
    third copy joins the existing case), the dry run skips (it would open
    nothing new). Both need the same list of pairs to make that call on.

    Deduplicated because a pair can match twice — same files AND same code puts
    both builds in two buckets, and the two buckets can surface them in either
    order — so the seen-key is sorted. One pair is one question, asked once.
    """
    threshold = duplicate_threshold() if threshold is None else threshold
    projects = list(_candidate_projects(user))
    if len(projects) < 2:
        return
    prints = [(p, fingerprint(p)) for p in projects]
    seen: set[tuple[int, int]] = set()
    for a, b, fa, fb in _pairs_worth_comparing(prints, threshold):
        key = (min(a.pk, b.pk), max(a.pk, b.pk))
        if key in seen:
            continue
        seen.add(key)
        if _is_legitimate_lineage(a, b):
            continue
        score, signals = similarity(fa, fb)
        if score < threshold:
            continue
        yield a, b, score, signals

def _pairs_worth_comparing(prints, threshold):
    """Yield (a, b, fingerprint_a, fingerprint_b) for pairs that can reach the
    threshold — and for no others.

    Why this is not a heuristic: the weak signals (title 25 + slug 10 + stack 8)
    add up to WEAK_SIGNAL_MAX, which is below the default threshold of 70. So a
    pair that matches neither the file list nor the code CANNOT qualify, and
    bucketing on those two hashes is exact — the same answers as comparing every
    pair, in O(n) instead of O(n²). That matters because this runs on a page
    load (/attention/ detects before it claims "nothing needs you"), and a
    builder with 300 uploads would otherwise cost 45,000 comparisons per visit.

    If an operator lowers ATTENTION_DUPLICATE_THRESHOLD to where a weak-only pair
    could qualify, the bucketing would start missing cases — so the function
    notices and falls back to comparing everything. A test pins the arithmetic.
    """
    if threshold <= WEAK_SIGNAL_MAX:
        for i, (a, fa) in enumerate(prints):
            for b, fb in prints[i + 1:]:
                yield a, b, fa, fb
        return

    buckets: dict[tuple[str, str], list] = {}
    for project, print_ in prints:
        if print_['paths_hash'] and len(print_['paths']) >= 2:
            buckets.setdefault(('paths', print_['paths_hash']), []).append((project, print_))
        if print_['code_hash']:
            buckets.setdefault(('code', print_['code_hash']), []).append((project, print_))

    for group in buckets.values():
        if len(group) < 2:
            continue
        for i, (a, fa) in enumerate(group):
            for b, fb in group[i + 1:]:
                yield a, b, fa, fb

def _open_case_for_project(user, kind: str, project):
    """The OPEN case this project is already part of (a third identical upload
    joins it instead of minting a second case about the same mess)."""
    return (
        AttentionCase.objects
        .filter(user=user, kind=kind, status='open', subject=project)
        .order_by('-created_at')
        .first()
    ) or (
        AttentionCase.objects
        .filter(user=user, kind=kind, status='open', candidates__project=project)
        .order_by('-created_at')
        .first()
    )

_COUNT_WORDS = {2: 'Two', 3: 'Three', 4: 'Four', 5: 'Five'}

def copies_headline(count: int, title: str) -> str:
    """'Two copies of “X”' — words for the small numbers a person actually hits,
    digits beyond that, where spelling it out stops helping."""
    word = _COUNT_WORDS.get(count, str(count))
    return f'{word} copie{"s" if count != 1 else ""} of “{title}”'[:200]

def _create_duplicate_case(user, a: AppProject, b: AppProject, score: int, signals: list[dict]):
    # The NEWER build is the subject: it is the one that caused the collision,
    # and "you just uploaded this, and you already had one" is the honest read.
    newer, older = (a, b) if a.created_at >= b.created_at else (b, a)
    key = pair_key_for('duplicate', user.pk, min(a.pk, b.pk), max(a.pk, b.pk))
    both_public = a.status == 'published' and b.status == 'published'
    # Two live copies of one build on the public feed is the damaging case —
    # visitors cannot tell which is real. One of them still being in review is
    # a workshop problem, so it is 'action' rather than 'critical'.
    severity = 'critical' if both_public else 'action'
    headline = copies_headline(2, newer.title)
    detail = (
        f'{newer.title} ({describe_age(newer)}) and {older.title} ({describe_age(older)}) '
        f'look like the same build — {", ".join(s["label"].lower() for s in signals)}. '
        f'Pick the one to keep, or let BlaqVibes decide in {decision_days()} days.'
    )
    evidence = {
        'score': score,
        'signals': signals,
        'threshold': duplicate_threshold(),
        'both_published': both_public,
        'fingerprints': {
            str(a.pk): {k: v for k, v in fingerprint(a).items() if k != 'paths'},
            str(b.pk): {k: v for k, v in fingerprint(b).items() if k != 'paths'},
        },
    }
    return _create_case(
        user=user, kind='duplicate', pair_key=key, severity=severity,
        headline=headline[:200], detail=detail[:500], subject=newer,
        evidence=evidence, candidates=[(older, 0, []), (newer, 0, [])],
    )

def _add_candidate(case, project, score: int, signals: list[dict]) -> bool:
    """Fold a further copy into an open case instead of opening a second one.

    Returns False (and writes nothing) when the build is already a candidate —
    detection runs on every /attention/ page load, so re-seeing a known pair
    must be a no-op. Rewriting the headline or bumping the notification here
    would turn "the same case, checked again" into "a new problem appeared".
    """
    try:
        if case.candidates.filter(project=project).exists():
            return False
        AttentionCandidate.objects.create(
            case=case, project=project, score=0,
            snapshot=snapshot_project(project),
            reasons=[s['label'] for s in signals],
        )
        evidence = case.evidence if isinstance(case.evidence, dict) else {}
        merged = evidence.setdefault('merged', [])
        merged.append({'project_id': project.pk, 'score': score, 'signals': signals})
        case.evidence = evidence
        count = case.candidates.count()
        title = case.subject.title if case.subject else project.title
        case.headline = copies_headline(count, title)
        case.save(update_fields=['evidence', 'headline', 'updated_at'])
        notify_case(case)
        return True
    except Exception:
        logger.exception('add candidate failed case=%s', case.pk)
        return False

# --- malfunctions -----------------------------------------------------
# (code, severity, headline, detail, fix hint). Ordered: the first match wins,
# so the most damaging true statement about a build is the one reported.
MALFUNCTIONS = ('quarantined', 'scan_failed', 'missing_zip', 'broken_preview', 'stuck_pending', 'empty_shell')

def malfunction_of(project, now=None) -> dict | None:
    """The provable defect on one build, or None. Every branch is a fact read
    from stored columns — no guessing, and nothing that requires re-running a
    scanner inside a request."""
    now = now or timezone.now()
    status = project.status

    if status == 'quarantined':
        return {
            'code': 'quarantined', 'severity': 'critical',
            'headline': f'“{project.title}” was quarantined by the scan',
            'detail': 'The scanner found a virus signature or a hard-coded secret in the upload, so it is not on the feed and nobody can download it.',
            'fix_hint': 'Remove the secret or the flagged file, then upload a clean ZIP — or delete this copy.',
        }
    scan_status = ''
    try:
        scan_status = project.scan_job.status
    except Exception:
        scan_status = ''
    if scan_status == 'failed':
        return {
            'code': 'scan_failed', 'severity': 'critical',
            'headline': f'“{project.title}” never finished its scan',
            'detail': 'The scan job failed, so the platform cannot say whether this build is safe. It is stuck out of the feed through no fault of yours.',
            'fix_hint': 'Re-upload the same ZIP to queue a fresh scan, or delete this copy.',
        }
    if project.preview_mode == 'static_zip' and not project.zip_file:
        return {
            'code': 'missing_zip', 'severity': 'critical',
            'headline': f'“{project.title}” promises a runnable preview with no archive',
            'detail': 'This build is marked as a static site that runs in the preview pane, but there is no ZIP attached — the preview has nothing to load.',
            'fix_hint': 'Attach the ZIP again, or switch the preview to the file list.',
        }
    if project.preview_mode == 'static_zip' and project.static_entry:
        paths = tree_paths(project)
        if paths and project.static_entry not in paths:
            return {
                'code': 'broken_preview', 'severity': 'action',
                'headline': f'“{project.title}” points its preview at a file that is not there',
                'detail': f'The preview is set to open “{project.static_entry}”, which is not in the {len(paths)} files of the archive — visitors get a blank pane.',
                'fix_hint': 'Pick the real entry file and rescan, or re-upload.',
            }
    if status == 'pending' and project.created_at and project.created_at <= now - timedelta(hours=stuck_hours()):
        queued = scan_status in ('', 'queued', 'scanning', 'pending')
        if queued:
            return {
                'code': 'stuck_pending', 'severity': 'action',
                'headline': f'“{project.title}” has been waiting {stuck_hours()}+ hours for review',
                'detail': 'It is still in the queue with no verdict. That is our backlog, not your mistake — but it is invisible to everyone until it clears.',
                'fix_hint': 'Leave it and we will keep nudging, or delete it and re-upload to jump the queue.',
            }
    if status == 'published' and not project.zip_file and not (project.html_code or '').strip():
        return {
            'code': 'empty_shell', 'severity': 'action',
            'headline': f'“{project.title}” is live with nothing to run or download',
            'detail': 'It is published, but there is no ZIP and no inline code — a visitor opens it and finds only text.',
            'fix_hint': 'Add the ZIP or the code, or delete it until it is ready.',
        }
    return None

def detect_malfunctions(user, now=None) -> list[AttentionCase]:
    """Open a case for each provable defect on this owner's builds."""
    if not enabled():
        return []
    now = now or timezone.now()
    created: list[AttentionCase] = []
    for project in _candidate_projects(user):
        problem = malfunction_of(project, now=now)
        if not problem:
            continue
        key = pair_key_for('malfunction', user.pk, project.pk, problem['code'])
        if AttentionCase.objects.filter(pair_key=key).exists():
            continue
        case = _create_case(
            user=user, kind='malfunction', pair_key=key, severity=problem['severity'],
            headline=problem['headline'][:200], detail=problem['detail'][:500],
            fix_hint=problem['fix_hint'][:300], subject=project,
            evidence={'code': problem['code'], 'snapshot_at': _iso(now)},
            candidates=[(project, 0, [])],
        )
        if case is not None:
            created.append(case)
    return created

def detect_for_user(user, now=None) -> dict:
    """Full detection pass for ONE owner. Called the moment a build lands
    (the upload pipeline) and by the hourly sweep."""
    if not enabled() or user is None:
        return {'duplicates': 0, 'malfunctions': 0}
    duplicates = 0
    malfunctions = 0
    try:
        duplicates = len(detect_duplicates(user))
    except Exception:
        logger.exception('duplicate detection failed user=%s', getattr(user, 'pk', None))
    try:
        malfunctions = len(detect_malfunctions(user, now=now))
    except Exception:
        logger.exception('malfunction detection failed user=%s', getattr(user, 'pk', None))
    return {'duplicates': duplicates, 'malfunctions': malfunctions}

def detect(limit: int | None = None, now=None) -> dict:
    """Sweep: most recently active owners first, bounded by `limit`.

    Recent-first is not a shortcut, it is the correct order: a duplicate is
    created by an upload, so the owners who just uploaded are the ones who can
    have one. Owners who were already swept come back around once the busy ones
    go quiet, and the per-upload hook in the pipeline means nobody waits on the
    sweep for the case that matters.
    """
    if not enabled():
        return {'owners': 0, 'duplicates': 0, 'malfunctions': 0}
    owners = sweep_owners(limit=limit)
    from django.contrib.auth.models import User
    totals = {'owners': 0, 'duplicates': 0, 'malfunctions': 0}
    for row in owners:
        user = User.objects.filter(pk=row['owner']).first()
        if user is None:
            continue
        result = detect_for_user(user, now=now)
        totals['owners'] += 1
        totals['duplicates'] += result['duplicates']
        totals['malfunctions'] += result['malfunctions']
    return totals

def sweep_owners(limit: int | None = None):
    """The owner ids a detection sweep visits, most recently active first.

    Its own function because the dry-run sweep has to look at the same owners in
    the same order, or it is a description of a different job. The annotate() is
    what makes this ONE ROW PER OWNER: `.values('owner')` on its own returns a
    row per build, so an owner with two uploads would be detected twice and a
    dry run would report every problem twice.
    """
    limit = limit or int(getattr(settings, 'ATTENTION_DETECT_BATCH', 400))
    return (
        AppProject.objects
        .exclude(status='removed')
        .values('owner')
        .annotate(n=Count('id'), latest=Max('updated_at'))
        .order_by('-latest')[:limit]
    )


# ----------------------------------------------------------------------
# Case creation
# ----------------------------------------------------------------------
def _create_case(user, kind, pair_key, severity, headline, detail, subject,
                 evidence, candidates, fix_hint='') -> AttentionCase | None:
    """Create the case + its candidate rows + the first notification, or return
    None when this problem already has a row (any status). Idempotent by
    design: an hourly sweep must be safe to run against an owner who already
    answered."""
    now = timezone.now()
    try:
        with transaction.atomic():
            case = AttentionCase.objects.create(
                user=user, kind=kind, pair_key=pair_key, severity=severity,
                headline=headline, detail=detail, fix_hint=fix_hint,
                subject=subject, evidence=evidence, status='open',
                expires_at=now + timedelta(days=decision_days()),
            )
            for project, score, reasons in candidates:
                AttentionCandidate.objects.create(
                    case=case, project=project, score=score,
                    reasons=[r if isinstance(r, str) else r.get('label', '') for r in (reasons or [])],
                    snapshot=snapshot_project(project),
                )
    except IntegrityError:
        # pair_key is UNIQUE: the last sweep (or the publish hook running in
        # parallel) already opened this problem. That is the design working.
        logger.info('attention case already exists key=%s', pair_key)
        return None
    except Exception:
        logger.exception('attention case creation failed key=%s', pair_key)
        return None
    notify_case(case)
    return case

# ----------------------------------------------------------------------
# The keeper strategy — the "strategic way" the system explains itself.
#
# Ordered by how much each fact PROVES, because the product standard is that
# proof outranks popularity. Stars and views are capped low on purpose: a
# build with 40 stars and no scan is not better evidence than a scanned build
# with 3 stars.
# ----------------------------------------------------------------------
KEEPER_STRATEGY = [
    'Never delete a receipt — a copy somebody paid for is untouchable.',
    'Keep the published copy over the one still in review.',
    'Keep the copy that passed the scan (verified beats scanned beats unknown).',
    'Keep the copy with the real artifact attached (a ZIP over an empty shell).',
    'Keep the copy you wrote proof for (problem, what you did, where AI failed).',
    'Keep the copy other builders remixed — deleting it orphans their lineage.',
    'Then, and only then: reviews, stars and views — capped, so popularity can tie-break but never decide.',
    'Finally: the copy you were still working on, then the original upload.',
]

# ----------------------------------------------------------------------
# The weights, as numbers a test can check.
#
# The README's line is "followers and stars do not decide that ranking", and a
# weight table is where that sentence is either true or a slogan. So: every
# popularity signal is capped, and MAX_POPULARITY (the sum of all of them at
# their caps) is deliberately smaller than ONE published copy's worth and
# smaller than two proof fields. A loud build can therefore tie-break between
# two otherwise identical copies and can never outvote evidence.
# ----------------------------------------------------------------------
PAID_POINTS = 1000        # a receipt: not a weight, a constraint
PUBLISHED_POINTS = 120
TRUST_VERIFIED_POINTS = 40
TRUST_SCANNED_POINTS = 20
ARTIFACT_POINTS = 25      # the real ZIP is attached
PROOF_FIELD_POINTS = 25   # each publisher-written proof field
PROOF_FIELD_CAP = 4
REMIX_CHILD_POINTS = 30
REMIX_CHILD_CAP = 3
RECENTLY_UPDATED_POINTS = 25
RECENT_WINDOW_DAYS = 30   # "still working on it" means touched within a month
ORIGINAL_POINTS = 12
FILE_POINTS = 0.5
FILE_CAP = 40

REVIEW_POINTS, REVIEW_CAP = 6, 3
STAR_POINTS, STAR_CAP = 2, 5
VIEW_POINTS, VIEW_CAP = 0.01, 500
MAX_POPULARITY = (REVIEW_POINTS * REVIEW_CAP) + (STAR_POINTS * STAR_CAP) + (VIEW_POINTS * VIEW_CAP)

PROOF_FIELDS = (
    ('problem_statement', 'the problem it solves'),
    ('human_did', 'what you did by hand'),
    ('ai_got_wrong', 'where the AI got it wrong'),
    ('remix_changed', 'what your remix changed'),
)

def _paid_counts(project) -> tuple[int, int]:
    try:
        return (Trade.objects.filter(project=project).count(),
                Sale.objects.filter(project=project).count())
    except Exception:
        return (0, 0)

def _remix_children(project) -> int:
    try:
        return AppProject.objects.filter(forked_from=project).count()
    except Exception:
        return 0

def score_keeper(project, all_projects=None) -> tuple[float, list[str]]:
    """(score, human sentences) for one candidate build.

    Every point has a sentence, because a number the owner cannot read is not an
    explanation — it is a shrug with arithmetic. The sentences go into
    AttentionCandidate.reasons and into the rationale, so the same words that
    justified the score are the words the owner reads.
    """
    from django.utils.timesince import timesince

    now = timezone.now()
    points = 0.0
    reasons: list[str] = []
    peers = list(all_projects or [])

    trades, sales = _paid_counts(project)
    if trades or sales:
        points += PAID_POINTS
        parts = []
        if trades:
            parts.append(f'{trades} star trade{"s" if trades != 1 else ""}')
        if sales:
            parts.append(f'{sales} paid sale{"s" if sales != 1 else ""}')
        who = ' and '.join(parts)
        # One receipt reads singular ("1 paid sale exists for it"); two kinds of
        # receipt together read plural. Getting this wrong is the sort of small
        # sloppiness that makes a machine's explanation sound like a machine.
        verb = 'exists' if (trades + sales) == 1 else 'exist'
        reasons.append(
            f'{who[0].upper()}{who[1:]} {verb} for it — deleting this copy would break a buyer’s '
            'receipt, so it can never be the one that goes.')

    if project.status == 'published':
        points += PUBLISHED_POINTS
        unpublished = [p for p in peers if p.pk != project.pk and p.status != 'published']
        if unpublished:
            reasons.append(
                f'It is the published copy; the other one is “{unpublished[0].get_status_display().lower()}”, '
                'so it is not on the feed at all.')
        else:
            reasons.append('It is published and on the feed.')

    trust = getattr(project, 'trust', '') or ''
    if trust == 'verified':
        points += TRUST_VERIFIED_POINTS
        reasons.append('Its scan came back verified — the strongest trust badge on the platform.')
    elif trust == 'scanned':
        points += TRUST_SCANNED_POINTS
        reasons.append('Its scan came back clean (scanned).')

    paths = tree_paths(project)
    if project.zip_file:
        points += ARTIFACT_POINTS
        reasons.append(f'The real artifact is attached: a ZIP with {project.file_count or len(paths)} file(s).')

    present = [label for field, label in PROOF_FIELDS if (getattr(project, field, '') or '').strip()]
    if present:
        points += PROOF_FIELD_POINTS * min(len(present), PROOF_FIELD_CAP)
        reasons.append('You wrote proof for it: ' + ', '.join(present) + '.')

    children = _remix_children(project)
    if children:
        points += REMIX_CHILD_POINTS * min(children, REMIX_CHILD_CAP)
        reasons.append(
            f'{children} builder{"s" if children != 1 else ""} remixed it — deleting it would orphan their lineage.')

    if project.review_count:
        points += REVIEW_POINTS * min(project.review_count, REVIEW_CAP)
        # Only quote the average when there is one. review_count is a cached
        # tally and avg_rating is only refreshed when a review lands, so a
        # count that outran its average would otherwise print "9 reviews
        # (0.0★ average)" — a sentence that contradicts itself on the page
        # where we are asking somebody to trust our arithmetic.
        average = float(project.avg_rating or 0)
        rating = f' ({average:g}★ average)' if average else ''
        reasons.append(f'{project.review_count} review{"s" if project.review_count != 1 else ""}{rating}.')
    if project.stars:
        points += STAR_POINTS * min(project.stars, STAR_CAP)
        reasons.append(f'{project.stars} star{"s" if project.stars != 1 else ""} — capped, so popularity can tie-break but never decide.')
    if project.views:
        points += VIEW_POINTS * min(project.views, VIEW_CAP)
        reasons.append(f'{project.views} view{"s" if project.views != 1 else ""} (capped).')

    # "The copy you were still working on" is a comparison, not a calendar
    # check. Both halves of a duplicate were usually touched this month, and
    # paying both of them for it would tell the owner that each one is THE
    # recent copy — the same claim twice, deciding nothing. So among peers only
    # the freshest touch earns it; with no peers to compare against (one broken
    # build scored on its own) the window itself is the question.
    cut = now - timedelta(days=RECENT_WINDOW_DAYS)
    if len(peers) > 1:
        touched = [p for p in peers if p.updated_at and p.updated_at >= cut]
        # Equal timestamps fall back to the lowest id, so two copies touched in
        # the same second still produce the same keeper on every run.
        freshest = max(touched, key=lambda p: (p.updated_at, -p.pk), default=None)
        is_recent = freshest is not None and freshest.pk == project.pk
    else:
        is_recent = bool(project.updated_at and project.updated_at >= cut)
    if is_recent:
        points += RECENTLY_UPDATED_POINTS
        reasons.append(f'It is the copy you were still working on — updated {timesince(project.updated_at, now)} ago.')

    if len(peers) > 1:
        oldest = min(peers, key=lambda p: (p.created_at or now, p.pk))
        if oldest.pk == project.pk:
            points += ORIGINAL_POINTS
            reasons.append('It is the original upload of the set, so it holds the history.')

    if paths:
        points += FILE_POINTS * min(len(paths), FILE_CAP)
    return round(points, 2), reasons

def rank_candidates(case) -> list[dict]:
    """Score every live candidate in a case, best first. Deterministic
    tie-break: highest score, then earliest upload, then lowest id — two runs
    of the same facts must produce the same keeper, or the "why" is fiction."""
    rows = []
    live = [c for c in case.candidates.select_related('project') if c.project is not None]
    projects = [c.project for c in live]
    for candidate in live:
        score, reasons = score_keeper(candidate.project, all_projects=projects)
        rows.append({
            'candidate': candidate,
            'project': candidate.project,
            'score': score,
            'reasons': reasons,
        })
    rows.sort(key=lambda r: (-r['score'], r['project'].created_at or timezone.now(), r['project'].pk))
    return rows

def explain_choice(case, ranked: list[dict]) -> str:
    """The strategic explanation shown verbatim to the owner.

    Structure, on purpose: WHAT was kept → WHY (their facts, best first) →
    WHAT THE RULE IS (so the choice is predictable, not arbitrary) → WHAT
    HAPPENS NEXT (nothing destroyed yet, and the two buttons).
    """
    if not ranked:
        return ''
    winner = ranked[0]
    project = winner['project']
    lines = [f'BlaqVibes kept “{project.title}” ({describe_age(project)}).']
    losers = [r for r in ranked[1:]]
    if losers:
        parked = ', '.join(f'“{r["project"].title}” ({describe_age(r["project"])})' for r in losers)
        lines.append(f'Parked: {parked}.')
    lines.append('')
    lines.append('Why this one:')
    for i, reason in enumerate(winner['reasons'][:6], start=1):
        lines.append(f'{i}. {reason}')
    if not winner['reasons']:
        lines.append('1. It scored highest on the rule below — the copies were otherwise identical, so the earliest upload holds the history.')
    if losers:
        lines.append('')
        lines.append('Score: ' + ' vs '.join(f'{r["project"].title} {r["score"]:g}' for r in ranked) + '.')
    lines.append('')
    lines.append('The rule, in order:')
    for i, rule in enumerate(KEEPER_STRATEGY, start=1):
        lines.append(f'{i}. {rule}')
    lines.append('')
    lines.append(
        f'Nothing is erased yet. The parked copy is off the public site but still yours — '
        f'FINAL DELETE erases it for good, LET ME CHOOSE puts the decision back in your hands.'
    )
    return '\n'.join(lines)

def malfunction_verdict(case) -> tuple[str, float, list[str]]:
    """For a broken build the question is not "which copy" but "is this worth
    your time to fix?". Same philosophy: evidence first, popularity capped.

    Returns ('keep'|'drop', score, reasons).
    """
    project = case.subject
    if project is None:
        return 'drop', 0.0, ['The build no longer exists, so there is nothing to fix.']
    score, reasons = score_keeper(project, all_projects=[project])
    keep_reasons: list[str] = []
    trades, sales = _paid_counts(project)
    if trades or sales:
        keep_reasons.append('Somebody paid for it — a receipt is a promise you keep, so the platform will not delete it for you.')
    if _remix_children(project):
        keep_reasons.append('Other builders remixed it, so its lineage is part of their proof too.')
    if project.review_count or project.stars:
        keep_reasons.append(f'People responded to it ({project.review_count} reviews, {project.stars} stars) — worth repairing rather than replacing.')
    if project.status == 'published':
        keep_reasons.append('It is live on the feed right now, so fixing it repairs proof you already have.')
    # 60 points ≈ "one real dependency on this build exists". Below that the
    # honest answer is that a broken, unnoticed, unpaid build is cheaper to
    # re-upload clean than to repair.
    verdict = 'keep' if (keep_reasons or score >= 60) else 'drop'
    if verdict == 'drop':
        keep_reasons = [
            'Nothing depends on it yet: no trades, no sales, no remixes, no reviews.',
            'A clean re-upload is faster than repairing it, and it would earn a fresh scan.',
        ]
    return verdict, score, keep_reasons or reasons

# ----------------------------------------------------------------------
# Decisions
# ----------------------------------------------------------------------
def _locked_case(case) -> AttentionCase:
    return AttentionCase.objects.select_for_update().get(pk=case.pk)

def decide(case, keeper_project=None, actor=None, source: str = 'user') -> dict:
    """Choose the keeper and park everything else. The only path into 'decided'.

    `source='user'` → the owner clicked, so they have seen the decision: the
    24h final-delete clock starts now.
    `source='system'` → the platform decided; the clock starts when the owner
    first OPENS it (with a longer backstop if they never do).
    """
    now = timezone.now()
    with transaction.atomic():
        locked = _locked_case(case)
        if locked.status not in ('open', 'decided'):
            return {'ok': False, 'message': f'This case is already {locked.get_status_display().lower()}.'}

        # ONE queryset, and every mutation below happens on THESE objects. A
        # second query here would hand the rationale writer a stale copy of each
        # candidate's role, and the "why" would describe a decision that was not
        # the one just made.
        live = [c for c in locked.candidates.select_related('project') if c.project is not None]
        if not live:
            return {'ok': False, 'message': 'There is nothing left to decide on.'}
        projects = [c.project for c in live]
        scored = []
        for candidate in live:
            score, reasons = score_keeper(candidate.project, all_projects=projects)
            scored.append({'candidate': candidate, 'project': candidate.project,
                           'score': score, 'reasons': reasons})
        scored.sort(key=lambda row: (-row['score'], row['project'].created_at or now, row['project'].pk))

        keeper_row = None
        if keeper_project is not None:
            keeper_row = next((row for row in scored if row['project'].pk == keeper_project.pk), None)
            if keeper_row is None:
                return {'ok': False, 'message': 'That build is not one of the builds in this case.'}
        elif locked.kind != 'malfunction':
            # A duplicate always needs a keeper — parking every copy would be a
            # deletion wearing a decision's clothes. A malfunction may be
            # dropped outright ("delete it"), which is what keeper=None means.
            return {'ok': False, 'message': 'Pick which copy to keep.'}

        for row in scored:
            row['candidate'].score = row['score']
            row['candidate'].reasons = row['reasons'][:8]
            row['candidate'].save(update_fields=['score', 'reasons'])
        locked.scores = [
            {'title': r['project'].title, 'slug': r['project'].slug,
             'score': r['score'], 'reasons': r['reasons'][:8]}
            for r in scored
        ]

        parked = []
        locked.keeper = keeper_row['project'] if keeper_row else None
        for row in scored:
            candidate = row['candidate']
            if keeper_row is not None and candidate.pk == keeper_row['candidate'].pk:
                candidate.role = 'keeper'
                candidate.outcome = 'kept'
                candidate.save(update_fields=['role', 'outcome'])
                continue
            park_project(candidate.project)
            snapshot = candidate.snapshot if isinstance(candidate.snapshot, dict) else {}
            # The status it had BEFORE the park, so "let me choose" can put it
            # back exactly where it was — restoring a pending build as
            # 'published' would be a silent publish nobody asked for.
            snapshot['status_before_park'] = snapshot.get('status') or candidate.project.status
            candidate.snapshot = snapshot
            candidate.role = 'dropped'
            candidate.outcome = 'parked'
            candidate.parked_at = now
            candidate.save(update_fields=['role', 'outcome', 'parked_at', 'snapshot'])
            parked.append(candidate)

        locked.status = 'decided'
        locked.decision_source = source
        locked.decided_at = now
        locked.dismissed_reason = ''
        # The rationale is written from the same numbers either way, but the
        # shape depends on who decided and what was decided:
        #   owner + broken build kept  → "you said you'll fix it"
        #   owner + anything else      → "you kept X", with the evidence attached
        #   system + broken build      → keep-or-reupload verdict, with the score
        #   system + duplicate         → the full strategic explanation
        if source == 'user' and locked.kind == 'malfunction' and not parked:
            locked.rationale = _keep_rationale(locked)
        elif source == 'user':
            locked.rationale = _user_rationale(locked, scored, keeper_row, parked)
        elif locked.kind == 'malfunction':
            verdict, score, reasons = malfunction_verdict(locked)
            locked.rationale = _malfunction_rationale(locked, verdict, score, reasons)
        else:
            locked.rationale = explain_choice(locked, scored)
        if parked:
            # The clock. A user decision is acknowledged by definition (they
            # clicked); a system decision waits to be seen, with a backstop so
            # an unopened one does not sit in limbo forever.
            locked.acknowledged_at = now if source == 'user' else locked.acknowledged_at
            deadline = now + timedelta(hours=final_delete_hours()) if source == 'user' \
                else now + timedelta(days=unopened_backstop_days())
            locked.final_delete_at = deadline
        else:
            locked.final_delete_at = None
        locked.save()
        case = locked

    notify_case(case)
    return {
        'ok': True,
        'case': case,
        'parked': len(parked),
        'message': _decision_message(case),
    }

def _user_rationale(case, scored, keeper_row, parked) -> str:
    """The record of a choice the OWNER made — with the platform's evidence
    attached, so a human decision is as auditable as a machine one."""
    lines = []
    if keeper_row is not None:
        project = keeper_row['project']
        lines.append(f'You kept “{project.title}” ({describe_age(project)}).')
    else:
        lines.append('You chose to remove this build.')
    if parked:
        lines.append('Parked: ' + ', '.join(
            f'“{c.project.title if c.project else c.title}”'
            f' ({describe_age(c.project) if c.project else "already deleted"})'
            for c in parked
        ) + '.')
        lines.append('')
        lines.append('For the record, here is what the evidence said about your pick:')
        for reason in (keeper_row['reasons'][:4] if keeper_row else []):
            lines.append(f'• {reason}')
        if keeper_row and scored and scored[0]['candidate'].pk != keeper_row['candidate'].pk:
            lines.append(
                f'• BlaqVibes would have kept “{scored[0]["project"].title}” '
                f'({scored[0]["score"]:g} points vs {keeper_row["score"]:g}). Your call wins — '
                'the platform ranks evidence, it cannot know intent.'
            )
        lines.append('')
        lines.append(
            f'Nothing is erased yet. FINAL DELETE removes the parked cop'
            f'{"y" if len(parked) == 1 else "ies"} for good; if you open this and do nothing for '
            f'{final_delete_hours()} hours, BlaqVibes treats that as your go-ahead.'
        )
    return '\n'.join(lines)

def _keep_rationale(case) -> str:
    """The owner said "keep it, I'll fix it" about a broken build."""
    project = case.subject
    title = project.title if project else 'this build'
    lines = [
        f'You kept “{title}” and said you will fix it.',
        'Nothing was removed and nothing is on a clock.',
    ]
    if case.fix_hint:
        lines.append('')
        lines.append(f'How to fix it: {case.fix_hint}')
    lines.append('')
    lines.append('This case stays closed — BlaqVibes will not ask you about the same problem twice.')
    return '\n'.join(lines)

def _malfunction_rationale(case, verdict: str, score: float, reasons: list[str]) -> str:
    project = case.subject
    title = project.title if project else 'this build'
    if verdict == 'keep':
        head = f'BlaqVibes did NOT delete “{title}” — it decided this one is worth fixing.'
    else:
        head = f'BlaqVibes parked “{title}” — it decided a clean re-upload beats a repair.'
    lines = [head, '', 'Why:']
    for i, reason in enumerate(reasons[:5], start=1):
        lines.append(f'{i}. {reason}')
    lines.append('')
    lines.append(f'Score {score:g} (60+ means something real depends on this build).')
    if case.fix_hint:
        lines.append('')
        lines.append(f'How to fix it: {case.fix_hint}')
    if verdict == 'drop':
        lines.append('')
        lines.append(
            f'Nothing is erased yet. FINAL DELETE removes it for good; if you open this and do nothing for '
            f'{final_delete_hours()} hours, BlaqVibes treats that as your go-ahead.'
        )
    else:
        lines.append('')
        lines.append('Nothing was removed. Fix it and this case closes itself the next time the sweep runs.')
    return '\n'.join(lines)

def _decision_message(case) -> str:
    if case.kind == 'malfunction' and not case.dropped:
        return 'Decision recorded — nothing was removed.'
    kept = case.keeper.title if case.keeper else 'your pick'
    n = len(case.dropped)
    return f'Kept “{kept}”. {n} parked cop{"y" if n == 1 else "ies"} — off the public site, restorable until you press FINAL DELETE.'

def delegate(case, actor=None) -> dict:
    """"Let BlaqVibes decide" — the system picks now, with the strategy written
    down. Same code path as the 7-day expiry, so the answer cannot differ
    depending on whether the owner asked or the clock ran out."""
    if case.kind == 'malfunction':
        verdict, score, reasons = malfunction_verdict(case)
        if verdict == 'keep':
            return decide(case, keeper_project=case.subject, actor=actor, source='system')
        return decide(case, keeper_project=None, actor=actor, source='system')
    ranked = rank_candidates(case)
    if not ranked:
        return {'ok': False, 'message': 'There is nothing left to decide on.'}
    return decide(case, keeper_project=ranked[0]['project'], actor=actor, source='system')

def expiry_due_queryset(now=None, limit: int | None = None):
    """The open cases whose decision window has run out, soonest first.

    Its own function because two callers need the SAME answer: the sweep that
    decides them, and `manage.py attention_sweep --dry-run` that reports what a
    sweep would do. A second copy of this filter in a management command is a
    second place for the promise to drift.
    """
    now = now or timezone.now()
    due = AttentionCase.objects.filter(status='open', expires_at__lte=now).order_by('expires_at')
    return due[:limit] if limit else due


def expire_due(now=None, limit: int = 200) -> int:
    """7 days of silence → the platform decides. This is the promise the
    notification made on day one, so it is enforced by the same clock it quoted."""
    now = now or timezone.now()
    due = expiry_due_queryset(now=now, limit=limit)
    decided = 0
    for case in due:
        try:
            result = delegate(case)
            if result.get('ok'):
                decided += 1
        except Exception:
            logger.exception('auto-decision failed case=%s', case.pk)
    return decided

def reopen(case, actor=None) -> dict:
    """"LET ME CHOOSE" — take the decision back after the platform made it.

    Restores every parked copy first, because a choice made between a live
    build and a deleted one is not a choice. The delete clock is cleared, the
    case goes back to 'open', and the 7-day window restarts from now so the
    owner gets a full window to make the call they just asked for.
    """
    now = timezone.now()
    with transaction.atomic():
        locked = _locked_case(case)
        if locked.status not in ('decided',):
            return {'ok': False, 'message': f'This case is {locked.get_status_display().lower()} — there is nothing to choose again.'}
        restored = 0
        for candidate in locked.candidates.select_related('project'):
            if candidate.outcome != 'parked' or candidate.project is None:
                continue
            before = (candidate.snapshot or {}).get('status_before_park') or 'pending'
            restore_project(candidate.project, before)
            candidate.role = ''
            candidate.outcome = 'restored'
            candidate.restored_at = now
            candidate.save(update_fields=['role', 'outcome', 'restored_at'])
            restored += 1
        locked.status = 'open'
        locked.keeper = None
        locked.decision_source = ''
        locked.rationale = ''
        locked.decided_at = None
        locked.acknowledged_at = None
        locked.final_delete_at = None
        locked.expires_at = now + timedelta(days=decision_days())
        locked.reminded_at = None
        locked.save()
        evidence = locked.evidence if isinstance(locked.evidence, dict) else {}
        evidence['reopened_at'] = _iso(now)
        evidence['reopened_restored'] = restored
        locked.evidence = evidence
        locked.save(update_fields=['evidence', 'updated_at'])
        case = locked
    notify_case(case)
    return {'ok': True, 'case': case, 'restored': restored,
            'message': f'Decision cancelled — {restored} build(s) put back. Pick the keeper yourself.'}

def dismiss(case, actor=None, reason: str = 'not_a_duplicate') -> dict:
    """"Keep both" / "I'll fix it" — the owner overrules the detector.

    Recorded, never re-litigated: the pair_key row survives with status
    'dismissed', so the sweep will not reopen the same argument next hour. A
    false positive the owner had to answer twice is worse than a false
    positive they answered once.
    """
    allowed = {'not_a_duplicate', 'will_fix'}
    if reason not in allowed:
        reason = 'not_a_duplicate' if case.kind == 'duplicate' else 'will_fix'
    now = timezone.now()
    with transaction.atomic():
        locked = _locked_case(case)
        if locked.status not in ('open', 'decided'):
            return {'ok': False, 'message': f'This case is already {locked.get_status_display().lower()}.'}
        # Anything already parked goes back — dismissing means "leave my work alone".
        for candidate in locked.candidates.select_related('project'):
            if candidate.outcome == 'parked' and candidate.project is not None:
                before = (candidate.snapshot or {}).get('status_before_park') or 'pending'
                restore_project(candidate.project, before)
                candidate.outcome = 'restored'
                candidate.restored_at = now
                candidate.role = ''
                candidate.save(update_fields=['outcome', 'restored_at', 'role'])
        locked.status = 'dismissed'
        locked.dismissed_reason = reason
        locked.final_delete_at = None
        locked.save()
        case = locked
    notify_case(case)
    message = ('Both copies kept. BlaqVibes will not ask about this pair again.'
               if reason == 'not_a_duplicate' else
               'Kept — fix it and the case stays closed.')
    return {'ok': True, 'case': case, 'message': message}

def acknowledge(case) -> bool:
    """The owner has SEEN a decision. Starts (or shortens) the final-delete
    clock to `final_delete_hours()` from now — the 24 hours of silence that
    counts as consent. Returns True when the row changed."""
    if case.status != 'decided' or not case.dropped:
        return False
    now = timezone.now()
    if case.acknowledged_at is None:
        case.acknowledged_at = now
        case.final_delete_at = now + timedelta(hours=final_delete_hours())
        case.save(update_fields=['acknowledged_at', 'final_delete_at', 'updated_at'])
        notify_case(case)
        return True
    # Already seen: never LENGTHEN the deadline (that would reward ignoring it
    # a second time), but do shorten it if the earlier backstop was longer.
    deadline = now + timedelta(hours=final_delete_hours())
    if case.final_delete_at is None or case.final_delete_at > deadline:
        case.final_delete_at = deadline
        case.save(update_fields=['final_delete_at', 'updated_at'])
        return True
    return False

def final_delete(case, candidate, actor=None) -> dict:
    """FINAL DELETE — the only hard-delete button, and it is the owner's.

    Money-aware through lifecycle.remove_project: if anybody ever paid for this
    build the erase stops at 'removed' and the owner is told why, because a
    purchase is a receipt that outlives the seller's tidiness.
    """
    if candidate.case_id != case.pk:
        return {'ok': False, 'message': 'That build is not part of this case.'}
    if candidate.outcome != 'parked' or candidate.project is None:
        return {'ok': False, 'message': 'There is nothing left to delete.'}
    title = candidate.title
    outcome = remove_project(candidate.project)
    now = timezone.now()
    candidate.outcome = 'deleted' if outcome == 'deleted' else 'parked'
    candidate.deleted_at = now if outcome == 'deleted' else None
    if outcome != 'deleted':
        # Money moved, so lifecycle refused the erase. Record WHY on the row the
        # owner is looking at, or the button reads as broken instead of honest.
        snapshot = candidate.snapshot if isinstance(candidate.snapshot, dict) else {}
        snapshot['cannot_erase'] = 'paid'
        candidate.snapshot = snapshot
    candidate.save(update_fields=['outcome', 'deleted_at', 'snapshot'])
    if outcome == 'deleted':
        candidate.project = None
        candidate.save(update_fields=['project'])
    remaining = case.candidates.filter(outcome='parked', project__isnull=False).count()
    if remaining == 0:
        case.status = 'deleted'
        case.deleted_at = now
        case.final_delete_at = None
        case.save(update_fields=['status', 'deleted_at', 'final_delete_at', 'updated_at'])
    notify_case(case)
    if outcome == 'deleted':
        return {'ok': True, 'message': f'“{title}” is deleted for good.'}
    return {'ok': True, 'message': (
        f'“{title}” is off the public site, but it cannot be erased: somebody paid for it, '
        'and their download is their receipt.'
    )}

def delete_due(now=None, limit: int = 100) -> int:
    """The 24 hours of silence after the owner opened a decision.

    This is the only place the platform erases anything without a click, and it
    is bounded on three sides: a decision must exist, it must have been SEEN
    (acknowledged_at) or outlived a longer unopened backstop, and money always
    stops the erase at 'removed'.
    """
    now = now or timezone.now()
    due = erase_due_queryset(now=now, limit=limit)
    erased = 0
    for case in due:
        for candidate in list(case.candidates.select_related('project')):
            if candidate.outcome != 'parked' or candidate.project is None:
                continue
            try:
                result = final_delete(case, candidate)
                if result.get('ok') and 'deleted for good' in result.get('message', ''):
                    erased += 1
            except Exception:
                logger.exception('final delete failed case=%s candidate=%s', case.pk, candidate.pk)
        case.refresh_from_db()
        if case.status == 'decided':
            # Something could not be erased (a paid receipt). Stop asking: the
            # case is finished as far as the platform is concerned.
            case.final_delete_at = None
            case.status = 'deleted'
            case.deleted_at = now
            case.save(update_fields=['status', 'deleted_at', 'final_delete_at', 'updated_at'])
            notify_case(case)
    return erased

# ----------------------------------------------------------------------
# Notifications — one pinned row per case, bumped on the 30-minute cadence.
# ----------------------------------------------------------------------
def countdown(case, now=None) -> str:
    """The deadline in words: '7 days left to decide', '23h left to press FINAL
    DELETE', 'overdue to decide'.

    Days round UP and hours round DOWN, on purpose: a case opened this morning
    must read "7 days left", not "6 days" — the copy promised seven, and a
    countdown that starts one short of its own promise reads as a bug. Hours
    round down for the same honesty in the other direction: "23h left" when 23h
    50m remain is a deadline the owner can still meet.
    """
    now = now or timezone.now()
    if case.status == 'open':
        target, noun = case.expires_at, 'to decide'
    elif case.status == 'decided' and case.final_delete_at:
        target, noun = case.final_delete_at, 'to press FINAL DELETE'
    else:
        return ''
    if not target:
        return ''
    delta = target - now
    if delta <= timedelta(0):
        return f'overdue {noun}'
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 60:
        return f'{total_minutes} min left {noun}'
    hours = total_minutes // 60
    if hours < 48:
        return f'{hours}h left {noun}'
    days = delta.days + (1 if delta.seconds else 0)
    return f'{days} days left {noun}'

def _sentence(text: str) -> str:
    """First letter up, and nothing else touched.

    `str.capitalize()` also LOWERCASES the rest of the string, which quietly
    turned the deadline into "23h left to press final delete" — an inbox row
    naming a button that does not exist on the page it links to. Sentence case
    is a one-character operation; there is no reason for it to rewrite labels.
    """
    return (text[:1].upper() + text[1:]) if text else text


def notification_copy(case, now=None) -> tuple[str, str]:
    """(title, body) for the ONE pinned inbox row, in the case's current state.

    The body is written compact on purpose. `Notification.body` is 400 chars,
    and the reminder marker has to survive truncation — a body that spends its
    budget on the long evidence sentence would push "(reminder 12)" off the end,
    and the marker is the part that tells the owner this is a nudge rather than
    a new problem. The full evidence lives on the case page, which is what the
    row links to.
    """
    now = now or timezone.now()
    clock = countdown(case, now=now)
    if case.status == 'open':
        if case.kind == 'duplicate':
            copies = case.candidates.count()
            title = f'{case.headline} — pick which one to keep'
            # Words, not digits, for the counts a person actually hits — the
            # headline says "Two copies of X", so the sentence under it must
            # not say "2 of your builds" in the same breath.
            how_many = _COUNT_WORDS.get(copies, str(copies))
            body = (
                f'{_sentence(clock)}. {how_many} of your builds look like the same one. '
                'Pick the keeper, or let BlaqVibes pick and write down why.'
            )
        else:
            title = case.headline
            body = f'{_sentence(clock)}. {case.fix_hint or case.detail}'
    elif case.status == 'decided':
        kept = case.keeper.title if case.keeper else 'nothing'
        who = 'BlaqVibes kept' if case.decision_source == 'system' else 'You kept'
        title = f'{who} “{kept}”'
        first = next(
            (line.strip()[2:].strip() for line in (case.rationale or '').split('\n')
             if line.strip().startswith('1.')),
            '',
        )
        body = first[:200] or case.detail[:200]
        if case.final_delete_at:
            body = f'{body} {_sentence(clock)}.'
            if case.acknowledged_at:
                body = f'{body} Then the parked cop{("y is" if len(case.dropped) == 1 else "ies are")} erased.'
        else:
            body = f'{body} Nothing was removed.'
    elif case.status == 'deleted':
        title = f'Cleaned up: {case.headline}'
        body = 'The parked copy is gone for good. Nothing else needs you.'
    elif case.status == 'restored':
        title = f'Put back: {case.headline}'
        body = 'Both builds are live again. BlaqVibes will not ask about this pair again.'
    else:
        title = f'Closed: {case.headline}'
        body = case.detail
    if case.remind_count and case.status == 'open':
        body = f'{body[:360]} (reminder {case.remind_count})'
    return title[:200], body[:400]

def notify_case(case) -> object:
    """Create or bump the ONE notification for this case. Colour = severity, so
    a critical case stripes red in the inbox and sorts above the social noise."""
    try:
        existing = case.notifications.order_by('-id').first()
        title, body = notification_copy(case)
        url = case.get_absolute_url()
        if existing is not None:
            return redeliver(existing, title=title, body=body, category=case.severity)
        return notify(case.user, case.kind, title, body, url,
                      category=case.severity, attention_case=case)
    except Exception:
        logger.exception('notify_case failed case=%s', case.pk)
        return None

def reminder_due_queryset(now=None, limit: int | None = None):
    """The cases a reminder pass would nudge, in the order it would nudge them.

    Shared with the dry run for the same reason as `expiry_due_queryset`: the
    report an operator reads before a sweep has to be the sweep.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(minutes=reminder_minutes())
    due = (
        AttentionCase.objects
        .filter(
            Q(status='open') | Q(status='decided', acknowledged_at__isnull=True),
            Q(reminded_at__isnull=True, created_at__lte=cutoff) | Q(reminded_at__lte=cutoff),
        )
        .order_by('reminded_at', 'created_at')
    )
    return due[:limit] if limit else due


def erase_due_queryset(now=None, limit: int | None = None):
    """The decided cases whose grace period has run out, soonest first."""
    now = now or timezone.now()
    due = (
        AttentionCase.objects
        .filter(status='decided', final_delete_at__isnull=False, final_delete_at__lte=now)
        .order_by('final_delete_at')
    )
    return due[:limit] if limit else due


def remind_due(now=None, limit: int = 500) -> int:
    """Re-surface every unanswered case whose last nudge is older than the
    reminder interval (30 minutes by default).

    Reminding stops the moment the owner has SEEN a decision: from then on the
    24-hour clock is running, the banner counts it down, and another unread
    bump every half hour would be noise on top of a deadline they already know
    about.
    """
    now = now or timezone.now()
    due = reminder_due_queryset(now=now, limit=limit)
    nudged = 0
    for case in due:
        try:
            case.remind_count = (case.remind_count or 0) + 1
            case.reminded_at = now
            case.save(update_fields=['remind_count', 'reminded_at', 'updated_at'])
            notify_case(case)
            nudged += 1
        except Exception:
            logger.exception('reminder failed case=%s', case.pk)
    return nudged

def sweep(now=None, limit: int | None = None) -> dict:
    """One pass of everything the clock owes: detect → expire → remind → erase.

    The order matters. Detection has to run before expiry so a case opened by
    this very pass is not immediately decided by it, and the erase runs last so
    a decision made by expiry in the same pass cannot also be erased by it.
    """
    now = now or timezone.now()
    result = {'detected': {}, 'expired': 0, 'reminded': 0, 'erased': 0}
    if not enabled():
        return result
    try:
        result['detected'] = detect(limit=limit, now=now)
    except Exception:
        logger.exception('attention detect failed')
    try:
        result['expired'] = expire_due(now=now)
    except Exception:
        logger.exception('attention expire failed')
    try:
        result['reminded'] = remind_due(now=now)
    except Exception:
        logger.exception('attention remind failed')
    try:
        result['erased'] = delete_due(now=now)
    except Exception:
        logger.exception('attention delete failed')
    return result

# ----------------------------------------------------------------------
# Read helpers for the surfaces (banner, inbox, attention centre, JS poll).
# ----------------------------------------------------------------------
def summary(user) -> dict:
    """One aggregate query for the site-wide banner + context processor."""
    empty = {'open': 0, 'critical': 0, 'awaiting_delete': 0, 'oldest_days': 0,
             'next_deadline': None, 'due_reminder': False,
             'reminder_seconds': reminder_minutes() * 60}
    if user is None or not getattr(user, 'is_authenticated', False) or not enabled():
        return empty
    try:
        now = timezone.now()
        rows = (
            AttentionCase.objects
            .filter(user=user, status__in=('open', 'decided'))
            .aggregate(
                open_cases=Count('id', filter=Q(status='open')),
                critical=Count('id', filter=Q(status='open', severity='critical')),
                awaiting=Count('id', filter=Q(status='decided', final_delete_at__isnull=False)),
                oldest=Max('created_at'),
                deadline=Max('expires_at', filter=Q(status='open')),
                reminded=Max('reminded_at'),
            )
        )
        oldest_days = 0
        if rows['oldest']:
            oldest_days = max(0, (now - rows['oldest']).days)
        due_reminder = False
        last = rows['reminded'] or rows['oldest']
        if last:
            due_reminder = last <= now - timedelta(minutes=reminder_minutes())
        return {
            'open': rows['open_cases'] or 0,
            'critical': rows['critical'] or 0,
            'awaiting_delete': rows['awaiting'] or 0,
            'oldest_days': oldest_days,
            'next_deadline': rows['deadline'],
            'due_reminder': due_reminder,
            # The browser polls on this cadence, so it comes from the same
            # setting the server-side reminder uses — one number, two clocks.
            'reminder_seconds': reminder_minutes() * 60,
        }
    except Exception:
        logger.exception('attention summary failed')
        return empty

def severity_legend() -> list[dict]:
    """The five stripes, in the order the inbox sorts them — rendered as a
    legend so the colour is never the only way to read the inbox."""
    return [dict(meta, **{'swatch': meta['swatch']}) for meta in
            (category_meta(key) for key in ('critical', 'action', 'money', 'social', 'system'))]
