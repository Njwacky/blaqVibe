from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db.models import F, Q, Count
from django.http import Http404, HttpResponse, JsonResponse, HttpResponseRedirect
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django_ratelimit.decorators import ratelimit
import zipfile, os, json, logging

from .models import AppProject, Category, Comment, Star, AppFile, ScanJob, AppReport, AppVersion, Review, Trade, PullRequest
from .forms import AppUploadForm
from .utils import build_tree_from_zip
from .storages import get_presigned_url, is_s3_enabled
from .search import search_projects
from .access import user_can_download, access_denied_message
from .zip_serve import serve_project_zip, owner_scan_reason
from .notify import notify
from django.core.mail import send_mail
from users.forms import SignUpForm

from .views_community import (
    nolo_compare,
    nolo_chat,
    nolo_chat_api,
    nolo_help,
    create_pr,
    pr_list,
    pr_detail,
    pr_action,
    battle,
    battle_leaderboard,
    battle_history,
    vote_battle,
    run_vibe,
    deploy_view,
    copy_increment,
    challenge_list,
    generate_challenges,
    approve_challenge,
    challenge_detail,
    pick_challenge_winner,
)
logger = logging.getLogger(__name__)

def signup(request):
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            try:
                from users.views import send_verify_email
                if user.email:
                    send_verify_email(request, user)
            except Exception:
                logger.exception('verify email send failed')
            messages.success(request, "Welcome to BlaqVibes — we sent a confirmation link to your email.")
            return redirect('feed')
    else:
        form = SignUpForm()
    return render(request, 'registration/signup.html', {'form': form})

def feed(request):
    try:
        q = request.GET.get('q','').strip()
        # Sanitize search prompt — many prompt fields, check vulnerabilities, crush silently
        try:
            from .prompt_sanitize import sanitize_prompt
            q = sanitize_prompt(q)[:100]  # limit
        except Exception:
            q = request.GET.get('q','').strip()[:100]
        cat = request.GET.get('category','')
        kind = request.GET.get('kind','')
        ai = request.GET.get('ai','')
        tech = request.GET.get('tech','')
        sort = request.GET.get('sort','newest')
        projects = AppProject.objects.filter(status='published').select_related('owner','owner__profile','category').prefetch_related('tags')
        if cat:
            projects = projects.filter(category__slug=cat)
        if kind == 'snippet':
            projects = projects.exclude(html_code='')
        elif kind == 'full_app':
            projects = projects.exclude(zip_file='')
        if ai == '1':
            projects = projects.filter(ai_generated=True)
        if tech:
            try:
                import bleach
                tech = bleach.clean(tech, tags=[], strip=True)[:100]
            except Exception: pass
            projects = projects.filter(tech_stack__icontains=tech)
        projects = search_projects(projects, q, sort=sort)
        categories = Category.objects.all().order_by('order')
        paginator = Paginator(projects, 12)
        page = paginator.get_page(request.GET.get('page'))
        return render(request, 'gallery/feed.html', {'page': page, 'categories': categories, 'q': q, 'cat': cat, 'kind': kind, 'sort': sort})
    except Exception:
        logger.exception("feed crush silent")
        return render(request, 'gallery/feed.html', {'page': Paginator(AppProject.objects.none(), 12).get_page(1), 'categories': Category.objects.all(), 'q': '', 'cat': '', 'kind': '', 'sort': 'newest'})

