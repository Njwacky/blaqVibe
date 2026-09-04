from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q, Count, Prefetch, Sum
from django.http import Http404, HttpResponse, JsonResponse, HttpResponseRedirect
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django_ratelimit.decorators import ratelimit
import zipfile, os, json, logging

from .models import AppProject, Category, Comment, Star, AppFile, ScanJob, AppReport, AppVersion, Review, Trade, PullRequest, ProjectCoOwner
from .forms import AppUploadForm, CoOwnerForm, CommentForm, ReviewForm
from .search import search_projects
from .access import (
    access_denied_message,
    last_scanned_version,
    user_can_download,
    user_can_see_project,
    user_is_moderator,
)
from .zip_serve import serve_named_zip, serve_project_zip, owner_scan_reason, scan_progress
from .notify import notify
from . import taste
from .taxonomy import KIND_BY_VALUE, PROGRAM_KINDS, coerce_kind
from django.core.mail import send_mail
from users.forms import SignUpForm
from django.utils.http import url_has_allowed_host_and_scheme

from .views_community import (
    nolo_compare,
    nolo_chat,
    nolo_chat_api,
    nolo_help,
    nolo_fix_api,
    nolo_readme_api,
    starter_gallery,
    studio,
    create_pr,
    pr_list,
    pr_detail,
    pr_action,
    battle,
    battle_leaderboard,
    battle_history,
    vote_battle,
    run_vibe,
    copy_increment,
    challenge_list,
    generate_challenges,
    approve_challenge,
    challenge_detail,
    pick_challenge_winner,
)
logger = logging.getLogger(__name__)

def safe_internal_next(request, default=''):
    """Same-origin relative `next` URL, or default.
    """
    candidate = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if not candidate.startswith('/') or candidate.startswith('//'):
        return default
    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default

@ratelimit(key='ip', rate='10/h', method='POST')
def signup(request):
    """Account creation — rate limited per IP.
    """
    if getattr(request, 'limited', False):
        return HttpResponse("Too many signups from this network. Try again later.", status=429)
    next_url = safe_internal_next(request)
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            try:
                from users.views import send_verify_email
                if user.email:
                    send_verify_email(request, user)
            except Exception:
                logger.exception('verify email send failed')
            messages.success(request, "Welcome to BlaqVibes — we sent a confirmation link to your email.")
            return redirect(next_url or 'feed')
    else:
        form = SignUpForm()
    return render(request, 'registration/signup.html', {'form': form, 'next': next_url})

def feed(request):
    """The discovery grid.
    """
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
        # 'foryou' is the default only for people we actually have signal on.
        default_sort = 'foryou' if taste.has_enough_signal(request.user) else 'newest'
        sort = request.GET.get('sort', '') or default_sort
        program_kind = coerce_program_kind_filter(request.GET.get('program'))
        runnable = request.GET.get('runnable', '')
        following = request.GET.get('following', '')
        # Trust filter — "instruction must always win", like every other
        # filter here. 4 points: (1) only the two whitelisted tier strings
        # are accepted, anything else is ignored — an unknown value can
        # never become a silent 'exclude everything'; (2) it rides the
        # existing trust db_index, so it stays a range scan; (3) it is a
        # GET param, never a session default — trust filtering is a
        # choice per visit, not a preference the site guesses for you;
        # (4) it composes with every other filter and sort, and the empty
        # result is honest (no verified vibes matching → show none, never
        # quietly refill the grid with unverified ones).
        trust_filter = request.GET.get('trust', '')
        if trust_filter not in ('verified', 'scanned'):
            trust_filter = ''
        projects = AppProject.objects.filter(status='published').select_related('owner','owner__profile','category').prefetch_related('tags')
        if cat:
            projects = projects.filter(category__slug=cat)
        if kind == 'snippet':
            projects = projects.exclude(html_code='')
        elif kind == 'full_app':
            projects = projects.exclude(zip_file='')
        if program_kind:
            projects = projects.filter(kind=program_kind)
        if trust_filter:
            projects = projects.filter(trust=trust_filter)
        if runnable == '1':
            # Honest filter: only vibes that really can run in the sandbox —
            # an inline snippet (html_code) OR an assembled static-site ZIP
            # (preview_mode static_zip, entry already found). Mirrors
            # AppProject.can_run_preview so the filter and the badge agree.
            from django.db.models import Q as _Q
            projects = projects.filter(
                _Q(preview_mode='snippet', html_code__gt='')
                | _Q(preview_mode='static_zip', static_entry__gt='')
            )
        # "From creators you follow" — a tab, not a sort, so it composes
        # with every filter above instead of replacing them.
        if following == '1':
            if not request.user.is_authenticated:
                messages.info(request, "Sign in to see vibes from creators you follow.")
                return redirect('feed')
            followed = request.user.following.values_list('following_id', flat=True)
            projects = projects.filter(owner_id__in=list(followed))
        if ai == '1':
            projects = projects.filter(ai_generated=True)
        if tech:
            try:
                import bleach
                tech = bleach.clean(tech, tags=[], strip=True)[:100]
            except Exception: pass
            projects = projects.filter(tech_stack__icontains=tech)
        # When search is disabled, force q to '': search_projects treats an
        # empty q as "no search" and returns the sorted queryset, so the other
        # filters still work (index-only, free) without a branch. Fail open — if
        # the setting can't be read, search stays on (default True) so a broken
        # DB row never silences the feed.
        try:
            from users.models import SiteSettings
            if not SiteSettings.get().search_enabled:
                q = ''
        except Exception:
            pass
        projects = search_projects(projects, q, sort=sort, user=request.user)
        if not projects.exists() and getattr(settings, 'SEED_DEMO', False):
            try:
                from .seed import seed_demo
                seed_demo()
                projects = search_projects(
                    AppProject.objects.filter(status='published').select_related(
                        'owner', 'owner__profile', 'category'
                    ).prefetch_related('tags'),
                    q, sort=sort, user=request.user,
                )
                if cat:
                    projects = projects.filter(category__slug=cat)
                if kind == 'snippet':
                    projects = projects.exclude(html_code='')
                elif kind == 'full_app':
                    projects = projects.exclude(zip_file='')
                if trust_filter:
                    projects = projects.filter(trust=trust_filter)
            except Exception:
                logger.exception('auto seed_demo failed')
        categories = Category.objects.all().order_by('order')
        paginator = Paginator(projects, 12)
        page = paginator.get_page(request.GET.get('page'))
        my_kinds = taste.top_kinds(request.user, limit=3) if request.user.is_authenticated else []
        ctx = {
            'page': page,
            'categories': categories,
            'q': q,
            'cat': cat,
            'kind': kind,
            'sort': sort,
            'program_kinds': PROGRAM_KINDS,
            'program_kind': program_kind,
            'runnable': runnable,
            'following': following,
            'trust': trust_filter,
            'personalized': sort == 'foryou' and bool(my_kinds),
            'my_kinds': [KIND_BY_VALUE[k] for k in my_kinds if k in KIND_BY_VALUE],
        }
        # Rails are only noise once somebody is filtering or searching: they
        # are a discovery surface for "I just landed here", not a second
        # result set on top of a query.
        unfiltered = not any([q, cat, kind, program_kind, runnable, trust_filter, following, ai, tech])
        # The "BlaqVibes Today" loop follows the same rule: it links to the
        # creator's own vibes and followed creators' work, so on a search or
        # the Following tab it would put cards on the page that the active
        # filter excluded.
        ctx['unfiltered'] = unfiltered
        if unfiltered:
            try:
                from . import trending
                from .daily import today_challenge
                exclude_owner = request.user if request.user.is_authenticated else None
                ctx['trending'], ctx['trending_is_hot'] = trending.trending_vibes(
                    limit=6, exclude_owner=exclude_owner)
                ctx['rising_creators'] = trending.rising_creators(
                    limit=4, exclude_user=request.user if request.user.is_authenticated else None)
                ctx['recent_remixes'] = trending.recent_remixes(limit=4)
                ctx['activity'] = trending.activity_summary()
                ctx['daily'] = today_challenge()
                if request.user.is_authenticated:
                    ctx['suggested_creators'] = trending.suggested_creators(request.user, limit=4)
                    ctx['following_count'] = request.user.following.count()
                    # First-run strip: only while there is something to do.
                    ctx['show_onboarding'] = (
                        request.user.projects.count() == 0
                        or request.user.following.count() == 0
                    )
            except Exception:
                logger.exception('feed rails failed')
        return render(request, 'gallery/feed.html', ctx)
    except Exception:
        logger.exception("feed crush silent")
        return render(request, 'gallery/feed.html', {'page': Paginator(AppProject.objects.none(), 12).get_page(1), 'categories': Category.objects.all(), 'q': '', 'cat': '', 'kind': '', 'sort': 'newest', 'program_kinds': PROGRAM_KINDS, 'program_kind': '', 'runnable': '', 'following': '', 'trust': '', 'personalized': False, 'my_kinds': []})

