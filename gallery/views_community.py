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
from django.http import Http404, JsonResponse
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
from .access import user_can_see_project

logger = logging.getLogger(__name__)

def _battle_visible(user, battle):
    """True only when BOTH vibes in a battle are visible to `user`.
    """
    try:
        return bool(
            user_can_see_project(user, battle.vibe_a)
            and user_can_see_project(user, battle.vibe_b)
        )
    except Exception:
        logger.exception('battle visibility check failed battle=%s', getattr(battle, 'id', '?'))
        return False

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
        answer, source, meta = get_nolo_ai_answer(prompt, return_meta=True)
        return JsonResponse({'reply': answer, 'source': source, 'meta': meta})
    except Exception as e:
        logging.getLogger(__name__).exception(f"nolo chat api crush: {e}")
        return JsonResponse({'error': 'Nolo could not answer right now. Try again soon.'}, status=500)

@require_POST
@ratelimit(key='ip', rate='30/h', method='POST')
def nolo_fix_api(request):
    """Nolo debugs a beginner's HTML/CSS/JS. Works with or without an API key.
    """
    try:
        if getattr(request, 'limited', False):
            return JsonResponse({'error': 'Too many requests. Try again later.'}, status=429)
        data = json.loads(request.body.decode('utf-8') or '{}')
        from .nolo_assist import fix_code
        summary, findings, source = fix_code(
            html=data.get('html', ''),
            css=data.get('css', ''),
            js=data.get('js', ''),
            error=data.get('error', ''),
        )
        return JsonResponse({'summary': summary, 'findings': findings, 'source': source})
    except Exception as e:
        logger.exception(f"nolo fix api crush: {e}")
        return JsonResponse({'error': 'Nolo could not analyse that right now.'}, status=500)

@require_POST
@ratelimit(key='ip', rate='30/h', method='POST')
def nolo_readme_api(request):
    """Nolo writes a README from the project's title/description/code.
    """
    try:
        if getattr(request, 'limited', False):
            return JsonResponse({'error': 'Too many requests. Try again later.'}, status=429)
        data = json.loads(request.body.decode('utf-8') or '{}')
        from .nolo_assist import write_readme
        markdown, source = write_readme(
            title=data.get('title', ''),
            description=data.get('description', ''),
            html=data.get('html', ''),
            css=data.get('css', ''),
            js=data.get('js', ''),
            tech=data.get('tech', ''),
        )
        return JsonResponse({'readme': markdown, 'source': source})
    except Exception as e:
        logger.exception(f"nolo readme api crush: {e}")
        return JsonResponse({'error': 'Nolo could not write that right now.'}, status=500)

def nolo_help(request):
    return redirect('nolo_chat')

def starter_gallery(request):
    """The on-ramp: pick a starter template (or a blank page) to open in Studio.
    """
    from .starters import STARTERS, STARTERS_VERSION
    return render(request, 'gallery/starter_gallery.html', {
        'starters': STARTERS,
        'starters_version': STARTERS_VERSION,
    })

def studio(request, slug=''):
    """In-browser editor. Write without an account; run preview after login.
        GET  — load a starter (or blank) into three editors (HTML/CSS/JS).
               Writing is public. The live preview iframe is only in the HTML
               when the visitor is signed in; anonymous visitors get a lock
               panel instead. Drafts live in sessionStorage so sign-in does
               not wipe the editors.
        POST — hand the edited fields to the ONE publish path so scan, classify,
               and trust all apply. Studio never writes an AppProject itself.
    """
    from .starters import get_starter, STARTERS_VERSION
    from .forms import AppUploadForm

    starter = get_starter(slug) if slug else None
    if slug and not starter:
        raise Http404

    if request.method == 'POST':
        # Reuse the single publish path so every rule (validate, scan,
        # classify, trust) applies to Studio output too. publish() itself
        # enforces login + the 5/h upload rate limit.
        from .views import publish as publish_view
        return publish_view(request)

    if starter:
        initial = {
            'title': starter['name'],
            'short_description': starter['blurb'],
            'readme': starter['readme'],
            'tech_stack': starter['tech_stack'],
            'html_code': starter['html'],
            'css_code': starter['css'],
            'js_code': starter['js'],
        }
    else:
        initial = {
            'html_code': '<main>\n  <h1>My vibe</h1>\n  <p>Start building. Edit me.</p>\n</main>',
            'css_code': ('body{font-family:system-ui,sans-serif;background:#0b1020;'
                         'color:#e5e7eb;display:grid;place-items:center;min-height:100vh}'),
            'js_code': '// JavaScript runs in the live preview after you sign in.\n',
        }
    form = AppUploadForm(initial=initial)
    return render(request, 'gallery/studio.html', {
        'form': form,
        'starter': starter,
        'initial': initial,
        'starters_version': STARTERS_VERSION,
        'can_preview': request.user.is_authenticated,
        'studio_next': request.path,
    })

