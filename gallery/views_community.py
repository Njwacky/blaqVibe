"""Community features: Nolo, PRs, battles, deploys, challenges.

Split out of views.py so the feed/publish/payment core stays readable.
URLs still resolve via gallery.views (re-exported), so urls.py is unchanged.
"""
import json
import logging
import random

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import F, Q, Count, Sum
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .models import (
    AppProject, Category, PullRequest, VibeBattle, BattleVote,
    Challenge, AppFile, AppVersion, ScanJob,
)
from .notify import notify

logger = logging.getLogger(__name__)

@login_required
@require_POST
def nolo_compare(request):
    """Nolo doesn't judge, just compares — backend only, crush silently."""
    try:
        from .prompt_sanitize import sanitize_prompt
        from .nolo import compare_apps
        a_slug = sanitize_prompt(request.POST.get('a_slug',''))[:100]
        b_slug = sanitize_prompt(request.POST.get('b_slug',''))[:100]
        if not a_slug or not b_slug:
            return JsonResponse({'error': 'Need two vibes'}, status=400)
        a = get_object_or_404(AppProject, slug=a_slug, status='published')
        b = get_object_or_404(AppProject, slug=b_slug, status='published')
        result = compare_apps(a, b)
        return JsonResponse(result)
    except Exception as e:
        # crush silently — log, return safe error
        import logging
        logging.getLogger(__name__).exception(f"nolo compare crush: {e}")
        return JsonResponse({'error': 'Compare failed silently', 'a':{},'b':{},'diff':{}}, status=500)

@ensure_csrf_cookie
def nolo_chat(request):
    """Nolo chat page with help content built into the chat experience."""
    recent_apps = AppProject.objects.filter(status='published').order_by('-created_at')[:8]
    categories = Category.objects.all().order_by('order')
    return render(request, 'gallery/nolo_chat.html', {
        'recent_apps': recent_apps,
        'categories': categories,
    })

@require_POST
@ratelimit(key='ip', rate='20/h', method='POST')
def nolo_chat_api(request):
    try:
        if getattr(request, 'limited', False):
            return JsonResponse({'error': 'Too many questions. Try again later.'}, status=429)
        data = json.loads(request.body.decode('utf-8') or '{}')
        from .prompt_sanitize import sanitize_prompt
        prompt = sanitize_prompt(data.get('prompt', '').strip())[:1000]
        if not prompt:
            return JsonResponse({'error': 'Ask Nolo a question first.'}, status=400)
        from .nolo_ai import get_nolo_ai_answer
        answer, source = get_nolo_ai_answer(prompt)
        return JsonResponse({'reply': answer, 'source': source})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"nolo chat api crush: {e}")
        return JsonResponse({'error': 'Nolo could not answer right now. Try again soon.'}, status=500)

def nolo_help(request):
    return redirect('nolo_chat')

@login_required
@ratelimit(key='user', rate='5/h', method='POST')
def create_pr(request, slug):
    """Create Pull Request from fork to its original — backend checks forked_from."""
    try:
        if getattr(request, 'limited', False):
            messages.error(request, "Rate limit: 5 PRs/hour")
            return redirect('app_detail', slug=slug)
        source = get_object_or_404(AppProject, slug=slug, owner=request.user)
        if not source.forked_from:
            messages.error(request, "Only forked vibes can create PR")
            return redirect(source.get_absolute_url())
        target = source.forked_from
        if not getattr(target.owner.profile, 'allow_prs', True):
            messages.error(request, "This creator disabled pull requests.")
            return redirect(source.get_absolute_url())
        # Must be open PR not already open for this source
        if PullRequest.objects.filter(source=source, target=target, status='open').exists():
            messages.info(request, "PR already open for this fork")
            return redirect(target.get_absolute_url())
        from .prompt_sanitize import sanitize_prompt
        from .profanity import PUBLIC_LANGUAGE_ERROR, contains_profanity
        title = sanitize_prompt(request.POST.get('title',''))[:200] or f"PR: {source.title} → {target.title}"
        description = sanitize_prompt(request.POST.get('description',''))[:2000]
        if contains_profanity(title) or contains_profanity(description):
            messages.error(request, PUBLIC_LANGUAGE_ERROR)
            return redirect(source.get_absolute_url())
        pr = PullRequest.objects.create(source=source, target=target, author=request.user, title=title, description=description, status='open')
        notify(target.owner, 'pr', f'@{request.user.username} opened PR #{pr.id} on {target.title}', title, f'/app/{target.slug}/prs/{pr.id}/view/')
        try:
            if target.owner.email:
                site = getattr(settings, 'SITE_URL', 'https://blaqvibes.co.za')
                send_mail(f"New PR for {target.title}", f"@{request.user.username} wants to merge {source.slug} into {target.slug}:\n{title}\n{description}\nView: {site}/app/{target.slug}/prs/", getattr(settings, 'DEFAULT_FROM_EMAIL','noreply@blaqvibes.co.za'), [target.owner.email], fail_silently=True)
        except Exception: pass
        messages.success(request, f"✓ PR #{pr.id} opened — {source.slug} → {target.slug}")
        return redirect('pr_list', slug=target.slug)
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"create_pr crush: {e}")
        messages.error(request, "PR failed silently")
        return redirect('app_detail', slug=slug)

