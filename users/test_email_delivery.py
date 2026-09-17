"""Regression tests for transactional email delivery failures.

The bug these cover: Brevo rejected the send (400 sender-not-verified, 401 bad
key), the backend logged one ERROR line, and the app went on to tell the new
user "we sent a confirmation link". Nothing arrived, nothing in the UI or the
logs said why, and "Brevo never works" had no answer.

Every test here mocks the Brevo HTTP call — no network, no real key.
"""
import io
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from blaqvibes.email_backends import BrevoSendError, mask_api_key

MOCK_URL = "https://mock.brevo.test/v3/smtp/email"

BREVO_SETTINGS = dict(
    BREVO_API_KEY="xkeysib-test-key",
    BREVO_API_URL=MOCK_URL,
    BREVO_ENABLED=True,
    EMAIL_BACKEND="blaqvibes.email_backends.BrevoEmailBackend",
    RATELIMIT_ENABLE=False,
)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        raise AssertionError("raise_for_status should not be reached")


def brevo_rejection():
    return FakeResponse(
        400,
        {"code": "invalid_parameter", "message": "Invalid sender. Sender email is not verified"},
    )


def brevo_accepted():
    return FakeResponse(201, {"messageId": "<mock@brevo>"})


class BackendErrorTests(TestCase):
    """The backend must make a Brevo rejection identifiable, not silent."""

    @override_settings(**BREVO_SETTINGS)
    def test_raises_brevo_send_error_with_parsed_reason(self):
        from django.core.mail import send_mail

        with patch("blaqvibes.email_backends.requests.post", return_value=brevo_rejection()):
            with self.assertRaises(BrevoSendError) as ctx:
                send_mail("s", "b", "noreply@blaqvibes.co.za", ["a@test.com"], fail_silently=False)

        err = ctx.exception
        self.assertEqual(err.status_code, 400)
        self.assertEqual(err.code, "invalid_parameter")
        self.assertIn("not verified", err.message)

    @override_settings(**BREVO_SETTINGS)
    def test_fail_silently_returns_zero_instead_of_raising(self):
        from django.core.mail import send_mail

        with patch("blaqvibes.email_backends.requests.post", return_value=brevo_rejection()):
            self.assertEqual(
                0, send_mail("s", "b", "noreply@blaqvibes.co.za", ["a@test.com"], fail_silently=True)
            )

    @override_settings(**BREVO_SETTINGS)
    def test_accepts_201(self):
        from django.core.mail import send_mail

        with patch("blaqvibes.email_backends.requests.post", return_value=brevo_accepted()) as post:
            self.assertEqual(1, send_mail("s", "b", "noreply@blaqvibes.co.za", ["a@test.com"]))
        self.assertEqual(MOCK_URL, post.call_args.args[0])
        self.assertEqual("xkeysib-test-key", post.call_args.kwargs["headers"]["api-key"])

    @override_settings(**BREVO_SETTINGS, BREVO_TIMEOUT=0)
    def test_zero_timeout_is_clamped_not_fatal(self):
        """BREVO_TIMEOUT=0 used to hand requests a 0s timeout and fail every send."""
        from django.core.mail import send_mail

        with patch("blaqvibes.email_backends.requests.post", return_value=brevo_accepted()) as post:
            send_mail("s", "b", "noreply@blaqvibes.co.za", ["a@test.com"])
        self.assertGreaterEqual(post.call_args.kwargs["timeout"], 1)

    def test_mask_api_key_hides_the_secret(self):
        masked = mask_api_key("xkeysib-abcdefghijklmnopqrstuvwxyz")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", masked)
        self.assertTrue(masked.startswith("xkeysib-"))
        self.assertEqual("(empty)", mask_api_key(""))


@override_settings(**BREVO_SETTINGS)
class SendVerifyEmailTests(TestCase):
    def test_returns_false_when_brevo_rejects(self):
        from users.views import send_verify_email

        u = User.objects.create_user("probe", password="pass12345", email="probe@test.com")
        with patch("blaqvibes.email_backends.requests.post", return_value=brevo_rejection()):
            self.assertFalse(send_verify_email(self.make_request(), u))

    def test_returns_true_when_brevo_accepts(self):
        from users.views import send_verify_email

        u = User.objects.create_user("probe", password="pass12345", email="probe@test.com")
        with patch("blaqvibes.email_backends.requests.post", return_value=brevo_accepted()):
            self.assertTrue(send_verify_email(self.make_request(), u))

    @staticmethod
    def make_request():
        class Req:
            def build_absolute_uri(self, path="/"):
                return f"https://blaqvibes.co.za{path}"

        return Req()


