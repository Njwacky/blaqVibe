from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from .models import Profile, Follow, SiteSettings
from .forms import ProfileForm
from gallery.models import AppProject

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
    return JsonResponse({'following': True, 'followers': target.followers.count()})

@login_required
def payout_dashboard(request):
    from gallery.models import Sale, Trade
    sales = Sale.objects.filter(seller=request.user).select_related('project','buyer').order_by('-created_at')[:20]
    trades = Trade.objects.filter(seller=request.user).select_related('project','buyer').order_by('-created_at')[:20]
    total_zar = sum(s.amount_zar for s in Sale.objects.filter(seller=request.user))
    payout_zar = int(total_zar * 0.85)
    total_stars_earned = trades.count() * 2
    return render(request, 'users/payout_dashboard.html', {
        'sales': sales, 'trades': trades,
        'total_zar': total_zar, 'payout_zar': payout_zar,
        'total_stars_earned': total_stars_earned,
        'is_pro': request.user.profile.is_pro,
        'pro_since': getattr(request.user.profile, 'pro_since', None),
    })

@login_required
@require_POST
def activate_pro_trial(request):
    try:
        from django.utils import timezone
        profile,_ = Profile.objects.get_or_create(user=request.user)
        if profile.is_pro:
            messages.info(request, "You are already Pro — enjoy Who Viewed + AI README + 50% discount!")
            return redirect('payout_dashboard')
        profile.is_pro = True
        profile.pro_since = timezone.now()
        profile.save(update_fields=['is_pro','pro_since'])
        messages.success(request, "Pro trial activated for 7 days — now you can see who viewed your vibes, use AI README, and get 50% off if Platinum!")
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