def pr_list(request, slug):
    """List PRs for a vibe — open/merged/closed, backend only."""
    try:
        target = get_object_or_404(AppProject, slug=slug)
        prs = PullRequest.objects.filter(target=target).select_related('source','author').order_by('-created_at')
        return render(request, 'gallery/pr_list.html', {'target': target, 'prs': prs})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"pr_list crush: {e}")
        return render(request, 'gallery/pr_list.html', {'target': get_object_or_404(AppProject, slug=slug), 'prs': PullRequest.objects.none()})

def pr_detail(request, slug, pr_id):
    """PR diff — real content diff of ZIPs plus Nolo feature compare."""
    try:
        target = get_object_or_404(AppProject, slug=slug)
        pr = get_object_or_404(PullRequest, id=pr_id, target=target)
        from .diff import diff_projects
        diff = diff_projects(pr.source, pr.target)
        # Nolo diff
        from .nolo import compare_apps
        nolo_diff = compare_apps(pr.source, pr.target)['diff']
        nolo_review = (pr.source.scan_report or {}).get('nolo_review')
        return render(request, 'gallery/pr_detail.html', {'pr': pr, 'target': target, 'diff': diff, 'nolo_diff': nolo_diff, 'nolo_review': nolo_review})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"pr_detail crush: {e}")
        return render(request, 'gallery/pr_detail.html', {'pr': get_object_or_404(PullRequest, id=pr_id), 'target': get_object_or_404(AppProject, slug=slug), 'diff': {'added':[],'removed':[],'modified':[],'unchanged':[],'added_count':0,'removed_count':0,'modified_count':0,'common_count':0}, 'nolo_diff': {'only_in_a':[],'only_in_b':[],'common':[]}, 'nolo_review': None})

@login_required
@require_POST
def pr_action(request, slug, pr_id):
    """Merge or close — only target owner can merge/close."""
    try:
        target = get_object_or_404(AppProject, slug=slug)
        pr = get_object_or_404(PullRequest, id=pr_id, target=target)
        if request.user != target.owner and not request.user.profile.is_admin():
            return render(request, '403.html', status=403)
        action = request.POST.get('action')
        if action == 'merge':
            from django.core.files.base import ContentFile
            from .tasks import process_upload_pipeline
            source = pr.source
            if target.zip_file:
                try:
                    AppVersion.objects.create(
                        project=target,
                        zip_file=target.zip_file,
                        version=f"1.{target.versions.count()+1}.0",
                        changelog=f"Before merge of PR #{pr.id}",
                    )
                except Exception:
                    logger.exception('pr version snapshot failed')
            if source.zip_file:
                source.zip_file.open()
                target.zip_file.save(f"{target.slug}.zip", ContentFile(source.zip_file.read()), save=True)
            target.file_tree = source.file_tree or {}
            target.file_count = source.file_count
            if source.readme:
                target.readme = source.readme
            # Merged PR replaces the target's bytes — reset the trust badge
            # with the same write (gallery.trust WHY 4). The re-queued scan
            # re-earns it from the merged content only.
            try:
                from .trust import invalidate_trust
                invalidate_trust(target, save=False)
            except Exception:
                pass
            target.status = 'pending'
            target.save()
            target.files.all().delete()
            for af in source.files.all():
                AppFile.objects.create(project=target, path=af.path, size=af.size)
            job, _ = ScanJob.objects.get_or_create(project=target)
            job.status = 'queued'
            job.save(update_fields=['status'])
            try:
                process_upload_pipeline.delay(target.id)
            except Exception:
                logger.exception('pr rematch scan queue failed')
            pr.status = 'merged'
            pr.save(update_fields=['status','updated_at'])
            notify(pr.author, 'pr', f'PR #{pr.id} merged into {target.title}', url=target.get_absolute_url())
            messages.success(request, f"✓ PR #{pr.id} merged — files copied from fork, re-queued for scan.")
        elif action == 'close':
            pr.status = 'closed'
            pr.save(update_fields=['status','updated_at'])
            messages.info(request, f"PR #{pr.id} closed")
        return redirect('pr_list', slug=slug)
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"pr_action crush: {e}")
        return redirect('pr_list', slug=slug)

