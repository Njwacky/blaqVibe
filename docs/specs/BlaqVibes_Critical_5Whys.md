# BlaqVibes — Critical 6 — 5 Whys (No Shortcuts, Full Code)

1. Email Notify — Why not toast only? Tab closes → lost. Why backend send_mail in finalize_publish? Queue finishes async, user offline. Why not JS? Secrets stay backend.

2. Edit/Re-upload Version — Why version not overwrite? History + stars preserved. Why new AppVersion model? Git-like v1.1.0, rollback. Why re-scan on re-upload? New ZIP may hide malware.

3. Delete & Report — Why report not just delete? 10k vibes, staff can't watch all. Why Report model? Backend moderation queue. Why owner delete allowed? GDPR, but published deletes → soft quarantine first.

4. Real Git — Why Dulwich HTTP clone not just string? String is demo, Dulwich serves packfile. Why queue on push? Push is upload → must scan. Why not SSH yet? SSH needs authorized_keys, HTTP first.

5. Search v2 — Why SearchVector not icontains? icontains slow at 1k, no ranking. Why GIN index? 10ms vs 300ms. Why trigram for typo? "dashbord" → dashboard.

6. Pagination — Why 12/page not all? 15 vibes already, 1k = OOM, SEO needs pages. Why select_related? N+1.

Full code below implements all 6.