@login_required
@ratelimit(key='user', rate='5/h', method='POST')
def create_pr(request, slug):
    """Create Pull Request from fork to its original — backend checks forked_from."""
    # Ownership is resolved BEFORE the try: Http404 is an Exception, so the
    # broad catch-all below used to swallow it and answer 302 for a fork
    # that isn't the caller's. A 404 is the honest answer — it neither
    # confirms the slug exists nor leaks that the row is someone else's.
    source = get_object_or_404(AppProject, slug=slug, owner=request.user)
    try:
        if getattr(request, 'limited', False):
            messages.error(request, "Rate limit: 5 PRs/hour")
            return redirect('app_detail', slug=slug)
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
    except Http404:
        raise
    except Exception as e:
        logging.getLogger(__name__).exception(f"create_pr crush: {e}")
        messages.error(request, "PR failed silently")
        return redirect('app_detail', slug=slug)

def pr_list(request, slug):
    """List PRs for a vibe — open/merged/closed, backend only.
        Published target only, same visibility rule as every other content
        page. A PR list rides on the *target* slug, so it must not confirm
    """
    try:
        target = get_object_or_404(AppProject, slug=slug, status='published')
        prs = PullRequest.objects.filter(target=target).select_related('source','author').order_by('-created_at')
        return render(request, 'gallery/pr_list.html', {'target': target, 'prs': prs})
    except Exception as e:
        logging.getLogger(__name__).exception(f"pr_list crush: {e}")
        return render(request, 'gallery/pr_list.html', {'target': get_object_or_404(AppProject, slug=slug, status='published'), 'prs': PullRequest.objects.none()})

def pr_detail(request, slug, pr_id):
    """PR diff — real content diff of ZIPs plus Nolo feature compare.

    Published target AND a visible source. The diff reads real ZIP bytes
    (gallery.diff), so a PR whose source fork is still pending must 404
    for strangers — the same rule every other content read enforces via
    user_can_see_project. The owner/moderator can still review it.
    """
    # Gate OUTSIDE the crush try/except. The fallback below must only render
    # the page for callers who already passed this check — otherwise an
    # Http404 raised here would be swallowed by `except Exception` and the
    # fallback's ungated re-fetch would hand the diff to a stranger anyway.
    #
    # Who may read a PR diff when the source fork is still pending?
    #   - the fork owner (user_can_see_project → owner),
    #   - a moderator (user_can_see_project → moderator),
    #   - the TARGET's owner — opening a PR against their vibe is an explicit
    #     invitation to review, and pr_action lets exactly that user merge it.
    # Note we must NOT check user_can_see_project on pr.target here: the
    # target is published (gated above), so that would be True for everyone
    # and would silently reopen the hole.
    target = get_object_or_404(AppProject, slug=slug, status='published')
    pr = get_object_or_404(PullRequest, id=pr_id, target=target)
    allowed = user_can_see_project(request.user, pr.source)
    if not allowed and getattr(request.user, 'is_authenticated', False) \
            and request.user.pk == pr.target.owner_id:
        allowed = True
    if not allowed:
        raise Http404
    try:
        from .diff import diff_projects
        diff = diff_projects(pr.source, pr.target)
        # Nolo diff
        from .nolo import compare_apps
        nolo_diff = compare_apps(pr.source, pr.target)['diff']
        nolo_review = (pr.source.scan_report or {}).get('nolo_review')
        return render(request, 'gallery/pr_detail.html', {'pr': pr, 'target': target, 'diff': diff, 'nolo_diff': nolo_diff, 'nolo_review': nolo_review})
    except Exception as e:
        logging.getLogger(__name__).exception(f"pr_detail crush: {e}")
        return render(request, 'gallery/pr_detail.html', {'pr': pr, 'target': target, 'diff': {'added':[],'removed':[],'modified':[],'unchanged':[],'added_count':0,'removed_count':0,'modified_count':0,'common_count':0}, 'nolo_diff': {'only_in_a':[],'only_in_b':[],'common':[]}, 'nolo_review': None})

