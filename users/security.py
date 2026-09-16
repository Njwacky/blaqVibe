"""Account session revocation and risk signals for interactive sign-ins."""
import hashlib
import hmac
import ipaddress

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import PasswordChangeView, PasswordResetConfirmView
from django.contrib.sessions.models import Session
from django.core.mail import send_mail
from django.urls import reverse_lazy

from .models import SecurityEvent

def _digest(value):
    return hmac.new(settings.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()

def client_ip(request):
    """Use forwarding headers only when the operator names trusted proxies."""
    remote = (request.META.get('REMOTE_ADDR') or '').strip()
    if remote in set(getattr(settings, 'SECURITY_TRUSTED_PROXY_IPS', ())):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return remote

def masked_network(ip):
    try:
        parsed = ipaddress.ip_address(ip)
        if parsed.version == 4:
            return '.'.join(ip.split('.')[:3]) + '.0/24'
        return ':'.join(parsed.exploded.split(':')[:3]) + '::/48'
    except ValueError:
        return 'unknown network'

def revoke_user_sessions(user, *, keep_session_key=None):
    """Delete every persisted session for a user except the current one."""
    revoked = 0
    for session in Session.objects.iterator():
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if str(data.get('_auth_user_id')) == str(user.pk) and session.session_key != keep_session_key:
            session.delete()
            revoked += 1
    return revoked

def revoke_git_token(user):
    """A password recovery is a compromise signal, not only a web logout."""
    profile = getattr(user, 'profile', None)
    if profile is None or not profile.git_token_hash:
        return False
    profile.git_token_hash = ''
    profile.save(update_fields=['git_token_hash'])
    return True

def record_login(request, user):
    ip = client_ip(request)
    agent = (request.META.get('HTTP_USER_AGENT') or 'unknown')[:512]
    ip_hash, device_hash = _digest(ip), _digest(agent)
    detail = masked_network(ip)
    prior = SecurityEvent.objects.filter(user=user, event__in=(
        'login_first_device', 'login_new_device', 'login_recognized_device',
    ))
    recognised = prior.filter(ip_hash=ip_hash, device_hash=device_hash).exists()
    event = 'login_recognized_device' if recognised else ('login_new_device' if prior.exists() else 'login_first_device')
    SecurityEvent.objects.create(user=user, event=event, ip_hash=ip_hash, device_hash=device_hash, detail=detail)
    if event == 'login_new_device' and user.email:
        # Use Brevo-ready EmailMultiAlternatives with HTML for better inbox placement
        try:
            from django.core.mail import EmailMultiAlternatives

            subject = 'New BlaqVibes sign-in detected'
            text_body = (
                f'Hi @{user.username},\n\n'
                f'Your account signed in from a new device or network ({detail}).\n\n'
                f'If this was you, you can ignore this email.\n'
                f'If this was NOT you, reset your password immediately at '
                f'{getattr(settings, "SITE_URL", "https://blaqvibes.co.za")}/accounts/password_reset/\n\n'
                f'Changing your password signs every other device out and revokes git tokens.\n\n'
                f'— BlaqVibes Security'
            )
            html_body = (
                f'<html><body style="font-family:Inter,Helvetica,Arial,sans-serif;background:#0a0a0f;padding:24px;color:#ddd;">'
                f'<div style="max-width:560px;margin:0 auto;background:#11111a;border:1px solid #222;border-radius:12px;padding:24px;">'
                f'<h2 style="margin:0 0 12px 0;color:#fff;">New sign-in detected</h2>'
                f'<p style="color:#aaa;line-height:1.6;">Hi @{user.username},<br><br>'
                f'Your account signed in from a new device or network (<strong style="color:#fff;">{detail}</strong>).</p>'
                f'<p style="color:#aaa;line-height:1.6;">If this was you, ignore this email.<br>'
                f'If not, <a href="{getattr(settings, "SITE_URL", "https://blaqvibes.co.za")}/accounts/password_reset/" '
                f'style="color:#ef4444;font-weight:600;">reset your password now</a> — it signs out other devices and revokes git tokens.</p>'
                f'<p style="color:#666;font-size:12px;margin-top:20px;">BlaqVibes Security • {getattr(settings, "SITE_URL", "")}</p>'
                f'</div></body></html>'
            )
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_body,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@blaqvibes.co.za'),
                to=[user.email],
            )
            msg.attach_alternative(html_body, 'text/html')
            msg.send(fail_silently=True)
        except Exception:
            # Fallback to simple send_mail — Brevo backend will still handle it
            try:
                send_mail(
                    'New BlaqVibes sign-in',
                    'Your account signed in from a new device or network (' + detail + '). If this was not you, reset your password immediately. Changing your password signs every other device out.',
                    settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True,
                )
            except Exception:
                pass

class AccountPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    template_name = 'registration/password_change_form.html'
    success_url = reverse_lazy('password_change_done')

    def form_valid(self, form):
        response = super().form_valid(form)
        count = revoke_user_sessions(self.request.user, keep_session_key=self.request.session.session_key)
        SecurityEvent.objects.create(user=self.request.user, event='password_changed')
        SecurityEvent.objects.create(user=self.request.user, event='sessions_revoked', detail=f'{count} other session(s)')
        if revoke_git_token(self.request.user):
            SecurityEvent.objects.create(user=self.request.user, event='git_tokens_revoked')
        messages.success(self.request, 'Password changed. Other devices and Git credentials have been signed out.')
        return response

class AccountPasswordResetConfirmView(PasswordResetConfirmView):
    def form_valid(self, form):
        user = form.user
        response = super().form_valid(form)
        count = revoke_user_sessions(user)
        SecurityEvent.objects.create(user=user, event='password_changed')
        SecurityEvent.objects.create(user=user, event='sessions_revoked', detail=f'{count} session(s) after reset')
        if revoke_git_token(user):
            SecurityEvent.objects.create(user=user, event='git_tokens_revoked')
        return response