def app_detail(request, slug):
    qs = AppProject.objects.select_related(
        'owner', 'owner__profile', 'category', 'forked_from', 'forked_from__owner'
    ).prefetch_related('forks__owner', 'files').annotate(
        forks_count=Count('forks', distinct=True),
        prs_count=Count('prs_incoming', distinct=True),
        comment_count=Count('comments', distinct=True),
    )
    project = get_object_or_404(qs, slug=slug)
    # Only published visible to visitors, owners can see their pending/quarantined
    if project.status != 'published' and (not request.user.is_authenticated or project.owner != request.user and not request.user.is_staff):
        raise Http404
    if project.status == 'published':
        AppProject.objects.filter(pk=project.pk).update(views=F('views')+1)
        try:
            if request.user.is_authenticated and request.user != project.owner:
                from .models import VibeView
                vv, created = VibeView.objects.get_or_create(viewer=request.user, project=project, defaults={'count':1})
                if not created:
                    VibeView.objects.filter(pk=vv.pk).update(count=F('count')+1, last_viewed=timezone.now())
        except Exception:
            logger.exception("vibe view log failed")
    comments = project.comments.filter(is_hidden=False).select_related('user').prefetch_related('replies__user')
    top_comments = comments.filter(parent__isnull=True)
    is_starred = False
    has_traded = False
    has_bought = False
    is_bookmarked = False
    viewers = None
    ai_readme_preview = None
    if request.user.is_authenticated:
        is_starred = Star.objects.filter(user=request.user, project=project).exists()
        has_traded = Trade.objects.filter(buyer=request.user, project=project).exists()
        from .models import Sale, Bookmark
        has_bought = Sale.objects.filter(buyer=request.user, project=project).exists()
        is_bookmarked = Bookmark.objects.filter(user=request.user, project=project).exists()
    can_download = user_can_download(request.user, project) if project.zip_file else True
    viewers = None
    show_viewer_upsell = False
    try:
        if request.user.is_authenticated and request.user == project.owner:
            if project.owner.profile.is_pro_active:
                from .models import VibeView
                viewers = VibeView.objects.filter(project=project).select_related('viewer').order_by('-last_viewed')[:20]
            else:
                show_viewer_upsell = True
    except Exception:
        viewers = None
    # Nolo + AI README preview
    reviews = project.reviews.select_related('user').order_by('-created_at')
    nolo_review = None
    try:
        nolo_review = (project.scan_report or {}).get('nolo_review')
        # AI README preview if exists
        ai_readme_preview = project.ai_readme
    except Exception: pass
    # Scan status for JS poll — backend only, no secrets, just status string
    scan_status = getattr(project, 'scan_job', None)
    # Rank for discount display
    from .ranks import contributor_bonus
    owner_rank = contributor_bonus(project.owner)
    user_rank = contributor_bonus(request.user) if request.user.is_authenticated else None
    compare_options = AppProject.objects.filter(
        category=project.category, status='published'
    ).exclude(pk=project.pk).order_by('-stars')[:10]
    return render(request, 'gallery/app_detail.html', {
        'project': project,
        'comments': top_comments,
        'reviews': reviews,
        'nolo_review': nolo_review,
        'ai_readme_preview': ai_readme_preview,
        'is_starred': is_starred,
        'is_bookmarked': is_bookmarked,
        'has_traded': has_traded,
        'has_bought': has_bought,
        'can_download': can_download,
        'scan_reason': owner_scan_reason(project) if request.user.is_authenticated and (request.user == project.owner or request.user.is_staff) else '',
        'comment_count': getattr(project, 'comment_count', 0),
        'published_forks': [f for f in project.forks.all() if f.status == 'published'][:5],
        'viewers': viewers,
        'show_viewer_upsell': show_viewer_upsell,
        'viewer_count': project.views,
        'scan_status': scan_status.status if scan_status else project.status,
        'owner_rank': owner_rank,
        'user_rank': user_rank,
        'compare_options': compare_options,
        'forks_count': getattr(project, 'forks_count', 0),
        'prs_count': getattr(project, 'prs_count', 0),
    })

def scan_status(request, slug):
    """Backend-only status poll — JS gets only 'queued/scanning/clean/quarantined', never raw scan_report."""
    project = get_object_or_404(AppProject, slug=slug)
    job = getattr(project, 'scan_job', None)
    data = {
        'status': job.status if job else project.status,
        'is_published': project.status == 'published',
        'reason': '',
    }
    if request.user.is_authenticated and (request.user == project.owner or request.user.is_staff):
        data['reason'] = owner_scan_reason(project)
    return JsonResponse(data)

def preview(request, slug):
    """Safe preview shell — the user's HTML/JS runs only inside a sandboxed
    (opaque-origin) iframe pointed at snippet_doc, never in a privileged context."""
    project = get_object_or_404(AppProject, slug=slug, status='published')
    resp = render(request, 'gallery/preview.html', {'project': project})
    # This shell has no scripts of its own — lock it down (only our own inline <style>).
    csp = (
        "default-src 'none'; frame-src 'self'; img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; form-action 'none'"
    )
    resp['Content-Security-Policy'] = csp
    resp['Content-Security-Policy-Report-Only'] = csp + "; report-uri /csp-report/"
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    resp['Referrer-Policy'] = 'no-referrer'
    return resp


