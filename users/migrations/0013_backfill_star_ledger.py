"""Backfill the star ledger for wallets that predate it.

5 Whys:
1. Why backfill? The invariant is sum(StarEvent.delta) == stars_balance.
   Existing users have a balance but zero ledger rows — every old wallet
   would look corrupt on day one.
2. Why one 'backfill' row per user instead of reconstructing history?
   History is unrecoverable (the old code kept no rows). One honest
   "opening balance" row is truthful; a fabricated history is not.
3. Why also mark old verified users as welcome-granted? The welcome grant
   is now paid on email verification. Without a 'welcome' row, every old
   verified user would be paid 5 ★ again on their next verify-triggering
   event (social login) — a retroactive mint.
4. Why zero-delta welcome markers? Their historical 5 ★ signup grant is
   already inside the opening balance; the marker only blocks a double
   payment.
5. Why is reverse a no-op that deletes only these rows? Reversing must
   not touch rows written by live code after this migration ran.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Profile = apps.get_model('users', 'Profile')
    StarEvent = apps.get_model('users', 'StarEvent')

    events = []
    for profile in Profile.objects.select_related('user').all():
        if profile.stars_balance:
            events.append(StarEvent(
                user_id=profile.user_id,
                delta=profile.stars_balance,
                reason='backfill',
                ref='opening-balance',
            ))
        if profile.email_verified or profile.stars_balance:
            events.append(StarEvent(
                user_id=profile.user_id,
                delta=0,
                reason='welcome',
                ref='pre-ledger-marker',
            ))
    if events:
        StarEvent.objects.bulk_create(events, batch_size=500)


def reverse(apps, schema_editor):
    StarEvent = apps.get_model('users', 'StarEvent')
    StarEvent.objects.filter(
        reason__in=['backfill', 'welcome'],
        ref__in=['opening-balance', 'pre-ledger-marker'],
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0012_star_ledger_and_paid_record_protection'),
    ]
    operations = [
        migrations.RunPython(backfill, reverse),
    ]