def coerce_program_kind_filter(value):
    """Only a real taxonomy value may reach the WHERE clause.

    Why not pass request.GET straight to .filter(kind=...)? An arbitrary
    string silently returns an empty grid, which looks like "the site lost
    my vibes" rather than "that filter does not exist". Blank means no
    filter, which is the honest default.
    """
    value = (value or '').strip().lower()
    if not value:
        return ''
    return value if value in KIND_BY_VALUE else ''

def app_detail(request, slug):
    qs = AppProject.objects.select_related(
        'owner', 'owner__profile', 'category', 'forked_from', 'forked_from__owner'
    ).prefetch_related('forks__owner', 'files', 'co_owners__user').annotate(
        forks_count=Count('forks', distinct=True),
        prs_count=Count('prs_incoming', distinct=True),
        comment_count=Count('comments', filter=Q(comments__is_hidden=False), distinct=True),
    )
    project = get_object_or_404(qs, slug=slug)
    # Only published visible to visitors, owners can see their pending/quarantined
    if not user_can_see_project(request.user, project):
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
        # Learn what this person opens. Deduped in cache, one row, never
        # fatal — see gallery/taste.py.
        taste.record(request.user, project, 'view', project=project)
    comments_open = bool(getattr(project.owner.profile, 'allow_comments', True))
    visible_replies = Prefetch(
        'replies',
        queryset=Comment.objects.filter(is_hidden=False).select_related('user'),
    )
    top_comments = (
        project.comments.filter(is_hidden=False, parent__isnull=True)
        .select_related('user')
        .prefetch_related(visible_replies)
        if comments_open else project.comments.none()
    )
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
    # Who may `git push` — owner or a co-owner, never anonymous, never a
    # removed vibe. The daemon re-checks this with Basic auth on the wire;
    # this flag only decides whether the page shows push instructions.
    can_push = (
        request.user.is_authenticated and project.status != 'removed'
        and (request.user.pk == project.owner_id
             or any(co.user_id == request.user.pk for co in project.co_owners.all()))
    )
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
    # Publish → launch loop: show the owner the next step for THIS artifact.
    launch_next = None
    if request.user.is_authenticated and request.user == project.owner and project.status == 'published':
        try:
            from .artifact_detect import artifact_route, detect_artifact
            detected = detect_artifact(project)
            route = artifact_route(detected) if detected else None
            if route:
                launch_next = {'value': detected, 'name': route['name'], 'icon': route['icon'], 'note': route['note']}
        except Exception:
            logger.exception('artifact detect failed %s', project.slug)
    return render(request, 'gallery/app_detail.html', {
        'project': project,
        'comments': top_comments,
        'comments_open': comments_open,
        'reviews': reviews,
        'nolo_review': nolo_review,
        'ai_readme_preview': ai_readme_preview,
        'is_starred': is_starred,
        'is_bookmarked': is_bookmarked,
        'has_traded': has_traded,
        'has_bought': has_bought,
        'can_download': can_download,
        'can_push': can_push,
        'scan_reason': owner_scan_reason(project) if request.user.is_authenticated and (request.user == project.owner or user_is_moderator(request.user)) else '',
        'scan_progress': (
            scan_progress(project)
            if project.status != 'published'
            and request.user.is_authenticated
            and (request.user == project.owner or user_is_moderator(request.user))
            else None
        ),
        'comment_count': getattr(project, 'comment_count', 0),
        'published_forks': [f for f in project.forks.all() if f.status == 'published'][:5],
        'viewers': viewers,
        'show_viewer_upsell': show_viewer_upsell,
        'viewer_count': project.views,
        'scan_status': scan_status.status if scan_status else project.status,
        'owner_rank': owner_rank,
        'user_rank': user_rank,
        'compare_options': compare_options,
        'launch_next': launch_next,
        'forks_count': getattr(project, 'forks_count', 0),
        'prs_count': getattr(project, 'prs_count', 0),
        'show_language': getattr(project.owner.profile, 'show_language', True),
    })

def scan_status(request, slug):
    """Owner/moderator poll for unpublished; public only after publish.
    """
    project = get_object_or_404(AppProject, slug=slug)
    if not user_can_see_project(request.user, project):
        raise Http404
    job = getattr(project, 'scan_job', None)
    data = {
        'status': job.status if job else project.status,
        'is_published': project.status == 'published',
        'reason': '',
    }
    if request.user.is_authenticated and (
        request.user.pk == project.owner_id or user_is_moderator(request.user)
    ):
        data['reason'] = owner_scan_reason(project)
        # Rich, owner-facing progress so the waiting page can keep the
        # step checklist, queue position and file details in sync as the
        # JS polls — not just a bare status word.
        data['progress'] = scan_progress(project)
    return JsonResponse(data)

def preview_files(request, slug):
    """Honest file preview — names + README. Not a Docker host, not a live server."""
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if project.html_code and not project.zip_file:
        return redirect('preview', slug=slug)
    files = project.files.all()[:100]
    taste.record(request.user, project, 'preview', project=project)
    return render(request, 'gallery/preview_files.html', {
        'project': project,
        'files': files,
        'can_download': user_can_download(request.user, project) if project.zip_file else True,
    })

