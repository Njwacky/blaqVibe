from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
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

handler404 = 'gallery.views.safe_404'
handler403 = 'gallery.views.safe_403'
handler500 = 'gallery.views.safe_500'

urlpatterns = [
    path('sw.js', serve_sw, name='sw'),
    path('blaq-admin-secure/', admin.site.urls),
    path('admin/', include('gallery.honeypot_urls')),
    path('', include('gallery.urls')),
    path('', include('users.urls')),
    path('accounts/social/', include('allauth.socialaccount.urls')),
    path('accounts/login/', auth_views.LoginView.as_view(
        template_name='registration/login.html',
        authentication_form=StyledAuthenticationForm,
    ), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('accounts/signup/', gviews.signup, name='signup'),
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
