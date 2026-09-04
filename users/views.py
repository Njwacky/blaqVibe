import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.http import Http404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db.models import Count, Sum
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.utils import timezone
from datetime import datetime, timedelta
from django_ratelimit.decorators import ratelimit
from .models import (
    MAX_PAYOUT_STARS,
    MIN_PAYOUT_STARS,
    Payout,
    Profile,
    Follow,
    SiteSettings,
    Tip,
    SecurityEvent,
    UsernameHistory,
    name_style_preview_maps,
)
from .forms import ChangeEmailForm, NameStyleForm, ProfileForm, RenameForm, TipForm
from .security import revoke_user_sessions
from .payouts import PayoutError, payout_rate_label, request_payout as request_payout_hold
from .social import social_connection_context
from .rename import (
    RENAME_COOLDOWN_DAYS,
    RENAME_COST_STARS,
    RENAME_RESERVE_DAYS,
    STYLE_COST_STARS,
    RenameError,
    cooldown_remaining,
    redirect_target_for_old_username,
    rename_user,
    set_name_style,
)
from gallery.models import AppProject
from gallery.access import user_can_see_project
from gallery.notify import notify


def profile_view(request, username):
    user = User.objects.filter(username=username).first()
    if user is None:
        target = redirect_target_for_old_username(username)
        if target is not None:
            return redirect('profile_view', username=target.username)
        raise Http404('No member with that username.')
    profile, _ = Profile.objects.get_or_create(user=user)
    is_own = request.user.is_authenticated and request.user == user

    vibes = AppProject.objects.filter(owner=user, status='published').order_by('-created_at')
    if is_own:
        vibes_all = AppProject.objects.filter(owner=user).order_by('-created_at')
    else:
        vibes_all = vibes
    counts = dict(
        AppProject.objects.filter(owner=user)
        .values_list('status')
        .annotate(c=Count('id'))
    )
    published_count = counts.get('published', 0)
    all_count = sum(counts.values())

    is_following = False
    if request.user.is_authenticated and not is_own:
        is_following = Follow.objects.filter(follower=request.user, following=user).exists()

    tab = request.GET.get('tab', 'vibes')
    if tab not in ('vibes', 'stars', 'followers', 'following'):
        tab = 'vibes'

    followers = []
    following = []
    following_set = set()
    if tab in ('followers', 'following'):
        followers = user.followers.select_related('follower')[:20]
        following = user.following.select_related('following')[:20]
        if request.user.is_authenticated:
            following_set = set(
                Follow.objects.filter(follower=request.user)
                .values_list('following__username', flat=True)
            )

    starred = []
    if tab == 'stars':
        from gallery.models import Star
        starred = [
            s for s in Star.objects.filter(user=user)
            .select_related('project', 'project__owner')
            .order_by('-created_at')
            if user_can_see_project(request.user, s.project)
        ]

    rank = profile.rank()
    stars_received = profile.stars_received()

    recent_tips = Tip.objects.filter(recipient=user).select_related('sender').order_by('-created_at')[:5]
    tips_total = Tip.objects.filter(recipient=user).aggregate(t=Sum('amount'))['t'] or 0

    from gallery.ranks import RANKS
    next_rank = None
    for threshold, name, _discount, _bonus in RANKS:
        if threshold > rank['threshold']:
            next_rank = {'name': name, 'threshold': threshold}
            break

    try:
        from .progress import ACHIEVEMENTS, progress_for
        progress = progress_for(user)
        progress['all_badges'] = ACHIEVEMENTS
    except Exception:
        logging.getLogger(__name__).exception('progress lookup failed for %s', username)
        progress = None

    return render(request, 'users/profile.html', {
        'progress': progress,
        'profile_user': user, 'profile': profile, 'is_own': is_own,
        'vibes': vibes, 'vibes_all': vibes_all,
        'published_count': published_count, 'all_count': all_count,
        'is_following': is_following, 'followers': followers, 'following': following,
        'following_set': following_set,
        'tab': tab, 'starred': starred,
        'rank': rank, 'next_rank': next_rank, 'stars_received': stars_received,
        'followers_count': user.followers.count(),
        'following_count': user.following.count(),
        'recent_tips': recent_tips, 'tips_total': tips_total,
    })