def preview(request, slug):
    """Safe preview shell — the user's HTML/JS runs only inside a sandboxed
        (opaque-origin) iframe, never in a privileged context.

          * a snippet → the iframe points at snippet_doc;
          * a static-site ZIP → the iframe points at run_static (an assembled,
            single-document version of the ZIP's entry HTML).
        Both are the same opaque-origin sandbox, so the shell's own CSP is
        identical; only the iframe src differs. A ZIP that is NOT static-runnable
        still redirects to the honest file list.
    """
    project = get_object_or_404(AppProject, slug=slug, status='published')
    # Which runnable shape is this? An inline snippet (html_code) always runs
    # as a snippet; otherwise a static-site ZIP runs if the classifier found
    # its entry. Deciding from concrete content (not preview_mode alone) keeps
    # a just-uploaded, not-yet-classified snippet runnable — html_code is the
    # only evidence the shell needs.
    if (project.html_code or '').strip():
        run_mode = 'snippet'
    elif project.preview_mode == 'static_zip' and (project.static_entry or '').strip():
        run_mode = 'static_zip'
    else:
        # Nothing runs here honestly — send them to files (ZIP) or detail.
        if project.zip_file:
            return redirect('preview_files', slug=slug)
        return redirect(project.get_absolute_url())
    from .preview_token import issue_snippet_token
    taste.record(request.user, project, 'preview', project=project)
    resp = render(request, 'gallery/preview.html', {
        'project': project,
        'snippet_token': issue_snippet_token(project.slug),
        'run_mode': run_mode,
    })
    # This shell has no scripts of its own — lock it down (its stylesheet is
    # an external file; 'unsafe-inline' is left off script-src entirely).
    csp = (
        "default-src 'none'; frame-src 'self'; img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; form-action 'none'"
    )
    resp['Content-Security-Policy'] = csp
    resp['Content-Security-Policy-Report-Only'] = csp + "; report-uri /csp-report/"
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    resp['Referrer-Policy'] = 'no-referrer'
    return resp

def _referer_is_our_preview(request, slug):
    from urllib.parse import urlparse
    referer = request.META.get('HTTP_REFERER', '')
    if not referer:
        return False
    parsed = urlparse(referer)
    if parsed.path.rstrip('/') != f'/app/{slug}/preview':
        return False
    referer_host = (parsed.hostname or '').lower()
    request_host = (request.get_host() or '').split(':')[0].lower()
    return bool(referer_host) and referer_host == request_host

def snippet_request_is_framed(request, slug):
    """True only for the sandboxed preview iframe with a valid signed token."""
    from .preview_token import snippet_token_is_valid
    dest = (request.META.get('HTTP_SEC_FETCH_DEST') or '').lower()
    if dest == 'document':
        return False
    if not snippet_token_is_valid(slug, request.GET.get('t', '')):
        return False
    if dest == 'iframe':
        return True
    # Old browsers omit Sec-Fetch-Dest. Require a same-host preview Referer
    # so a stolen token cannot be opened as a top-level first-party page.
    return _referer_is_our_preview(request, slug)

def snippet_asset_request_is_allowed(request, slug, dest_values):
    """Gate for a snippet's own CSS/JS files (subresources of snippet_doc).

    Modern browsers tag subresources with Sec-Fetch-Dest ('style'/'script');
    a subresource dest can never be a top-level navigation, so the
    short-lived signed token is enough. Older browsers omit the header, so
    fall back to a same-host Referer pointing at the snippet document — the
    token rides along in its query string and we verify it either way.
    """
    from urllib.parse import parse_qs, urlparse
    from .preview_token import snippet_token_is_valid
    dest = (request.META.get('HTTP_SEC_FETCH_DEST') or '').lower()
    if dest:
        if dest not in dest_values:
            return False
        return snippet_token_is_valid(slug, request.GET.get('t', ''))
    referer = request.META.get('HTTP_REFERER', '')
    parsed = urlparse(referer)
    if not referer or not parsed.path:
        return False
    if parsed.path.rstrip('/') != f'/app/{slug}/snippet':
        return False
    referer_host = (parsed.hostname or '').lower()
    request_host = (request.get_host() or '').split(':')[0].lower()
    if referer_host != request_host:
        return False
    referer_token = (parse_qs(parsed.query).get('t') or [''])[0]
    return snippet_token_is_valid(slug, request.GET.get('t', '') or referer_token)

def snippet_asset(request, slug, kind):
    """A snippet's CSS or JS served as its own file (text/css / text/javascript).

    Same gate as snippet_doc: reachable only by the sandboxed (opaque-origin)
    preview document holding the short-lived signed token, so user code never
    loads — let alone runs — in a privileged context.
    """
    project = get_object_or_404(AppProject, slug=slug, status='published')
    dest_values = ('style',) if kind == 'css' else ('script',)
    if not snippet_asset_request_is_allowed(request, slug, dest_values):
        return render(request, 'gallery/snippet_blocked.html', {'project': project}, status=403)
    if kind == 'css':
        body = project.css_code or ''
        content_type = 'text/css; charset=utf-8'
    else:
        body = project.js_code or ''
        content_type = 'text/javascript; charset=utf-8'
    resp = HttpResponse(body, content_type=content_type)
    resp['Content-Security-Policy'] = "default-src 'none'"
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Cross-Origin-Resource-Policy'] = 'same-origin'
    resp['Referrer-Policy'] = 'no-referrer'
    resp['Cache-Control'] = 'no-store'
    return resp

def snippet_doc(request, slug):
    """The raw snippet document — HTML only.

    The snippet's CSS and JS are separate files (snippet_asset) loaded via
    <link>/<script src>. Served ONLY into an <iframe sandbox="allow-scripts">
    (opaque origin) with a short-lived signed token. CSP sandbox on the
    response also applies if this URL is ever opened outside that iframe.
    """
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if not snippet_request_is_framed(request, slug):
        return render(request, 'gallery/snippet_blocked.html', {'project': project}, status=403)
    resp = render(request, 'gallery/snippet_doc.html', {
        'project': project,
        'token': request.GET.get('t', ''),
    })
    # 'unsafe-inline' stays because the user's own html_code is rendered raw
    # and may carry its own inline blocks; our page chrome uses none.
    resp['Content-Security-Policy'] = (
        "sandbox allow-scripts; "
        "default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src data: https: http:; media-src data: https:; "
        "font-src data: https://fonts.gstatic.com; "
        "connect-src https://cdn.tailwindcss.com; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'self'"
    )
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    resp['Referrer-Policy'] = 'no-referrer'
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Cross-Origin-Resource-Policy'] = 'same-origin'
    return resp

