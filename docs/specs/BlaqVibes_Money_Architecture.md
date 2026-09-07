# BlaqVibes Money Architecture (Spec)

Replaces the retired `BlaqVibes_Pro_AI_Money_Spec.md`, which promised a
creator payout program. That promise is removed, on purpose.

## The one rule

Nobody should ever wonder whether BlaqVibes owes creators money.

```
BUYER
  ↓
PAY (Paystack, or stars inside the platform economy)
  ↓
BLAQVIBES
  ↓
UNLOCK PROJECT
```

That is the entire money path.

## What BlaqVibes is NOT

- No creator cash-outs.
- No star → ZAR exchange rate.
- No "earned Rands" balances.
- No payback or reimbursement program.
- No bank transfer path back to creators.

## Stars

Stars remain, but only as:

- reputation,
- the in-platform economy (they unlock downloads),
- competition (ranks, battles, challenges),
- social status.

**Stars are NOT redeemable for ZAR.**

## Card purchases (Paystack)

`gallery/payments.py` implements buyer checkout only:

1. Buyer starts checkout for a published project (`create_checkout`).
2. Paystack verifies the charge (`fulfill_signed_webhook`, HMAC-checked,
   then re-verified against the Paystack API).
3. The sale is recorded (`Sale` row) and the project ZIP unlocks for the
   buyer.

The module contains no function that can move money toward a creator. The
payment-boundary test (`gallery/test_payment_boundary.py`) pins this.

## The Sales page

The old `/payout/` dashboard is now `/sales/` ("Sales" in the nav). It
shows:

- **Sales** — buyers who unlocked the creator's projects with card payments.
- **Star trades / Buyer activity** — star trades on the creator's projects.
- **Purchases** — projects the user unlocked.
- **Tips received** and the full star ledger.

It never shows cash-out, withdrawal, or ZAR-balance-for-creators language.

## Legacy data

The historical `users_payout` table and the `payout_hold` / `payout_refund`
ledger reasons stay in the database temporarily for migration/data safety.
No code path writes to them; the `Payout` model is marked LEGACY and can be
dropped in a later migration once the history is archived.
