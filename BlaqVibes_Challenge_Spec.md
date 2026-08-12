# Challenge of the Week — Spec (5 Whys)

**User wants:** Add Challenge of the Week via Start Again flow.

**Why challenge?** Vibe coders have Vol.1 sitting idle — need a reason to build Vol.2. Weekly brief + bounty gives purpose.

**Why weekly not monthly?** Weekly is 7 days — short enough to stay hot, long enough to build a landing.

**Why bounty in stars?** Stars already = currency + rank. Winner gets 10 ★ + Pro + top 1st.

**Why tag?** Challenge vibes tagged #challenge-week-12, filterable, leaderboard per challenge.

**How:**
- Model Challenge(title, description, bounty_stars, tag, start, end, is_active, winner)
- Admin creates challenge via /admin/ or /challenges/create/ (superadmin)
- Publish wizard: Step 1 has “Challenge: Build a clinic landing in Setswana (10 ★) — Join” checkbox → if checked, auto-adds tag to vibe.
- Challenge detail: /challenges/<id>/ → shows brief, bounty, deadline, submissions (vibes with tag), winner
- Cron: every Monday, old challenge closes, new one auto-created (or manual).

**Start Again:** Wizard Step 3 gets “Start Again — Clear Form” button that resets all inputs and goes to Step 1, so vibe coder can immediately start next challenge vibe.