def run_static(request, slug):
    """Assembled single-document run of a static-site ZIP.
        The ZIP's entry HTML with its local CSS/JS/images inlined (gallery.runner),
        served ONLY into the same sandboxed, opaque-origin iframe as snippet_doc,
        behind the same short-lived signed token. No file is served from our
        origin — the assembled bytes are one document, exactly like a snippet.
    """
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if project.preview_mode != 'static_zip' or not project.static_entry:
        raise Http404
    if not snippet_request_is_framed(request, slug):
        return render(request, 'gallery/snippet_blocked.html', {'project': project}, status=403)
    from .runner import assemble_runnable_document
    document = assemble_runnable_document(project.zip_file, project.static_entry)
    if not document:
        # Nothing to run — honest empty state inside the frame.
        return render(request, 'gallery/snippet_blocked.html', {'project': project}, status=404)
    resp = HttpResponse(document, content_type='text/html; charset=utf-8')
    resp['Content-Security-Policy'] = (
        "sandbox allow-scripts; "
        "default-src 'none'; script-src 'unsafe-inline' https://cdn.tailwindcss.com; "
        "style-src 'unsafe-inline' https://fonts.googleapis.com; "
        "img-src data: https: http:; media-src data: https:; "
        "font-src data: https://fonts.gstatic.com; "
        "connect-src https://cdn.tailwindcss.com; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'self'"
    )
    resp['X-Frame-Options'] = 'SAMEORIGIN'
    resp['Referrer-Policy'] = 'no-referrer'
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Cross-Origin-Resource-Policy'] = 'same-origin'
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
                    from .ziputil import build_tree
                    tree, file_list = build_tree(project.zip_file)
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
                try:
                    if SiteSettings.get().auto_run_enabled:
                        messages.info(request, "File preview is on the vibe page after the scan. This is not a live server.")
                except Exception:
                    pass
            else:
                # Snippet scan step (gallery.trust.snippet_evidence): a pure
                # regex secrets sweep in-request — no queue, no LLM, no
                # subprocess — so snippets get REAL badge evidence too.
                # Evidence only; the verdict is still written by
                # apply_trust_grade alone. Crush silently.
                try:
                    from .trust import snippet_evidence
                    snippet_evidence(project)
                except Exception:
                    logger.exception('snippet evidence failed %s', project.slug)

                flagged = bool((project.scan_report or {}).get('snippet_scan', {}).get('secrets_found'))
                if flagged:
                    project.status = 'pending'
                    project.save(update_fields=['status'])
                    try:
                        from .trust import apply_trust_grade
                        apply_trust_grade(project)
                    except Exception:
                        logger.exception('snippet grade failed %s', project.slug)
                    notify(
                        project.owner,
                        'quarantined',
                        f'“{project.title}” is held for review',
                        'The code looks like it contains an API key or token. Remove it and '
                        'edit the vibe — it goes straight to the feed after that.',
                        project.get_absolute_url(),
                    )
                    try:
                        from .reports import moderators_to_notify
                        for staff in moderators_to_notify(project.owner):
                            notify(
                                staff,
                                'report',
                                f'Snippet held: {project.title}',
                                'Secret-shaped content flagged at publish — needs a human look.',
                                project.get_absolute_url(),
                            )
                    except Exception:
                        logger.debug('moderator fan-out skipped for %s', project.slug)
                    messages.warning(
                        request,
                        "“%s” is held for review — the code looks like it contains an API "
                        "key or token. Remove it and edit the vibe to go live." % project.title,
                    )
                else:
                    project.status = 'published'
                    project.save(update_fields=['status'])
                    # Status just became published — re-grade so the badge
                    # lands in the same request (pending graded 'unknown').
                    try:
                        from .trust import apply_trust_grade
                        apply_trust_grade(project)
                    except Exception:
                        logger.exception('snippet grade failed %s', project.slug)
                    messages.success(
                        request,
                        f"Your snippet “{project.title}” is published — it’s on the feed now.",
                    )
                    try:
                        from users.progress import award
                        award(project.owner, 'publish', ref=f'project:{project.pk}')
                    except Exception:
                        logger.exception('publish xp failed %s', project.slug)
                # Snippets never enter the scan queue, so this is the only
                # place they can be labelled. Heuristic only: a publish is a
                # user waiting on a response, and an LLM call belongs on a
                # queue, not in that wait.
                try:
                    from .classify import classify_project
                    from .interest import refresh_project
                    classify_project(project, allow_llm=False)
                    refresh_project(project)
                except Exception:
                    logger.exception('snippet classify failed %s', project.slug)
            # What you build is evidence of what you like. Re-read first:
            # for a ZIP upload the classifier runs in the scan pipeline and
            # writes `kind` behind this in-memory copy's back.
            try:
                project.refresh_from_db(fields=['kind'])
            except Exception:
                pass
            taste.record(request.user, project, 'publish', project=project)
            return redirect(project.get_absolute_url())
    else:
        form = AppUploadForm()
    return render(request, 'gallery/publish.html', {'form': form, 'challenge': challenge})

def download_zip(request, slug):
    # 'removed' and 'pending' are reachable on purpose: buyers of a
    # soft-deleted vibe keep their paid ZIP, and a buyer whose vibe is being
    # re-scanned keeps access to the last scanned version instead of losing
    # the download for the length of the scan (user_can_download enforces
    # the Trade/Sale receipt; quarantined is refused outright).
    project = get_object_or_404(AppProject, slug=slug, status__in=['published', 'removed', 'pending'])
    if not project.zip_file:
        raise Http404
    if not user_can_download(request.user, project):
        if project.status in ('removed', 'pending'):
            # Don't leak a redirect to a dead page — the listing is gone
            # (or, for a pending rescan, is not confirmable to a stranger).
            raise Http404
        messages.error(request, access_denied_message(request.user, project))
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        return redirect(project.get_absolute_url())
    if project.status == 'pending':
        # Serve the bytes the scanner already cleared, never the archive that
        # is still in the queue. No scanned version yet → nothing to serve.
        version = last_scanned_version(project)
        if not version or not version.zip_file:
            messages.info(
                request,
                "This vibe is being re-scanned and has no earlier version yet — "
                "your download comes back the moment the scan clears it.",
            )
            return redirect(project.get_absolute_url())
        taste.record(request.user, project, 'download', project=project)
        return serve_named_zip(version.zip_file, f'{project.slug}-{version.version}.zip')
    taste.record(request.user, project, 'download', project=project)
    return serve_project_zip(project, user=request.user, ip=request.META.get('REMOTE_ADDR', ''))

def file_preview(request, slug, path):
    project = get_object_or_404(AppProject, slug=slug, status='published')
    from .validators import is_safe_zip_name
    if not is_safe_zip_name(path):
        raise Http404
    if not project.zip_file:
        raise Http404
    if not user_can_download(request.user, project):
        return JsonResponse({'error': 'Unlock this vibe to preview files.'}, status=403)
    try:
        # open_zip streams via the storage API — same code path on local
        # disk and S3/R2 (FieldFile.path breaks on remote storage).
        from .ziputil import open_zip
        with open_zip(project.zip_file) as z:
            if path not in z.namelist():
                raise Http404
            data = z.read(path)
    except Http404:
        raise
    except Exception:
        raise Http404
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
        form = CommentForm(request.POST)
        if not form.is_valid():
            # Surface the first error (length or language) so the author
            # can reword. Never persist, never notify.
            err = next(iter(form.errors.values()))[0]
            messages.error(request, err)
            return redirect(project.get_absolute_url() + '#comments')
        body = form.cleaned_data['body']
        parent = None
        parent_id = form.cleaned_data.get('parent_id')
        if parent_id:
            try:
                parent = Comment.objects.get(pk=parent_id, project=project, is_hidden=False)
            except Exception:
                parent = None
        comment = Comment.objects.create(project=project, user=request.user, body=body, parent=parent)
        taste.record(request.user, project, 'comment', project=project)
        if project.owner_id != request.user.id:
            _notify_project_owner(
                project.owner,
                'comment',
                f'@{request.user.username} commented on {project.title}',
                project.get_absolute_url() + '#comments',
                actor=request.user,
                body=body[:160],
            )
        # Feedback is the repeatable half of the loop, so it pays a little
        # XP — capped per day in users.progress so it cannot be farmed.
        try:
            from users.progress import award
            award(request.user, 'comment_given', ref=f'comment:{comment.pk}')
        except Exception:
            logger.exception('comment xp failed %s', project.slug)
        return redirect(project.get_absolute_url() + '#comments')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"post_comment crush: {e}")
        return redirect(get_object_or_404(AppProject, slug=slug).get_absolute_url() + '#comments')

