from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q, Count, Prefetch
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
from .access import user_can_download, user_can_see_project, user_is_moderator, access_denied_message
from .zip_serve import serve_project_zip, owner_scan_reason
from .notify import notify
from .pending import hold_state, notify_queued, owner_hold_payload
from . import taste
from .taxonomy import KIND_BY_VALUE, PROGRAM_KINDS, coerce_kind
from django.core.mail import send_mail
from users.forms import SignUpForm

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

@ratelimit(key='ip', rate='10/h', method='POST')
def signup(request):
    """Account creation — rate limited per IP.

    5 Whys:
    1. Why rate limit signup? Accounts used to arrive with 5 spendable ★;
       a loop could mint a wallet per second.
    2. Why keep the limit now that the grant moved to email-verify?
       Defense in depth — sockpuppets are still the raw material for vote
       farming (battles) and fake reviews.
    3. Why per-IP not per-user? There is no user yet.
    4. Why 10/h not 3/h? Shared NATs (campus, office) are real; 10 blocks
       scripts without locking out a classroom.
    5. Why 429 not a silent pass? Failing open makes the limit decorative.
    """
    if getattr(request, 'limited', False):
        return HttpResponse("Too many signups from this network. Try again later.", status=429)
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
            return redirect('feed')
    else:
        form = SignUpForm()
    return render(request, 'registration/signup.html', {'form': form})

