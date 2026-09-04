"""Trust badge — turn scan evidence into a public verdict nobody can fake.
The public surface is three tiers, chosen because the majority of vibe

    verified  — virus-scanned clean, no leaked secrets, dependencies checked
    scanned   — went through the pipeline, at least one check incomplete
    unknown   — no complete evidence (pending, quarantined, removed, legacy)

each Why carries 4 points. Any point that fails has a documented
fallback approach: the design degrades, it never breaks, and it never lies.

WHY 2 — Why is the grade STORED on the row (not computed per render)?
WHY 3 — Why is the scan pipeline the ONLY writer (apply_trust_grade)?
WHY 4 — Why does ANY content change reset the badge to unknown?
WHY 5 — Why does the grade feed the ranking (small boost, ≤ 8%)?
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

# Tiers — the entire public vocabulary of the badge.
TRUST_VERIFIED = 'verified'
TRUST_SCANNED = 'scanned'
TRUST_UNKNOWN = 'unknown'

TRUST_TIERS = (TRUST_VERIFIED, TRUST_SCANNED, TRUST_UNKNOWN)

TRUST_CHOICES = [
    (TRUST_VERIFIED, 'Verified — virus clean, no secrets, deps checked'),
    (TRUST_SCANNED, 'Scanned — pipeline ran, some checks incomplete'),
    (TRUST_UNKNOWN, 'Unknown — no complete scan evidence'),
]

# Fixed presentation table. Server-rendered only; nothing in here is ever
# user-supplied, so a creator cannot style or spoof their way to a ✓.
TRUST_META = {
    TRUST_VERIFIED: {
        'label': 'Checked',
        'icon': '🛡️',
        'title': 'Virus-scanned clean · no leaked secrets · dependencies checked',
        'css': 'background:var(--success-bg,#DCFCE7);color:var(--success-text,#166534)',
    },
    TRUST_SCANNED: {
        'label': 'Scanned',
        'icon': '🛡️',
        'title': 'Went through the scan pipeline — some checks were incomplete',
        'css': 'background:var(--input);border:1px solid var(--line);color:var(--muted)',
    },
    TRUST_UNKNOWN: {
        'label': '',
        'icon': '',
        'title': '',
        'css': '',
    },
}

# Ranking multiplier per tier. Verified may reorder equals (+8%); it can
# never out-rank genuinely better content. See WHY 5 above.
TRUST_MULTIPLIER = {
    TRUST_VERIFIED: 1.08,
    TRUST_SCANNED: 1.03,
    TRUST_UNKNOWN: 1.00,
}

# Keys whose presence proves the pipeline produced evidence for this row.
# 4 points: (1) whitelist → future report keys can't fake "pipeline ran";
# (2) each key is written by a different pipeline step, so any one proves
# the chain reached this project; (3) all are backend-written, none can be
# set through a form; (4) if a key is renamed, absence degrades to
# 'unknown' — a missing badge, never a wrong one.
_EVIDENCE_KEYS = ('clamav', 'secrets', 'nolo_review', 'dep_audit', 'snippet_scan')

def _pipeline_ran(report):
    """True if any known pipeline step wrote evidence into the report."""
    try:
        return any(k in (report or {}) for k in _EVIDENCE_KEYS)
    except Exception:
        return False

def _virus_check(project, report):
    """(ok, reason_key). Snippets have no ZIP — nothing to virus-scan."""
    try:
        if not getattr(project, 'zip_file', None):
            return True, 'snippet_no_zip'
        clamav = (report or {}).get('clamav')
        if clamav == 'clean':
            return True, 'clean'
        if clamav == 'disabled':
            # Site operator turned ClamAV off — honest tier is 'scanned',
            # never 'verified': we did NOT check, so we do not claim it.
            return False, 'scanner_disabled'
        return False, 'scanner_missing'
    except Exception:
        return False, 'scanner_missing'

def _secrets_check(project, report):
    """(ok, reason_key). ZIPs use the pipeline's secrets scan; snippets are
    regex-checked live against the same SECRET_PATTERNS — pure and cheap,
    so a snippet can honestly earn 'verified' too."""
    try:
        if getattr(project, 'zip_file', None):
            found = (report or {}).get('secrets') or []
            return (not found), ('clean' if not found else 'secrets_found')
        # Snippet: check the stored code fields directly. Import here so
        # this module never imports models/validators at load time
        # (models.py imports this module — circular otherwise).
        from .validators import SECRET_PATTERNS
        for field in ('js_code', 'html_code', 'css_code'):
            text = getattr(project, field, '') or ''
            for pat in SECRET_PATTERNS:
                if pat.search(text):
                    return False, 'secrets_found'
        return True, 'clean'
    except Exception:
        return False, 'check_failed'

def _deps_check(project, report):
    """(ok, reason_key). Uses evidence the vuln task writes:

    dep_audit = {'ran': bool, 'reason': 'ok'|'no_manifests'|'tool_missing'|'snippet_no_deps'}

    plus any vulnerable packages the audits found (npm/pip lists) and any
    hallucinated-package flags a future dep-check step may add
    ('unknown_deps' — slopsquatting defence).
    """
    try:
        rep = report or {}
        if not getattr(project, 'zip_file', None):
            # Snippet: no manifest can exist, so the check is vacuously
            # true — evidence that the row was examined comes from the
            # 'snippet_scan' key (see snippet_evidence below).
            return True, 'snippet_no_deps'
        audit = rep.get('dep_audit') or {}
        if rep.get('unknown_deps'):
            return False, 'unknown_deps'
        if rep.get('npm') or rep.get('pip'):
            return False, 'vulnerable_deps'
        if audit.get('ran'):
            return True, audit.get('reason') or 'ok'
        # No evidence either way (legacy rows scanned before dep_audit
        # existed): do not claim the check — 'scanned', not 'verified'.
        return False, 'not_audited'
    except Exception:
        return False, 'check_failed'

# Human sentences for the detail page / tooltip "read". Fixed table — a
# reason_key can never inject text, filenames, or secret material.
_REASON_TEXT = {
    'clean': 'Passed',
    'snippet_no_zip': 'Nothing to scan (no ZIP)',
    'snippet_no_deps': 'No installable dependencies',
    'no_manifests': 'No dependency manifest found',
    'ok': 'Passed',
    'scanner_disabled': 'Virus scanner switched off by the operator',
    'scanner_missing': 'Virus scanner unavailable',
    'secrets_found': 'Possible secret detected — held for review',
    'vulnerable_deps': 'Known-vulnerable dependency versions found',
    'unknown_deps': 'Dependency not found on the registry (possible fake package)',
    'not_audited': 'Dependencies not audited',
    'check_failed': 'Check could not complete',
}

_CHECK_ORDER = (('Virus scan', _virus_check), ('Secrets', _secrets_check), ('Dependencies', _deps_check))

def trust_grade(project):
    """Pure tier derivation. Never raises, never writes, never lies.

    published + all three checks ok          → verified
    published + pipeline ran, check incomplete → scanned
    everything else (pending/quarantined/removed/no evidence) → unknown
    """
    try:
        if getattr(project, 'status', None) != 'published':
            return TRUST_UNKNOWN
        report = getattr(project, 'scan_report', None) or {}
        if not _pipeline_ran(report):
            return TRUST_UNKNOWN
        results = [check(project, report) for _, check in _CHECK_ORDER]
        if all(ok for ok, _ in results):
            return TRUST_VERIFIED
        return TRUST_SCANNED
    except Exception:
        logger.exception('trust_grade failed for %s', getattr(project, 'slug', '?'))
        return TRUST_UNKNOWN

def trust_reasons(project):
    """Safe human-readable check results for the detail page.

    4 points: (1) strings come from _REASON_TEXT only — no filenames, no
    secret values, nothing user-typed; (2) order is fixed so the page is
    stable; (3) unknown tiers get [] (nothing to explain, nothing to
    leak); (4) any unrecognised reason_key renders as 'Check could not
    complete' — degrade honestly.
    """
    try:
        if trust_grade(project) == TRUST_UNKNOWN:
            return []
        report = getattr(project, 'scan_report', None) or {}
        out = []
        for name, check in _CHECK_ORDER:
            ok, reason_key = check(project, report)
            out.append({'check': name, 'ok': bool(ok), 'detail': _REASON_TEXT.get(reason_key, _REASON_TEXT['check_failed'])})
        return out
    except Exception:
        return []

def _award_verified_xp(project, tier):
    """One XP grant the first time a vibe proves itself clean.

    Why here? apply_trust_grade is the single writer of the tier, so this
    is the one place where 'verified' can be observed as a fact. The grant
    is ref-keyed to the project, so re-scans, re-grades and repeated task
    runs pay exactly once.
    """
    try:
        if tier != TRUST_VERIFIED:
            return
        owner = getattr(project, 'owner', None)
        if not owner or not getattr(owner, 'pk', None):
            return
        from users.progress import award
        award(owner, 'verified', ref=f'verified:{project.pk}')
    except Exception:
        logger.exception('verified xp failed %s', getattr(project, 'slug', '?'))

def apply_trust_grade(project, save=True):
    """The ONE pipeline writer of the stored grade.

    Monotonic guard: if trust_graded_at on the row is NEWER than now
    (clock skew / out-of-order task), refuse to overwrite — last write is
    by timestamp, not by task finishing order.
    """
    try:
        grade = trust_grade(project)
        now = timezone.now()
        existing = getattr(project, 'trust_graded_at', None)
        if existing and existing > now:
            logger.warning('trust grade write refused (stale clock) for %s', getattr(project, 'slug', '?'))
            return getattr(project, 'trust', TRUST_UNKNOWN)
        project.trust = grade
        project.trust_graded_at = now
        if save:
            project.save(update_fields=['trust', 'trust_graded_at'])
        # Progression rides the verdict, not the view: whichever path got a
        # vibe to 'verified' (pipeline, edit rescan, backfill) pays once.
        _award_verified_xp(project, grade)
        return grade
    except Exception:
        logger.exception('apply_trust_grade failed for %s', getattr(project, 'slug', '?'))
        return getattr(project, 'trust', TRUST_UNKNOWN)

def invalidate_trust(project, save=True, extra_fields=None):
    """Reset to unknown the moment ANY content changes.

    Call sites: edit_vibe (new ZIP/codes), git push, PR merge — every
    place that already sets status='pending'. The badge must never
    vouch for bytes the pipeline has not checked (WHY 4).
    """
    try:
        project.trust = TRUST_UNKNOWN
        project.trust_graded_at = timezone.now()
        if save:
            fields = ['trust', 'trust_graded_at'] + list(extra_fields or [])
            project.save(update_fields=fields)
    except Exception:
        logger.exception('invalidate_trust failed for %s', getattr(project, 'slug', '?'))

def trust_multiplier(tier):
    """Ranking boost per tier (see TRUST_MULTIPLIER / WHY 5). Never raises."""
    try:
        return float(TRUST_MULTIPLIER.get(tier, 1.0))
    except Exception:
        return 1.0

def trust_meta(tier):
    """Presentation row for a tier — fixed table, nothing user-supplied."""
    return TRUST_META.get(tier) or TRUST_META[TRUST_UNKNOWN]

def snippet_evidence(project, save=True):
    """The snippet's scan step — pure, fast, run at publish/review time.
        ZIPs get the queued chain (clamav → secrets → dep audits). Snippets
        never enter the scan queue (a publish is a user waiting on a response,
        and the queue is for subprocess work), so their evidence is produced
        here, in-request.
    """
    try:
        from .validators import SECRET_PATTERNS
        report = dict(getattr(project, 'scan_report', None) or {})
        hits = []
        for field in ('js_code', 'html_code', 'css_code'):
            text = getattr(project, field, '') or ''
            for pat in SECRET_PATTERNS:
                if pat.search(text):
                    # Field NAME only — the token itself is never stored,
                    # never rendered, never logged.
                    hits.append(field)
                    break
        report['snippet_scan'] = {'checked': True, 'secrets_found': bool(hits), 'fields_flagged': hits}
        project.scan_report = report
        if save:
            project.save(update_fields=['scan_report'])
        return apply_trust_grade(project)
    except Exception:
        logger.exception('snippet_evidence failed for %s', getattr(project, 'slug', '?'))
        return getattr(project, 'trust', TRUST_UNKNOWN)
