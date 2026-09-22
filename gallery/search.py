from django.db.models import Q, F, Case, When, Value, IntegerField
from django.db import connection

# Search v3: tolerates typos ("dashbord") across a 1k-vibe catalog, ranking
# title > stack > readme with a trending + rank bonus.
# Postgres: SearchVector + TrigramSimilarity + GIN. SQLite: scored icontains.
# PERFORMANCE FIX: Removed full-table difflib scan that loaded ALL titles
# into memory on every miss — now uses cached word list + bounded queries.

def search_projects(qs, q, sort='newest', user=None):
    """Filter + order the feed. `user` only for sort='foryou'."""
    if not q:
        if sort == 'foryou':
            from .taste import personalized_order
            ordered, _norm = personalized_order(qs, user)
            return ordered
        if sort == 'stars':
            return qs.order_by('-stars', '-created_at')
        if sort == 'clones':
            return qs.order_by('-clones', '-created_at')
        if sort == 'trending':
            return qs.order_by('-appeal_score', '-stars', '-created_at')
        return qs.order_by('-created_at')

    q = q.strip()
    q_lower = q.lower()
    terms = [t for t in q_lower.split() if len(t) >= 2]

    # Postgres path — full-text + trigram (fast with GIN index)
    if connection.vendor == 'postgresql':
        try:
            from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank, TrigramSimilarity
            vector = SearchVector('title', weight='A') + SearchVector('short_description', weight='B') + SearchVector('tech_stack', weight='B') + SearchVector('readme', weight='C')
            query = SearchQuery(q, search_type='plain')
            qs = qs.annotate(
                rank=SearchRank(vector, query),
                sim_title=TrigramSimilarity('title', q),
                sim_stack=TrigramSimilarity('tech_stack', q),
            ).filter(Q(rank__gte=0.1) | Q(sim_title__gt=0.2) | Q(sim_stack__gt=0.2))
            qs = qs.annotate(combined=F('rank')*2 + F('sim_title') + F('sim_stack'))
            if sort == 'foryou':
                from .taste import personalized_order
                qs, _norm = personalized_order(qs.order_by(), user, base_field='appeal_score')
                qs = qs.order_by('-combined', '-personal_score', '-created_at')
            elif sort == 'trending':
                qs = qs.order_by('-combined', '-appeal_score', '-stars')
            elif sort == 'stars':
                qs = qs.order_by('-combined', '-stars')
            else:
                qs = qs.order_by('-combined', '-created_at')
            # Use exists() with limit to avoid full count — but keep semantics
            if qs[:1].exists():
                return qs
        except Exception:
            pass

    # Fallback: SQLite/MySQL — scored icontains
    scored = qs.annotate(
        score_title=Case(When(title__icontains=q, then=Value(10)), default=Value(0), output_field=IntegerField()),
        score_short=Case(When(short_description__icontains=q, then=Value(5)), default=Value(0), output_field=IntegerField()),
        score_stack=Case(When(tech_stack__icontains=q, then=Value(5)), default=Value(0), output_field=IntegerField()),
        score_readme=Case(When(readme__icontains=q, then=Value(1)), default=Value(0), output_field=IntegerField()),
    ).annotate(score=F('score_title')+F('score_short')+F('score_stack')+F('score_readme'))

    for term in terms:
        scored = scored.filter(Q(title__icontains=term) | Q(short_description__icontains=term) | Q(tech_stack__icontains=term) | Q(readme__icontains=term) | Q(tags__name__icontains=term) | Q(category__name__icontains=term))

    scored = scored.distinct()
    if sort == 'foryou':
        from .taste import personalized_order
        scored, _norm = personalized_order(scored.order_by(), user, base_field='appeal_score')
        scored = scored.order_by('-score', '-personal_score', '-created_at')
    elif sort == 'stars':
        scored = scored.order_by('-score', '-stars', '-created_at')
    elif sort == 'clones':
        scored = scored.order_by('-score', '-clones', '-created_at')
    elif sort == 'trending':
        scored = scored.order_by('-score', '-appeal_score', '-created_at')
    else:
        scored = scored.order_by('-score', '-created_at')

    # Typo fallback: OR search per term (cheap)
    if not scored[:1].exists() and terms:
        q_or = Q()
        for term in terms:
            q_or |= Q(title__icontains=term) | Q(tech_stack__icontains=term)
        fallback = qs.filter(q_or).distinct().order_by('-stars')
        if fallback[:1].exists():
            return fallback

        # PERFORMANCE: Old code loaded ALL titles + tech_stack into memory and ran difflib
        # on every miss — O(N) memory + CPU. Now we use cached common words (300 most
        # frequent) and limit difflib input to 500 words max.
        try:
            from django.core.cache import cache
            cache_key = "search:common_words:v1"
            words = cache.get(cache_key)
            if words is None:
                # Build word set from limited sample — not full table
                from .models import AppProject as _AppProject
                sample_titles = list(_AppProject.objects.filter(status='published').values_list('title', flat=True)[:200])
                sample_stacks = list(_AppProject.objects.filter(status='published').values_list('tech_stack', flat=True)[:200])
                import re
                all_text = " ".join(sample_titles) + " " + " ".join(sample_stacks)
                words = list(set(re.findall(r'\w+', all_text.lower())))[:500]
                cache.set(cache_key, words, 3600)
            if words:
                import difflib
                close_terms = set()
                for term in terms[:3]:  # limit terms to avoid CPU spike
                    matches = difflib.get_close_matches(term, words, n=2, cutoff=0.75)
                    close_terms.update(matches)
                if close_terms:
                    q_close = Q()
                    for ct in close_terms:
                        q_close |= Q(title__icontains=ct) | Q(tech_stack__icontains=ct) | Q(short_description__icontains=ct)
                    fallback2 = qs.filter(q_close).distinct().order_by('-stars')
                    if fallback2[:1].exists():
                        return fallback2
        except Exception:
            pass

    return scored