@require_POST
@login_required
@ratelimit(key='user', rate='10/h', method='POST')
def post_review(request, slug):
    try:
        from .models import Review
        project = get_object_or_404(AppProject, slug=slug, status='published')
        if not getattr(project.owner.profile, 'allow_reviews', True):
            messages.error(request, "Reviews are turned off for this vibe.")
            return redirect(project.get_absolute_url())
        form = ReviewForm(request.POST)
        if not form.is_valid():
            err = next(iter(form.errors.values()))[0]
            messages.error(request, err)
            return redirect(project.get_absolute_url() + '#reviews')
        rating = form.cleaned_data['rating']
        text = form.cleaned_data['text']
        if Trade.objects.filter(buyer=request.user, project=project).exists() or Star.objects.filter(user=request.user, project=project).exists() or project.owner == request.user:
            # Allow review if traded/starred/owner
            review, created = Review.objects.update_or_create(user=request.user, project=project, defaults={'rating': rating, 'text': text})
            if created:
                messages.success(request, f"Review {rating}★ posted — Nolo and human ratings now show.")
            else:
                messages.success(request, f"Review updated to {rating}★")
            if project.owner != request.user:
                _notify_project_owner(
                    project.owner,
                    'review',
                    f'@{request.user.username} reviewed “{project.title}” {rating}★',
                    project.get_absolute_url() + '#reviews',
                    actor=request.user,
                    body=(text or '')[:160],
                )
            try:
                from users.progress import award
                award(request.user, 'review_given', ref=f'review:{review.pk}')
            except Exception:
                logger.exception('review xp failed %s', project.slug)
            # Email the owner (if they opted in) on top of the in-app notify: a
            # review changes the vibe's average rating and ranking, so they
            # should know offline too. fail_silently so an MTA blip can't block
            # the saved review.

            if project.owner != request.user and getattr(project.owner.profile, 'email_on_review', True) and project.owner.email:
                try:
                    send_mail(
                        subject=f"★ New {rating}★ review on “{project.title}”",
                        message=(
                            f"Hi @{project.owner.username},\n\n"
                            f"@{request.user.username} just left a {rating}★ review on "
                            f"your vibe “{project.title}”.\n\n"
                            f"Review: {text[:200] if text else '(no text)'}\n"
                            f"View: {settings.SITE_URL}/app/{project.slug}/#reviews\n\n"
                            f"BlaqVibes — Publish the Vibes.\n"
                        ),
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[project.owner.email],
                        fail_silently=True,
                    )
                except Exception:
                    logger.exception(f"review email fail {project.slug}")
        else:
            messages.error(request, "Trade or star the vibe first to review — earn the right.")
        return redirect(project.get_absolute_url() + '#reviews')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"post_review crush: {e}")
        return redirect(get_object_or_404(AppProject, slug=slug).get_absolute_url() + '#reviews')

def _notify_project_owner(owner, kind, title, url, actor=None, body=''):
    """Social notification, honouring the recipient's per-kind preference.
    """
    pref_map = {
        'star': 'notify_on_star',
        'fork': 'notify_on_fork',
        'follow': 'notify_on_follow',
        'comment': 'notify_on_comment',
        'review': 'notify_on_comment',
        'trade': 'notify_on_trade',
        'sale': 'notify_on_trade',
        'milestone': 'notify_on_milestone',
    }
    key = pref_map.get(kind)
    try:
        if key and not getattr(owner.profile, key, True):
            return False
    except Exception:
        pass
    return bool(notify(owner, kind, title, body, url))

@login_required
@require_POST
def toggle_star(request, slug):
    project = get_object_or_404(AppProject, slug=slug, status='published')
    from .economy import toggle_project_star
    starred = toggle_project_star(request.user, project)
    # Only a star ADDS signal. Why not subtract on unstar? Removing a star
    # is ambiguous (misclick, tidying a profile) and a subtractable signal
    # is a griefing tool against your own recommendations.
    if starred:
        taste.record(request.user, project, 'star', project=project)
        # The creator loop: somebody liked your work → tell them, and pay
        # the reputation. Self-stars are not an event for anybody else, so
        # no notification and no XP (progress.award is ref-keyed, so even a
        # replayed request cannot pay it twice).
        if project.owner_id != request.user.id:
            _notify_project_owner(
                project.owner,
                'star',
                f'@{request.user.username} starred “{project.title}”',
                project.get_absolute_url(),
                actor=request.user,
            )
            try:
                from users.progress import award
                award(
                    project.owner,
                    'star_received',
                    ref=f'star:{project.id}:{request.user.id}',
                )
            except Exception:
                logger.exception('star xp failed %s', project.slug)
            project.refresh_from_db(fields=['stars'])
            if project.stars in (10, 50, 100, 500, 1000):
                notify(
                    project.owner,
                    'milestone',
                    f'★ {project.stars} on “{project.title}”',
                    'Your vibe crossed a milestone — nice.',
                    project.get_absolute_url(),
                )
    return JsonResponse({'starred': starred})

@login_required
def my_vibes(request):
    vibes = (
        AppProject.objects.filter(owner=request.user)
        .order_by('-created_at')
        .select_related('category', 'scan_job')
    )
    # Attach the same rich waiting info the detail page shows, so the owner
    # can see file size, stage and queue position for every unpublished vibe
    # at a glance — not just a "Queued" tag. Published vibes need none of it.
    for p in vibes:
        p.progress = scan_progress(p) if p.status != 'published' else None
    return render(request, 'gallery/my_vibes.html', {'vibes': vibes})

def _content_fields_changed(project, cleaned):
    """True only when the *executable* bytes changed.
    """
    for field in ('html_code', 'css_code', 'js_code'):
        if (cleaned.get(field) or '') != (getattr(project, field, '') or ''):
            return True
    return False

@login_required
def vibe_stats(request, slug):
    """Creator analytics for ONE of your own vibes.

    Owner-only by construction: the queryset filters on owner=request.user,
    so another creator's slug is a 404 (not a 403 — a 403 would confirm the
    vibe exists to somebody guessing slugs).
    """
    project = get_object_or_404(
        AppProject.objects.filter(owner=request.user).select_related('category'),
        slug=slug,
    )
    from .analytics import creator_stats, project_stats
    stats = project_stats(project)
    # Headline the thing that actually moved, instead of dumping a table.
    if stats.get('views_today'):
        headline = "Your vibe got %d view%s today." % (stats['views_today'], 's' if stats['views_today'] != 1 else '')
    elif stats.get('downloads_week'):
        headline = "Downloaded %d times this week." % stats['downloads_week']
    elif stats.get('stars_week'):
        headline = "%d new star%s this week." % (stats['stars_week'], 's' if stats['stars_week'] != 1 else '')
    else:
        headline = "Quiet week. Share the link or enter today's challenge to get eyes on it."
    return render(request, 'gallery/vibe_stats.html', {
        'project': project,
        'stats': stats,
        'creator': creator_stats(request.user),
        'headline': headline,
    })

