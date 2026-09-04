from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.template import Context, Template
from django.utils import timezone

from gallery.models import Notification
from users.models import XPEvent


class TodayLoopTemplateTagTests(TestCase):
    def render(self, user):
        request = RequestFactory().get('/')
        request.user = user
        return Template(
            '{% load today_tags %}{% today_loop %}'
        ).render(Context({'request': request}))

    def test_anonymous_users_get_no_personal_loop(self):
        user = User()
        user.set_unusable_password()
        output = self.render(user)
        self.assertEqual(output.strip(), '')

    def test_authenticated_user_sees_momentum_and_inbox(self):
        user = User.objects.create_user(username='today-user', password='x')
        XPEvent.objects.create(user=user, amount=20, reason='publish', ref='project:1')
        Notification.objects.create(
            user=user,
            kind='star',
            title='Someone starred your vibe',
            body='Nice work',
            url='/app/example/',
        )
        output = self.render(user)
        self.assertIn('BLAQVIBES TODAY', output)
        self.assertIn('+20 XP', output)
        self.assertIn('1 new', output)
        self.assertIn("Someone starred your vibe", output)

    def test_loop_is_scoped_to_the_current_user(self):
        user = User.objects.create_user(username='current', password='x')
        other = User.objects.create_user(username='other', password='x')
        Notification.objects.create(user=other, kind='star', title='Other user secret')
        output = self.render(user)
        self.assertNotIn('Other user secret', output)
