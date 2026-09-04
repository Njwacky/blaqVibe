"""Friendly CSRF failure — the default Django page is a dead end.
"""
from django.shortcuts import render

def csrf_failure(request, reason=''):
    return render(
        request,
        '403_csrf.html',
        {'reason': reason, 'login_url': '/accounts/login/'},
        status=403,
    )
