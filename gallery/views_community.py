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
    Deploy, Challenge, AppFile, AppVersion, ScanJob,
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
        answer = get_nolo_ai_answer(prompt)
        return JsonResponse({'reply': answer})
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
        title = sanitize_prompt(request.POST.get('title',''))[:200] or f"PR: {source.title} → {target.title}"
        description = sanitize_prompt(request.POST.get('description',''))[:2000]
        pr = PullRequest.objects.create(source=source, target=target, author=request.user, title=title, description=description, status='open')
        notify(target.owner, 'pr', f'@{request.user.username} opened PR #{pr.id} on {target.title}', title, f'/app/{target.slug}/prs/{pr.id}/view/')
        try:
            if target.owner.email:
                send_mail(f"New PR for {target.title}", f"@{request.user.username} wants to merge {source.slug} into {target.slug}:\n{title}\n{description}\nView: https://blaqvibes.co.za/app/{target.slug}/prs/", getattr(settings, 'DEFAULT_FROM_EMAIL','noreply@blaqvibes.co.za'), [target.owner.email], fail_silently=True)
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

@login_required
@require_POST
def run_vibe(request, slug):
    """Preview — sandboxed snippet or file list. Not a Docker host."""
    try:
        from .models import Deploy
        from django.utils import timezone
        from datetime import timedelta
        import secrets
        project = get_object_or_404(AppProject, slug=slug, status='published')
        if not project.zip_file and not project.html_code:
            messages.error(request, "Nothing to run — no ZIP or snippet")
            return redirect(project.get_absolute_url())
        # One active deploy per user per vibe
        existing = Deploy.objects.filter(project=project, owner=request.user, status='running', expires_at__gt=timezone.now()).first()
        if existing:
            messages.info(request, f"Already running: {existing.live_url} — expires in {(existing.expires_at - timezone.now()).seconds//60} min")
            return redirect(existing.live_url)
        token = f"{project.slug}-{secrets.token_hex(3)}"
        live_url = f"/deploy/{token}/"
        # In prod: live_url = f"https://{token}.blaqvibes.run"
        expires = timezone.now() + timedelta(hours=1)
        deploy = Deploy.objects.create(project=project, owner=request.user, token=token, live_url=live_url, status='running', expires_at=expires)
        # Mock Docker: for MVP, we don't actually docker run, we just serve ZIP via deploy_view
        # Real: subprocess.run(['docker','run','-d','--name',token,'-p','0:8000', image])
        messages.success(request, f"Preview ready at {live_url} for 1 hour. This is an in-app preview, not a Docker host.")
        return redirect(live_url)
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"run crush: {e}")
        messages.error(request, "Run failed silently")
        return redirect('app_detail', slug=slug)

def deploy_view(request, token):
    """Live URL — serves the vibe's content. For ZIP, shows file list + README preview. For snippet, renders html_code. Backend only."""
    try:
        from .models import Deploy
        from django.utils import timezone
        deploy = get_object_or_404(Deploy, token=token)
        if deploy.expires_at < timezone.now():
            deploy.status = 'expired'
            deploy.save(update_fields=['status'])
            return render(request, 'gallery/deploy_expired.html', {'deploy': deploy})
        # For snippet, just render preview
        if deploy.project.html_code:
            return render(request, 'gallery/deploy_live.html', {'deploy': deploy, 'project': deploy.project})
        # For ZIP, show file list + README
        return render(request, 'gallery/deploy_live.html', {'deploy': deploy, 'project': deploy.project})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"deploy view crush: {e}")
        return render(request, 'gallery/deploy_expired.html', {'deploy': None})

@require_POST
@ratelimit(key='ip', rate='60/h', method='POST')
def copy_increment(request, slug):
    try:
        project = get_object_or_404(AppProject, slug=slug)
        AppProject.objects.filter(pk=project.pk).update(copies=F('copies')+1)
        return JsonResponse({'ok': True})
    except Exception:
        # crush silently
        import logging
        logging.getLogger(__name__).exception("copy_increment crush")
        return JsonResponse({'ok': False}, status=500)

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
    from .models import Challenge
    from users.decorators import admin_required
    # Only admin/superadmin can pick winner
    if not request.user.profile.is_admin():
        return render(request, '403.html', status=403)
    challenge = get_object_or_404(Challenge, tag=tag)
    winner_id = request.POST.get('winner_id')
    if winner_id:
        from gallery.models import AppProject
        winner = get_object_or_404(AppProject, id=winner_id)
        challenge.winner = winner
        challenge.save(update_fields=['winner'])
        # Reward winner: +10 stars + Pro 7 days
        try:
            winner.owner.profile.stars_balance = F('stars_balance') + challenge.bounty_stars
            winner.owner.profile.save(update_fields=['stars_balance'])
            # Also give stars to project
            AppProject.objects.filter(pk=winner.pk).update(stars=F('stars')+challenge.bounty_stars)
            from django.utils import timezone
            from datetime import timedelta
            winner.owner.profile.is_pro = True
            winner.owner.profile.pro_since = timezone.now()
            winner.owner.profile.pro_until = timezone.now() + timedelta(days=30)
            winner.owner.profile.save(update_fields=['is_pro','pro_since','pro_until'])
        except Exception: pass
        messages.success(request, f"Winner picked: {winner.title} by @{winner.owner.username} — +{challenge.bounty_stars} ★ + Pro!")
    return redirect('challenge_detail', tag=tag)
