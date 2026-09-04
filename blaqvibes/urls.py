from functools import wraps
from importlib import import_module

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.http import FileResponse, Http404
from django.urls import reverse_lazy
from django.urls.resolvers import URLPattern, URLResolver
from django.views.decorators.cache import never_cache
from django_ratelimit.decorators import ratelimit
from gallery import views as gviews
from users.forms import StyledAuthenticationForm, StyledPasswordResetForm, StyledSetPasswordForm, StyledPasswordChangeForm
from users.security import AccountPasswordChangeView, AccountPasswordResetConfirmView

@never_cache
def serve_sw(request):
    # Serve SW at /sw.js with scope / — backend, no secrets, crush silently
    try:
        return FileResponse(open(settings.BASE_DIR / 'static' / 'sw.js', 'rb'), content_type='application/javascript')
    except Exception:
        from django.http import Http404
        raise Http404

def serve_robots(request):
    # Serve robots.txt — plain text, no secrets. Collected from static/.
    try:
        return FileResponse(open(settings.BASE_DIR / 'static' / 'robots.txt', 'rb'), content_type='text/plain')
    except Exception:
        from django.http import Http404
        raise Http404

handler404 = 'gallery.views.safe_404'
handler403 = 'gallery.views.safe_403'
handler500 = 'gallery.views.safe_500'

# BlaqVibes owns login/signup (they are Django auth views, not allauth ones),
# but allauth's own pages reverse the names `account_login`/`account_signup`
# when they render "back to sign in" links. Without these aliases every
# allauth-rendered page — the social signup form, the cancelled page, the
# authentication-error page — dies with NoReverseMatch instead of rendering.
# Same URL, same view, second name: no duplicate route, no redirect hop.
# Credential-guessing surface. Every other write endpoint carries a
# @ratelimit; login and password-reset are the two that gate *accounts*, so
# they are bounded too (per IP, POST only). block=True raises Ratelimited
# (a PermissionDenied) → handler403 → safe_403, the same friendly page the
# rest of the site shows. GET stays unlimited so a bot cannot 403 the login
# form itself out from under a classroom/NAT.
login_view = ratelimit(key='ip', rate='20/m', method='POST')(
    auth_views.LoginView.as_view(
        template_name='registration/login.html',
        authentication_form=StyledAuthenticationForm,
    )
)

# Reset-email bombing + password-set brute force: same ceiling. The email
# form can't leak whether an address exists (Django's generic response), but
# an unbounded POST loop still costs one email each — bound it.
password_reset_view = ratelimit(key='ip', rate='10/m', method='POST')(
    auth_views.PasswordResetView.as_view(
        template_name='registration/password_reset_form.html',
        email_template_name='registration/password_reset_email.txt',
        subject_template_name='registration/password_reset_subject.txt',
        form_class=StyledPasswordResetForm,
        success_url=reverse_lazy('password_reset_done'),
    )
)

password_reset_confirm_view = ratelimit(key='ip', rate='20/m', method='POST')(
    AccountPasswordResetConfirmView.as_view(
        template_name='registration/password_reset_confirm.html',
        form_class=StyledSetPasswordForm,
        success_url=reverse_lazy('password_reset_complete'),
    )
)

def social_provider_urls():
    """Mount every provider's login/callback routes, 404 when unconfigured.
    """
    patterns = []
    for slug in ('google', 'github', 'facebook'):
        module = import_module(f'allauth.socialaccount.providers.{slug}.urls')
        guarded = [
            _require_configured_provider(slug, pattern)
            for pattern in module.urlpatterns
        ]
        # No namespace: allauth reverses these as plain `github_callback`.
        patterns.append(path('accounts/social/', include(guarded)))
    return patterns

def _require_configured_provider(slug, pattern):
    """Wrap one URL pattern (or the provider's include) with the guard.

    Rebuilds rather than mutating: `module.urlpatterns` is allauth's own
    module-level list, and editing it in place would leak our guard into any
    other import of the same module.
    """
    if isinstance(pattern, URLResolver):
        nested = [
            _require_configured_provider(slug, sub) for sub in pattern.url_patterns
        ]
        return URLResolver(pattern.pattern, nested, pattern.default_kwargs)

    inner = pattern.callback

    @wraps(inner)
    def guarded(request, *args, **kwargs):
        if slug not in getattr(settings, 'SOCIALACCOUNT_PROVIDERS', {}):
            raise Http404(f'{slug} sign-in is not enabled')
        return inner(request, *args, **kwargs)

    return URLPattern(pattern.pattern, guarded, pattern.default_args, pattern.name)

urlpatterns = [
    path('sw.js', serve_sw, name='sw'),
    path('robots.txt', serve_robots, name='robots'),
    path('blaq-admin-secure/', admin.site.urls),
    path('admin/', include('gallery.honeypot_urls')),
    path('', include('gallery.urls')),
    path('', include('users.urls')),
    path('accounts/social/', include('allauth.socialaccount.urls')),
    *social_provider_urls(),
    path('accounts/login/', login_view, name='login'),
    path('accounts/login/', login_view, name='account_login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('accounts/password/change/', AccountPasswordChangeView.as_view(form_class=StyledPasswordChangeForm), name='password_change'),
    path('accounts/password/change/done/', auth_views.PasswordChangeDoneView.as_view(template_name='registration/password_change_done.html'), name='password_change_done'),
    path('accounts/signup/', gviews.signup, name='signup'),
    path('accounts/signup/', gviews.signup, name='account_signup'),
    path('accounts/password_reset/', password_reset_view, name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='registration/password_reset_done.html',
    ), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', password_reset_confirm_view, name='password_reset_confirm'),
    path('accounts/reset/done/', auth_views.PasswordResetCompleteView.as_view(
        template_name='registration/password_reset_complete.html',
    ), name='password_reset_complete'),
]
if settings.DEBUG:
    from gallery.media_views import serve_public_media
    urlpatterns += [
        path('media/<path:path>', serve_public_media),
    ]
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
