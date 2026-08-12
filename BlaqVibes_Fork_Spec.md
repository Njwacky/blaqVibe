# Fork & Remix — Spec (5 Whys, No Shortcuts)

**Feature:** Any vibe can be **forked** → creates your copy with `forked_from` link, like GitHub. Edit, publish as your vibe, stars flow back via `forks` count.

## 5 Whys

1. Why fork not just download? Download is dead ZIP. Fork keeps attribution (`forked from @nolo.ai/stock-app-vibes`) + version history + stars count for original.
2. Why not copy stars? Fork is new vibe, starts at 0 ★ — earns its own stars. Original gets `forks` count + appears in fork network.
3. Why re-queue scan? Forked ZIP may be edited → must re-scan for virus/secrets, even though parent was clean.
4. Why backend only? Fork creates new DB row + copies file_tree, language_stats, not JS.
5. Why at scale? 1 vibe → 20 forks → network effect, trending boosted by `forks` count.

## Model

- `AppProject.forked_from = ForeignKey('self', null, blank, related_name='forks')`
- `AppProject.fork_count` cached? Actually `forks.count()` is enough, but cache for trending.

## Flow

1. User on `/app/stock-app-vibes/` clicks **Fork → Create your vibe** (login required, rate limit 5/h)
2. Backend: `new = AppProject.objects.create(owner=request.user, title=f"{original.title} (forked)", forked_from=original, ...copy fields..., status='pending')` → copy `file_tree, language_stats, star_cost=0` (forked is free initially), `forks` reverse.
3. Copy `AppFile` rows, `ScanJob(queued)`, `process_upload_pipeline.delay(new.id)` → re-scan.
4. Redirect to `/app/<new-slug>/edit/` → user edits, publishes → appears in feed with `forked from @nolo.ai` badge + Fork network link.
5. Detail shows `Forks: 3` + list of forks, and trending sort includes `forks` bonus.

## Security

- Fork requires login, ratelimit 5/h, cannot fork own vibe.
- Forked ZIP is new file copy, not symlink — prevents original delete from breaking fork.
- No JS secrets — fork is POST with CSRF, backend only.

## UI

- Detail: **Fork** button next to Star, shows `Forks 3` + `Forked from @nolo.ai/stock-app-vibes` link.
- Feed card: small `forked from @nolo` badge if forked.
- Profile: `Vibes` tab includes forks, `Forks` tab shows forks of your vibes.

