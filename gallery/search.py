from django.db.models import Q, F, Case, When, Value, IntegerField
from django.db import connection


def search_projects(qs, q, sort='newest', user=None):
    """Filter + order the feed.

    `user` is only consulted for sort='foryou'.

    5 Whys — why does personalisation live inside search_projects instead
    of being applied by the view afterwards?
    1. Because ordering must be part of the queryset the Paginator slices;
       reordering after pagination would only shuffle one page.
    2. Because 'for you' has to compose with a text query — searching
       "platformer" as a game-lover should still favour games.
    3. Because every caller of the feed (feed view, and any future one)
       then gets the same ordering rules for free.
    4. Because the view already passes `sort` here; a second ordering hook
       elsewhere would make "which one wins?" ambiguous.
    5. Because the fall-through is explicit: an unknown or unsupported sort
       lands on the same '-created_at' it always did.
    """
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
            if qs.exists():
                return qs
        except Exception as e:
            pass

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

    if not scored.exists() and terms:
        q_or = Q()
        for term in terms:
            q_or |= Q(title__icontains=term) | Q(tech_stack__icontains=term)
        fallback = qs.filter(q_or).distinct().order_by('-stars')
        if fallback.exists():
            return fallback
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
