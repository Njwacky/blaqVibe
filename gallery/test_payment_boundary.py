from django.test import SimpleTestCase

from gallery.payments import PaymentError, initiate_payout_transfer


class PaymentBoundaryTests(SimpleTestCase):
    def test_creator_transfer_is_disabled(self):
        with self.assertRaisesRegex(PaymentError, 'Creator cash-outs are disabled'):
            initiate_payout_transfer(object())
