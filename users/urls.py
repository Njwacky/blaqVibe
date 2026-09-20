from django.urls import path
from . import views, admin_views, quarantine_views, feedback as feedback_views
from . import welcome_views
urlpatterns = [
    # First-time welcome overlay (users/welcome.py). One write endpoint:
    # `/welcome/seen` records that the account has answered the overlay.
    path('welcome/seen', welcome_views.welcome_seen, name='welcome_seen'),
    path('u/<str:username>/proof/', views.proof_cv_view, name='proof_cv'),
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
    # The quarantined person's notice + appeal. Reached from the site-wide
    # banner, the inbox notification, and every blocked write.
    path('quarantine/', quarantine_views.quarantine_notice, name='quarantine_notice'),
    path('sales/', views.sales_dashboard, name='sales_dashboard'),
    path('pro/activate/', views.activate_pro_trial, name='activate_pro_trial'),
    path('u/<str:username>/follow/', views.toggle_follow, name='toggle_follow'),
    path('u/<str:username>/tip/', views.tip_user, name='tip_user'),
    path('admin/dashboard/', admin_views.admin_dashboard, name='admin_dashboard'),
    path('admin/footer-contacts/', admin_views.footer_contacts, name='footer_contacts'),
    # Search-first role admin: /admin/roles/?q=@kwame finds the person,
    # /admin/roles/kwame/ is the one page that changes their role.
    path('admin/roles/', admin_views.manage_roles, name='manage_roles'),
    path('admin/roles/<str:username>/', admin_views.manage_user_role, name='manage_user_role'),
    # Same view under the old name: links and POSTs written against `set_role`
    # keep resolving instead of dying with NoReverseMatch after this change.
    path('admin/roles/<str:username>/set/', admin_views.manage_user_role, name='set_role'),
    path('admin/audit/', admin_views.audit_log, name='audit_log'),
    # Feedback conversations — the temporary construction channel behind the
    # glowing floating button. /feedback/ is the builder's side; /admin/feedback/
    # is the superadmin's inbox (queue + reply).
    path('feedback/', feedback_views.feedback_inbox, name='feedback_inbox'),
    path('feedback/<int:pk>/', feedback_views.feedback_conversation, name='feedback_conversation'),
    path('admin/feedback/', feedback_views.admin_feedback_queue, name='admin_feedback_queue'),
    path('admin/feedback/<int:pk>/', feedback_views.admin_feedback_conversation, name='admin_feedback_conversation'),
]
