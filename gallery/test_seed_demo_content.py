from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from gallery.models import AppProject, Challenge, Comment, Notification, Sale, Star, Trade
from gallery.skill_models import Skill
from users.models import Follow, Profile


class DemoContentCommandTests(TestCase):
    def run_seed(self):
        output = StringIO()
        call_command("seed_demo_content", stdout=output)
        return output.getvalue()

    def test_creates_showcase_content(self):
        self.run_seed()
        self.assertEqual(User.objects.filter(username__startswith="demo_").count(), 5)
        self.assertEqual(AppProject.objects.filter(slug__startswith="demo-").count(), 14)
        self.assertEqual(Skill.objects.filter(slug__startswith="demo-").count(), 7)
        self.assertEqual(Challenge.objects.filter(tag__in=[
            "durban-in-60-seconds", "african-future", "my-creative-identity", "local-brand-challenge",
        ]).count(), 4)
        self.assertTrue(Profile.objects.get(user__username="demo_studio").bio.startswith("Demo profile"))

    def test_is_idempotent(self):
        self.run_seed()
        counts = (User.objects.count(), AppProject.objects.count(), Skill.objects.count(), Challenge.objects.count())
        self.run_seed()
        self.assertEqual(counts, (User.objects.count(), AppProject.objects.count(), Skill.objects.count(), Challenge.objects.count()))

    def test_does_not_modify_real_users_or_create_engagement(self):
        real = User.objects.create_user(username="real_creator", password="not-a-fixture")
        Profile.objects.get_or_create(user=real, defaults={"bio": "A real creator"})
        before_password = real.password
        self.run_seed()
        real.refresh_from_db()
        self.assertEqual(real.password, before_password)
        self.assertEqual(Star.objects.count(), 0)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertEqual(Follow.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(Trade.objects.count(), 0)
        self.assertEqual(Sale.objects.count(), 0)

    def test_demo_accounts_have_no_login_password(self):
        self.run_seed()
        for user in User.objects.filter(username__startswith="demo_"):
            self.assertFalse(user.has_usable_password())
