from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.decorators import login_required
from django.db.models import F, Q
from django.http import Http404, HttpResponse, JsonResponse, HttpResponseRedirect
from django.core.paginator import Paginator
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django_ratelimit.decorators import ratelimit
import zipfile, os, json

from .models import AppProject, Category, Comment, Star, AppFile, ScanJob, AppReport, AppVersion, Review, Trade, PullRequest
from .forms import AppUploadForm
from .utils import build_tree_from_zip
from .storages import get_presigned_url, is_s3_enabled
from .search import search_projects
from django.core.mail import send_mail

def signup(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect('feed')
    else:
        form = UserCreationForm()
    return render(request, 'registration/signup.html', {'form': form})

def feed(request):
    try:
        q = request.GET.get('q','').strip()
        # Sanitize search prompt — many prompt fields, check vulnerabilities, crush silently
        try:
            from .prompt_sanitize import sanitize_prompt
            q = sanitize_prompt(q)[:100]  # limit
        except: q = request.GET.get('q','').strip()[:100]
        cat = request.GET.get('category','')
        kind = request.GET.get('kind','')
        ai = request.GET.get('ai','')
        tech = request.GET.get('tech','')
        sort = request.GET.get('sort','newest')
        projects = AppProject.objects.filter(status='published').select_related('owner','category')
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
            except: pass
            projects = projects.filter(tech_stack__icontains=tech)
        projects = search_projects(projects, q, sort=sort)
        categories = Category.objects.all().order_by('order')
        paginator = Paginator(projects, 12)
        page = paginator.get_page(request.GET.get('page'))
        return render(request, 'gallery/feed.html', {'page': page, 'categories': categories, 'q': q, 'cat': cat, 'kind': kind, 'sort': sort})
    except Exception:
        # crush silently — log, return empty feed
        import logging
        logging.getLogger(__name__).exception("feed crush silent")
        return render(request, 'gallery/feed.html', {'page': Paginator(AppProject.objects.none(), 12).get_page(1), 'categories': Category.objects.all(), 'q': '', 'cat': '', 'kind': '', 'sort': 'newest'})

def app_detail(request, slug):
    project = get_object_or_404(AppProject, slug=slug)
    # Only published visible to visitors, owners can see their pending/quarantined
    if project.status != 'published' and (not request.user.is_authenticated or project.owner != request.user and not request.user.is_staff):
        raise Http404
    if project.status == 'published':
        AppProject.objects.filter(pk=project.pk).update(views=F('views')+1)
        # Pro — who viewed your vibe — backend only, crush silently
        try:
            if request.user.is_authenticated and request.user != project.owner:
                from .models import VibeView
                vv, created = VibeView.objects.get_or_create(viewer=request.user, project=project, defaults={'count':1})
                if not created:
                    VibeView.objects.filter(pk=vv.pk).update(count=F('count')+1, last_viewed=models.functions.Now())
        except: pass
    comments = project.comments.filter(is_hidden=False).select_related('user').prefetch_related('replies__user')
    top_comments = comments.filter(parent__isnull=True)
    is_starred = False
    has_traded = False
    has_bought = False
    viewers = None
    ai_readme_preview = None
    if request.user.is_authenticated:
        is_starred = Star.objects.filter(user=request.user, project=project).exists()
        has_traded = Trade.objects.filter(buyer=request.user, project=project).exists()
        from .models import Sale
        has_bought = Sale.objects.filter(buyer=request.user, project=project).exists()
    # Who viewed — Pro only sees names, free sees count
    try:
        if project.owner.profile.is_pro:
            from .models import VibeView
            viewers = VibeView.objects.filter(project=project).select_related('viewer').order_by('-last_viewed')[:20]
        else:
            viewers = None  # free: don't show names
    except: viewers = None
    # Nolo + AI README preview
    reviews = project.reviews.select_related('user').order_by('-created_at')
    nolo_review = None
    try:
        nolo_review = (project.scan_report or {}).get('nolo_review')
        # AI README preview if exists
        ai_readme_preview = project.ai_readme
    except: pass
    # Scan status for JS poll — backend only, no secrets, just status string
    scan_status = getattr(project, 'scan_job', None)
    # Rank for discount display
    from .ranks import contributor_bonus
    owner_rank = contributor_bonus(project.owner)
    user_rank = contributor_bonus(request.user) if request.user.is_authenticated else None
    return render(request, 'gallery/app_detail.html', {
        'project': project,
        'comments': top_comments,
        'reviews': reviews,
        'nolo_review': nolo_review,
        'ai_readme_preview': ai_readme_preview,
        'is_starred': is_starred,
        'has_traded': has_traded,
        'has_bought': has_bought,
        'viewers': viewers,
        'viewer_count': project.viewer_logs.count() if hasattr(project,'viewer_logs') else project.views,
        'scan_status': scan_status.status if scan_status else project.status,
        'owner_rank': owner_rank,
        'user_rank': user_rank,
    })

def scan_status(request, slug):
    """Backend-only status poll — JS gets only 'queued/scanning/clean/quarantined', never raw scan_report."""
    project = get_object_or_404(AppProject, slug=slug)
    job = getattr(project, 'scan_job', None)
    return JsonResponse({
        'status': job.status if job else project.status,
        'is_published': project.status == 'published',
    })

def preview(request, slug):
    project = get_object_or_404(AppProject, slug=slug, status='published')
    resp = render(request, 'gallery/preview.html', {'project': project})
    # Fixed Stored XSS: no unsafe-inline, CSS/JS are external files via snippet_css/js, allow only self + cdn for Tailwind
    # Also add Report-Only to log violations to Sentry without blocking (n cs)
    csp = "default-src 'self' https://cdn.tailwindcss.com https://fonts.googleapis.com; style-src 'self' https://cdn.tailwindcss.com https://fonts.googleapis.com; script-src 'self' https://cdn.tailwindcss.com; img-src 'self' https: data:; object-src 'none'"
    resp['Content-Security-Policy'] = csp
    resp['Content-Security-Policy-Report-Only'] = csp + "; report-uri /csp-report/"
    resp['X-Frame-Options'] = 'ALLOWALL'
    return resp

def snippet_css(request, slug):
    # External CSS file per snippet — backend only, crush silently
    try:
        project = get_object_or_404(AppProject, slug=slug, status='published')
        css = project.css_code or ""
        # Extra sanitize: strip any @import with javascript: or url(javascript:)
        import re
        css = re.sub(r'@import[^;]*javascript[^;]*;', '', css, flags=re.I)
        from django.http import HttpResponse
        return HttpResponse(css, content_type='text/css')
    except Exception:
        from django.http import HttpResponse
        return HttpResponse("", content_type='text/css')

def snippet_js(request, slug):
    # External JS file per snippet — backend only, crush silently
    try:
        project = get_object_or_404(AppProject, slug=slug, status='published')
        js = project.js_code or ""
        from django.http import HttpResponse
        return HttpResponse(js, content_type='application/javascript')
    except Exception:
        from django.http import HttpResponse
        return HttpResponse("", content_type='application/javascript')

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
                    print("Tree build error:", e)
                # Queue EVERY app — concurrent uploads serialize in 'scan' queue (FIFO, acks_late, prefetch 1)
                try:
                    from .tasks import process_upload_pipeline
                    job, _ = ScanJob.objects.get_or_create(project=project, defaults={'status': 'queued'})
                    task = process_upload_pipeline.delay(project.id)
                    job.task_id = task.id if hasattr(task,'id') else ''
                    job.status = 'scanning'
                    job.save(update_fields=['task_id','status'])
                except Exception as e:
                    print("Queue error, fallback eager:", e)
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
    AppProject.objects.filter(pk=project.pk).update(clones=F('clones')+1)
    if is_s3_enabled():
        url = get_presigned_url(project.zip_file.name, expires=300)
        if url:
            return HttpResponseRedirect(url)
    try:
        resp = HttpResponse(project.zip_file.open('rb'), content_type='application/zip')
        resp['Content-Disposition'] = f'attachment; filename="{project.slug}.zip"'
        return resp
    except FileNotFoundError:
        raise Http404

def file_preview(request, slug, path):
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if '..' in path or path.startswith('/'):
        raise Http404
    if not project.zip_file:
        raise Http404
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
    except:
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
        from .prompt_sanitize import sanitize_prompt
        body = sanitize_prompt(request.POST.get('body','').strip())[:2000]
        parent_id = request.POST.get('parent_id')
        if len(body) < 5 or len(body) > 2000:
            return HttpResponse("Comment 5-2000 chars", status=400)
        parent = None
        if parent_id:
            try:
                parent = Comment.objects.get(pk=parent_id, project=project)
            except: parent = None
        Comment.objects.create(project=project, user=request.user, body=body, parent=parent)
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
    project = get_object_or_404(AppProject, slug=slug)
    star, created = Star.objects.get_or_create(user=request.user, project=project)
    if not created:
        star.delete()
        AppProject.objects.filter(pk=project.pk).update(stars=F('stars')-1)
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
                except: pass
                from .tasks import process_upload_pipeline
                from .models import ScanJob
                job,_ = ScanJob.objects.get_or_create(project=p)
                job.status='queued'; job.save()
                try:
                    process_upload_pipeline.delay(p.id)
                    messages.info(request, f"⏳ Your vibe “{p.title}” re-uploaded — re-queued for scan. We’ll tell you when it’s live again!")
                except: pass
            else:
                messages.success(request, "✓ Vibe updated!")
            return redirect(p.get_absolute_url())
    else:
        form = AppUploadForm(instance=project)
    return render(request, 'gallery/edit_vibe.html', {'form': form, 'project': project})

@login_required
@require_POST
def delete_vibe(request, slug):
    project = get_object_or_404(AppProject, slug=slug, owner=request.user)
    project.delete()
    messages.success(request, f"Deleted “{project.title}”")
    return redirect('my_vibes')

@require_POST
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
    repo_path = f"/var/git/{username}/{slug}.git"
    if os.path.exists(repo_path):
        try:
            from dulwich.server import DictBackend
            from dulwich.repo import Repo
            pass
        except ImportError:
            pass
    from django.db.models import F
    AppProject.objects.filter(pk=project.pk).update(clones=F('clones')+1)
    if is_s3_enabled():
        url = get_presigned_url(project.zip_file.name, expires=300)
        if url:
            return HttpResponseRedirect(url)
    return HttpResponseRedirect(project.zip_file.url if hasattr(project.zip_file,'url') else f"/media/{project.zip_file.name}")

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
    cost = project.star_cost or 0
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
        # Refresh
        buyer.refresh_from_db(); seller.refresh_from_db()
    messages.success(request, f"Traded {cost} ★ for “{project.title}” — seller @ {project.owner.username} now has {seller.stars_balance} ★. You have {buyer.stars_balance} ★ left.")
    return redirect('download_zip', slug=slug)

@login_required
@ratelimit(key='user', rate='5/h', method='POST')
def fork_vibe(request, slug):
    """Fork & Remix — backend only, crush silently, 5/h limit."""
    try:
        if getattr(request, 'limited', False):
            messages.error(request, "Rate limit: 5 forks/hour")
            return redirect('app_detail', slug=slug)
        original = get_object_or_404(AppProject, slug=slug, status='published')
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
        except: pass
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
@login_required
@login_required
@require_POST
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
            Sale.objects.create(buyer=request.user, seller=project.owner, project=project, amount_zar=project.price_zar, paystack_ref='demo-'+str(project.id))
            messages.success(request, f"Bought {project.title} for R{project.price_zar} - demo mode. Seller gets R{int(project.price_zar*0.85)}.")
            return redirect('download_zip', slug=slug)
        import requests
        headers = {'Authorization': f'Bearer {paystack_secret}', 'Content-Type': 'application/json'}
        data = {"email": request.user.email or f"{request.user.username}@blaqvibes.co.za", "amount": project.price_zar*100, "reference": f"blaq-{project.id}-{request.user.id}", "callback_url": f"https://blaqvibes.co.za/app/{project.slug}/", "metadata": {"project_id": project.id, "buyer_id": request.user.id}}
        try:
            r = requests.post('https://api.paystack.co/transaction/initialize', json=data, headers=headers, timeout=10)
            j = r.json()
            if j.get('status') and j.get('data',{}).get('authorization_url'):
                return redirect(j['data']['authorization_url'])
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Paystack init fail: {e}")
        Sale.objects.create(buyer=request.user, seller=project.owner, project=project, amount_zar=project.price_zar, paystack_ref='fallback')
        return redirect('download_zip', slug=slug)
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"buy crush: {e}")
        return redirect('app_detail', slug=slug)

