from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from allauth.account import views as allauth_account_views
from django.http import FileResponse
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache
from gallery import views as gviews
from users.forms import StyledAuthenticationForm, StyledPasswordResetForm, StyledSetPasswordForm

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

# Auth views are defined once and routed twice: under the app's own URL
# names AND under allauth's names. allauth's internal pages — the social
# signup form the language gate routes dirty handles to, login-cancelled,
# login-error — reverse('account_login'/'account_signup') unconditionally.
# Without these aliases those pages 500 (NoReverseMatch). Aliasing to the
# app's own views keeps one login page, one signup page — no duplicates.
_login_view = auth_views.LoginView.as_view(
    template_name='registration/login.html',
    authentication_form=StyledAuthenticationForm,
)
_logout_view = auth_views.LogoutView.as_view()

# Social OAuth routes — docs/specs/SOCIAL_AUTH.md promises
# /accounts/social/<provider>/login/ + /login/callback/. Mount ONLY the
# providers that have real credentials in the environment: an
# unconfigured provider would 500 on login (no client id), and its
# button is already hidden by users.social.configured_social_providers().
_social_urlpatterns = [path('', include('allauth.socialaccount.urls'))]
for _provider_id in (settings.SOCIALACCOUNT_PROVIDERS or {}):
    # The provider urlconf already carries its own "<provider>/" prefix
    # (github/login/, github/login/callback/) — include it unprefixed.
    _social_urlpatterns.append(
        path('', include(f'allauth.socialaccount.providers.{_provider_id}.urls'))
    )

urlpatterns = [
    path('sw.js', serve_sw, name='sw'),
    path('robots.txt', serve_robots, name='robots'),
    path('blaq-admin-secure/', admin.site.urls),
    path('admin/', include('gallery.honeypot_urls')),
    path('', include('gallery.urls')),
    path('', include('users.urls')),
    path('accounts/social/', include(_social_urlpatterns)),
    # allauth email-confirmation mechanism — social signup verification
    # mails link here. The pages are branded (templates/account/*), and
    # only this mechanism is mounted: no parallel allauth login/signup.
    path('accounts/confirm-email/', allauth_account_views.email_verification_sent, name='account_email_verification_sent'),
    re_path(r'^accounts/confirm-email/(?P<key>[-:\w]+)/$', allauth_account_views.confirm_email, name='account_confirm_email'),
    path('accounts/login/', _login_view, name='login'),
    path('accounts/login/', _login_view, name='account_login'),
    path('accounts/logout/', _logout_view, name='logout'),
    path('accounts/logout/', _logout_view, name='account_logout'),
    path('accounts/signup/', gviews.signup, name='signup'),
    path('accounts/signup/', gviews.signup, name='account_signup'),
    path('accounts/password_reset/', auth_views.PasswordResetView.as_view(
        template_name='registration/password_reset_form.html',
        email_template_name='registration/password_reset_email.txt',
        subject_template_name='registration/password_reset_subject.txt',
        form_class=StyledPasswordResetForm,
        success_url=reverse_lazy('password_reset_done'),
    ), name='password_reset'),
    path('accounts/password_reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='registration/password_reset_done.html',
    ), name='password_reset_done'),
    path('accounts/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='registration/password_reset_confirm.html',
        form_class=StyledSetPasswordForm,
        success_url=reverse_lazy('password_reset_complete'),
    ), name='password_reset_confirm'),
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
