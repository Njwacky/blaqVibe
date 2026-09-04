"""send_tip() error-path regression tests.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Profile, StarEvent, Tip
from .wallet import send_tip

User = get_user_model()
PW = 'Tipper@BlaqVibe2026'

def _verified(username, email, balance=0):
    u = User.objects.create_user(username, email, PW)
    # The post_save receiver on User already created the Profile.
    Profile.objects.filter(user=u).update(email_verified=True, stars_balance=balance)
    u.refresh_from_db()
    return u

class SendTipHappyPathTest(TestCase):
    def test_moves_stars_and_writes_both_ledger_rows(self):
        sender = _verified('sender', 's@example.com', balance=10)
        recipient = _verified('recip', 'r@example.com', balance=1)
        tip = send_tip(sender, recipient, 4, 'loved it')
        self.assertIsNotNone(tip.pk)
        sender.profile.refresh_from_db()
        recipient.profile.refresh_from_db()
        self.assertEqual(sender.profile.stars_balance, 6)
        self.assertEqual(recipient.profile.stars_balance, 5)
        self.assertEqual(StarEvent.objects.filter(user=sender, reason='tip_spend', delta=-4).count(), 1)
        self.assertEqual(StarEvent.objects.filter(user=recipient, reason='tip_earn', delta=4).count(), 1)

    def test_overspend_rejected_before_anything_is_written(self):
        sender = _verified('sender', 's@example.com', balance=2)
        recipient = _verified('recip', 'r@example.com')
        with self.assertRaises(ValueError):
            send_tip(sender, recipient, 5)
        self.assertEqual(Tip.objects.count(), 0)
        self.assertEqual(StarEvent.objects.count(), 0)

class SendTipIntegrityErrorTest(TestCase):
    def test_constraint_violation_returns_friendly_message_and_rolls_back(self):
        sender = _verified('sender', 's@example.com', balance=0)
        recipient = _verified('recip', 'r@example.com')

        # Capture the real locked read before we shadow it.
        real_get = Profile.objects.select_for_update().get

        def stale_get(**kwargs):
            obj = real_get(**kwargs)
            if obj.user_id == sender.pk:
                # In-memory only: the guard sees 10, the DB row still holds 0.
                obj.stars_balance = 10
            return obj

        with mock.patch('users.wallet.Profile.objects.select_for_update') as sfu:
            sfu.return_value.get.side_effect = stale_get
            with self.assertRaises(ValueError) as ctx:
                send_tip(sender, recipient, 5)

        self.assertIn('Could not complete the tip', str(ctx.exception))
        # Rolled back — no tip, no ledger rows, balance untouched.
        self.assertEqual(Tip.objects.count(), 0)
        self.assertEqual(StarEvent.objects.count(), 0)
        sender.profile.refresh_from_db()
        self.assertEqual(sender.profile.stars_balance, 0)