@login_required
@require_POST
@ratelimit(key='user', rate='20/h', method='POST')
def pr_action(request, slug, pr_id):
    """Merge or close — only target owner can merge/close."""
    try:
        target = get_object_or_404(AppProject, slug=slug)
        pr = get_object_or_404(PullRequest, id=pr_id, target=target)
        if request.user != target.owner and not request.user.profile.is_admin():
            return render(request, '403.html', status=403)
        if pr.status != 'open':
            messages.info(request, f'PR #{pr.id} is already {pr.status}.')
            return redirect('pr_list', slug=slug)
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
                try:
                    target.zip_file.save(
                        f"{target.slug}.zip", ContentFile(source.zip_file.read()), save=False,
                    )
                finally:
                    source.zip_file.close()
            else:
                # A snippet PR must not keep the target's old ZIP attached.
                target.zip_file = None
            # PRs can contain a snippet rather than a ZIP. Copy the executable
            # source as well as archive metadata; otherwise a "merged" snippet
            # only changed the README and never changed the app people run.
            target.html_code = source.html_code
            target.css_code = source.css_code
            target.js_code = source.js_code
            target.file_tree = source.file_tree or {}
            target.file_count = source.file_count
            target.readme = source.readme
            target.tech_stack = source.tech_stack
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
            try:
                from users.progress import award
                award(pr.author, 'pr_merged', ref=f'pr:{pr.id}')
            except Exception:
                logger.exception('pr merge xp failed %s', pr.id)
            messages.success(request, f"✓ PR #{pr.id} merged — files copied from fork, re-queued for scan.")
        elif action == 'close':
            pr.status = 'closed'
            pr.save(update_fields=['status','updated_at'])
            messages.info(request, f"PR #{pr.id} closed")
        return redirect('pr_list', slug=slug)
    except Exception as e:
        logging.getLogger(__name__).exception(f"pr_action crush: {e}")
        return redirect('pr_list', slug=slug)

def battle(request):
    """Vibe Battles — two random vibes side-by-side, crush silently."""
    try:
        from .models import VibeBattle, AppProject
        from .daily import ensure_daily_battle
        import random
        # Pick 2 random published vibes, not same, exclude own if logged in
        qs = AppProject.objects.filter(status='published')
        if qs.count() < 2:
            return render(request, 'gallery/battle.html', {'battle': None})
        daily_battle = ensure_daily_battle()
        # Try to find a battle not voted by this user
        if request.user.is_authenticated:
            voted_ids = request.user.battle_votes.values_list('battle_id', flat=True)
            if daily_battle and daily_battle.id not in voted_ids and _battle_visible(request.user, daily_battle):
                return render(request, 'gallery/battle.html', {'battle': daily_battle})
            # A battle is a view of two vibes; if either has since gone
            # non-public it must not be surfaced to this person (same rule as
            # every other content read). One helper, applied to every battle
            # this page could otherwise render.
            available = [
                b for b in VibeBattle.objects.exclude(id__in=voted_ids)
                .select_related('vibe_a__owner', 'vibe_b__owner')
                .order_by('-created_at')[:30]
                if _battle_visible(request.user, b)
            ]
            if available:
                return render(request, 'gallery/battle.html', {'battle': available[0]})
        elif daily_battle:
            return render(request, 'gallery/battle.html', {'battle': daily_battle})
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
        logging.getLogger(__name__).exception(f"leaderboard crush: {e}")
        return render(request, 'gallery/battle_leaderboard.html', {'top_vibes': [], 'top_users': []})

