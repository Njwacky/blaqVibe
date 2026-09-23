"""First-time welcome overlay helpers.

The welcome screen answers three questions — What is BlaqVibes, What do I
do here, Why should I care — in 3+1 screens, shown full-screen the first
time someone signs in and never again.

Gate semantics (single source of truth = Profile.overlay_seen):

    True  -> done. Never rendered or shown again, on any device.
    False -> the account has not answered yet, so render the overlay.
             The browser-side flag (localStorage `blaq-welcome-seen`) is the
             JS's last line of defence against a *lost* POST: if the server
             flag is still False but this browser already closed the overlay,
             it stays closed here and the JS reconciles the server flag with
             an idempotent POST.

Why keep a server flag at all if the browser also tracks it? The server
answer is the one that survives a different browser/device, and it keeps
"what the URL/login knows" vs "what this visitor has seen" in one place the
tests can read. The client flag only patches the race window around the
driver POST.
"""

# localStorage key the inline boot script reads. It lives here too so the
# Python side and the JS side never drift apart silently (grep for it, not
# for two different spellings).
CSP_MARKER = "blaq-welcome-seen"


def should_show_overlay(user):
    """True while the account has not answered the welcome (any device).

    Never blocks the page if the ask is broken: attribute access degrades to
    False via the guard, so a missing profile can't 500 the render.
    """
    if user is None or not user.is_authenticated:
        return False
    try:
        return not user.profile.overlay_seen
    except Exception:
        return False


def mark_seen(user):
    """Record the first-time welcome as completed or skipped.

    Idempotent by design: the client POSTs once per tab that answers, and
    the endpoint may be called many times. The flag only ever moves False ->
    True; nothing in this module un-sets it, because re-asking a person who
    already answered breaks the "never again" promise the overlay is built on.
    """
    from users.models import Profile

    if user is None or not user.is_authenticated:
        return
    profile = user.profile
    if profile.overlay_seen:
        return
    Profile.objects.filter(pk=profile.pk).update(overlay_seen=True)
    # `update()` bypasses the ORM, so the in-memory instance this request
    # already loaded still says False. The request keeps rendering after
    # this call (the /publish/?welcome=1 handoff), and base.html's overlay
    # gate reads `user.profile.overlay_seen` — without this line the page
    # would re-show the overlay the handoff just answered.
    profile.overlay_seen = True
