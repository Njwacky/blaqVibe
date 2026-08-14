from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from .models import Profile, Follow, SiteSettings
from .forms import ProfileForm
from gallery.models import AppProject
from gallery.notify import notify

# 5 Whys: Why /u/<username>/ not /profile/<id>? Username is brand, SEO, like GitHub. Why not expose email? Privacy — only bio/location.

def profile_view(request, username):
    user = get_object_or_404(User, username=username)
    profile, _ = Profile.objects.get_or_create(user=user)
    vibes = AppProject.objects.filter(owner=user, status='published').order_by('-created_at')
    # If viewing own profile, also show pending/quarantined
    if request.user == user:
        vibes_all = AppProject.objects.filter(owner=user).order_by('-created_at')
    else:
        vibes_all = vibes
    is_following = False
    if request.user.is_authenticated and request.user != user:
        is_following = Follow.objects.filter(follower=request.user, following=user).exists()
    followers = user.followers.select_related('follower')[:20]
    following = user.following.select_related('following')[:20]
    tab = request.GET.get('tab','vibes')
    stars = []
    if tab == 'stars':
        from gallery.models import Star
        stars = AppProject.objects.filter(star_set__user=user).order_by('-star_set__created_at')
    return render(request, 'users/profile.html', {
        'profile_user': user, 'profile': profile, 'vibes': vibes, 'vibes_all': vibes_all,
        'is_following': is_following, 'followers': followers, 'following': following,
        'tab': tab, 'stars': stars
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
    return render(request, 'users/edit_profile.html', {'form': form, 'profile': profile})

@require_POST
@login_required
def toggle_follow(request, username):
    target = get_object_or_404(User, username=username)
    if target == request.user:
        return JsonResponse({'error': 'Cannot follow yourself'}, status=400)
    # Backend only — no JS secrets, just follower count
    follow, created = Follow.objects.get_or_create(follower=request.user, following=target)
    if not created:
        follow.delete()
        return JsonResponse({'following': False, 'followers': target.followers.count()})
    notify(target, 'follow', f'@{request.user.username} followed you', url=f'/u/{request.user.username}/')
    return JsonResponse({'following': True, 'followers': target.followers.count()})

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
    # The append-only ledger — every wallet move, newest first. This is the
    # answer to "why is my balance N ★?" without a support ticket.
    star_events = StarEvent.objects.filter(user=request.user)[:50]
    return render(request, 'users/payout_dashboard.html', {
        'sales': sales,
        'trades': trades,
        'bought': bought,
        'stars_balance': request.user.profile.stars_balance,
        'stars_earned': stars_earned(request.user),
        'stars_spent': stars_spent(request.user),
        'star_events': star_events,
        'total_zar': total_zar,
        'paystack_enabled': paystack_enabled(),
        'is_pro': request.user.profile.is_pro_active,
        'pro_since': getattr(request.user.profile, 'pro_since', None),
        'pro_until': getattr(request.user.profile, 'pro_until', None),
    })

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

@login_required
def settings_view(request):
    profile, _ = Profile.objects.get_or_create(user=request.user)
    site = SiteSettings.get() if profile.is_superadmin() else None
    return render(request, 'users/settings.html', {'profile': profile, 'site': site})

@login_required
@require_POST
def toggle_setting(request):
    """Toggle any user or site setting — 1 tap, no form, crush silently, backend only."""
    try:
        key = request.POST.get('key')
        value = request.POST.get('value') == 'true'
        # User toggles
        user_keys = ['auto_language','nolo_enabled','auto_thumbnail','allow_trading','email_on_trade','email_on_review','show_language','allow_forks','allow_prs','allow_comments','allow_reviews']
        if key in user_keys:
            profile,_ = Profile.objects.get_or_create(user=request.user)
            setattr(profile, key, value)
            profile.save(update_fields=[key])
            return JsonResponse({'ok': True, key: value})
        # Site toggles — superadmin only, but very critical ones are locked always-on
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
        # The 5 ★ welcome grant is bound to a verified mailbox, not to signup.
        # grant_welcome_stars is idempotent — replaying the link pays nothing.
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
@require_POST
def resend_verify_email(request):
    if request.user.profile.email_verified:
        messages.info(request, "Your email is already confirmed.")
        return redirect('settings')
    if not request.user.email:
        messages.error(request, "Add an email to your account first.")
        return redirect('edit_profile')
    send_verify_email(request, request.user)
    messages.success(request, "Confirmation email sent. Check your inbox (or the server console in dev).")
    return redirect('settings')