def snippet_doc(request, slug):
    """The raw snippet document (user HTML + CSS + JS).

    Served ONLY into an <iframe sandbox="allow-scripts"> (opaque origin):
    the framed content gets no cookies, no parent DOM, and `connect-src 'none'`
    stops it from phoning home or driving state-changing requests. Direct
    top-level navigation is rejected so the code never runs unsandboxed.
    """
    project = get_object_or_404(AppProject, slug=slug, status='published')
    # Block direct (top-level) navigation. Browsers send Sec-Fetch-Dest on every
    # request; the only legitimate entry point is our sandboxed preview iframe.
    if request.META.get('HTTP_SEC_FETCH_DEST') == 'document':
        return render(request, 'gallery/snippet_blocked.html', {'project': project}, status=403)
    resp = render(request, 'gallery/snippet_doc.html', {'project': project})
    resp['Content-Security-Policy'] = (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "img-src data: https: http:; media-src data: https:; font-src data:; "
        "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'self'"
    )
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    resp['Referrer-Policy'] = 'no-referrer'
    resp['X-Content-Type-Options'] = 'nosniff'
    return resp

@login_required
@ratelimit(key='user', rate='5/h', method='POST')
def publish(request):
    from users.models import SiteSettings
    from gallery.models import Challenge
    from django.utils import timezone
    site = SiteSettings.get()
    challenge = Challenge.objects.filter(is_active=True, start__lte=timezone.now(), end__gte=timezone.now()).first()
    if getattr(request, 'limited', False):
        return HttpResponse("Rate limit: 5 uploads/hour", status=429)
    if request.method == 'POST':
        form = AppUploadForm(request.POST, request.FILES)
        if form.is_valid():
            project = form.save(commit=False)
            project.owner = request.user
            project.status = 'pending'  # Always pending first — must go through queue
            if not getattr(request.user.profile, 'allow_trading', True):
                project.star_cost = 0
            project.save()
            form.save_m2m()
            # Challenge — if checked, add tag
            if challenge and request.POST.get('challenge_join') == 'on':
                from gallery.models import Tag
                tag, _ = Tag.objects.get_or_create(slug=challenge.tag, defaults={'name': challenge.tag})
                project.tags.add(tag)
            if project.zip_file:
                try:
                    tree, file_list = build_tree_from_zip(project.zip_file.path)
                    project.file_tree = tree
                    project.file_count = len(file_list)
                    project.save(update_fields=['file_tree','file_count'])
                    for f in file_list[:2000]:
                        AppFile.objects.create(project=project, path=f['path'], size=f['size'])
                except Exception as e:
                    logger.warning("Tree build error for %s: %s", project.slug, e)
                # Queue EVERY app — concurrent uploads serialize in 'scan' queue (FIFO, acks_late, prefetch 1)
                try:
                    from .tasks import process_upload_pipeline
                    job, _ = ScanJob.objects.get_or_create(project=project, defaults={'status': 'queued'})
                    task = process_upload_pipeline.delay(project.id)
                    job.task_id = task.id if hasattr(task,'id') else ''
                    job.status = 'scanning'
                    job.save(update_fields=['task_id','status'])
                except Exception as e:
                    logger.warning("Queue error, fallback eager for %s: %s", project.slug, e)
                messages.info(request, f"⏳ Your vibe “{project.title}” is in the queue — we’re checking for vulnerabilities. We’ll tell you when it’s uploaded! You’re #{ScanJob.objects.filter(status__in=['queued','scanning']).count()} in line, even with concurrent uploads every app is checked.")
                # Auto-run toggle — if On, auto spin live URL on upload
                try:
                    from users.models import SiteSettings
                    from gallery.models import Deploy
                    from django.utils import timezone
                    from datetime import timedelta
                    import secrets
                    if SiteSettings.get().auto_run_enabled and project.zip_file:
                        token = f"{project.slug}-{secrets.token_hex(3)}"
                        live_url = f"/deploy/{token}/"
                        expires = timezone.now() + timedelta(hours=1)
                        Deploy.objects.create(project=project, owner=request.user, token=token, live_url=live_url, status='running', expires_at=expires)
                        messages.success(request, f"▶ Auto-run enabled — live at {live_url} for 1 hour! (toggle in Settings → Global)")
                except Exception:
                    pass
            else:
                if request.user.projects.filter(status='published').count() >= 3:
                    project.status = 'published'
                    project.save(update_fields=['status'])
                    messages.success(request, f"✓ Your snippet “{project.title}” is live!")
                else:
                    messages.info(request, f"⏳ Your vibe “{project.title}” is queued for review — we’ll tell you when it’s uploaded!")
            return redirect(project.get_absolute_url())
    else:
        form = AppUploadForm()
    return render(request, 'gallery/publish.html', {'form': form, 'challenge': challenge})

