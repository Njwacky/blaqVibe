"""Force-rename accounts whose usernames fail the public-language gate.

The signup gates (SignUpForm, the allauth adapters) stop NEW dirty
handles. Migration 0025 scrubbed comments, bios, tips and reviews — but
not the accounts themselves: an old @fuckyou handle was still rendered
on every card, every comment, every profile.

Policy here is a FORCED RENAME, not a silent rewrite:
  * the username becomes user_<pk> (neutral, unique, obviously a
    placeholder),
  * the person is TOLD via an in-app 'moderation' notification —
    the old word is never echoed back, but the reason is,
  * dirty first/last names and GitHub/Twitter handles are blanked
    (hidden, not rewritten).

Account rows survive intact: vibes, stars, trades, and receipts all key
off the user id, never the username. Git clone URLs under the old handle
stop working — that is the price of removing the word, and it is why the
rename is announced instead of whispered.
"""

from django.db import migrations

RENAMED_TITLE = 'Your BlaqVibes username was changed'
RENAMED_BODY = (
    'Your previous username broke our public-language rules, so it was '
    'changed. Your account, vibes, stars and trades are untouched. The '
    'new handle is a placeholder — contact support to pick a fresh one.'
)


def _clean_name(apps, value):
    from gallery.profanity import contains_profanity
    return '' if contains_profanity(value or '') else (value or '')


def _scrub_accounts(apps, schema_editor):
    from gallery.profanity import contains_profanity

    User = apps.get_model('auth', 'User')
    Profile = apps.get_model('users', 'Profile')
    Notification = apps.get_model('gallery', 'Notification')

    for user in User.objects.all().iterator():
        changed = []

        if contains_profanity(user.username):
            candidate = f'user_{user.pk}'
            suffix = 0
            while User.objects.filter(username=candidate).exclude(pk=user.pk).exists():
                suffix += 1
                candidate = f'user_{user.pk}_{suffix}'
            user.username = candidate
            changed.append('username')
            renamed_to = candidate
        else:
            renamed_to = None

        first = _clean_name(apps, user.first_name)
        if first != (user.first_name or ''):
            user.first_name = first
            changed.append('first_name')
        last = _clean_name(apps, user.last_name)
        if last != (user.last_name or ''):
            user.last_name = last
            changed.append('last_name')

        if changed:
            user.save(update_fields=changed)

        if renamed_to:
            Notification.objects.create(
                user_id=user.pk,
                kind='moderation',
                title=RENAMED_TITLE,
                body=RENAMED_BODY[:400],
                url=f'/u/{renamed_to}/',
            )

    for profile in Profile.objects.all().iterator():
        changed = []
        github = _clean_name(apps, profile.github)
        if github != (profile.github or ''):
            profile.github = github
            changed.append('github')
        twitter = _clean_name(apps, profile.twitter)
        if twitter != (profile.twitter or ''):
            profile.twitter = twitter
            changed.append('twitter')
        if changed:
            profile.save(update_fields=changed)

    # Migration 0025 scrubbed notification bodies and titles but not the
    # url field — old rows can still carry /u/<dirty-handle>/ links that
    # render in the inbox. Blank any url that trips the gate.
    Notification = apps.get_model('gallery', 'Notification')
    for row in Notification.objects.exclude(url='').iterator():
        if contains_profanity(row.url):
            row.url = ''
            row.save(update_fields=['url'])


def _noop(apps, schema_editor):
    # Irreversible on purpose: we will not put the words back.
    return


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0027_hold_public_vibe_profanity'),
        ('users', '0016_profile_git_token_hash'),
    ]

    operations = [
        migrations.RunPython(_scrub_accounts, _noop),
    ]
