from django.contrib.auth.models import AnonymousUser, User
from django.core.cache import cache
from django.template import Context, Template
from django.test import RequestFactory, TestCase

from gallery.models import AppProject, Category, Notification
from users.models import Follow


class TodayLoopTemplateTagTests(TestCase):
    """The return loop (§12, §19): 'What are people building?' — projects
    and people first, XP nowhere in sight (§20 keeps gamification in its
    place). The loop is per-user, cached, and must never leak across users.
    """

    def setUp(self):
        cache.clear()
        self.cat = Category.objects.create(name='Apps', slug='apps', type='full_app')

    def render(self, user):
        request = RequestFactory().get('/')
        request.user = user
        return Template(
            '{% load today_tags %}{% today_loop %}'
        ).render(Context({'request': request}))

    def _make_user(self, username):
        return User.objects.create_user(username=username, password='x')

    def _publish(self, owner, title):
        return AppProject.objects.create(
            owner=owner, category=self.cat, title=title,
            short_description='A short description used by today-loop tests.',
            readme='# Today\n\n' + ('Enough readme characters for the test. ' * 4),
            status='published',
        )

    def test_anonymous_users_get_no_personal_loop(self):
        output = self.render(AnonymousUser())
        self.assertEqual(output.strip(), '')

    def test_authenticated_user_sees_the_builder_pulse(self):
        user = self._make_user('today-user')
        Notification.objects.create(
            user=user,
            kind='star',
            title='Someone starred your vibe',
            body='Nice work',
            url='/app/example/',
        )
        output = self.render(user)
        self.assertIn('TODAY ON BLAQVIBES', output)
        self.assertIn('What are people building?', output)
        self.assertIn('1 new', output)
        self.assertIn('Build → Show → Remix → Compete', output)
        # §20: the loop leads with projects, not gamification stats.
        self.assertNotIn('XP', output)

    def test_loop_is_scoped_to_the_current_user(self):
        user = self._make_user('current')
        other = self._make_user('other')
        Notification.objects.create(user=other, kind='star', title='Other user secret')
        output = self.render(user)
        self.assertNotIn('Other user secret', output)
        self.assertNotIn('1 new', output)  # someone else's inbox is not yours

    def test_cached_loop_does_not_leak_between_users(self):
        first = self._make_user('first')
        second = self._make_user('second')
        Notification.objects.create(user=first, kind='star', title='First only')
        first_output = self.render(first)
        second_output = self.render(second)
        self.assertIn('1 new', first_output)
        self.assertNotIn('1 new', second_output)

    def test_recently_built_shows_the_network_not_yourself(self):
        user = self._make_user('builder-a')
        stranger = self._make_user('builder-b')
        self._publish(stranger, 'Fresh Network Vibe')
        self._publish(user, 'My Own Vibe')
        output = self.render(user)
        self.assertIn('WHAT ARE PEOPLE BUILDING', output)
        self.assertIn('Fresh Network Vibe', output)
        # Discovery is about OTHER builders — your own project has its own slot.
        self.assertNotIn('My Own Vibe', output.split('WHAT ARE PEOPLE BUILDING')[1].split('YOUR LATEST PROJECT')[0])
        self.assertIn('YOUR LATEST PROJECT', output)
        self.assertIn("Today's remix", output)
        self.assertIn("Today's review", output)

    def test_worth_remixing_surfaces_followed_creators(self):
        user = self._make_user('follower')
        star = self._make_user('star-builder')
        self._publish(star, 'Remix Me Please')
        Follow.objects.create(follower=user, following=star)
        output = self.render(user)
        self.assertIn('WORTH REMIXING', output)
        self.assertIn('Remix Me Please', output)