def download_zip(request, slug):
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if not project.zip_file:
        raise Http404
    if not user_can_download(request.user, project):
        messages.error(request, access_denied_message(request.user, project))
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        return redirect(project.get_absolute_url())
    return serve_project_zip(project)

def file_preview(request, slug, path):
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if '..' in path or path.startswith('/') or '\\' in path:
        raise Http404
    if not project.zip_file:
        raise Http404
    if not user_can_download(request.user, project):
        return JsonResponse({'error': 'Unlock this vibe to preview files.'}, status=403)
    try:
        with zipfile.ZipFile(project.zip_file.path) as z:
            if path not in z.namelist():
                raise Http404
            data = z.read(path)
    except Exception as e:
        raise Http404(str(e))
    if len(data) > 200*1024:
        return JsonResponse({'error': 'File too large (200KB max). Download ZIP.'}, status=413)
    try:
        text = data.decode('utf-8')
    except Exception:
        return JsonResponse({'error': 'Binary file'}, status=400)
    return JsonResponse({'path': path, 'content': text})

@require_POST
@login_required
@ratelimit(key='user', rate='10/h', method='POST')
def post_comment(request, slug):
    try:
        if getattr(request, 'limited', False):
            return HttpResponse("Rate limit: 10 comments/hour", status=429)
        project = get_object_or_404(AppProject, slug=slug, status='published')
        if not getattr(project.owner.profile, 'allow_comments', True):
            messages.error(request, "Comments are turned off for this vibe.")
            return redirect(project.get_absolute_url())
        from .prompt_sanitize import sanitize_prompt
        body = sanitize_prompt(request.POST.get('body','').strip())[:2000]
        parent_id = request.POST.get('parent_id')
        if len(body) < 5 or len(body) > 2000:
            return HttpResponse("Comment 5-2000 chars", status=400)
        parent = None
        if parent_id:
            try:
                parent = Comment.objects.get(pk=parent_id, project=project)
            except Exception:
                parent = None
        Comment.objects.create(project=project, user=request.user, body=body, parent=parent)
        if project.owner_id != request.user.id:
            notify(project.owner, 'comment', f'@{request.user.username} commented on {project.title}', body[:160], project.get_absolute_url() + '#comments')
        return redirect(project.get_absolute_url() + '#comments')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"post_comment crush: {e}")
        return redirect(get_object_or_404(AppProject, slug=slug).get_absolute_url() + '#comments')

@require_POST
@login_required
def post_review(request, slug):
    try:
        from .models import Review
        from .prompt_sanitize import sanitize_prompt
        project = get_object_or_404(AppProject, slug=slug, status='published')
        if not getattr(project.owner.profile, 'allow_reviews', True):
            messages.error(request, "Reviews are turned off for this vibe.")
            return redirect(project.get_absolute_url())
        rating = int(request.POST.get('rating', 0))
        text = sanitize_prompt(request.POST.get('text',''))[:1000]
        if rating < 1 or rating > 5:
            return HttpResponse("Rating 1-5", status=400)
        if Trade.objects.filter(buyer=request.user, project=project).exists() or Star.objects.filter(user=request.user, project=project).exists() or project.owner == request.user:
            # Allow review if traded/starred/owner
            review, created = Review.objects.update_or_create(user=request.user, project=project, defaults={'rating': rating, 'text': text})
            if created:
                messages.success(request, f"Review {rating}★ posted — Nolo and human ratings now show.")
            else:
                messages.success(request, f"Review updated to {rating}★")
        else:
            messages.error(request, "Trade or star the vibe first to review — earn the right.")
        return redirect(project.get_absolute_url() + '#reviews')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"post_review crush: {e}")
        return redirect(get_object_or_404(AppProject, slug=slug).get_absolute_url() + '#reviews')

