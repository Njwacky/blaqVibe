"""The money boundary — BUYER → PAYSTACK → BLAQVIBES → UNLOCK.

BlaqVibes has no creator cash-out program and never promises creators
money. This test pins that boundary in code: the payments module exposes a
buyer checkout path and NOTHING that could move money back to a creator.
"""
from django.test import SimpleTestCase

import gallery.payments as payments


class PaymentBoundaryTests(SimpleTestCase):
    def test_module_has_buyer_checkout_only(self):
        self.assertTrue(callable(getattr(payments, 'create_checkout', None)))
        self.assertTrue(callable(getattr(payments, 'fulfill_signed_webhook', None)))

    def test_no_creator_transfer_path_exists(self):
        # The old transfer helper was deleted, not disabled: any symbol that
        # could initiate money moving TO a creator must not exist at all.
        self.assertFalse(hasattr(payments, 'initiate_payout_transfer'))
        for name in dir(payments):
            self.assertNotIn('transfer', name.lower())
            self.assertNotIn('cashout', name.lower())