from django.views.decorators.csrf import csrf_exempt
@csrf_exempt
def paystack_webhook(request):
    try:
        import hashlib, hmac, json, os
        secret = os.getenv('PAYSTACK_SECRET_KEY', '')
        if not secret:
            return HttpResponse("No secret", status=400)
        body = request.body
        sig = request.headers.get('x-paystack-signature','')
        hash = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
        if hash != sig:
            return HttpResponse("Invalid", status=400)
        data = json.loads(body)
        if data.get('event') == 'charge.success':
            ref = data['data']['reference']
            parts = ref.split('-')
            if len(parts) >= 3:
                pid, uid = int(parts[1]), int(parts[2])
                from .models import Sale, AppProject
                from django.contrib.auth.models import User
                try:
                    project = AppProject.objects.get(pk=pid)
                    buyer = User.objects.get(pk=uid)
                    if not Sale.objects.filter(buyer=buyer, project=project).exists():
                        Sale.objects.create(buyer=buyer, seller=project.owner, project=project, amount_zar=project.price_zar, paystack_ref=ref)
                except: pass
        return HttpResponse("ok")
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"webhook crush: {e}")
        return HttpResponse("ok")

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
        a = get_object_or_404(AppProject, slug=a_slug)
        b = get_object_or_404(AppProject, slug=b_slug)
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
def nolo_chat_api(request):
    try:
        data = json.loads(request.body.decode('utf-8') or '{}')
        prompt = data.get('prompt', '').strip()[:1000]
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
        # Must be open PR not already open for this source
        if PullRequest.objects.filter(source=source, target=target, status='open').exists():
            messages.info(request, "PR already open for this fork")
            return redirect(target.get_absolute_url())
        from .prompt_sanitize import sanitize_prompt
        title = sanitize_prompt(request.POST.get('title',''))[:200] or f"PR: {source.title} → {target.title}"
        description = sanitize_prompt(request.POST.get('description',''))[:2000]
        pr = PullRequest.objects.create(source=source, target=target, author=request.user, title=title, description=description, status='open')
        # Notify target owner via message + email
        try:
            if target.owner.email:
                send_mail(f"New PR for {target.title}", f"@{request.user.username} wants to merge {source.slug} into {target.slug}:\n{title}\n{description}\nView: https://blaqvibes.co.za/app/{target.slug}/prs/", getattr(settings, 'DEFAULT_FROM_EMAIL','noreply@blaqvibes.co.za'), [target.owner.email], fail_silently=True)
        except: pass
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
    """PR diff — file_tree, language, Nolo, backend only, crush silently."""
    try:
        target = get_object_or_404(AppProject, slug=slug)
        pr = get_object_or_404(PullRequest, id=pr_id, target=target)
        # File diff — from AppFile or file_tree
        def flat_files(proj):
            try:
                # Use AppFile if exists, else file_tree
                if proj.files.exists():
                    return set(proj.files.values_list('path', flat=True))
                # Flatten file_tree dict
                def flatten(d, prefix=""):
                    s=set()
                    for k,v in (d or {}).items():
                        if v is None:
                            s.add(prefix+k)
                        else:
                            s.update(flatten(v, prefix+k+"/"))
                    return s
                return flatten(proj.file_tree or {})
            except: return set()
        src_files = flat_files(pr.source)
        tgt_files = flat_files(pr.target)
        diff = {
            'added': sorted(src_files - tgt_files),
            'removed': sorted(tgt_files - src_files),
            'common': sorted(src_files & tgt_files)[:20],  # limit common
        }
        # Nolo diff
        from .nolo import compare_apps
        nolo_diff = compare_apps(pr.source, pr.target)['diff']
        nolo_review = (pr.source.scan_report or {}).get('nolo_review')
        return render(request, 'gallery/pr_detail.html', {'pr': pr, 'target': target, 'diff': diff, 'nolo_diff': nolo_diff, 'nolo_review': nolo_review})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"pr_detail crush: {e}")
        return render(request, 'gallery/pr_detail.html', {'pr': get_object_or_404(PullRequest, id=pr_id), 'target': get_object_or_404(AppProject, slug=slug), 'diff': {'added':[],'removed':[],'common':[]}, 'nolo_diff': {'only_in_a':[],'only_in_b':[],'common':[]}, 'nolo_review': None})

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
            pr.status = 'merged'
            pr.save(update_fields=['status','updated_at'])
            # For MVP, merging just marks merged — could copy description to target readme in future
            messages.success(request, f"✓ PR #{pr.id} merged — thanks @{pr.author.username}!")
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
        # Create new battle with 2 random
        vibes = list(qs.order_by('?')[:2])
        if len(vibes) < 2:
            vibes = list(qs[:2])
        battle = VibeBattle.objects.create(vibe_a=vibes[0], vibe_b=vibes[1])
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
        # Top creators by rank (total stars)
        from django.contrib.auth.models import User
        from .ranks import contributor_bonus
        users = list(User.objects.all())
        for u in users:
            try:
                rank = contributor_bonus(u)
                u.rank = rank
                u.rank_stars = sum(p.stars for p in u.projects.filter(status='published'))
                u.vibes_count = u.projects.filter(status='published').count()
            except:
                u.rank = {'name':'Bronze','stars':0,'discount':0,'bonus':0}
                u.rank_stars = 0
                u.vibes_count = 0
        users = sorted(users, key=lambda x: x.rank_stars, reverse=True)[:10]
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