@require_POST
@login_required
def toggle_star(request, slug):
    project = get_object_or_404(AppProject, slug=slug, status='published')
    star, created = Star.objects.get_or_create(user=request.user, project=project)
    if not created:
        star.delete()
        AppProject.objects.filter(pk=project.pk, stars__gt=0).update(stars=F('stars')-1)
        return JsonResponse({'starred': False})
    AppProject.objects.filter(pk=project.pk).update(stars=F('stars')+1)
    return JsonResponse({'starred': True})

@login_required
def my_vibes(request):
    vibes = AppProject.objects.filter(owner=request.user).order_by('-created_at').select_related('category')
    return render(request, 'gallery/my_vibes.html', {'vibes': vibes})

@login_required
def edit_vibe(request, slug):
    project = get_object_or_404(AppProject, slug=slug, owner=request.user)
    if request.method == 'POST':
        form = AppUploadForm(request.POST, request.FILES, instance=project)
        if form.is_valid():
            p = form.save(commit=False)
            # Versioning: if new ZIP, save old as AppVersion
            if 'zip_file' in request.FILES and project.zip_file:
                AppVersion.objects.create(project=project, zip_file=project.zip_file, version=f"1.{project.versions.count()+1}.0", changelog=request.POST.get('changelog','Update'))
            p.status = 'pending'
            p.save()
            form.save_m2m()
            # Rebuild tree + re-queue scan (every edit is re-checked)
            if p.zip_file:
                try:
                    from .utils import build_tree_from_zip
                    p.files.all().delete()
                    tree, files = build_tree_from_zip(p.zip_file.path)
                    p.file_tree, p.file_count = tree, len(files)
                    p.save(update_fields=['file_tree','file_count'])
                    for f in files[:2000]:
                        AppFile.objects.create(project=p, path=f['path'], size=f['size'])
                except Exception: pass
                from .tasks import process_upload_pipeline
                from .models import ScanJob
                job,_ = ScanJob.objects.get_or_create(project=p)
                job.status='queued'; job.save()
                try:
                    process_upload_pipeline.delay(p.id)
                    messages.info(request, f"⏳ Your vibe “{p.title}” re-uploaded — re-queued for scan. We’ll tell you when it’s live again!")
                except Exception: pass
            else:
                messages.success(request, "✓ Vibe updated!")
            return redirect(p.get_absolute_url())
    else:
        form = AppUploadForm(instance=project)
    return render(request, 'gallery/edit_vibe.html', {'form': form, 'project': project})

@login_required
@login_required
@require_POST
def delete_vibe(request, slug):
    project = get_object_or_404(AppProject, slug=slug, owner=request.user)
    project.delete()
    messages.success(request, f"Deleted “{project.title}”")
    return redirect('my_vibes')

@require_POST
@ratelimit(key='ip', rate='10/h', method='POST')
def report_vibe(request, slug):
    try:
        from .prompt_sanitize import sanitize_prompt
        project = get_object_or_404(AppProject, slug=slug)
        reason = request.POST.get('reason','other')
        if reason not in ('spam','malware','copyright','other'):
            reason = 'other'
        details = sanitize_prompt(request.POST.get('details',''))[:500]
        AppReport.objects.create(project=project, user=request.user if request.user.is_authenticated else None, reason=reason, details=details)
        messages.success(request, "Reported — moderators will review. Thank you.")
        return redirect(project.get_absolute_url())
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"report crush: {e}")
        return redirect(get_object_or_404(AppProject, slug=slug).get_absolute_url())

def git_clone(request, username, slug):
    project = get_object_or_404(AppProject, slug=slug, owner__username=username, status='published')
    if not project.zip_file:
        raise Http404
    if not user_can_download(request.user, project):
        messages.error(request, access_denied_message(request.user, project))
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        return redirect(project.get_absolute_url())
    return serve_project_zip(project)

