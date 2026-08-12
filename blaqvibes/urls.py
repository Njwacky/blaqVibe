from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.http import FileResponse
from django.views.decorators.cache import never_cache
from gallery import views as gviews

@never_cache
def serve_sw(request):
    # Serve SW at /sw.js with scope / — backend, no secrets, crush silently
    try:
        return FileResponse(open(settings.BASE_DIR / 'static' / 'sw.js', 'rb'), content_type='application/javascript')
    except:
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
    path('accounts/login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('accounts/signup/', gviews.signup, name='signup'),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