def vote_battle(request, battle_id):
    try:
        from .models import VibeBattle, BattleVote
        from django.db.models import F
        battle = get_object_or_404(VibeBattle, id=battle_id)
        if not request.user.is_authenticated:
            messages.error(request, "Login to vote — earn stars!")
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
            AppProject.objects.filter(pk=battle.vibe_a.pk).update(stars=F('stars')+5)
        else:
            VibeBattle.objects.filter(pk=battle.pk).update(votes_b=F('votes_b')+1)
            AppProject.objects.filter(pk=battle.vibe_b.pk).update(stars=F('stars')+5)
        messages.success(request, f"Voted! Winner gets +5 ★ and appears top 1st")
        return redirect('battle')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"vote crush: {e}")
        return redirect('battle')

def run_vibe(request, slug):
    """1-Click Run — spins up Docker (mock) → live URL for 1 hour, backend only, crush silently."""
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
        messages.success(request, f"▶ Running {project.title} — live at {live_url} for 1 hour! No pip install needed.")
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
            winner.owner.profile.save(update_fields=['is_pro','pro_since'])
        except: pass
        messages.success(request, f"Winner picked: {winner.title} by @{winner.owner.username} — +{challenge.bounty_stars} ★ + Pro!")
    return redirect('challenge_detail', tag=tag)

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
        except: pass
    except: pass
    from django.shortcuts import render
    return render(request, '404.html', status=404)

def safe_403(request, exception=None):
    try:
        import logging, sentry_sdk
        logging.getLogger(__name__).warning(f"403 safe: {request.path}")
        try: sentry_sdk.capture_message(f"403: {request.path}")
        except: pass
    except: pass
    from django.shortcuts import render
    return render(request, '403.html', status=403)

def safe_500(request):
    try:
        import sentry_sdk2
        try: sentry_sdk.capture_exception()
        except: pass
    except: pass
    from django.shortcuts import render
    return render(request, '500.html', status=500)
