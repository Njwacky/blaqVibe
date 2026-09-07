"""Tests for the non-interactive Render admin bootstrap command."""
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


User = get_user_model()


class BootstrapAdminCommandTest(TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            call_command("bootstrap_admin")
        self.assertFalse(User.objects.exists())

    def test_missing_credentials_fail_when_enabled(self):
        with patch.dict(os.environ, {"BOOTSTRAP_ADMIN": "true"}, clear=True):
            with self.assertRaises(CommandError):
                call_command("bootstrap_admin")
        self.assertFalse(User.objects.exists())

    def test_creates_superadmin_from_environment(self):
        env = {
            "BOOTSTRAP_ADMIN": "true",
            "OWNER_USERNAME": "njwayelo",
            "OWNER_EMAIL": "owner@example.com",
            "OWNER_PASSWORD": "A-strong-production-password-2026!",
        }
        with patch.dict(os.environ, env, clear=True):
            call_command("bootstrap_admin")

        user = User.objects.get(username="njwayelo")
        self.assertEqual(user.email, "owner@example.com")
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.profile.role, "superadmin")
        self.assertTrue(user.profile.email_verified)
        self.assertTrue(user.check_password(env["OWNER_PASSWORD"]))

    def test_existing_account_is_promoted_without_resetting_password(self):
        user = User.objects.create_user(
            "njwayelo", "owner@example.com", "Existing-password-2026!"
        )
        env = {
            "BOOTSTRAP_ADMIN": "true",
            "OWNER_USERNAME": "njwayelo",
            "OWNER_EMAIL": "owner@example.com",
            "OWNER_PASSWORD": "New-password-must-not-overwrite-2026!",
        }
        with patch.dict(os.environ, env, clear=True):
            call_command("bootstrap_admin")

        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.profile.role, "superadmin")
        self.assertTrue(user.check_password("Existing-password-2026!"))
        self.assertFalse(user.check_password(env["OWNER_PASSWORD"]))