def battle(request):
    """Vibe Battles — two random vibes side-by-side, crush silently."""
    try:
        from .models import VibeBattle, AppProject
        import random
        # Pick 2 random published vibes, not same, exclude own if logged in
        qs = AppProject.objects.filter(status='published')
        if qs.count() < 2:
            return render(request, 'gallery/battle.html', {'battle': None})
        # Try to find a battle not voted by this user
        if request.user.is_authenticated:
            voted_ids = request.user.battle_votes.values_list('battle_id', flat=True)
            available = VibeBattle.objects.exclude(id__in=voted_ids).order_by('-created_at')[:5]
            if available.exists():
                battle = available.first()
                return render(request, 'gallery/battle.html', {'battle': battle})
        vibes = list(qs.order_by('?')[:2])
        if len(vibes) < 2:
            vibes = list(qs[:2])
        a, b = vibes[0], vibes[1]
        existing = VibeBattle.objects.filter(
            Q(vibe_a=a, vibe_b=b) | Q(vibe_a=b, vibe_b=a)
        ).first()
        if existing:
            return render(request, 'gallery/battle.html', {'battle': existing})
        battle = VibeBattle.objects.create(vibe_a=a, vibe_b=b)
        return render(request, 'gallery/battle.html', {'battle': battle})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"battle crush: {e}")
        return render(request, 'gallery/battle.html', {'battle': None})

def battle_leaderboard(request):
    try:
        from .models import VibeBattle, AppProject
        from django.db.models import Count, F
        # Top vibes by battle wins — count wins per vibe
        # Simple: top by stars (battle wins already +5 stars) + battle_wins annotation
        # Compute wins per vibe via Python (at scale, use annotation)
        vibes = list(AppProject.objects.filter(status='published').order_by('-stars')[:10])
        # Annotate battle_wins for display
        for v in vibes:
            wins = VibeBattle.objects.filter(vibe_a=v, votes_a__gt=F('votes_b')).count() + VibeBattle.objects.filter(vibe_b=v, votes_b__gt=F('votes_a')).count()
            v.battle_wins = wins
        vibes = sorted(vibes, key=lambda x: getattr(x, 'battle_wins', 0), reverse=True)[:10]
        from django.contrib.auth.models import User
        from django.db.models import Sum
        from .ranks import contributor_bonus
        users = list(
            User.objects.annotate(
                rank_stars=Sum('projects__stars', filter=Q(projects__status='published')),
                vibes_count=Count('projects', filter=Q(projects__status='published')),
            ).order_by(F('rank_stars').desc(nulls_last=True))[:10]
        )
        for u in users:
            u.rank_stars = u.rank_stars or 0
            u.rank = contributor_bonus(u)
        return render(request, 'gallery/battle_leaderboard.html', {'top_vibes': vibes, 'top_users': users})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"leaderboard crush: {e}")
        return render(request, 'gallery/battle_leaderboard.html', {'top_vibes': [], 'top_users': []})

def battle_history(request):
    try:
        from .models import VibeBattle, BattleVote
        my_votes = []
        recent = VibeBattle.objects.order_by('-created_at')[:10]
        if request.user.is_authenticated:
            my_votes = BattleVote.objects.filter(user=request.user).select_related('battle__vibe_a__owner','battle__vibe_b__owner').order_by('-created_at')[:20]
        return render(request, 'gallery/battle_history.html', {'my_votes': my_votes, 'recent': recent})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"battle_history crush: {e}")
        return render(request, 'gallery/battle_history.html', {'my_votes': [], 'recent': []})

@login_required
@require_POST
@ratelimit(key='user', rate='30/h', method='POST')
def vote_battle(request, battle_id):
    try:
        from .models import VibeBattle, BattleVote
        from django.db.models import F
        if getattr(request, 'limited', False):
            messages.error(request, "Rate limit: too many votes.")
            return redirect('battle')
        battle = get_object_or_404(VibeBattle, id=battle_id)
        if BattleVote.objects.filter(user=request.user, battle=battle).exists():
            messages.info(request, "You already voted on this battle")
            return redirect('battle')
        # Cannot vote on own vibe
        if battle.vibe_a.owner == request.user or battle.vibe_b.owner == request.user:
            messages.error(request, "Can't vote on your own vibe")
            return redirect('battle')
        choice = request.POST.get('choice')
        if choice not in ('a','b'):
            return redirect('battle')
        BattleVote.objects.create(user=request.user, battle=battle, choice=choice)
        if choice == 'a':
            VibeBattle.objects.filter(pk=battle.pk).update(votes_a=F('votes_a')+1)
        else:
            VibeBattle.objects.filter(pk=battle.pk).update(votes_b=F('votes_b')+1)
        messages.success(request, "Voted! Battle score updated — project stars are unchanged.")
        return redirect('battle')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"vote crush: {e}")
        return redirect('battle')

