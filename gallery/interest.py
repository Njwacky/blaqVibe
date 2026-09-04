"""appeal_score — "how interesting is this vibe to a stranger?", 0-100.
This is the *global* half of ranking. `taste.personalized_order` adds the
per-user half on top of it.
"""
import logging
import math

from django.utils import timezone

from .trust import trust_multiplier

logger = logging.getLogger(__name__)

# Weights sum to 100 before the freshness multiplier.
W_ENGAGEMENT = 45.0
W_QUALITY = 35.0
W_RUNNABLE = 10.0
W_LLM = 10.0

# Engagement halves every this many days.
FRESHNESS_HALF_LIFE_DAYS = 14.0
# The floor keeps a genuinely great old vibe discoverable instead of
# decaying it to zero — decay reorders, it does not delete.
FRESHNESS_FLOOR = 0.35

def _log_scale(value, ceiling):
    """0..1, logarithmic. Why log? The 500th star means less than the 5th."""
    try:
        v = max(0.0, float(value or 0))
    except Exception:
        return 0.0
    if v <= 0:
        return 0.0
    return min(1.0, math.log1p(v) / math.log1p(max(1.0, ceiling)))

def engagement_component(project):
    """Weighted, log-scaled interaction signal.

    Trades and downloads count most: they are the actions people spend
    scarce currency on, so they are the hardest to fake.
    """
    try:
        trades = getattr(project, 'trade_count', None)
        if trades is None:
            trades = project.trades.count()
    except Exception:
        trades = 0
    stars = getattr(project, 'stars', 0) or 0
    clones = getattr(project, 'clones', 0) or 0
    views = getattr(project, 'views', 0) or 0
    reviews = getattr(project, 'review_count', 0) or 0

    return (
        0.34 * _log_scale(trades, 50)
        + 0.26 * _log_scale(stars, 200)
        + 0.16 * _log_scale(clones, 100)
        + 0.14 * _log_scale(views, 5000)
        + 0.10 * _log_scale(reviews, 30)
    )

def quality_component(project):
    """Signals that a human put effort in — readable without any traffic.

    Why does this matter? A brand-new upload has zero engagement by
    definition. Without a traffic-free quality signal, nothing new could
    ever out-rank anything, and the cold-start problem would be permanent.
    """
    score = 0.0
    readme = getattr(project, 'readme', '') or ''
    if len(readme) >= 300:
        score += 0.20
    if len(readme) >= 1200:
        score += 0.10
    if '# ' in readme:
        score += 0.05
    if getattr(project, 'thumbnail', None):
        score += 0.15
    if (getattr(project, 'tech_stack', '') or '').strip():
        score += 0.08
    fc = getattr(project, 'file_count', 0) or 0
    if fc >= 5:
        score += 0.10
    if fc >= 20:
        score += 0.05
    if getattr(project, 'language_stats', None):
        score += 0.05
    rating = getattr(project, 'avg_rating', 0) or 0
    if rating:
        score += 0.12 * (float(rating) / 5.0)
    try:
        nolo = (getattr(project, 'scan_report', None) or {}).get('nolo_review') or {}
        nolo_score = float(nolo.get('score') or 0)
        if nolo_score:
            score += 0.10 * min(1.0, nolo_score / 10.0)
    except Exception:
        pass
    return min(1.0, score)

def runnable_component(project):
    """A vibe you can play right now is more interesting than one you can't.

    Why reward this and not punish the rest? The user's rule is that
    everything gets published, including things we cannot preview. A bonus
    for runnable content ranks honestly without hiding the others.
    """
    try:
        if getattr(project, 'can_run_preview', False):
            return 1.0
    except Exception:
        pass
    if (getattr(project, 'html_code', '') or '').strip():
        return 0.8
    if getattr(project, 'zip_file', None):
        return 0.25
    return 0.0

def freshness_multiplier(project, now=None):
    now = now or timezone.now()
    try:
        age_days = max(0.0, (now - project.created_at).total_seconds() / 86400.0)
    except Exception:
        return 1.0
    decay = math.pow(0.5, age_days / FRESHNESS_HALF_LIFE_DAYS)
    return FRESHNESS_FLOOR + (1.0 - FRESHNESS_FLOOR) * decay

def compute_appeal(project, now=None):
    """Return the 0-100 score. Pure — no writes, never raises."""
    try:
        llm_appeal = 0.0
        try:
            stored = (getattr(project, 'scan_report', None) or {}).get('kind_llm') or {}
            llm_appeal = float(stored.get('appeal') or 0) / 100.0
        except Exception:
            llm_appeal = 0.0

        base = (
            W_ENGAGEMENT * engagement_component(project)
            + W_QUALITY * quality_component(project)
            + W_RUNNABLE * runnable_component(project)
            + W_LLM * llm_appeal
        )
        # Featured is an editorial thumb on the scale, not a bypass.
        if getattr(project, 'is_featured', False):
            base += 8.0
        # Trust boost — verified/scanned vibes outrank EQUALS, never betters.
        # 4 points: (1) users rank trust as the deciding signal in 2026 and a
        # ranking that ignores it floats slop above checked work; (2) it is
        # capped at +8% (see TRUST_MULTIPLIER) so a weak verified vibe cannot
        # out-rank a strong unverified one — reorder equals, don't buy rank;
        # (3) it multiplies the base BEFORE freshness, so decay still governs
        # old content identically; (4) the tier is pipeline-written only, so
        # no user action can ever move this number — unfarmable by design.
        base *= trust_multiplier(getattr(project, 'trust', None) or 'unknown')
        score = base * freshness_multiplier(project, now=now)
        return round(max(0.0, min(100.0, score)), 3)
    except Exception:
        logger.exception('compute_appeal failed for %s', getattr(project, 'slug', '?'))
        return 0.0

def refresh_project(project, save=True):
    score = compute_appeal(project)
    if save:
        try:
            project.appeal_score = score
            project.appeal_updated_at = timezone.now()
            project.save(update_fields=['appeal_score', 'appeal_updated_at'])
        except Exception:
            logger.exception('refresh_project save failed')
    return score

def refresh_batch(queryset=None, limit=500):
    """Recompute the oldest-scored published vibes.
    """
    from .models import AppProject
    from django.db.models import Count

    qs = queryset
    if qs is None:
        qs = (AppProject.objects.filter(status='published')
              .order_by(_oldest_scored_first()))
    qs = qs.annotate(trade_count=Count('trades', distinct=True))[:limit]
    rows = list(qs)
    now = timezone.now()
    for p in rows:
        p.appeal_score = compute_appeal(p, now=now)
        p.appeal_updated_at = now
    if rows:
        AppProject.objects.bulk_update(rows, ['appeal_score', 'appeal_updated_at'], batch_size=200)
    return len(rows)

def _oldest_scored_first():
    """Never-scored rows must be picked up before already-scored ones."""
    from django.db.models import F
    return F('appeal_updated_at').asc(nulls_first=True)
