from django.db.models import Q, F, Case, When, Value, IntegerField
from django.db import connection

# 5 Whys Search v3 — Why very well? 1k vibes, typo "dashbord", ranking by title > stack > readme, trending + rank bonus.
# Postgres: SearchVector + TrigramSimilarity + GIN. SQLite: scored icontains with title weight.

def search_projects(qs, q, sort='newest'):
    # Base sort if no query
    if not q:
        if sort == 'stars':
            return qs.order_by('-stars', '-created_at')
        if sort == 'clones':
            return qs.order_by('-clones', '-created_at')
        if sort == 'trending':
            # Include rank bonus for trending: clones*3 + stars + bonus (Bronze 0, Silver 5, Gold 15, Platinum 30)
            # We annotate bonus via python later if needed; here simple
            return qs.order_by('-stars', '-clones', '-created_at')
        return qs.order_by('-created_at')

    q = q.strip()
    q_lower = q.lower()
    terms = [t for t in q_lower.split() if len(t) >= 2]

    # Postgres path — full-text + trigram
    if connection.vendor == 'postgresql':
        try:
            from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank, TrigramSimilarity
            vector = SearchVector('title', weight='A') + SearchVector('short_description', weight='B') + SearchVector('tech_stack', weight='B') + SearchVector('readme', weight='C')
            query = SearchQuery(q, search_type='plain')
            # Trigram for typo
            qs = qs.annotate(
                rank=SearchRank(vector, query),
                sim_title=TrigramSimilarity('title', q),
                sim_stack=TrigramSimilarity('tech_stack', q),
            ).filter(Q(rank__gte=0.1) | Q(sim_title__gt=0.2) | Q(sim_stack__gt=0.2))
            # Combined score: rank*2 + trigram
            qs = qs.annotate(combined=F('rank')*2 + F('sim_title') + F('sim_stack'))
            # Apply sort
            if sort == 'trending':
                qs = qs.order_by('-combined', '-stars')
            elif sort == 'stars':
                qs = qs.order_by('-combined', '-stars')
            else:
                qs = qs.order_by('-combined', '-created_at')
            if qs.exists():
                return qs
        except Exception as e:
            pass

    # Fallback: SQLite/MySQL — scored icontains (very well without Postgres)
    # Score: title 10, short_description 5, tech_stack 5, readme 1, tag 3
    # We annotate via Case/When, then order by score
    scored = qs.annotate(
        score_title=Case(When(title__icontains=q, then=Value(10)), default=Value(0), output_field=IntegerField()),
        score_short=Case(When(short_description__icontains=q, then=Value(5)), default=Value(0), output_field=IntegerField()),
        score_stack=Case(When(tech_stack__icontains=q, then=Value(5)), default=Value(0), output_field=IntegerField()),
        score_readme=Case(When(readme__icontains=q, then=Value(1)), default=Value(0), output_field=IntegerField()),
    ).annotate(score=F('score_title')+F('score_short')+F('score_stack')+F('score_readme'))

    # Term-level: each term adds
    for term in terms:
        scored = scored.filter(Q(title__icontains=term) | Q(short_description__icontains=term) | Q(tech_stack__icontains=term) | Q(readme__icontains=term) | Q(tags__name__icontains=term) | Q(category__name__icontains=term))

    # Distinct because tags join
    scored = scored.distinct()
    # Rank: score desc, then sort
    if sort == 'stars':
        scored = scored.order_by('-score', '-stars', '-created_at')
    elif sort == 'clones':
        scored = scored.order_by('-score', '-clones', '-created_at')
    elif sort == 'trending':
        scored = scored.annotate(trending=F('clones')*3 + F('stars')).order_by('-score', '-trending', '-created_at')
    else:
        scored = scored.order_by('-score', '-created_at')

    # Typo fallback: if no results, try contains per term with OR (not AND)
    if not scored.exists() and terms:
        q_or = Q()
        for term in terms:
            q_or |= Q(title__icontains=term) | Q(tech_stack__icontains=term)
        fallback = qs.filter(q_or).distinct().order_by('-stars')
        if fallback.exists():
            return fallback
        # SQLite typo: difflib close match on title words (e.g., dashbord -> dashboard)
        import difflib, re
        from .models import AppProject as _AppProject
        all_text = " ".join(_AppProject.objects.filter(status='published').values_list('title', flat=True)) + " " + " ".join(_AppProject.objects.filter(status='published').values_list('tech_stack', flat=True))
        words = set(re.findall(r'\w+', all_text.lower()))
        close_terms = set()
        for term in terms:
            matches = difflib.get_close_matches(term, words, n=3, cutoff=0.7)
            close_terms.update(matches)
        if close_terms:
            q_close = Q()
            for ct in close_terms:
                q_close |= Q(title__icontains=ct) | Q(tech_stack__icontains=ct) | Q(short_description__icontains=ct)
            fallback2 = qs.filter(q_close).distinct().order_by('-stars')
            if fallback2.exists():
                return fallback2

    return scored