def run_vibe(request, slug):
    """Send people to the honest preview. Not a Docker host."""
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if project.html_code:
        return redirect('preview', slug=slug)
    if project.zip_file:
        return redirect('preview_files', slug=slug)
    messages.error(request, "Nothing to preview — no snippet or files.")
    return redirect(project.get_absolute_url())

# deploy_view removed with the Deploy model. 5 Whys: Why kill the route?
# It promised a live deployment and delivered a redirect — a lie in the URL
# space. Old /deploy/<token>/ links now 404 honestly.

@require_POST
@ratelimit(key='ip', rate='20/h', method='POST')
def copy_increment(request, slug):
    """One copy count per session per published vibe. Not a leaderboard.

    5 Whys:
    1. Why not a naked F()+1? Anyone can POST /copy/ in a loop.
    2. Why session not IP-only? IP rate limits rotate; a session is the
       browser that actually copied.
    3. Why ignore the owner? Self-copies would be the first farm.
    4. Why published only? A pending slug must not be confirmable this way.
    5. Why keep the endpoint? The snippet Copy button already calls it;
       dropping the stat would lie in the UI. Cap it instead.
    """
    if getattr(request, 'limited', False):
        return JsonResponse({'ok': True, 'ignored': 'limited'}, status=429)
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if request.user.is_authenticated and request.user.pk == project.owner_id:
        return JsonResponse({'ok': True, 'ignored': 'owner'})
    key = f'copied:{project.pk}'
    if request.session.get(key):
        return JsonResponse({'ok': True, 'ignored': 'already'})
    request.session[key] = True
    request.session.modified = True
    AppProject.objects.filter(pk=project.pk).update(copies=F('copies') + 1)
    return JsonResponse({'ok': True})

def challenge_list(request):
    from .models import Challenge
    from django.utils import timezone
    challenges = Challenge.objects.all().order_by('-start')
    active = Challenge.objects.filter(is_active=True, start__lte=timezone.now(), end__gte=timezone.now()).first()
    return render(request, 'gallery/challenge_list.html', {'challenges': challenges, 'active': active})

@login_required
@require_POST
def generate_challenges(request):
    # Superadmin only — AI drafts 3 challenges, deduped, is_active=False
    try:
        if not request.user.profile.is_superadmin():
            return render(request, '403.html', status=403)
        from .challenge_ai import create_draft_challenges
        created = create_draft_challenges()
        from django.contrib import messages
        messages.success(request, f"AI drafted {len(created)} challenges — approve one to make it live!")
        return redirect('challenge_list')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"generate_challenges crush: {e}")
        return redirect('challenge_list')

@login_required
@require_POST
def approve_challenge(request, tag):
    # Superadmin approves draft → is_active=True
    try:
        if not request.user.profile.is_superadmin():
            return render(request, '403.html', status=403)
        from .models import Challenge
        ch = get_object_or_404(Challenge, tag=tag)
        ch.is_active = True
        ch.save(update_fields=['is_active'])
        from django.contrib import messages
        messages.success(request, f"Challenge '{ch.title}' is now live!")
        return redirect('challenge_detail', tag=tag)
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"approve_challenge crush: {e}")
        return redirect('challenge_list')

def challenge_detail(request, tag):
    from .models import Challenge
    from gallery.models import AppProject
    challenge = get_object_or_404(Challenge, tag=tag)
    submissions = AppProject.objects.filter(tags__slug=challenge.tag, status='published').select_related('owner').order_by('-created_at')
    # Also include pending for owner/admin view?
    if request.user.is_authenticated and (request.user.profile.is_admin() if hasattr(request.user, 'profile') else False):
        submissions = AppProject.objects.filter(tags__slug=challenge.tag).select_related('owner').order_by('-created_at')
    return render(request, 'gallery/challenge_detail.html', {'challenge': challenge, 'submissions': submissions})

@login_required
@require_POST
def pick_challenge_winner(request, tag):
    from .challenges import PRO_PRIZE_DAYS, ChallengeAwardError, award_challenge_winner
    from .models import Challenge
    if not request.user.profile.is_admin():
        return render(request, '403.html', status=403)
    challenge = get_object_or_404(Challenge, tag=tag)
    winner_id = request.POST.get('winner_id')
    if not winner_id:
        messages.error(request, 'Pick a submission first.')
        return redirect('challenge_detail', tag=tag)
    winner = get_object_or_404(AppProject, id=winner_id)
    try:
        award_challenge_winner(challenge, winner, actor=request.user)
    except ChallengeAwardError as exc:
        messages.error(request, exc.message)
        return redirect('challenge_detail', tag=tag)
    messages.success(
        request,
        f'Winner picked: {winner.title} by @{winner.owner.username} — '
        f'+{challenge.bounty_stars} ★ + Pro {PRO_PRIZE_DAYS} days.',
    )
    return redirect('challenge_detail', tag=tag)
