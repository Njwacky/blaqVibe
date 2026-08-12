# BlaqVibes — Admin Role Spec (5 Whys, No Shortcuts)

**Missing:** We had `is_staff` only — no real Admin role, no Moderator, no permissions. Massive app needs RBAC.

## 5 Whys
1. Why role not just is_staff? One staff = can delete DB. Need least privilege.
2. Why 4 roles? Super Admin (all), Admin (moderate + users), Moderator (queue only), User (publish).
3. Why not just Django Group? Group is backend, we need `Profile.role` for fast `user.profile.role` checks in templates.
4. Why backend only? Role stored in DB + session, never in JS (no JS can fake).
5. Why at scale? 10k vibes → need 5 moderators without giving them superuser.

## Roles

| Role | `profile.role` | Can Moderation Queue | Can Delete Any Vibe | Can Ban User | Can View Admin Dashboard | Can Assign Roles |
|------|----------------|----------------------|---------------------|--------------|--------------------------|------------------|
| User | `user` | ❌ (own vibes only) | ❌ (own only) | ❌ | ❌ | ❌ |
| Moderator | `moderator` | ✅ approve/quarantine | ❌ | ❌ | ❌ | ❌ |
| Admin | `admin` | ✅ | ✅ | ✅ (temp) | ✅ | ❌ (can make moderator) |
| Super Admin | `superadmin` | ✅ | ✅ | ✅ (perm) | ✅ | ✅ |

## Implementation (Full Code)

- `users/models.py:Profile.role` CharField choices, default `user`
- `users/decorators.py: @moderator_required, @admin_required, @superadmin_required` — check `request.user.profile.role` + `is_authenticated`, on fail → safe 403 page (fork image, “It’s not you, it’s me”, Home btn), crush silently + Sentry.
- `gallery/moderation.py` now checks `@moderator_required` not just `staff_member_required`.
- `users/views.py:admin_dashboard` + `manage_roles` — list users, change role (superadmin only), stats: total vibes, pending, quarantined, trades, top creators.
- `gallery/admin.py` list_display role, filter.

## Security
- Role change: POST only, CSRF, superadmin only, logs to `AdminLog`.
- No JS: role never in `data-` attributes, only backend `request.user.profile.role`.

## Demo
- `nolo.ai` → Super Admin, `blaq` → Admin, `sbu` → Moderator, new users → User.