def battle_history(request):
    try:
        from .models import VibeBattle, BattleVote
        my_votes = []
        # The history page is public — it must never render a battle whose
        # vibes have since gone non-public (same rule as the battle page).
        # Fetch a slightly larger window, filter with the one visibility
        # helper, then cap the visible count.
        recent = [
            b for b in VibeBattle.objects.select_related('vibe_a__owner', 'vibe_b__owner')
            .order_by('-created_at')[:30]
            if _battle_visible(request.user, b)
        ][:10]
        if request.user.is_authenticated:
            my_votes = [
                v for v in BattleVote.objects.filter(user=request.user)
                .select_related('battle__vibe_a__owner', 'battle__vibe_b__owner')
                .order_by('-created_at')[:40]
                if _battle_visible(request.user, v.battle)
            ][:20]
        return render(request, 'gallery/battle_history.html', {'my_votes': my_votes, 'recent': recent})
    except Exception as e:
        logging.getLogger(__name__).exception(f"battle_history crush: {e}")
        return render(request, 'gallery/battle_history.html', {'my_votes': [], 'recent': []})

@login_required
@require_POST
@ratelimit(key='user', rate='30/h', method='POST')
def vote_battle(request, battle_id):
    # Visibility + object lookup are resolved BEFORE the try. Http404 is an
    # Exception, and the broad catch-all below would swallow it and answer a
    # 302 — which both confirms the battle exists and hides the refusal from
    # the caller. Same anti-pattern create_pr was fixed for.
    battle = get_object_or_404(VibeBattle, id=battle_id)
    # You cannot vote on a battle you cannot see: if either vibe has since
    # gone non-public, the battle is no longer a real contest (and a
    # stranger must not confirm its existence via a vote).
    if not _battle_visible(request.user, battle):
        raise Http404
    try:
        from django.db.models import F
        if getattr(request, 'limited', False):
            messages.error(request, "Rate limit: too many votes.")
            return redirect('battle')
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
        logging.getLogger(__name__).exception(f"vote crush: {e}")
        return redirect('battle')

def run_vibe(request, slug):
    """Send people to the honest preview. Not a Docker host.
    """
    project = get_object_or_404(AppProject, slug=slug, status='published')
    # An inline snippet always runs; a static-site ZIP runs when the
    # classifier found its entry. Both land on the sandboxed preview shell.
    if (project.html_code or '').strip():
        return redirect('preview', slug=slug)
    if project.preview_mode == 'static_zip' and (project.static_entry or '').strip():
        return redirect('preview', slug=slug)
    if project.zip_file:
        return redirect('preview_files', slug=slug)
    messages.error(request, "Nothing to preview — no snippet or files.")
    return redirect(project.get_absolute_url())

# deploy_view was removed with the Deploy model: it promised a live deployment
# but delivered a redirect — a lie in the URL space. Old /deploy/<token>/ links
# now 404 honestly.

@require_POST
@ratelimit(key='ip', rate='20/h', method='POST')
def copy_increment(request, slug):
    """One copy count per session per published vibe. Not a leaderboard.
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
    # A challenge every day, with or without the AI generator: the pool is
    # derived from the date, so simply loading this page materialises it.
    # Settling here too means a finished day pays its winner the first time
    # anybody looks, with no Celery beat required.
    try:
        from .daily import ensure_daily_challenge, settle_past_challenges
        ensure_daily_challenge()
        settle_past_challenges()
    except Exception:
        logging.getLogger(__name__).exception('daily challenge setup failed')
    challenges = Challenge.objects.all().order_by('-start')
    active = Challenge.objects.filter(is_active=True, start__lte=timezone.now(), end__gte=timezone.now()).first()
    if active is not None:
        try:
            from .daily import leaderboard, submissions
            active.submission_count = submissions(active).count()
            active.top_submissions = leaderboard(active, limit=3)
        except Exception:
            logging.getLogger(__name__).exception('challenge leaderboard failed')
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
        logging.getLogger(__name__).exception(f"approve_challenge crush: {e}")
        return redirect('challenge_list')

def challenge_detail(request, tag):
    from .models import Challenge
    from gallery.models import AppProject
    challenge = get_object_or_404(Challenge, tag=tag)
    # Ranked, not just listed: a challenge page is a scoreboard. Highest
    # stars first, earliest publish as the tie-break (see daily.settle).
    submissions = AppProject.objects.filter(tags__slug=challenge.tag, status='published').select_related('owner').order_by('-stars', 'created_at')
    # Also include pending for owner/admin view?
    if request.user.is_authenticated and (request.user.profile.is_admin() if hasattr(request.user, 'profile') else False):
        submissions = AppProject.objects.filter(tags__slug=challenge.tag).select_related('owner').order_by('-stars', 'created_at')
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
