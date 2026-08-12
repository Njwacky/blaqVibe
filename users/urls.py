from django.urls import path
from . import views, admin_views
urlpatterns = [
    path('u/<str:username>/', views.profile_view, name='profile_view'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/toggle/', views.toggle_setting, name='toggle_setting'),
    path('settings/profile/', views.edit_profile, name='edit_profile'),
    path('payout/', views.payout_dashboard, name='payout_dashboard'),
    path('pro/activate/', views.activate_pro_trial, name='activate_pro_trial'),
    path('u/<str:username>/follow/', views.toggle_follow, name='toggle_follow'),
    path('admin/dashboard/', admin_views.admin_dashboard, name='admin_dashboard'),
    path('admin/roles/', admin_views.manage_roles, name='manage_roles'),
    path('admin/roles/<str:username>/', admin_views.set_role, name='set_role'),
    path('admin/audit/', admin_views.audit_log, name='audit_log'),
]
