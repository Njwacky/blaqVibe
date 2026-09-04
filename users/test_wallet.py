"""send_tip() error-path regression tests.

5 Whys: why pin the `except IntegrityError` branch in CI?
1. Why test an exception handler at all? It is dead code until the exact
   race it exists for happens — which is precisely when nobody is looking.
2. Why did it need a test? It was broken in a way no test could see:
   wallet.py imported `transaction` but never `IntegrityError`, so the
   handler raised NameError instead of its friendly ValueError. ruff F821
   caught it; a test keeps it caught.
3. Why a real constraint violation and not a patched-in exception? A
   mock that raises IntegrityError would still pass with the import
   missing if the handler were ever rewritten, and it proves nothing about
   the transaction rolling back. Tripping the real stars_balance_gte_0
   CheckConstraint exercises the DB, the rollback and the handler.
4. Why a stale read to get there? The guard rejects amount > balance, so
   the only way to drive the row negative is the guard reading a balance
   that is no longer true — the concurrent-tip race the select_for_update
   locks exist to stop (and which SQLite's no-op FOR UPDATE lets us model).
5. Why assert the ledger is empty afterwards? "Balance the ledger cannot
   explain" is the bug the ledger exists to prevent. A friendly message
   with a half-written ledger would be worse than an exception.
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

        real_get = Profile.objects.select_for_update().get

        def stale_get(**kwargs):
            obj = real_get(**kwargs)
            if obj.user_id == sender.pk:
                obj.stars_balance = 10
            return obj

        with mock.patch('users.wallet.Profile.objects.select_for_update') as sfu:
            sfu.return_value.get.side_effect = stale_get
            with self.assertRaises(ValueError) as ctx:
                send_tip(sender, recipient, 5)

        self.assertIn('Could not complete the tip', str(ctx.exception))
        self.assertEqual(Tip.objects.count(), 0)
        self.assertEqual(StarEvent.objects.count(), 0)
        sender.profile.refresh_from_db()
        self.assertEqual(sender.profile.stars_balance, 0)