@login_required
def edit_vibe(request, slug):
    project = get_object_or_404(AppProject, slug=slug, owner=request.user)
    if request.method == 'POST':
        form = AppUploadForm(request.POST, request.FILES, instance=project)
        if form.is_valid():
            was_published = project.status == 'published'
            new_zip = 'zip_file' in request.FILES
            code_changed = _content_fields_changed(project, form.cleaned_data)
            p = form.save(commit=False)
            # Versioning: if new ZIP, save old as AppVersion
            if new_zip and project.zip_file:
                from .profanity import validate_public_text
                from .prompt_sanitize import sanitize_prompt
                try:
                    changelog = validate_public_text(
                        sanitize_prompt(request.POST.get('changelog', 'Update'))[:280]
                    ) or 'Update'
                except Exception:
                    changelog = 'Update'
                AppVersion.objects.create(project=project, zip_file=project.zip_file, version=f"1.{project.versions.count()+1}.0", changelog=changelog)

            # Metadata-only edit: nothing to re-scan, nothing to hide
            if not new_zip and not code_changed:
                # Deliberately does NOT touch status or trust: the bytes the
                # badge vouched for are still the bytes we are serving.
                p.save()
                form.save_m2m()
                return _finish_snippet_edit(request, p, republished=False)

            # Any content change resets the trust badge (gallery.trust WHY 4):
            # the old ✓ vouched for the old bytes; the rescan re-earns it.
            try:
                from .trust import invalidate_trust
                invalidate_trust(p, save=False)
            except Exception:
                pass

            if p.zip_file:
                # New archive bytes must be re-checked before they are served
                # to the public, so the vibe does step out of the feed while
                # the chain runs. Two guarantees come with that (see
                # gallery.access.user_can_download): people who already PAID
                # keep their download, and the vibe is only hidden — never
                # deleted. If the queue is healthy this is seconds.
                p.status = 'pending'
                p.save()
                form.save_m2m()
                try:
                    from .ziputil import build_tree
                    p.files.all().delete()
                    tree, files = build_tree(p.zip_file)
                    p.file_tree, p.file_count = tree, len(files)
                    p.save(update_fields=['file_tree','file_count'])
                    for f in files[:2000]:
                        AppFile.objects.create(project=p, path=f['path'], size=f['size'])
                except Exception:
                    logger.exception('tree rebuild failed on edit %s', p.slug)
                from .tasks import process_upload_pipeline
                from .models import ScanJob
                job, _ = ScanJob.objects.get_or_create(project=p)
                job.status = 'queued'
                job.save(update_fields=['status'])
                try:
                    process_upload_pipeline.delay(p.id)
                except Exception:
                    # The broker is down. Say so instead of pretending the
                    # queued state is progress — a silent "we'll tell you"
                    # is how a vibe disappears with no trace.
                    logger.exception('edit rescan queue failed %s', p.slug)
                    messages.error(
                        request,
                        "The scan service is offline, so “%s” is held for review "
                        "instead of being re-checked. Nothing was lost — it returns "
                        "to the feed as soon as a scan worker is back." % p.title,
                    )
                    return redirect(p.get_absolute_url())
                if was_published:
                    messages.info(
                        request,
                        f"⏳ “{p.title}” is re-scanning — it stays out of the feed for a "
                        f"moment so nobody downloads unchecked files. Buyers keep their "
                        f"downloads. We’ll tell you when it’s live again.",
                    )
                else:
                    messages.info(request, f"⏳ “{p.title}” re-queued for scan.")
                return redirect(p.get_absolute_url())

            # Snippet edit: re-scan in-request, stay live when clean
            # Snippets never enter the queue (see trust.snippet_evidence): the
            # check is a regex over three text fields, so it costs
            # microseconds and can run while the creator waits. That is what
            # lets an edit keep the vibe published instead of dropping it into
            # a queue that would never publish it again.
            secrets_found = False
            try:
                from .trust import apply_trust_grade, snippet_evidence
                snippet_evidence(p, save=False)
                secrets_found = bool(
                    (p.scan_report or {}).get('snippet_scan', {}).get('secrets_found')
                )
                p.status = 'pending' if secrets_found else ('published' if was_published else p.status)
                p.save()
                form.save_m2m()
                apply_trust_grade(p)
            except Exception:
                logger.exception('snippet rescan failed on edit %s', p.slug)
            if secrets_found:
                notify(
                    p.owner,
                    'quarantined',
                    f'“{p.title}” is held for review',
                    'The edited code contains something that looks like an API key or token. '
                    'Remove it and edit again to put the vibe straight back on the feed.',
                    p.get_absolute_url(),
                )
                messages.warning(
                    request,
                    "Saved, but “%s” is held for review: the code looks like it contains "
                    "an API key or token. Remove it and edit again to go live." % p.title,
                )
                return redirect(p.get_absolute_url())
            return _finish_snippet_edit(request, p, republished=True)
    else:
        form = AppUploadForm(instance=project)
    return render(request, 'gallery/edit_vibe.html', {'form': form, 'project': project, 'co_owner_form': CoOwnerForm()})

def _finish_snippet_edit(request, project, republished):
    """Re-label a snippet after an edit and tell the creator what happened."""
    try:
        from .tasks import classify_and_score
        classify_and_score(project)
    except Exception:
        logger.exception('reclassify on edit failed %s', project.slug)
    if republished:
        messages.success(request, f"✓ Vibe updated — “{project.title}” stayed live.")
    else:
        messages.success(request, "✓ Vibe updated!")
    return redirect(project.get_absolute_url())

@login_required
@require_POST
@ratelimit(key='user', rate='10/h', method='POST')
def add_co_owner(request, slug):
    """Add a co-owner with a % share of star trade revenue. The project row is
    locked because the trade path locks it too — this serializes "edit the
    split" against "pay out" so a trade can never pay an old split while the
    form reads a new one. It doesn't re-queue moderation: a split is metadata
    about money, not content, so re-scanning an unchanged ZIP would be noise.
    """
    project = get_object_or_404(AppProject, slug=slug, owner=request.user)
    form = CoOwnerForm(request.POST, project=project)
    if form.is_valid():
        from django.contrib.auth.models import User
        user = User.objects.get(username=form.cleaned_data['username'])
        share = form.cleaned_data['share_percent']
        try:
            with transaction.atomic():
                locked = AppProject.objects.select_for_update().get(pk=project.pk, owner=request.user)
                # The form's total is only an early error: another request can
                # add a co-owner between validation and this lock. Re-check
                # here so concurrent additions cannot exceed 100%.
                existing_share = (
                    ProjectCoOwner.objects.filter(project=locked)
                    .aggregate(total=Sum('share_percent'))['total'] or 0
                )
                if ProjectCoOwner.objects.filter(project=locked, user=user).exists():
                    messages.error(request, f'@{user.username} is already a co-owner.')
                    return redirect('edit_vibe', slug=slug)
                if existing_share + share > 100:
                    messages.error(
                        request,
                        f'Co-owner shares now total {existing_share}% - adding {share}% would exceed 100%.',
                    )
                    return redirect('edit_vibe', slug=slug)
                ProjectCoOwner.objects.create(project=locked, user=user, share_percent=share)
        except Exception:
            logger.exception('add co-owner failed %s %s', project.slug, user.username)
            messages.error(request, 'Could not add co-owner. Try again.')
            return redirect('edit_vibe', slug=slug)
        notify(
            user, 'co_owner',
            f'You are now a co-owner of “{project.title}” — {share}% of star trades',
            url=project.get_absolute_url(),
        )
        messages.success(
            request,
            f'@{user.username} now receives {share}% of star trades on “{project.title}”. You keep the remainder.',
        )
    else:
        for err in form.errors.values():
            messages.error(request, err[0])
    return redirect('edit_vibe', slug=slug)

@login_required
@require_POST
@ratelimit(key='user', rate='10/h', method='POST')
def remove_co_owner(request, slug, user_id):
    """Remove a co-owner — their share returns to the owner automatically."""
    project = get_object_or_404(AppProject, slug=slug, owner=request.user)
    try:
        with transaction.atomic():
            locked = AppProject.objects.select_for_update().get(pk=project.pk, owner=request.user)
            removed = ProjectCoOwner.objects.filter(project=locked, user_id=user_id).delete()[0]
    except Exception:
        logger.exception('remove co-owner failed %s %s', project.slug, user_id)
        messages.error(request, 'Could not remove co-owner. Try again.')
        return redirect('edit_vibe', slug=slug)
    if removed:
        messages.success(request, 'Co-owner removed — their share returns to you.')
    else:
        messages.info(request, 'That co-owner was not on this vibe.')
    return redirect('edit_vibe', slug=slug)

