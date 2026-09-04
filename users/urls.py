from django.urls import path
from . import views, admin_views
urlpatterns = [
    path('u/<str:username>/', views.profile_view, name='profile_view'),
    path('settings/', views.settings_view, name='settings'),
    path('settings/logout-other-devices/', views.logout_other_devices, name='logout_other_devices'),
    path('settings/rename/', views.rename_username, name='rename_username'),
    path('settings/name-style/', views.set_name_style_view, name='set_name_style'),
    path('settings/git-token/', views.regenerate_git_token, name='regenerate_git_token'),
    path('settings/toggle/', views.toggle_setting, name='toggle_setting'),
    path('settings/delete-account/', views.delete_account, name='delete_account'),
    path('accounts/verify/<uidb64>/<token>/', views.verify_email, name='verify_email'),
    path('accounts/verify/email/', views.edit_email, name='edit_email'),
    path('accounts/verify/send/', views.resend_verify_email, name='resend_verify_email'),
    path('settings/profile/', views.edit_profile, name='edit_profile'),
    path('pro/activate/', views.activate_pro_trial, name='activate_pro_trial'),
    path('u/<str:username>/follow/', views.toggle_follow, name='toggle_follow'),
    path('u/<str:username>/tip/', views.tip_user, name='tip_user'),
    path('admin/dashboard/', admin_views.admin_dashboard, name='admin_dashboard'),
    path('admin/roles/', admin_views.manage_roles, name='manage_roles'),
    path('admin/roles/<str:username>/', admin_views.set_role, name='set_role'),
    path('admin/audit/', admin_views.audit_log, name='audit_log'),
]
