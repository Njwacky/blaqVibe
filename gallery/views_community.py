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
        answer, source, meta = get_nolo_ai_answer(prompt, return_meta=True)
        return JsonResponse({'reply': answer, 'source': source, 'meta': meta})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"nolo chat api crush: {e}")
        return JsonResponse({'error': 'Nolo could not answer right now. Try again soon.'}, status=500)

@require_POST
@ratelimit(key='ip', rate='30/h', method='POST')
def nolo_fix_api(request):
    """Nolo debugs a beginner's HTML/CSS/JS. Works with or without an API key.

    5 Whys:
    1. Why a separate endpoint from chat? The Studio sends structured code
       (html/css/js/error), not a free chat line; a typed contract lets the
       UI render findings next to the code.
    2. Why no login gate? A beginner tinkering in Studio before signing up is
       exactly who needs debugging help; the rate limit is by IP, not user.
    3. Why crush to a safe JSON error? A broken analyser must never take the
       Studio down mid-edit.
    4. Why return `source`? The UI must be honest about whether a live model
       or the built-in checks answered (same rule as chat).
    5. Why cap at 30/h? Debugging is bursty but a paste loop is abuse; the
       cap matches chat's order of magnitude.
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

    5 Whys:
    1. Why here and not the existing ai_readme.py? That one works on a saved
       ZIP AppProject; the Studio needs a README BEFORE anything is saved,
       straight from the editor fields.
    2. Why guarantee a '# ' heading + length? The publish form rejects a
       README under 100 chars or without a heading; a generator that produced
       an unpublishable README would be a trap.
    3. Why no login gate? Same as fix: the beginner needs it before signing up.
    4. Why sanitise the code first? It is user text; it goes through the prompt
       scrub before it can reach a model or the page.
    5. Why return source? Honesty about live-model vs built-in, every time.
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

    5 Whys — why a gallery page separate from the feed?
    1. Why not just seed starters into the feed? The feed is finished vibes to
       clone/trade; a starter is a *blank canvas to begin from*. Mixing them
       would blur "learn from this" with "publish over this".
    2. Why public (no login)? A beginner deciding whether to sign up should be
       able to pick a starter and write first; the login wall lands at
       *running* the preview and at publish — never at the editors.
    3. Why data-driven (gallery.starters)? Starters must be trustworthy on
       first paint — code-reviewed data, never user bytes (see starters.py).
    4. Why include a blank option? Someone with their own idea should not have
       to delete a template first; blank is the honest "start from nothing".
    5. Why keep it read-only? The gallery only routes to Studio; all editing
       and the publish gate live in one place (studio), so rules cannot drift.
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

    5 Whys:
    1. Why does the live preview run entirely client-side? An `<iframe
       sandbox="allow-scripts" srcdoc>` is an opaque origin — it cannot read
       our cookies or DOM — so the user's in-progress code is safe to run
       with zero round-trips. This IS the client-side runner, reused.
    2. Why route publish through gallery.views.publish instead of saving here?
       Edited starter code is user content; it must pass the same validation,
       snippet secret-scan, classification, and trust grading as any upload.
       A second save path would be a second place for those rules to rot.
    3. Why require login to RUN the preview, not to write? Writing is text
       in a textarea — no execution, no account. Running HTML/JS executes in
       the visitor's browser. An account is the same gate we already use for
       publish, so a shared /studio/ URL cannot become an anonymous script
       runner, and the conversion is honest: you wrote it, sign in to see it
       run.
    4. Why omit the iframe from the anonymous HTML rather than hide it with
       CSS? `display:none` still leaves an iframe a visitor (or an extension)
       can unhide, and studio.js would still assign srcdoc. A missing element
       cannot run. The JS flag `canPreview` is rendered from
       `request.user.is_authenticated` — flipping it in DevTools cannot
       conjure an iframe that was never sent.
    5. Why persist the draft in sessionStorage? Sign-in is a full navigation.
       Without a local draft, "you can write without an account" deletes the
       work at the conversion moment. sessionStorage (not localStorage) dies
       with the tab on a shared computer; the server never stores anonymous
       code (that would be a pastebin). After login, `?next=` returns to the
       same /studio/<slug>/ and the editors restore.
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
    """List PRs for a vibe — open/merged/closed, backend only.

    Published target only, same visibility rule as every other content
    page. A PR list rides on the *target* slug, so it must not confirm
    (or hand out forks' metadata for) an unpublished vibe. 5 Whys:
    1. Why gate here when create_pr already checks forked_from? A fork is
       created pending; its PR rows describe that pending source. Listing
       them from a guessed pending slug is exactly the leak the rest of
       the site 404s.
    2. Why 404, not 403? A 403 confirms the vibe exists (scan_status says
       the same thing).
    3. Why gate the TARGET, not just the source? The page title, owner and
       PR metadata all describe the target; a pending target is private.
    4. Why keep pr_action ungated? Merge/close is owner/admin-only and
       re-routes through this page; it cannot read more than it already
       knows about its own vibe.
    5. Why not also require the SOURCE to be published? Owners legitimately
       hold open PRs from their own still-pending forks — the source gate
       (user_can_see_project) lives on pr_detail, where content is read.
    """
    try:
        target = get_object_or_404(AppProject, slug=slug, status='published')
        prs = PullRequest.objects.filter(target=target).select_related('source','author').order_by('-created_at')
        return render(request, 'gallery/pr_list.html', {'target': target, 'prs': prs})
    except Exception as e:
        import logging
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
        import logging
        logging.getLogger(__name__).exception(f"pr_detail crush: {e}")
        return render(request, 'gallery/pr_detail.html', {'pr': pr, 'target': target, 'diff': {'added':[],'removed':[],'modified':[],'unchanged':[],'added_count':0,'removed_count':0,'modified_count':0,'common_count':0}, 'nolo_diff': {'only_in_a':[],'only_in_b':[],'common':[]}, 'nolo_review': None})

@login_required
@require_POST
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
    """Send people to the honest preview. Not a Docker host.

    5 Whys: Why ask can_run_preview instead of `if html_code`?
    1. A static-site ZIP now runs live too (preview_mode == 'static_zip'),
       so "runnable" is no longer "has an inline snippet".
    2. can_run_preview is the single source of truth the badge and the shell
       already use — routing on anything else could disagree with the badge.
    3. A ZIP that only shows files must still land on the file list, not a
       blank live frame.
    4. No snippet and no files → honest error, never a fake preview.
    5. One property, three call sites (card, shell, this router) — they can
       never drift.
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