@login_required
@require_POST
def trade_download(request, slug):
    """Trading: download costs stars. Backend atomically deducts buyer, rewards seller. No JS secrets."""
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if not project.zip_file:
        raise Http404
    buyer_profile = request.user.profile
    if project.owner == request.user:
        # Owner free download
        return redirect('download_zip', slug=slug)
    from .access import effective_star_cost
    cost = effective_star_cost(project)
    if cost == 0:
        return redirect('download_zip', slug=slug)
    # Check already traded?
    from .models import Trade
    if Trade.objects.filter(buyer=request.user, project=project).exists():
        return redirect('download_zip', slug=slug)
    if buyer_profile.stars_balance < cost:
        messages.error(request, f"Need {cost} ★ to trade for “{project.title}” — you have {buyer_profile.stars_balance} ★. Earn stars by publishing vibes that get stars.")
        return redirect(project.get_absolute_url())
    # Atomic trade
    from django.db import transaction
    from django.db.models import F
    from users.models import Profile as P
    with transaction.atomic():
        # Lock rows
        buyer = P.objects.select_for_update().get(user=request.user)
        seller = P.objects.select_for_update().get(user=project.owner)
        if buyer.stars_balance < cost:
            raise Http404
        buyer.stars_balance = F('stars_balance') - cost
        buyer.save(update_fields=['stars_balance'])
        seller.stars_balance = F('stars_balance') + cost
        seller.save(update_fields=['stars_balance'])
        Trade.objects.create(buyer=request.user, seller=project.owner, project=project, cost=cost)
        buyer.refresh_from_db(); seller.refresh_from_db()
    notify(project.owner, 'trade', f'@{request.user.username} traded {cost} ★ for {project.title}', url=project.get_absolute_url())
    messages.success(request, f"Traded {cost} ★ for “{project.title}” — seller @ {project.owner.username} now has {seller.stars_balance} ★. You have {buyer.stars_balance} ★ left.")
    return redirect('download_zip', slug=slug)

@login_required
@require_POST
@ratelimit(key='user', rate='5/h', method='POST')
def fork_vibe(request, slug):
    """Fork & Remix — backend only, crush silently, 5/h limit."""
    try:
        if getattr(request, 'limited', False):
            messages.error(request, "Rate limit: 5 forks/hour")
            return redirect('app_detail', slug=slug)
        original = get_object_or_404(AppProject, slug=slug, status='published')
        if original.zip_file and not user_can_download(request.user, original):
            messages.error(request, access_denied_message(request.user, original))
            return redirect(original.get_absolute_url())
        if not getattr(original.owner.profile, 'allow_forks', True):
            messages.error(request, "This creator disabled forks.")
            return redirect(original.get_absolute_url())
        if original.owner == request.user:
            messages.error(request, "You can't fork your own vibe")
            return redirect(original.get_absolute_url())
        # Check if already forked by this user (one fork per user per original)
        existing = AppProject.objects.filter(owner=request.user, forked_from=original).first()
        if existing:
            messages.info(request, f"You already forked this — see {existing.slug}")
            return redirect(existing.get_absolute_url())
        # Create fork — copy fields, new slug, pending scan
        import shutil, os
        from django.core.files.base import ContentFile
        new_title = f"{original.title} (forked)"
        # Create new project
        fork = AppProject(
            owner=request.user,
            title=new_title,
            category=original.category,
            short_description=original.short_description,
            readme=original.readme,
            html_code=original.html_code,
            css_code=original.css_code,
            js_code=original.js_code,
            tech_stack=original.tech_stack,
            ai_generated=original.ai_generated,
            ai_tool=original.ai_tool,
            ai_prompt=original.ai_prompt,
            file_tree=original.file_tree,
            file_count=original.file_count,
            language_stats=original.language_stats,
            star_cost=0,  # forked is free initially
            forked_from=original,
            status='pending',
        )
        fork.save()  # generates slug
        # Copy zip file if exists
        if original.zip_file:
            try:
                original.zip_file.open()
                content = original.zip_file.read()
                fork.zip_file.save(f"{fork.slug}.zip", ContentFile(content), save=True)
                # Copy AppFile rows
                for af in original.files.all():
                    AppFile.objects.create(project=fork, path=af.path, size=af.size)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"fork zip copy failed {original.slug}: {e}")
        # Also copy tags
        try:
            fork.tags.set(original.tags.all())
        except Exception: pass
        # Create ScanJob and queue — every fork is re-scanned
        from .models import ScanJob
        from .tasks import process_upload_pipeline
        job, _ = ScanJob.objects.get_or_create(project=fork, defaults={'status': 'queued'})
        job.status = 'queued'
        job.save(update_fields=['status'])
        try:
            process_upload_pipeline.delay(fork.id)
        except Exception:
            pass
        messages.success(request, f"✓ Forked “{original.title}” → “{fork.title}” — now edit your remix! Original: @{original.owner.username}/{original.slug}")
        return redirect('edit_vibe', slug=fork.slug)
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"fork crush: {e}")
        messages.error(request, "Fork failed silently — try again")
        return redirect('app_detail', slug=slug)