@login_required
@require_POST
def delete_vibe(request, slug):
    """Owner delete — hard when nothing was paid, soft when money moved.

    remove_project() decides: no Trade/Sale rows → real delete;
    otherwise status='removed' so buyers keep the ZIP they paid for
    while the public page disappears.
    """
    from .lifecycle import remove_project
    project = get_object_or_404(AppProject, slug=slug, owner=request.user)
    outcome = remove_project(project)
    if outcome == 'removed':
        messages.success(
            request,
            f"Removed “{project.title}” from BlaqVibes. People who already "
            "traded or bought it keep their download — those are their receipts.",
        )
    else:
        messages.success(request, f"Deleted “{project.title}”")
    return redirect('my_vibes')

@require_POST
@ratelimit(key='ip', rate='10/h', method='POST')
def report_vibe(request, slug):
    # Published only — gate OUTSIDE the crush try/except. The report button
    # only appears on a visible vibe, and accepting reports against
    # pending/quarantined/removed slugs would let a guessed slug confirm
    # existence + spam the queue. Raising Http404 here (not inside the try)
    # means an unpublished slug 404s directly instead of the old fallback's
    # 302-to-the-listing, which confirmed the vibe exists.
    project = get_object_or_404(AppProject, slug=slug, status='published')
    try:
        from .prompt_sanitize import sanitize_prompt
        from .reports import create_report

        reason = request.POST.get('reason','other')
        if reason not in ('spam','malware','copyright','other'):
            reason = 'other'
        details = sanitize_prompt(request.POST.get('details',''))[:500]
        # Exactly one creation path. create_report handles dedupe (one open
        # report per signed-in user per vibe within 24h), moderator fan-out
        # and profanity-safe notify — never a bare .objects.create() here.
        report, created = create_report(project, request.user, reason, details)
        if not created:
            messages.info(request, "You've already reported this vibe — it's in the queue for review.")
        else:
            messages.success(request, "Reported — moderators will review. Thank you.")
        return redirect(project.get_absolute_url())
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"report crush: {e}")
        return redirect(project.get_absolute_url())

@csrf_exempt
# Two limiters, deliberately. The POST-only one below (60/m) bounds pushes; this one
# counts EVERY verb from one IP, because a clone is mostly GETs — info/refs, the
# objects fetch, and the dumb-protocol fallback — and a POST-only throttle left
# the read half of the endpoint unlimited per IP for anyone who wanted to mirror
# the whole gallery one small file at a time.
@ratelimit(key='ip', rate='240/m')
@ratelimit(key='ip', rate='60/m', method='POST')
def git_clone(request, username, slug, rest=''):
    """Real git smart-HTTP endpoint — `git clone` AND `git push` work.

    gating lives in git_daemon.handle_git_request:
    - pull follows the download rules (free vibes clone anonymously,
      paid vibes 401 until Basic credentials that traded/bought are sent)
    - push REQUIRES Basic auth (password or git token) as owner/co-owner,
      never a browser session, so a cross-site POST cannot move refs
      (csrf_exempt is safe for exactly that reason)
    - a successful push re-enters the scan queue before the vibe goes
      live again — no scan bypass via git.
    """
    from .git_daemon import handle_git_request
    return handle_git_request(request, username, slug, rest)

@login_required
@require_POST
def trade_download(request, slug):
    """Stars money path: spend star_cost, seller is credited, ZIP unlocks."""
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if not project.zip_file:
        raise Http404
    if project.owner == request.user:
        return redirect('download_zip', slug=slug)
    from .economy import TradeError, trade_for_download
    try:
        trade = trade_for_download(request.user, project)
    except TradeError as exc:
        messages.error(request, exc.message)
        return redirect(project.get_absolute_url())
    if trade:
        # Strongest taste signal on the site: they spent scarce currency.
        taste.record(request.user, project, 'trade', project=project)
        # With co-owners, one purchase produces one Trade row PER recipient.
        # The buyer-paid total is the SUM of those rows — trade.cost alone
        # would be just the owner's share and would understate the message.
        # Reading the actual rows back also means the notifications can never
        # drift from what the transaction actually paid out.
        rows = list(Trade.objects.filter(buyer=request.user, project=project))
        paid = sum(r.cost for r in rows)
        is_split = len(rows) > 1
        try:
            from users.progress import award
            award(request.user, 'trade_made', ref=f'trade:{rows[0].pk}')
        except Exception:
            logger.exception('trade xp failed %s', project.slug)
        for r in rows:
            who = r.seller
            if who:
                share_note = f' ({r.cost}★ of {paid}★ — your share)' if is_split else ''
                _notify_project_owner(
                    who,
                    'trade',
                    f'@{request.user.username} traded {r.cost} ★ for {project.title}{share_note}',
                    project.get_absolute_url(),
                    actor=request.user,
                )
                try:
                    from users.progress import award
                    award(who, 'trade_received', ref=f'trade:{r.pk}')
                except Exception:
                    logger.exception('trade xp failed %s', project.slug)
            # Email the seller(s) if opted in, on top of the in-app notify: a
            # trade is a money event and must survive a closed tab, so email is
            # the durable channel. fail_silently so an MTA outage can't crash
            # the download.

            if who and getattr(who.profile, 'email_on_trade', True) and who.email:
                try:
                    send_mail(
                        subject=f"★ Trade: @{request.user.username} traded {r.cost} ★ for {project.title}",
                        message=(
                            f"Hi @{who.username},\n\n"
                            f"@{request.user.username} just traded {r.cost} ★ "
                            f"for your vibe “{project.title}”.\n\n"
                            f"View: {settings.SITE_URL}/app/{project.slug}/\n"
                            f"Dashboard: {settings.SITE_URL}/payout/\n\n"
                            f"BlaqVibes — Publish the Vibes.\n"
                        ),
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[who.email],
                        fail_silently=True,
                    )
                except Exception:
                    logger.exception(f"trade email fail {project.slug}")
        request.user.profile.refresh_from_db()
        messages.success(
            request,
            f"Traded {paid} ★ for “{project.title}”. You have {request.user.profile.stars_balance} ★ left.",
        )
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
            # Inherit the label so the fork is filterable the moment it
            # exists; its own scan re-classifies it from its own files.
            kind=original.kind,
            kind_source=original.kind_source,
            kind_confidence=original.kind_confidence,
            preview_mode=original.preview_mode,
            static_entry=original.static_entry,
            star_cost=0,  # forked is free initially
            forked_from=original,
            status='pending',
        )
        fork.save()  # generates slug
        # Forking is a loud statement of interest in this kind of program.
        taste.record(request.user, original, 'fork', project=original)
        # The remix loop: the original creator hears about it and is paid in
        # reputation. Keyed to the fork row, so a replay pays nothing twice.
        try:
            from users.progress import award
            _notify_project_owner(
                original.owner,
                'fork',
                f'@{request.user.username} forked “{original.title}”',
                fork.get_absolute_url(),
                actor=request.user,
                body='Their remix is live — see what they changed.',
            )
            award(original.owner, 'fork_received', ref=f'fork:{fork.pk}')
        except Exception:
            logger.exception('fork notify/xp failed %s', original.slug)
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
# A rate limit is a *cost* control here, not just an abuse control: every
# call runs a hosted LLM. 10/h is generous for editing a README and a hard
# ceiling on a loop with someone else's API bill.
@ratelimit(key='user', rate='10/h', method='POST')
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
            from .profanity import PUBLIC_LANGUAGE_ERROR, contains_profanity
            if contains_profanity(project.ai_readme):
                messages.error(request, PUBLIC_LANGUAGE_ERROR)
                return redirect(project.get_absolute_url())
            project.readme = project.ai_readme
            project.save()
            messages.success(request, "AI README applied!")
        return redirect(project.get_absolute_url())
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"apply_ai crush: {e}")
        return redirect('app_detail', slug=slug)

