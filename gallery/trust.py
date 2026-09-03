"""Trust badge — turn scan evidence into a public verdict nobody can fake.

The public surface is three tiers, chosen because the majority of vibe
builders are NOT developers and need a verdict, not a metric:

    verified  — virus-scanned clean, no leaked secrets, dependencies checked
    scanned   — went through the pipeline, at least one check incomplete
    unknown   — no complete evidence (pending, quarantined, removed, legacy)

5 WHYS — each Why carries 4 points. Any point that fails has a documented
fallback approach: the design degrades, it never breaks, and it never lies.

WHY 1 — Why a *derived grade* instead of showing scan_report raw?
  1. scan_report is backend-only by design — secret *filenames* and audit
     details live there; a projection can never leak what it does not
     contain. Fails-if: a future key adds sensitive data → the grader
     reads only whitelisted keys, so new keys are invisible to it.
  2. A tier is judgement-free for non-devs ("✓ Checked"), the 63% of the
     vibe market who cannot parse "npm audit: 2 high".
     Fails-if: copy confuses a user → tier text lives in ONE table
     (TRUST_META) editable without touching logic.
  3. A grade is stable vocabulary for templates, the API, and ranking —
     three consumers, one definition. Fails-if: a consumer needs more
     detail → trust_reasons() offers safe sentences, still no raw report.
  4. Raw reports differ per ecosystem (npm/pip/none) — a tier is the only
     honest common denominator. Fails-if: a new ecosystem is added →
     extend _deps_ok(); tiers do not change.

WHY 2 — Why is the grade STORED on the row (not computed per render)?
  1. The feed orders and filters by appeal_score in SQL; a Python-derived
     trust would be invisible to WHERE/ORDER BY (same 5-Whys the kind
     field already answers).
  2. Cards render 12+ vibes per page; re-deriving per card re-runs regex
     scans per page view. Fails-if: a row is stale → the pipeline is the
     only writer and every content change re-queues a scan, bounding
     staleness to the queue depth.
  3. A stored verdict is auditable: trust + trust_graded_at say what was
     decided and when. Fails-if: evidence changes out-of-band →
     invalidate_trust() resets to unknown immediately at the mutation
     site, so the stored value can never describe the previous ZIP.
  4. db_index on trust lets a future "verified only" filter stay a range
     scan. Fails-if: no filter ever ships → an unused index costs a few
     bytes; drop it in a later migration.

WHY 3 — Why is the scan pipeline the ONLY writer (apply_trust_grade)?
  1. Unfarmable: stars can be earned with sockpuppets, but a grade only
     the virus→secrets→vuln→publish chain can write cannot be voted into
     existence. Fails-if: a moderator needs to override → add a moderator
     path that writes kind_source-style provenance, never a user path.
  2. One writer means one place to audit. Fails-if: a second legitimate
     writer appears (e.g. a rescan command) → it must call this same
     function; the code review rule is "no direct trust= writes".
  3. The chain is FIFO on one queue (existing design) so two writers
     cannot race a row; last-write-wins is also chronological.
     Fails-if: queues are ever parallelised → trust_graded_at lets the
     older write refuse to overwrite a newer one (monotonic guard).
  4. Forms/API/admin cannot set it: AppUploadForm uses a field allowlist,
     the API is read-only, and no template ever posts trust.
     Fails-if: someone adds 'trust' to a form → the spoof test
     (test_publish_form_ignores_trust_field) fails the build.

WHY 4 — Why does ANY content change reset the badge to unknown?
  1. A buyer pays stars for the ZIP that was scanned. If a new ZIP could
     inherit the old ✓, the badge would vouch for bytes nobody checked —
     that is how people get robbed. Fails-if: a creator is annoyed by the
     reset → the rescan is automatic and fast; the reset is the price of
     the badge meaning something.
  2. Every mutation path already returns the project to status='pending'
     (edit, git push, PR merge) — invalidate_trust() rides those exact
     sites, so no new scheduling machinery is needed. Fails-if: a new
     mutation path is added → the rule "status='pending' must be written
     through invalidate_trust()" is enforced by docs + review.
  3. Resetting is fail-safe in the other direction too: a crashed rescan
     leaves 'unknown', which renders NO badge — absence is the honest
     signal, never a wrong ✓. Fails-if: the queue is backed up → cards
     show badge-less, the site keeps working, nothing is claimed.
  4. Forks/PR merges create pending rows anyway, so downstream content
     starts unverifed by construction. Fails-if: a fork of a verified
     vibe looks worse in feed → correct: it IS unchecked until rescanned.

WHY 5 — Why does the grade feed the ranking (small boost, ≤ 8%)?
  1. Users told us trust matters (AI-code trust fell 77%→60%); a ranking
     that ignores it shows slop above checked vibes. Fails-if: boost
     overshadows quality → it is capped at +8% so it reorders equals,
     it cannot buy rank (test_asserts verified-worst < unverified-best).
  2. Money safety: the boost applies to ordering only — it never touches
     star_cost, payouts, or trade logic, so it cannot be arbitraged.
     Fails-if: someone tries farm-the-boost → grade is pipeline-written,
     so there is nothing a user action can do to raise it.
  3. It is applied to the base before the freshness multiplier, so a new
     verified vibe beats a new unverified one, and an old great one still
     decays identically. Fails-if: decay must dominate → floor 0.35
     applies after the boost; ordering among old vibes is unchanged.
  4. The multiplier is a pure function of the tier string — no network,
     no IO — so compute_appeal stays pure and testable.
     Fails-if: tiers are retuned → TRUST_MULTIPLIER is one dict beside
     the tiers; tests re-derive expectations from it instead of literals.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Tiers — the entire public vocabulary of the badge.
# --------------------------------------------------------------------------
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
# 5 Whys (4 points) on why a table and not template conditionals:
# 1. One source of truth for web, API label, and emails — three surfaces
#    cannot drift apart if they all read TRUST_META.
# 2. Titles are plain static strings → nothing user-controlled ever enters
#    a title="" attribute (no tooltip injection).
# 3. A test asserts TRUST_META keys == TRUST_TIERS == model choices, so a
#    tier added without copy fails the build, not the user's trust.
# 4. If the fails-if happens (tier renamed), templates keep rendering the
#    old key's copy — degrade to 'unknown' is impossible to miss.
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

    4 points on why this shape:
    1. Pure regex over at most three text fields costs microseconds — no
       subprocess, no LLM, no queue hop on the request path, so the
       existing "snippets never enter the scan queue" rule survives.
       Fails-if: a snippet ever grows heavier checks → move them to the
       queue and leave this function as the evidence marker.
    2. It writes REAL evidence, not a hardcoded pass: a snippet with a
       leaked token records secrets_found=True and the same grader that
       grades ZIPs grades it 'scanned'. One truth for both shapes.
       Fails-if: the grader changes → it reads the same evidence keys,
       so the two cannot drift.
    3. It writes evidence only — the trust FIELD is still written solely
       by apply_trust_grade, which this calls at the end. The writer rule
       (WHY 3) is not weakened: views may produce evidence, never verdicts.
       Fails-if: someone writes project.trust directly from a view →
       that is the bug the spoof/robbery tests exist to catch.
    4. Crush-silently: on any failure no evidence is written, which grades
       'unknown' — no badge. Degrade, never a wrong ✓.
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
