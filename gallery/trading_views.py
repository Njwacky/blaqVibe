from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Trade


@login_required
def trading_history(request):
    bought = Trade.objects.filter(buyer=request.user).select_related('project','seller').order_by('-created_at')
    sold = Trade.objects.filter(seller=request.user).select_related('project','buyer').order_by('-created_at')
    return render(request, 'gallery/trading_history.html', {'bought': bought, 'sold': sold})