@login_required
def edit_profile(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        form = ProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "✓ Profile updated — no sensitive info leaked, all backend sanitized.")
            return redirect('profile_view', username=request.user.username)
    else:
        form = ProfileForm(instance=profile)
    cooldown = cooldown_remaining(profile)
    style_form = NameStyleForm(initial={
        'name_font': profile.name_font,
        'name_color': profile.name_color,
        'name_size': profile.name_size,
        'name_fx': profile.name_fx,
        'name_persona': profile.name_persona or 'classic',
    })
    return render(request, 'users/edit_profile.html', {
        'form': form,
        'profile': profile,
        'style_form': style_form,
        'name_style_maps': name_style_preview_maps(),
        'rename_cost': RENAME_COST_STARS,
        'style_cost': STYLE_COST_STARS,
        'cooldown_days': cooldown.days if cooldown else None,
        'cooldown_hours': (cooldown.seconds // 3600) if cooldown else None,
        'rename_cooldown_days': RENAME_COOLDOWN_DAYS,
        'rename_reserve_days': RENAME_RESERVE_DAYS,
        'rename_count': UsernameHistory.objects.filter(user=request.user).count(),
    })

@require_POST
@login_required
@ratelimit(key='user', rate='30/h', method='POST')
def toggle_follow(request, username):
    target = get_object_or_404(User, username=username)
    if target == request.user:
        return JsonResponse({'error': 'Cannot follow yourself'}, status=400)
    follow, created = Follow.objects.get_or_create(follower=request.user, following=target)
    if not created:
        follow.delete()
        return JsonResponse({'following': False, 'followers': target.followers.count()})
    if getattr(target.profile, 'notify_on_follow', True):
        notify(target, 'follow', f'@{request.user.username} followed you', url=f'/u/{request.user.username}/')
    return JsonResponse({'following': True, 'followers': target.followers.count()})


@require_POST
@login_required
@ratelimit(key='user', rate='20/h', method='POST')
def tip_user(request, username):
    target = get_object_or_404(User, username=username)
    if target == request.user:
        return JsonResponse({'error': 'You cannot tip yourself'}, status=400)
    form = TipForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'error': 'Tip must be 1–1000 stars with a note up to 200 chars.'}, status=400)
    from .wallet import send_tip
    try:
        tip = send_tip(
            request.user,
            target,
            form.cleaned_data['amount'],
            form.cleaned_data['message'],
        )
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    notify(
        target,
        'tip',
        f'@{request.user.username} tipped you {tip.amount}★',
        body=tip.message or 'No message — just stars.',
        url=f'/u/{request.user.username}/',
    )
    request.user.profile.refresh_from_db()
    return JsonResponse({
        'ok': True,
        'amount': tip.amount,
        'message': tip.message,
        'balance': request.user.profile.stars_balance,
    })