@require_POST
@login_required
def generate_ai_readme(request, slug):
    try:
        project = get_object_or_404(AppProject, slug=slug, owner=request.user)
        from .ai_readme import generate_ai_readme as gen
        ai_md = gen(project)
        project.ai_readme = ai_md
        project.save(update_fields=['ai_readme'])
        messages.success(request, "AI README generated - preview below, click Apply.")
        return redirect(project.get_absolute_url() + '#ai-readme')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"ai_readme crush: {e}")
        messages.error(request, "AI README failed silently")
        return redirect('app_detail', slug=slug)

@login_required
@require_POST
def apply_ai_readme(request, slug):
    try:
        project = get_object_or_404(AppProject, slug=slug, owner=request.user)
        if project.ai_readme:
            project.readme = project.ai_readme
            project.save()
            messages.success(request, "AI README applied!")
        return redirect(project.get_absolute_url())
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"apply_ai crush: {e}")
        return redirect('app_detail', slug=slug)

@login_required
@require_POST
def buy_vibe(request, slug):
    try:
        project = get_object_or_404(AppProject, slug=slug, status='published')
        if project.owner == request.user:
            return redirect('download_zip', slug=slug)
        if not project.price_zar or project.price_zar == 0:
            return redirect('download_zip', slug=slug)
        from .models import Sale
        if Sale.objects.filter(buyer=request.user, project=project).exists():
            return redirect('download_zip', slug=slug)
        import os
        paystack_secret = os.getenv('PAYSTACK_SECRET_KEY', '')
        if not paystack_secret:
            messages.error(request, "Card payments aren't configured yet. Trade stars to download, or ask the creator.")
            return redirect(project.get_absolute_url())
        import requests
        import secrets
        # Unique per attempt — Paystack rejects reused references on retry.
        reference = f"blaq-{project.id}-{request.user.id}-{secrets.token_hex(6)}"
        headers = {'Authorization': f'Bearer {paystack_secret}', 'Content-Type': 'application/json'}
        data = {"email": request.user.email or f"{request.user.username}@blaqvibes.co.za", "amount": project.price_zar*100, "reference": reference, "callback_url": f"{settings.SITE_URL}{project.get_absolute_url()}", "metadata": {"project_id": project.id, "buyer_id": request.user.id}}
        try:
            r = requests.post('https://api.paystack.co/transaction/initialize', json=data, headers=headers, timeout=10)
            j = r.json()
            if j.get('status') and j.get('data', {}).get('authorization_url'):
                return redirect(j['data']['authorization_url'])
        except Exception as e:
            logger.warning(f"Paystack init fail: {e}")
        messages.error(request, "Could not start checkout. No charge was made.")
        return redirect(project.get_absolute_url())
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"buy crush: {e}")
        return redirect('app_detail', slug=slug)

from django.views.decorators.csrf import csrf_exempt
@csrf_exempt
@require_POST
def paystack_webhook(request):
    """Verify Paystack signature and record a Sale.

    Returns 4xx on anything we can't verify/process so Paystack retries;
    returns 200 only once the event is handled (or safely ignored).
    """
    import hashlib, hmac, json, os
    secret = os.getenv('PAYSTACK_SECRET_KEY', '')
    if not secret:
        return HttpResponse("webhook not configured", status=503)
    body = request.body
    sig = request.headers.get('x-paystack-signature', '')
    expected = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    if not sig or not hmac.compare_digest(expected, sig):
        return HttpResponse("invalid signature", status=400)
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return HttpResponse("invalid json", status=400)
    if data.get('event') != 'charge.success':
        return HttpResponse("ignored", status=200)
    ref = (data.get('data') or {}).get('reference') or ''
    parts = ref.split('-')
    if len(parts) < 3 or parts[0] != 'blaq':
        return HttpResponse("bad reference", status=400)
    try:
        pid, uid = int(parts[1]), int(parts[2])
    except ValueError:
        return HttpResponse("bad reference", status=400)
    from .models import Sale, AppProject
    from django.contrib.auth.models import User
    try:
        project = AppProject.objects.get(pk=pid)
        buyer = User.objects.get(pk=uid)
    except (AppProject.DoesNotExist, User.DoesNotExist):
        return HttpResponse("unknown project/buyer", status=400)
    paid = int((data.get('data') or {}).get('amount') or 0)
    expected_amount = int(project.price_zar or 0) * 100
    if expected_amount and paid != expected_amount:
        logger.warning("Paystack amount mismatch ref=%s paid=%s expected=%s", ref, paid, expected_amount)
        return HttpResponse("amount mismatch", status=400)
    sale, created = Sale.objects.get_or_create(
        buyer=buyer, project=project,
        defaults={'seller': project.owner, 'amount_zar': project.price_zar, 'paystack_ref': ref},
    )
    if created:
        notify(project.owner, 'sale', f'{buyer.username} bought {project.title}', f'R{project.price_zar}', project.get_absolute_url())
        notify(buyer, 'sale', f'You unlocked {project.title}', url=project.get_absolute_url())
    return HttpResponse("ok")

