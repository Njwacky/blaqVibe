"""Cash-outs are intentionally disabled.

BlaqVibes does not promise creator cash-outs or return stars as money. The
stars economy is an in-app reputation/trading system only. This compatibility
module remains temporarily so older imports fail closed instead of moving
money.
"""

class PayoutError(Exception):
    def __init__(self, message='Creator cash-outs are disabled.'):
        self.message = message
        super().__init__(message)

def _disabled(*args, **kwargs):
    raise PayoutError()

def payout_rate_label():
    return 'Creator cash-outs are disabled.'

request_payout = _disabled
decide_payout = _disabled
record_transfer_reference = _disabled
