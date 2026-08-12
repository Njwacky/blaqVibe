from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Trade

# 5 Whys Trading History:
# 1. Why history? Trade costs stars — user must audit who he traded with, like bank statement.
# 2. Why both buyer & seller? Buyer sees what he bought, seller sees who bought his vibes (income).
# 3. Why backend not JS? Trade table has cost, buyer/seller IDs — never expose raw via JS, only via view with login.
# 4. Why not just list in profile? Dedicated /trades/ page with filters (bought/sold) is clearer at 100 trades.
# 5. Why audit log for admin? Who changed role, who quarantined — same pattern, for accountability.

@login_required
def trading_history(request):
    bought = Trade.objects.filter(buyer=request.user).select_related('project','seller').order_by('-created_at')
    sold = Trade.objects.filter(seller=request.user).select_related('project','buyer').order_by('-created_at')
    # Also include star gifts via stars? For now trades only
    return render(request, 'gallery/trading_history.html', {'bought': bought, 'sold': sold})
