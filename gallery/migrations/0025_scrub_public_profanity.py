"""Hide or blank already-stored public text that would fail the new gate.

Schema is unchanged. We walk existing user-generated rows because a
comment posted last week is still public tomorrow — a write-time gate
alone would leave the old words on the page.
"""

from django.db import migrations


def _scrub(apps, schema_editor):
    from gallery.profanity import contains_profanity

    Comment = apps.get_model('gallery', 'Comment')
    for row in Comment.objects.all().iterator():
        if contains_profanity(row.body):
            row.is_hidden = True
            row.body_html = (
                '<p>This comment was hidden because it used language '
                'that is not allowed here.</p>'
            )
            row.save(update_fields=['is_hidden', 'body_html'])

    Review = apps.get_model('gallery', 'Review')
    for row in Review.objects.all().iterator():
        if contains_profanity(row.text):
            row.text = ''
            row.save(update_fields=['text'])

    Notification = apps.get_model('gallery', 'Notification')
    for row in Notification.objects.all().iterator():
        changed = []
        if contains_profanity(row.body):
            row.body = ''
            changed.append('body')
        if contains_profanity(row.title):
            row.title = 'New activity on BlaqVibes'
            changed.append('title')
        if changed:
            row.save(update_fields=changed)

    PullRequest = apps.get_model('gallery', 'PullRequest')
    for row in PullRequest.objects.all().iterator():
        changed = []
        if contains_profanity(row.description):
            row.description = ''
            changed.append('description')
        if contains_profanity(row.title):
            row.title = 'Pull request'
            changed.append('title')
        if changed:
            row.save(update_fields=changed)

    Tip = apps.get_model('users', 'Tip')
    for row in Tip.objects.all().iterator():
        if contains_profanity(row.message):
            row.message = ''
            row.save(update_fields=['message'])

    Profile = apps.get_model('users', 'Profile')
    for row in Profile.objects.all().iterator():
        changed = []
        if contains_profanity(row.bio):
            row.bio = ''
            changed.append('bio')
        if contains_profanity(row.location):
            row.location = ''
            changed.append('location')
        if changed:
            row.save(update_fields=changed)


def _noop(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ('gallery', '0024_cloneevent_git_push_kind'),
        ('users', '0016_profile_git_token_hash'),
    ]

    operations = [
        migrations.RunPython(_scrub, _noop),
    ]