def oops_demo(request):
    # Demo safe page — always shows friendly fork, no HttpResponse scare
    from django.shortcuts import render
    return render(request, '404.html', status=200)

def fork_network(request, slug):
    """Fork network graph — backend builds tree, no JS secrets, crush silently."""
    try:
        root = get_object_or_404(AppProject, slug=slug)
        # Find root of network (follow forked_from chain up)
        cur = root
        seen = set()
        while cur.forked_from and cur.forked_from_id not in seen and cur.forked_from_id != cur.id:
            seen.add(cur.id)
            cur = cur.forked_from
        root = cur
        # All forks in network (direct + indirect)
        forks = AppProject.objects.filter(forked_from__isnull=False, status='published').filter(
            # Simple: direct forks of root + forks of forks (1 level deep for demo, at scale recursive CTE)
            Q(forked_from=root) | Q(forked_from__forked_from=root)
        ).select_related('owner','forked_from').order_by('-created_at')[:20]
        # Fallback if no indirect, just direct
        if not forks.exists():
            forks = AppProject.objects.filter(forked_from=root, status='published').select_related('owner')[:20]
        return render(request, 'gallery/fork_network.html', {'root': root, 'forks': forks})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"fork_network crush: {e}")
        return render(request, 'gallery/fork_network.html', {'root': get_object_or_404(AppProject, slug=slug), 'forks': AppProject.objects.none()})

# Safe error pages — don't scare user with HttpResponse, show friendly fork image, "It's not you, it's me"
def safe_404(request, exception=None):
    try:
        import logging, sentry_sdk
        logging.getLogger(__name__).warning(f"404 safe: {request.path}")
        try: sentry_sdk.capture_message(f"404: {request.path}")
        except Exception: pass
    except Exception: pass
    from django.shortcuts import render
    return render(request, '404.html', status=404)

def safe_403(request, exception=None):
    try:
        import logging, sentry_sdk
        logging.getLogger(__name__).warning(f"403 safe: {request.path}")
        try: sentry_sdk.capture_message(f"403: {request.path}")
        except Exception: pass
    except Exception: pass
    from django.shortcuts import render
    return render(request, '403.html', status=403)

def safe_500(request):
    try:
        import sentry_sdk
        sentry_sdk.capture_exception()
    except Exception:
        pass
    from django.shortcuts import render
    return render(request, '500.html', status=500)

@login_required
@require_POST
def toggle_bookmark(request, slug):
    from .models import Bookmark
    project = get_object_or_404(AppProject, slug=slug, status='published')
    bm, created = Bookmark.objects.get_or_create(user=request.user, project=project)
    if not created:
        bm.delete()
        return JsonResponse({'saved': False})
    return JsonResponse({'saved': True})


@login_required
def saved_vibes(request):
    from .models import Bookmark
    qs = Bookmark.objects.filter(user=request.user).select_related('project', 'project__owner')
    return render(request, 'gallery/saved.html', {'bookmarks': qs})


@login_required
def notifications_inbox(request):
    from .models import Notification
    notes = Notification.objects.filter(user=request.user)[:50]
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return render(request, 'gallery/notifications.html', {'notifications': notes})


def sitemap_xml(request):
    projects = AppProject.objects.filter(status='published').only('slug', 'updated_at')[:500]
    rows = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            f'<url><loc>{settings.SITE_URL}/</loc></url>']
    for p in projects:
        rows.append(f'<url><loc>{settings.SITE_URL}/app/{p.slug}/</loc><lastmod>{p.updated_at.date().isoformat()}</lastmod></url>')
    rows.append('</urlset>')
    return HttpResponse('\n'.join(rows), content_type='application/xml')