@login_required
def payout_dashboard(request):
    from gallery.models import Sale, Trade
    from gallery.economy import stars_earned, stars_spent
    from gallery.payments import paystack_enabled
    from .models import StarEvent
    sales = Sale.objects.filter(seller=request.user).select_related('project','buyer').order_by('-created_at')[:20]
    trades = Trade.objects.filter(seller=request.user).select_related('project','buyer').order_by('-created_at')[:20]
    bought = Trade.objects.filter(buyer=request.user).select_related('project','seller').order_by('-created_at')[:20]
    total_zar = sum(s.amount_zar for s in Sale.objects.filter(seller=request.user))
    star_events = StarEvent.objects.filter(user=request.user)[:50]
    tips_received = Tip.objects.filter(recipient=request.user).select_related('sender').order_by('-created_at')[:20]
    tips_total = Tip.objects.filter(recipient=request.user).aggregate(t=Sum('amount'))['t'] or 0

    now_local = timezone.localtime()
    start_date = now_local.date() - timedelta(days=13)
    start_dt = timezone.make_aware(
        datetime.combine(start_date, datetime.min.time()),
        timezone.get_current_timezone(),
    )
    ledger_events = StarEvent.objects.filter(user=request.user, created_at__gte=start_dt).only('created_at', 'delta')
    net_by_day = {}
    earned_by_day = {}
    spent_by_day = {}
    for ev in ledger_events:
        d = timezone.localtime(ev.created_at).date()
        net_by_day[d] = net_by_day.get(d, 0) + ev.delta
        if ev.delta >= 0:
            earned_by_day[d] = earned_by_day.get(d, 0) + ev.delta
        else:
            spent_by_day[d] = spent_by_day.get(d, 0) - ev.delta
    chart_days = []
    max_val = 0
    for i in range(14):
        d = start_date + timedelta(days=i)
        e, s = earned_by_day.get(d, 0), spent_by_day.get(d, 0)
        max_val = max(max_val, e, s)
        chart_days.append({'date': d, 'earned': e, 'spent': s})
    trend = [0] * 14
    running = request.user.profile.stars_balance
    for i in range(13, -1, -1):
        d = start_date + timedelta(days=i)
        trend[i] = running
        running -= net_by_day.get(d, 0)
    from .charts import activity_chart, balance_chart
    chart_activity_svg = activity_chart(chart_days, max_val)
    chart_balance_svg = balance_chart(trend, min(trend), max(trend))

    return render(request, 'users/payout_dashboard.html', {
        'sales': sales,
        'trades': trades,
        'bought': bought,
        'stars_balance': request.user.profile.stars_balance,
        'stars_earned': stars_earned(request.user),
        'stars_spent': stars_spent(request.user),
        'star_events': star_events,
        'tips_received': tips_received,
        'tips_total': tips_total,
        'chart_activity_svg': chart_activity_svg,
        'chart_balance_svg': chart_balance_svg,
        'total_zar': total_zar,
        'paystack_enabled': paystack_enabled(),
        'is_pro': request.user.profile.is_pro_active,
        'pro_since': getattr(request.user.profile, 'pro_since', None),
        'pro_until': getattr(request.user.profile, 'pro_until', None),
        'payouts': Payout.objects.filter(user=request.user)[:10],
        'open_payout': Payout.objects.filter(user=request.user, status='requested').first(),
        'payout_rate_label': payout_rate_label(),
        'payout_min_stars': MIN_PAYOUT_STARS,
        'payout_max_stars': MAX_PAYOUT_STARS,
    })

@login_required
@require_POST
@ratelimit(key='user', rate='5/h', method='POST')
def request_payout(request):
    """Queue a cash-out. All money rules live in users.payouts — the view
    only carries the user's input and the outcome back to the dashboard."""
    try:
        payout = request_payout_hold(
            request.user,
            request.POST.get('amount_stars'),
            request.POST.get('bank_name'),
            request.POST.get('account_number'),
            request.POST.get('holder_name'),
        )
        messages.success(
            request,
            f'Cash-out queued: {payout.amount_stars} ★ → R{payout.amount_zar} to '
            f'{payout.bank_name} {payout.account_masked}. A money admin reviews it next.',
        )
    except PayoutError as e:
        messages.error(request, e.message)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('payout view crush')
        messages.error(request, 'Cash-out failed — nothing was debited. Try again.')
    return redirect('payout_dashboard')

@login_required
@require_POST
def activate_pro_trial(request):
    try:
        from django.utils import timezone
        profile,_ = Profile.objects.get_or_create(user=request.user)
        if profile.is_pro_active:
            messages.info(request, "You are already Pro — enjoy Who Viewed + AI README.")
            return redirect('payout_dashboard')
        if profile.pro_until:
            messages.error(request, "Your free Pro trial already ended. Pro is a paid upgrade now.")
            return redirect('payout_dashboard')
        now = timezone.now()
        from datetime import timedelta
        profile.is_pro = True
        profile.pro_since = now
        profile.pro_until = now + timedelta(days=7)
        profile.save(update_fields=['is_pro', 'pro_since', 'pro_until'])
        messages.success(request, "Pro trial activated for 7 days — see who viewed your vibes and use AI README.")
        return redirect('payout_dashboard')
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"pro trial crush: {e}")
        messages.error(request, "Pro activation failed silently")
        return redirect('payout_dashboard')

NOTIFICATION_PREF_ROWS = (
    ('notify_on_star', 'Someone stars your vibe', 'The quiet one — the first sign a stranger liked your work.'),
    ('notify_on_fork', 'Someone forks/remixes your vibe', 'Usually the most useful note you will get: somebody built on you.'),
    ('notify_on_comment', 'Comments and reviews', 'Feedback on a vibe you published.'),
    ('notify_on_follow', 'New followers', 'Who started following you.'),
    ('notify_on_trade', 'Trades and sales', 'Money events — stars moved for one of your vibes.'),
    ('notify_on_milestone', 'Star milestones', 'Only at 10 / 50 / 100 / 500 ★, so this one is quiet by design.'),
)