@login_required
def download_version(request, slug, version_id):
    """Owner (or unlocked buyer) can fetch a historical ZIP. Never via .url.

    Visibility-gated: the caller must be able to *see* the project (owner/
    moderator/published) or hold a download receipt (Trade/Sale). A stranger
    hitting a pending/quarantined/removed slug now 404s instead of bouncing
    to a page that itself 404s — same honest "not confirmable" rule as the
    rest of the site.
    """
    project = get_object_or_404(AppProject, slug=slug)
    if not (user_can_see_project(request.user, project) or user_can_download(request.user, project)):
        raise Http404
    version = get_object_or_404(AppVersion, pk=version_id, project=project)
    if request.user != project.owner and not user_can_download(request.user, project):
        messages.error(request, access_denied_message(request.user, project))
        return redirect(project.get_absolute_url())
    from .zip_serve import serve_named_zip
    return serve_named_zip(version.zip_file, f'{project.slug}-v{version.version}.zip')

@login_required
@require_POST
@ratelimit(key='user', rate='10/h', method='POST')
def buy_vibe(request, slug):
    if getattr(request, 'limited', False):
        messages.error(request, 'Rate limit: 10 checkouts/hour.')
        return redirect('app_detail', slug=slug)
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if project.owner == request.user:
        return redirect('download_zip', slug=slug)
    if not project.price_zar:
        return redirect('download_zip', slug=slug)
    from .models import Sale
    if Sale.objects.filter(buyer=request.user, project=project).exists():
        return redirect('download_zip', slug=slug)
    from .payments import PaymentError, create_checkout
    try:
        return redirect(create_checkout(request.user, project))
    except PaymentError as exc:
        if exc.message == 'already_unlocked':
            return redirect('download_zip', slug=slug)
        messages.error(request, exc.message)
        return redirect(project.get_absolute_url())

@csrf_exempt
@require_POST
def paystack_webhook(request):
    """Verify Paystack signature and fulfill the frozen PaymentIntent."""
    from .payments import fulfill_signed_webhook
    status, msg = fulfill_signed_webhook(
        request.body, request.headers.get('x-paystack-signature', ''),
    )
    return HttpResponse(msg, status=status)

def oops_demo(request):
    # Demo safe page — always shows friendly fork, no HttpResponse scare
    from django.shortcuts import render
    return render(request, '404.html', status=200)

def fork_network(request, slug):
    """Fork network graph — backend builds tree, no JS secrets, crush silently.

    Published root only: the network page enumerates forks, and a pending
    root must not be confirmable (or its forks surfaced) from a guessed
    slug — same 404 rule as every other content read.

    The root is re-gated with user_can_see_project AFTER following the
    forked_from chain up. A published fork can point at a now-pending or
    removed original; showing that root's metadata to a stranger would
    confirm a slug the rest of the site 404s. The check sits OUTSIDE the
    crush try/except so the Http404 propagates instead of the fallback
    re-rendering the network from the requested (published) slug.
    """
    root = get_object_or_404(AppProject, slug=slug, status='published')
    # Find root of network (follow forked_from chain up)
    cur = root
    seen = set()
    while cur.forked_from and cur.forked_from_id not in seen and cur.forked_from_id != cur.id:
        seen.add(cur.id)
        cur = cur.forked_from
    root = cur
    if not user_can_see_project(request.user, root):
        raise Http404
    try:
        # Published forks for everybody, plus your own still-scanning forks
        # for you: a creator must be able to find the remix they just made,
        # while a stranger must not be able to confirm it exists.
        visible = Q(status='published')
        if getattr(request.user, 'is_authenticated', False):
            visible = visible | Q(owner=request.user)
        # All forks in network (direct + indirect)
        forks = AppProject.objects.filter(forked_from__isnull=False).filter(visible).filter(
            # Simple: direct forks of root + forks of forks (1 level deep for demo, at scale recursive CTE)
            Q(forked_from=root) | Q(forked_from__forked_from=root)
        ).select_related('owner','forked_from').order_by('-created_at')[:20]
        # Fallback if no indirect, just direct
        if not forks.exists():
            forks = AppProject.objects.filter(forked_from=root).filter(visible).select_related('owner')[:20]
        return render(request, 'gallery/fork_network.html', {'root': root, 'forks': forks})
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"fork_network crush: {e}")
        # Crush fallback: root is already visibility-gated above, so it is
        # safe to render an empty network from it (never re-fetch ungated).
        return render(request, 'gallery/fork_network.html', {'root': root, 'forks': AppProject.objects.none()})

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
@ratelimit(key='user', rate='60/h', method='POST')
def toggle_bookmark(request, slug):
    from .models import Bookmark
    project = get_object_or_404(AppProject, slug=slug, status='published')
    bm, created = Bookmark.objects.get_or_create(user=request.user, project=project)
    if not created:
        bm.delete()
        return JsonResponse({'saved': False})
    taste.record(request.user, project, 'save', project=project)
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

@login_required
@require_POST
def notifications_mark_read(request, notification_id):
    """Mark one notification read. Owner-scoped: a stranger's id is a 404.
    """
    from .models import Notification
    n = get_object_or_404(Notification, pk=notification_id, user=request.user)
    if not n.is_read:
        n.is_read = True
        n.save(update_fields=['is_read'])
    unread = request.user.notifications.filter(is_read=False).count()
    return JsonResponse({'ok': True, 'unread': unread})

@login_required
@require_POST
def notifications_mark_all_read(request):
    """Mark every notification for the current user as read. Owner-scoped."""
    from .models import Notification
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return JsonResponse({'ok': True, 'unread': 0})

def sitemap_xml(request):
    projects = AppProject.objects.filter(status='published').only('slug', 'updated_at')[:500]
    rows = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            f'<url><loc>{settings.SITE_URL}/</loc></url>']
    for p in projects:
        rows.append(f'<url><loc>{settings.SITE_URL}/app/{p.slug}/</loc><lastmod>{p.updated_at.date().isoformat()}</lastmod></url>')
    rows.append('</urlset>')
    return HttpResponse('\n'.join(rows), content_type='application/xml')

def prompt_skills(request):
    """A practical, provider-neutral prompt efficiency workbench.
    """
    return render(request, 'gallery/prompt_skills.html')

def trust_legend(request):
    """Public "what does the badge mean" page — the anti-fake read.
    """
    from .trust import TRUST_META, TRUST_VERIFIED, TRUST_SCANNED, TRUST_UNKNOWN
    context = {
        'verified': TRUST_META[TRUST_VERIFIED],
        'scanned': TRUST_META[TRUST_SCANNED],
        'unknown': TRUST_META[TRUST_UNKNOWN],
        'checked_count': AppProject.objects.filter(status='published', trust=TRUST_VERIFIED).count(),
        'scanned_count': AppProject.objects.filter(status='published', trust=TRUST_SCANNED).count(),
    }
    return render(request, 'gallery/trust_legend.html', context)