def feed(request):
    """The discovery grid.

    5 Whys — why is 'for you' the default sort for a signed-in user?

    1. Why personalise at all? Everything gets published here, so the grid
       fills with kinds a given person will never open. Sorting by taste is
       what keeps a firehose usable.
    2. Why default to it instead of hiding it behind a dropdown option? A
       default nobody selects is a feature nobody gets; the user asked for
       games to be pushed to the FRONT for people who like games.
    3. Why does it silently fall back to global order? Anonymous visitors
       and brand-new accounts have no signal — reordering on noise would be
       worse than not reordering (see taste.has_enough_signal).
    4. Why keep every other sort working exactly as before? 'Newest' is a
       promise about ordering; personalising it would make it a lie.
    5. Why still allow an explicit kind filter on top? Ranking is a guess;
       a filter is an instruction, and an instruction must always win.
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
        if ai == '1':
            projects = projects.filter(ai_generated=True)
        if tech:
            try:
                import bleach
                tech = bleach.clean(tech, tags=[], strip=True)[:100]
            except Exception: pass
            projects = projects.filter(tech_stack__icontains=tech)
        # 5 Whys: Why force q to '' instead of conditionally calling
        # search_projects? search_projects handles empty q by returning
        # the sorted queryset; setting q to '' reuses that path without
        # adding a branch. Why allow filters to still work when search
        # is off? A feed without text search should still let users
        # browse by category/kind/tech — those filters are index-only
        # and cost nothing. Why fail-closed? If the setting cannot be
        # read, search stays on (default True) so a broken DB row never
        # silences the feed.
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
        return render(request, 'gallery/feed.html', {
            'page': page,
            'categories': categories,
            'q': q,
            'cat': cat,
            'kind': kind,
            'sort': sort,
            'program_kinds': PROGRAM_KINDS,
            'program_kind': program_kind,
            'runnable': runnable,
            'trust': trust_filter,
            'personalized': sort == 'foryou' and bool(my_kinds),
            'my_kinds': [KIND_BY_VALUE[k] for k in my_kinds if k in KIND_BY_VALUE],
        })
    except Exception:
        logger.exception("feed crush silent")
        return render(request, 'gallery/feed.html', {'page': Paginator(AppProject.objects.none(), 12).get_page(1), 'categories': Category.objects.all(), 'q': '', 'cat': '', 'kind': '', 'sort': 'newest', 'program_kinds': PROGRAM_KINDS, 'program_kind': '', 'runnable': '', 'trust': '', 'personalized': False, 'my_kinds': []})


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
        'owner', 'owner__profile', 'category', 'forked_from', 'forked_from__owner', 'scan_job'
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
        'comment_count': getattr(project, 'comment_count', 0),
        'published_forks': [f for f in project.forks.all() if f.status == 'published'][:5],
        'viewers': viewers,
        'show_viewer_upsell': show_viewer_upsell,
        'viewer_count': project.views,
        'scan_status': scan_status.status if scan_status else project.status,
        'hold': hold_state(project),
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

    5 Whys:
    1. Why not public on pending? A guessed slug leaks queued/quarantined.
    2. Why 404 not 403? 403 confirms the vibe exists.
    3. Why still public after publish? The detail-page poll reloads when
       is_published flips. Strangers then only see 'clean'.
    4. Why hide reason from strangers? owner_scan_reason names virus/secrets.
    5. Why moderator not is_staff? Django staff is not a BlaqVibes role.
    """
    project = get_object_or_404(AppProject, slug=slug)
    if not user_can_see_project(request.user, project):
        raise Http404
    job = getattr(project, 'scan_job', None)
    data = {
        'status': job.status if job else project.status,
        'is_published': project.status == 'published',
        'reason': '',
        'poll': False,
        'poll_ms': 0,
    }
    if request.user.is_authenticated and (
        request.user.pk == project.owner_id or user_is_moderator(request.user)
    ):
        data['reason'] = owner_scan_reason(project)
        data.update(owner_hold_payload(project))
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

    Two runnable shapes share this one shell:
      * a snippet → the iframe points at snippet_doc;
      * a static-site ZIP → the iframe points at run_static (an assembled,
        single-document version of the ZIP's entry HTML).
    Both are the same opaque-origin sandbox, so the shell's own CSP is
    identical; only the iframe src differs. A ZIP that is NOT static-runnable
    still redirects to the honest file list.

    5 Whys: Why one shell for both instead of a second preview page?
    1. The security posture (locked-down shell, opaque-origin child) is
       identical; duplicating it risks the copy drifting weaker.
    2. detail.js and app_detail already link to this URL; keeping it means no
       template churn and no second thing to keep in sync.
    3. `can_run_preview` already encodes "is there something to run"; the
       shell just asks the model which runnable kind it is.
    4. A static ZIP that fails to assemble falls back to the file list — the
       shell decides that once, not every template.
    5. One shell, one CSP header block — the audit surface stays small.
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


def snippet_doc(request, slug):
    """The raw snippet document (user HTML + CSS + JS).

    Served ONLY into an <iframe sandbox="allow-scripts"> (opaque origin)
    with a short-lived signed token. CSP sandbox on the response also
    applies if this URL is ever opened outside that iframe.
    """
    project = get_object_or_404(AppProject, slug=slug, status='published')
    if not snippet_request_is_framed(request, slug):
        return render(request, 'gallery/snippet_blocked.html', {'project': project}, status=403)
    resp = render(request, 'gallery/snippet_doc.html', {'project': project})
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


def run_static(request, slug):
    """Assembled single-document run of a static-site ZIP.

    The ZIP's entry HTML with its local CSS/JS/images inlined (gallery.runner),
    served ONLY into the same sandboxed, opaque-origin iframe as snippet_doc,
    behind the same short-lived signed token. No file is served from our
    origin — the assembled bytes are one document, exactly like a snippet.

    5 Whys:
    1. Why reuse snippet_request_is_framed? The threat is identical (a stolen
       token opened top-level must not run user JS first-party); one guard,
       one place to get it right.
    2. Why not serve `/run/<path>` per asset? That needs `allow-same-origin`
       or CSP `'self'`, which reopens the exact XSS surface the sandbox
       closes. Inlining keeps the opaque origin.
    3. Why gate on preview_mode == 'static_zip'? A ZIP that needs a build or a
       server has no honest run; assembling its index.html would show broken
       chrome — the fake preview the site forbids.
    4. Why the same CSP as snippet_doc? The assembled document is the same
       shape (inline styles/scripts, remote CDNs, data-URI images); a
       different policy would either break real vibes or weaken the sandbox.
    5. Why fall back to snippet_blocked / files instead of 500 on empty
       assembly? A ZIP we cannot assemble is honestly "no live preview", not
       a server error the visitor caused.
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
                    raw_id = getattr(task, 'id', '') or ''
                    job.task_id = raw_id if isinstance(raw_id, str) else ''
                    job.status = 'scanning'
                    job.save(update_fields=['task_id', 'status'])
                except Exception as e:
                    logger.warning("Queue error, fallback eager for %s: %s", project.slug, e)
                messages.info(request, f"⏳ Your vibe “{project.title}” is in the queue — we’re checking for vulnerabilities. We’ll tell you when it’s uploaded! You’re #{ScanJob.objects.filter(status__in=['queued','scanning']).count()} in line. This page is not stuck — Inbox will ping you when it is approved.")
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
                if request.user.projects.filter(status='published').count() >= 3:
                    project.status = 'published'
                    project.save(update_fields=['status'])
                    # Status just became published — re-grade so the badge
                    # lands in the same request (pending graded 'unknown').
                    try:
                        from .trust import apply_trust_grade
                        apply_trust_grade(project)
                    except Exception:
                        logger.exception('snippet grade failed %s', project.slug)
                    messages.success(request, f"Your snippet “{project.title}” is published.")
                else:
                    messages.info(request, f"Your vibe “{project.title}” is waiting for approval — first snippets need a human. This page is not stuck; Inbox will ping you when it is live.")
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
            # Inbox note while it waits — the toast dies with the tab.
            if project.status == 'pending':
                notify_queued(project)
            return redirect(project.get_absolute_url())
    else:
        form = AppUploadForm()
    return render(request, 'gallery/publish.html', {'form': form, 'challenge': challenge})

def download_zip(request, slug):
    # 'removed' is reachable on purpose: buyers of a soft-deleted vibe keep
    # their paid ZIP (user_can_download enforces the Trade/Sale receipt).
    project = get_object_or_404(AppProject, slug=slug, status__in=['published', 'removed'])
    if not project.zip_file:
        raise Http404
    if not user_can_download(request.user, project):
        if project.status == 'removed':
            # Don't leak a redirect to a dead page — the listing is gone.
            raise Http404
        messages.error(request, access_denied_message(request.user, project))
        if not request.user.is_authenticated:
            return redirect(f"{settings.LOGIN_URL}?next={request.path}")
        return redirect(project.get_absolute_url())
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
        Comment.objects.create(project=project, user=request.user, body=body, parent=parent)
        taste.record(request.user, project, 'comment', project=project)
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
            # Email the owner if they want review emails.
            # 5 Whys: Why email on top of the in-app notify? A review
            # changes the vibe's average rating and affects its ranking —
            # the owner needs to know even when they are offline. Why a
            # per-user toggle? A creator with many vibes may not want an
            # email for every single review. Why fail_silently? An MTA
            # blip must not block the user from seeing their updated review.
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

@require_POST
@login_required
def toggle_star(request, slug):
    project = get_object_or_404(AppProject, slug=slug, status='published')
    from .economy import toggle_project_star
    starred = toggle_project_star(request.user, project)
    # Only a star ADDS signal. Why not subtract on unstar? Removing a star
    # is ambiguous (misclick, tidying a profile) and a subtractable signal
    # is a griefing tool against your own recommendations.
    if starred:
        taste.record(request.user, project, 'star', project=project)
    return JsonResponse({'starred': starred})

@login_required
def my_vibes(request):
    vibes = list(
        AppProject.objects.filter(owner=request.user)
        .order_by('-created_at')
        .select_related('category', 'scan_job')
    )
    for vibe in vibes:
        vibe.hold = hold_state(vibe)
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
                from .profanity import validate_public_text
                from .prompt_sanitize import sanitize_prompt
                try:
                    changelog = validate_public_text(
                        sanitize_prompt(request.POST.get('changelog', 'Update'))[:280]
                    ) or 'Update'
                except Exception:
                    changelog = 'Update'
                AppVersion.objects.create(project=project, zip_file=project.zip_file, version=f"1.{project.versions.count()+1}.0", changelog=changelog)
            # Any content change resets the trust badge (gallery.trust WHY 4):
            # the old ✓ vouched for the old bytes; the rescan re-earns it.
            try:
                from .trust import invalidate_trust
                invalidate_trust(p, save=False)
            except Exception:
                pass
            p.status = 'pending'
            p.save()
            form.save_m2m()
            # Rebuild tree + re-queue scan (every edit is re-checked)
            if p.zip_file:
                try:
                    from .ziputil import build_tree
                    p.files.all().delete()
                    tree, files = build_tree(p.zip_file)
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
                    messages.info(request, f"⏳ Your vibe “{p.title}” re-uploaded — waiting for approval again. This page is not stuck; Inbox will ping you when it is live.")
                except Exception: pass
            else:
                # No ZIP means no scan queue run, so nothing else would
                # ever re-label this vibe after an edit — do it here.
                try:
                    from .tasks import classify_and_score
                    classify_and_score(p)
                except Exception:
                    logger.exception('reclassify on edit failed %s', p.slug)
                messages.success(request, "✓ Vibe updated!")
            if p.status == 'pending':
                notify_queued(p)
            return redirect(p.get_absolute_url())
    else:
        form = AppUploadForm(instance=project)
    return render(request, 'gallery/edit_vibe.html', {'form': form, 'project': project, 'co_owner_form': CoOwnerForm()})


@login_required
@require_POST
@ratelimit(key='user', rate='10/h', method='POST')
def add_co_owner(request, slug):
    """Add a co-owner with a % share of star trade revenue.

    5 Whys: Why lock the project row? The trade path locks it too —
    locking here serializes "edit the split" vs "pay out", so a trade can
    never pay an old split while the form reads a new one.
    Why re-queue moderation? It doesn't — a split is metadata about money,
    not content; re-scanning a ZIP that didn't change would be noise.
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
        for r in rows:
            who = r.seller
            if who:
                share_note = f' ({r.cost}★ of {paid}★ — your share)' if is_split else ''
                notify(
                    who, 'trade',
                    f'@{request.user.username} traded {r.cost} ★ for {project.title}{share_note}',
                    url=project.get_absolute_url(),
                )
            # Email the seller(s) if they want trade emails.
            # 5 Whys: Why email on top of the in-app notify? A star trade
            # is a money event — the notification must survive a closed
            # tab. Email is the durable channel. Why a per-user toggle?
            # Big creators with high trade volume don't want an inbox
            # flooded with "1 ★ traded" emails every hour. Why fail_silently?
            # An MTA outage must not crash the download.
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
        if fork.status == 'pending':
            notify_queued(fork)
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
    """Owner (or unlocked buyer) can fetch a historical ZIP. Never via .url."""
    project = get_object_or_404(AppProject, slug=slug)
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


def sitemap_xml(request):
    projects = AppProject.objects.filter(status='published').only('slug', 'updated_at')[:500]
    rows = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            f'<url><loc>{settings.SITE_URL}/</loc></url>']
    for p in projects:
        rows.append(f'<url><loc>{settings.SITE_URL}/app/{p.slug}/</loc><lastmod>{p.updated_at.date().isoformat()}</lastmod></url>')
    rows.append('</urlset>')
    return HttpResponse('\n'.join(rows), content_type='application/xml')