@login_required
def settings_view(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    site = SiteSettings.get() if profile.is_superadmin() else None
    notification_prefs = [
        {'key': key, 'label': label, 'help': help_text,
         'value': getattr(profile, key, True)}
        for key, label, help_text in NOTIFICATION_PREF_ROWS
    ]
    return render(request, 'users/settings.html', {
        'profile': profile,
        'site': site,
        'notification_prefs': notification_prefs,
        'security_events': SecurityEvent.objects.filter(user=request.user)[:8],
        **social_connection_context(request.user),
    })


@login_required
@require_POST
@ratelimit(key='user', rate='5/h', method='POST')
def logout_other_devices(request):
    """Owner-controlled emergency response for a suspected account takeover."""
    count = revoke_user_sessions(
        request.user, keep_session_key=request.session.session_key
    )
    SecurityEvent.objects.create(
        user=request.user, event='sessions_revoked', detail=f'{count} other session(s) by account owner'
    )
    messages.success(request, f'Signed out {count} other device(s).')
    return redirect('settings')


@login_required
@require_POST
@ratelimit(key='user', rate='5/h', method='POST')
def rename_username(request):
    """Spend a rename card (Pro) or burn stars — users.rename is the only
    writer of User.username after signup.

    5 Whys: why 5/h when the 30-day cooldown already throttles? The ratelimit
    guards the FAILURE path — a bot (or a bug) hammering the endpoint with
    rejected candidates must not get 100 free validation oracle calls a
    second (which usernames are taken? which are reserved?). Same shape as
    git-token rotation: the cooldown limits success, the ratelimit limits
    attempts.
    """
    form = RenameForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect('edit_profile')
    try:
        history = rename_user(request.user, form.cleaned_data['new_username'])
    except RenameError as e:
        messages.error(request, e.message)
        return redirect('edit_profile')
    except Exception:
        import logging
        logging.getLogger(__name__).exception('rename view crush')
        messages.error(request, 'Rename failed — nothing was charged. Try again.')
        return redirect('edit_profile')
    if history.method == 'pro':
        messages.success(
            request,
            f'✓ Renamed to @{history.new_username} — Pro rename card, 0 ★ '
            f'charged. Your old name @{history.old_username} stays reserved '
            f'for {RENAME_RESERVE_DAYS} days and old links redirect here.',
        )
    else:
        messages.success(
            request,
            f'✓ Renamed to @{history.new_username} — {history.cost_stars} ★ '
            f'burned for the rename card. Your old name @{history.old_username} '
            f'stays reserved for {RENAME_RESERVE_DAYS} days and old links '
            f'redirect here.',
        )
    return redirect('settings')


@login_required
@require_POST
@ratelimit(key='user', rate='10/h', method='POST')
def set_name_style_view(request):
    """Style the display name — free while Pro, else STYLE_COST_STARS ★
    burned per change (users/rename.py holds the rules). 10/h: styling is
    cheaper than renaming, so attempts are cheaper to probe; both stay far
    below the wallet-moving endpoints (tip 20/h)."""
    form = NameStyleForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Pick a people-style, font, color, size and effect from the list.')
        return redirect('edit_profile')
    try:
        profile, changed = set_name_style(
            request.user,
            form.cleaned_data['name_font'],
            form.cleaned_data['name_color'],
            form.cleaned_data['name_size'],
            form.cleaned_data['name_fx'],
            form.cleaned_data.get('name_persona') or 'classic',
        )
    except RenameError as e:
        messages.error(request, e.message)
        return redirect('edit_profile')
    except Exception:
        import logging
        logging.getLogger(__name__).exception('name style view crush')
        messages.error(request, 'Style change failed — nothing was charged. Try again.')
        return redirect('edit_profile')
    if not changed:
        messages.info(request, 'That is already your name style — nothing charged.')
    elif profile.is_pro_active:
        messages.success(request, '✓ Name styled — Pro perk, 0 ★ charged. Flex it.')
    else:
        messages.success(
            request,
            f'✓ Name styled — {STYLE_COST_STARS} ★ burned. It shows on your '
            'profile, follower lists and tips.',
        )
    return redirect('edit_profile')

@login_required
@require_POST
@ratelimit(key='user', rate='5/h', method='POST')
def regenerate_git_token(request):
    """Issue a fresh git credential — plaintext shown ONCE, hash stored.

    5 Whys: Why rotate instead of a fixed token? A git credential that
    leaked (shell history, CI logs) must die without deleting the account.
    Why show it in a flash message? We do not store plaintext, so the
    rotation response is the only moment the user can copy it. Why 5/h?
    Rotation invalidates the old token; a flood would be a self-DoS.
    """
    if getattr(request, 'limited', False):
        messages.error(request, 'Rate limit: 5 git tokens per hour.')
        return redirect('settings')
    try:
        token = request.user.profile.rotate_git_token()
        messages.success(
            request,
            f'New git token (shows once — copy it now): {token} '
            'Use your username + this token as the password for git clone/push.',
        )
    except Exception:
        messages.error(request, 'Could not rotate the git token. Try again.')
    return redirect('settings')

@login_required
@require_POST
def toggle_setting(request):
    """Toggle any user or site setting — 1 tap, no form, crush silently, backend only."""
    try:
        key = request.POST.get('key')
        value = request.POST.get('value') == 'true'
        user_keys = ['auto_language','nolo_enabled','auto_thumbnail','allow_trading','email_on_trade','email_on_review','show_language','allow_forks','allow_prs','allow_comments','allow_reviews',
                     'notify_on_star','notify_on_fork','notify_on_follow','notify_on_comment','notify_on_trade','notify_on_milestone']
        if key in user_keys:
            profile,_ = Profile.objects.get_or_create(user=request.user)
            setattr(profile, key, value)
            profile.save(update_fields=[key])
            return JsonResponse({'ok': True, key: value})
        critical_locked = ['clamav_enabled', 'r2_enabled']
        if key in critical_locked:
            return JsonResponse({'error': 'Critical — always on, cannot toggle'}, status=400)
        site_keys = ['maintenance','search_enabled','pwa_enabled','auto_run_enabled']
        if key in site_keys:
            if not request.user.profile.is_superadmin():
                return JsonResponse({'error': 'Only superadmin'}, status=403)
            site = SiteSettings.get()
            setattr(site, key, value)
            site.save(update_fields=[key])
            return JsonResponse({'ok': True, key: value})
        return JsonResponse({'error': 'Unknown key'}, status=400)
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"toggle_setting crush: {e}")
        return JsonResponse({'error': 'Failed silently'}, status=500)