@override_settings(**BREVO_SETTINGS)
class SignupHonestyTests(TestCase):
    def signup(self):
        return self.client.post("/accounts/signup/", {
            "username": "newbie",
            "email": "newbie@test.com",
            "password1": "correcthorse1",
            "password2": "correcthorse1",
        }, follow=True)

    def test_signup_does_not_claim_a_link_was_sent_when_brevo_rejects(self):
        with patch("blaqvibes.email_backends.requests.post", return_value=brevo_rejection()):
            body = self.signup().content.decode()

        self.assertNotIn("we sent a confirmation link", body)
        self.assertIn("could not send the confirmation email", body)
        # The account still gets created — a broken mail config must not block signup.
        self.assertTrue(User.objects.filter(username="newbie").exists())

    def test_signup_confirms_when_brevo_accepts(self):
        with patch("blaqvibes.email_backends.requests.post", return_value=brevo_accepted()):
            body = self.signup().content.decode()

        self.assertIn("we sent a confirmation link", body)
        self.assertNotIn("could not send the confirmation email", body)


@override_settings(**BREVO_SETTINGS)
class DiagnoseEmailCommandTests(TestCase):
    """`manage.py diagnose_email` must name the broken layer, not just fail."""

    def run_cmd(self, *args, **kwargs):
        from django.core.management import call_command

        out = io.StringIO()
        kwargs.setdefault('stdout', out)
        kwargs.setdefault('stderr', out)
        try:
            call_command('diagnose_email', *args, **kwargs)
            exit_code = 0
        except SystemExit as exc:
            exit_code = exc.code
        return out.getvalue(), exit_code

    def test_happy_path_exits_zero(self):
        with patch("users.management.commands.diagnose_email.requests.get",
                   return_value=FakeResponse(200, {"senders": [
                       {"email": "noreply@blaqvibes.co.za", "active": True}]})):
            text, code = self.run_cmd()
        self.assertEqual(0, code)
        self.assertIn("Every check passed", text)

    def test_bad_key_is_named_as_a_401(self):
        with patch("users.management.commands.diagnose_email.requests.get",
                   return_value=FakeResponse(401, {"code": "unauthorized",
                                                   "message": "authentication failed"})):
            text, code = self.run_cmd()
        self.assertEqual(1, code)
        self.assertIn("401 Unauthorized", text)
        self.assertIn("BREVO_API_KEY is wrong", text)

    def test_unverified_sender_is_named(self):
        with patch("users.management.commands.diagnose_email.requests.get",
                   return_value=FakeResponse(200, {"senders": [
                       {"email": "someone@elsewhere.com", "active": True}]})):
            text, code = self.run_cmd()
        self.assertEqual(1, code)
        self.assertIn("no sender for it", text)
        self.assertIn("blaqvibes.co.za", text)

    def test_live_send_rejection_reports_brevo_reason(self):
        def fake_get(url, **kw):
            if url.endswith("/account"):
                return FakeResponse(200, {"email": "a@b.com"})
            return FakeResponse(200, {"senders": [
                {"email": "noreply@blaqvibes.co.za", "active": True}]})

        with patch("users.management.commands.diagnose_email.requests.get", side_effect=fake_get):
            with patch("blaqvibes.email_backends.requests.post", return_value=brevo_rejection()):
                text, code = self.run_cmd(to="me@test.com")
        self.assertEqual(1, code)
        self.assertIn("not verified", text)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_console_backend_is_called_out_as_never_delivering(self):
        text, code = self.run_cmd()
        self.assertEqual(1, code)
        self.assertIn("never delivers it", text)


@override_settings(**BREVO_SETTINGS)
class EditEmailHonestyTests(TestCase):
    def test_reports_failure_instead_of_promise(self):
        User.objects.create_user("editer", password="pass12345", email="e@test.com")
        self.client.login(username="editer", password="pass12345")

        with patch("blaqvibes.email_backends.requests.post", return_value=brevo_rejection()):
            body = self.client.post(
                "/accounts/verify/email/", {"email": "e@test.com"}, follow=True
            ).content.decode()

        self.assertNotIn("Confirmation sent", body)
        self.assertIn("could not be sent", body)

    def test_confirms_on_success(self):
        User.objects.create_user("editer", password="pass12345", email="e@test.com")
        self.client.login(username="editer", password="pass12345")

        with patch("blaqvibes.email_backends.requests.post", return_value=brevo_accepted()):
            body = self.client.post(
                "/accounts/verify/email/", {"email": "e@test.com"}, follow=True
            ).content.decode()

        self.assertIn("Confirmation sent", body)