def trust_legend(request):
    """Public "what does the badge mean" page — the anti-fake read.

    5 Whys (4 points each) — why a static legend page at all?
    1. Why explain the badge? A verdict without a published standard is
       just marketing; the standard is what makes it trust. Non-devs (the
       majority of vibe builders) cannot infer "verified" from a tooltip.
       Fails-if: copy drifts from logic → the page imports TRUST_META and
       the check table from gallery.trust, so the site renders the code's
       actual definitions, not a writer's memory of them.
    2. Why no database on this page? Zero queries means zero leak surface
       and zero cost; the legend is the same for every visitor.
       Fails-if: per-vibe detail is wanted → the detail page already
       shows that vibe's trust_reasons() rows.
    3. Why spell out what is NOT checked? Overclaiming is how platforms
       lose trust; saying "we do not run your code" is the honesty rule
       the rest of the site already follows (no fake previews).
       Fails-if: a new check is added → add a row to the table below and
       the grader — a check the page claims but the code skips is a bug.
    4. Why explain how fakes are handled? The page is also the deterrent:
       stating that any content change resets the badge tells a would-be
       bait-and-switcher the trick cannot work here. Fails-if: someone
       finds a mutation path that skips the reset → it is a bug class the
       docs name ("status='pending' must ride invalidate_trust"), making
       it findable in review.
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