@login_required
@require_POST
def delete_account(request):
    """Delete the account without destroying other people's purchases.

    5 Whys:
    1. Why not a plain user.delete()? Sale/Trade PROTECT their project —
       cascading a sold vibe would raise ProtectedError (and rightly so).
    2. Why hand sold vibes to a ghost user instead of deleting them?
       Buyers paid for those ZIPs. The file must stay downloadable.
    3. Why status='removed'? The vibe must vanish from the public feed the
       moment its creator leaves — only existing buyers keep access.
    4. Why do Sale/Trade rows survive with buyer/seller = NULL? They are
       money records. Deleting your account must not delete a counterparty's
       receipt.
    5. Why release BEFORE user.delete()? The cascade runs inside delete();
       by then it is too late to reassign anything.
    """
    confirm = (request.POST.get('confirm') or '').strip()
    if confirm != request.user.username:
        messages.error(request, "Type your username to confirm account deletion.")
        return redirect('settings')
    from django.contrib.auth import logout
    from gallery.lifecycle import release_account_projects
    user = request.user
    release_account_projects(user)
    logout(request)
    user.delete()
    messages.success(request, "Your account and vibes were deleted. Vibes people already bought stay downloadable for them.")
    return redirect('feed')


def send_verify_email(request, user):
    if not (user and user.email):
        return
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = request.build_absolute_uri(f'/accounts/verify/{uid}/{token}/')
    send_mail(
        'Confirm your BlaqVibes email',
        f'Hi @{user.username},\n\nConfirm your email:\n{link}\n\nBlaqVibes',
        getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za'),
        [user.email],
        fail_silently=True,
    )


