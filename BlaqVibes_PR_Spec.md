# Pull Request — Spec (5 Whys, No Shortcuts)

**Why PR not just star?** Star is like, PR is contribution — fork → edit → propose merge back to original. Original owner decides.

**Why separate model not just comment?** PR has status open/merged/closed, diff, source/target, author, moderated. Comment is chat.

**Why not auto-merge?** Security: fork may have malware. Owner reviews diff (file_tree, language %, README) before merge. Auto-merge is shortcut.

**Why backend only?** PR creation checks `forked_from == target`, not JS. JS only shows button.

**Why at scale?** 1 popular vibe → 20 forks → 5 PRs → owner triages via `PRs (3 open)` tab.

**Flow:**
1. User forks `stock-app-vibes` → edits fork → clicks **Create Pull Request** on fork (or on original's Forks tab)
2. Form: title, description (what changed), diff preview (file_tree diff, readme diff)
3. `PullRequest(source=fork, target=original, author=user, status=open)` created, `target.owner` notified (messages + email if set)
4. Original owner sees **PRs (1 open)** on original vibe → **Merge** (sets status merged, optionally copies PR description to target's readme? For MVP, just marks merged) or **Close**.
5. Fork network shows PR links.

**Security:** Only fork owner can create PR for that fork, only target owner can merge/close, rate limit 5/h, crush silently.
