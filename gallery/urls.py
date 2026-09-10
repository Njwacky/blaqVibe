from django.urls import path
from django.views.generic import RedirectView
from . import views, trading_views, api_views, launch_views, health
from .build_views import build_hub, discover
from .csp_views import csp_report
from .moderation import moderation_queue, moderation_action, reports_queue, report_action
from .skill_views import skill_list, skill_detail, use_skill, create_skill, update_skill
from .share_card import share_card
urlpatterns = [
    # Ops probes: liveness (process up) and readiness (DB reachable).
    # Unauthenticated, no-store JSON — see gallery/health.py.
    path('healthz', health.liveness, name='healthz'),
    path('readyz', health.readiness, name='readyz'),
    path('', views.feed, name='feed'),
    path('trust/', views.trust_legend, name='trust_legend'),
    # Primary navigation (§4): PROJECTS | DISCOVER | SKILLS | CHALLENGES | BUILD.
    # '' is PROJECTS (the feed); the other four live here.
    path('discover/', discover, name='discover'),
    path('build/', build_hub, name='build_hub'),
    # Builder Skills. '/skills/' is canonical — the old '/prompt-skills/'
    # prefix belonged to the prompt-marketplace era (§5/§15) and now
    # permanently redirects, so shared links keep working. `prompt_skills`
    # survives as a second NAME for the canonical route (same trick the
    # login/account_login aliases use) so older reverses keep resolving.
    path('skills/', skill_list, name='skills'),
    path('skills/', skill_list, name='prompt_skills'),
    path('skills/new/', create_skill, name='create_skill'),
    path('skills/<slug:slug>/', skill_detail, name='skill_detail'),
    path('skills/<slug:slug>/use/', use_skill, name='use_skill'),
    path('skills/<slug:slug>/update/', update_skill, name='update_skill'),
    path('prompt-skills/', RedirectView.as_view(pattern_name='skills', permanent=True)),
    path('prompt-skills/new/', RedirectView.as_view(pattern_name='skills', permanent=True)),
    path('prompt-skills/<slug:slug>/', RedirectView.as_view(pattern_name='skill_detail', permanent=True)),
    # A stale form POST would lose its body on a 301, so send it to the
    # skill page (GET) instead of pretending the use was recorded.
    path('prompt-skills/<slug:slug>/use/', RedirectView.as_view(pattern_name='skill_detail', permanent=False)),
    path('sitemap.xml', views.sitemap_xml, name='sitemap'),
    path('api/v1/apps/', api_views.api_apps, name='api_apps'),
    path('api/v1/program-kinds/', api_views.api_program_kinds, name='api_program_kinds'),
    path('api/v1/apps/<slug:slug>/', api_views.api_app_detail, name='api_app_detail'),
    path('inbox/', views.notifications_inbox, name='notifications'),
    path('inbox/read-all/', views.notifications_mark_all_read, name='notifications_mark_all_read'),
    path('inbox/<int:notification_id>/read/', views.notifications_mark_read, name='notifications_mark_read'),
    path('saved/', views.saved_vibes, name='saved_vibes'),
    path('oops/', views.oops_demo, name='oops_demo'),
    path('publish/', views.publish, name='publish'),
    # New-user demo: import a public GitHub repo as your first vibe. GitHub
    # already serves the ZIP; this fetches it, normalizes it and runs the
    # normal publish pipeline — see gallery/repo_import.py.
    path('import/github/', views.import_from_github, name='import_from_github'),
    path('start/', views.starter_gallery, name='starter_gallery'),
    path('studio/', views.studio, name='studio_blank'),
    path('studio/<slug:slug>/', views.studio, name='studio'),
    path('launch/', launch_views.launch_hub, name='launch_hub'),
    path('launch/<slug:slug>/', launch_views.launch_guide, name='launch_guide'),
    path('my-vibes/', views.my_vibes, name='my_vibes'),
    path('trades/', trading_views.trading_history, name='trading_history'),
    path('moderation/queue/', moderation_queue, name='moderation_queue'),
    # Specific report triage routes MUST precede the catch-all
    # 'moderation/<slug:slug>/' below, or Django would bind
    # /moderation/reports/ to the @require_POST moderation_action and answer
    # GET with 405 instead of rendering the queue.
    path('moderation/reports/', reports_queue, name='reports_queue'),
    path('moderation/reports/<int:report_id>/', report_action, name='report_action'),
    path('moderation/<slug:slug>/', moderation_action, name='moderation_action'),
    path('app/<slug:slug>/', views.app_detail, name='app_detail'),
    path('app/<slug:slug>/edit/', views.edit_vibe, name='edit_vibe'),
    path('app/<slug:slug>/stats/', views.vibe_stats, name='vibe_stats'),
    path('app/<slug:slug>/co-owners/add/', views.add_co_owner, name='add_co_owner'),
    path('app/<slug:slug>/co-owners/<int:user_id>/remove/', views.remove_co_owner, name='remove_co_owner'),
    path('app/<slug:slug>/delete/', views.delete_vibe, name='delete_vibe'),
    path('app/<slug:slug>/report/', views.report_vibe, name='report_vibe'),
    path('app/<slug:slug>/scan-status/', views.scan_status, name='scan_status'),
    path('app/<slug:slug>/preview/', views.preview, name='preview'),
    path('app/<slug:slug>/files/', views.preview_files, name='preview_files'),
    path('app/<slug:slug>/snippet/', views.snippet_doc, name='snippet_doc'),
    path('app/<slug:slug>/snippet/style.css', views.snippet_asset, {'kind': 'css'}, name='snippet_css'),
    path('app/<slug:slug>/snippet/script.js', views.snippet_asset, {'kind': 'js'}, name='snippet_js'),
    path('app/<slug:slug>/run-static/', views.run_static, name='run_static'),
    path('app/<slug:slug>/download/', views.download_zip, name='download_zip'),
    path('app/<slug:slug>/versions/<int:version_id>/download/', views.download_version, name='download_version'),
    path('git/<str:username>/<str:slug>.git/<path:rest>', views.git_clone, name='git_clone_catchall'),
    path('git/<str:username>/<str:slug>.git/', views.git_clone, name='git_clone'),
    path('app/<slug:slug>/file/<path:path>', views.file_preview, name='file_preview'),
    path('app/<slug:slug>/comment/', views.post_comment, name='post_comment'),
    path('app/<slug:slug>/review/', views.post_review, name='post_review'),
    path('app/<slug:slug>/star/', views.toggle_star, name='toggle_star'),
    path('app/<slug:slug>/save/', views.toggle_bookmark, name='toggle_bookmark'),
    path('app/<slug:slug>/fork/', views.fork_vibe, name='fork_vibe'),
    path('app/<slug:slug>/forks/', views.fork_network, name='fork_network'),
    path('app/<slug:slug>/share-card.png', share_card, name='share_card'),
    path('app/<slug:slug>/share/anypost/', views.share_to_anypost, name='share_to_anypost'),
    path('app/<slug:slug>/pr/create/', views.create_pr, name='create_pr'),
    path('app/<slug:slug>/prs/', views.pr_list, name='pr_list'),
    path('app/<slug:slug>/prs/<int:pr_id>/', views.pr_action, name='pr_action'),
    path('app/<slug:slug>/prs/<int:pr_id>/view/', views.pr_detail, name='pr_detail'),
    path('problems/', views.problems_board, name='problems_board'),
    path('challenges/', views.challenge_list, name='challenge_list'),
    path('challenges/generate/', views.generate_challenges, name='generate_challenges'),
    path('challenges/<str:tag>/', views.challenge_detail, name='challenge_detail'),
    path('challenges/<str:tag>/approve/', views.approve_challenge, name='approve_challenge'),
    path('challenges/<str:tag>/pick-winner/', views.pick_challenge_winner, name='pick_challenge_winner'),
    path('app/<slug:slug>/run/', views.run_vibe, name='run_vibe'),
    path('battle/', views.battle, name='battle'),
    path('battle/history/', views.battle_history, name='battle_history'),
    path('battle/leaderboard/', views.battle_leaderboard, name='battle_leaderboard'),
    path('battle/<int:battle_id>/vote/', views.vote_battle, name='vote_battle'),
    path('app/<slug:slug>/trade/', views.trade_download, name='trade_download'),
    path('app/<slug:slug>/ai-readme/generate/', views.generate_ai_readme, name='generate_ai_readme'),
    path('app/<slug:slug>/ai-readme/apply/', views.apply_ai_readme, name='apply_ai_readme'),
    path('app/<slug:slug>/buy/', views.buy_vibe, name='buy_vibe'),
    path('paystack/webhook/', views.paystack_webhook, name='paystack_webhook'),
    path('nolo/chat/', views.nolo_chat, name='nolo_chat'),
    path('nolo/chat/send/', views.nolo_chat_api, name='nolo_chat_api'),
    path('nolo/fix/', views.nolo_fix_api, name='nolo_fix_api'),
    path('nolo/readme/', views.nolo_readme_api, name='nolo_readme_api'),
    path('nolo/help/', views.nolo_help, name='nolo_help'),
    path('nolo/compare/', views.nolo_compare, name='nolo_compare'),
    path('app/<slug:slug>/copy/', views.copy_increment, name='copy_increment'),
    path('csp-report/', csp_report, name='csp_report'),
]