def apply_unverified_email(user, email):
    """Point this account at a new unconfirmed mailbox.

    5 Whys: why a helper, not `user.email = …` in the view?
    1. Why touch allauth EmailAddress too? Password-reset and email-login
       read that table. Updating User.email alone leaves the old address
       as primary/verified and the banner never matches the inbox.
    2. Why drop other EmailAddress rows for this user? An unverified
       account has one mailbox. Keeping the typo as a second row would
       let a later confirm of the old token revive it.
    3. Why never steal another user's EmailAddress? Email is unique in
       allauth. The form already rejected a taken User.email; this is
       the same fail-closed for the allauth row.
    4. Why force email_verified=False? Changing the address must lock
       the welcome grant and recovery until the NEW mailbox clicks.
    5. Why lowercase here too? Signup and ChangeEmailForm store lower;
       a mixed-case write would dodge iexact uniqueness later.
    """
    email = (email or '').strip().lower()
    if not user or not email:
        return
    if (user.email or '').strip().lower() != email:
        user.email = email
        user.save(update_fields=['email'])
    profile, _ = Profile.objects.get_or_create(user=user)
    if profile.email_verified:
        profile.email_verified = False
        profile.save(update_fields=['email_verified'])
    try:
        from allauth.account.models import EmailAddress
        clash = EmailAddress.objects.filter(email__iexact=email).exclude(user=user).first()
        if clash:
            return
        EmailAddress.objects.filter(user=user).exclude(email__iexact=email).delete()
        addr, created = EmailAddress.objects.get_or_create(
            user=user,
            email=email,
            defaults={'verified': False, 'primary': True},
        )
        if not created and (addr.verified or not addr.primary):
            addr.verified = False
            addr.primary = True
            addr.save(update_fields=['verified', 'primary'])
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            'allauth EmailAddress update failed for user=%s', getattr(user, 'pk', None)
        )


def verify_email(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except Exception:
        user = None
    if user and default_token_generator.check_token(user, token):
        profile, _ = Profile.objects.get_or_create(user=user)
        profile.email_verified = True
        profile.save(update_fields=['email_verified'])
        from .wallet import grant_welcome_stars
        from .models import WELCOME_STARS
        if grant_welcome_stars(user):
            messages.success(request, f"Email confirmed — your {WELCOME_STARS} ★ welcome grant is in your wallet.")
        else:
            messages.success(request, "Email confirmed.")
        return redirect('feed')
    messages.error(request, "That confirmation link is invalid or expired.")
    return redirect('login')


@login_required
def resend_verify_email(request):
    """Old /send/ URL. Do not fire mail until they confirm the address."""
    return redirect('edit_email')


@login_required
@ratelimit(key='user', rate='5/h', method='POST')
def edit_email(request):
    """Confirm-or-fix the mailbox, then send the activation link.

    5 Whys: why a page instead of the old one-click resend?
    1. Why stop the POST-resend? A wrong address at signup mailed a
       mailbox the person cannot open. Resend made that worse.
    2. Why edit + send in one POST? Two steps (save, then resend) is a
       place people bounce. One button: this is the address, send it.
    3. Why 5/h? Same ceiling as git-token rotation — enough for a typo
       retry, brutal for a loop against someone else's inbox.
    4. Why bounce verified accounts to settings? Changing a confirmed
       mailbox is a different (takeover) flow; this page is for activate.
    5. Why stay on this page after send? They may still have the typo
       and need another edit without hunting settings.
    """
    profile, _ = Profile.objects.get_or_create(user=request.user)
    if profile.email_verified:
        messages.info(request, 'Your email is already confirmed.')
        return redirect('settings')
    if request.method == 'POST':
        if getattr(request, 'limited', False):
            messages.error(request, 'Rate limit: 5 confirmation emails per hour.')
            return redirect('edit_email')
        form = ChangeEmailForm(request.POST, user=request.user)
        if form.is_valid():
            email = form.cleaned_data['email']
            changed = (request.user.email or '').strip().lower() != email
            if changed:
                apply_unverified_email(request.user, email)
                request.user.refresh_from_db(fields=['email'])
            if not request.user.email:
                messages.error(request, 'Add an email to your account first.')
                return redirect('edit_email')
            send_verify_email(request, request.user)
            if changed:
                messages.success(
                    request,
                    f'Email updated to {email}. Confirmation sent — check that inbox.',
                )
            else:
                messages.success(
                    request,
                    f'Confirmation sent to {email}. Check that inbox (or the server console in dev).',
                )
            return redirect('edit_email')
    else:
        form = ChangeEmailForm(user=request.user, initial={'email': request.user.email})
    return render(request, 'users/edit_email.html', {
        'form': form,
        'profile': profile,
    })
