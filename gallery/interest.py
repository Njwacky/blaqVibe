"""appeal_score — "how interesting is this vibe to a stranger?", 0-100.

This is the *global* half of ranking. `taste.personalized_order` adds the
per-user half on top of it.

5 Whys — why a stored, batch-computed score instead of ordering by stars?

1. Why not `ORDER BY stars`? Stars accumulate forever, so the same handful
   of old vibes own page one permanently. With "tons of people uploading
   every second" that is the whole product failing: nothing new is ever
   seen, so nothing new can ever earn stars — a closed loop.
2. Why not `ORDER BY created_at` then? That is the opposite failure: the
   feed becomes a firehose where a polished game is buried by fifty
   half-finished uploads within a minute.
3. Why blend engagement, quality and freshness with a time decay? Each
   alone is gameable or degenerate; the decay is what guarantees a new
   good upload can out-rank an old good upload without anyone voting.
4. Why compute it in a task and store it, rather than as a query
   annotation? An annotation recomputes logarithms and date arithmetic for
   every row on every page load and cannot be indexed. A stored float can
   be, so the feed stays a range scan as the table grows.
5. Why recompute on a schedule instead of on every interaction? Interactions
   are the highest-volume events on the site; making each one write to a
   ranked, indexed column would turn the hot path into an index-churn
   problem. Staleness of a few minutes is invisible to a browsing user.
"""
import logging
import math

from django.utils import timezone

from .trust import trust_multiplier

logger = logging.getLogger(__name__)

W_ENGAGEMENT = 45.0
W_QUALITY = 35.0
W_RUNNABLE = 10.0
W_LLM = 10.0

FRESHNESS_HALF_LIFE_DAYS = 14.0
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
        if getattr(project, 'is_featured', False):
            base += 8.0
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

    5 Whys — why oldest-first with a limit, not "everything"?
    1. Why a limit? An unbounded pass over a growing table eventually takes
       longer than the interval it runs on, and then it never finishes.
    2. Why oldest-scored first? It is the only ordering that guarantees
       every row is eventually refreshed, no matter how many are added.
    3. Why not only score rows that changed? Freshness decays with the
       clock, not with edits — an untouched row's correct score changes
       every hour on its own.
    4. Why bulk_update? One UPDATE per row would make a 500-row pass 500
       round trips.
    5. Why published only? Nothing else is ever ranked, so scoring it is
       work with no reader.
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
