"""No profile picture → first letter of the username, everywhere.

The shared tile (templates/users/_avatar.html) renders the uploaded photo
when there is one, otherwise the FIRST LETTER of the username (uppercase).
These tests pin that behaviour on the profile header, the nav, the battle
leaderboard's creators column, the followers tab and the edit-profile
preview — so no page can silently fall back to a brand icon or a blank.
"""
import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image


def make_avatar_file(name='pic.png'):
    buf = io.BytesIO()
    Image.new('RGB', (8, 8), color='red').save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests-avatar', SEED_DEMO=False)
class AvatarFallbackTests(TestCase):
    def setUp(self):
        self.kwame = User.objects.create_user('kwame', password='pass12345', email='k@test.com')

    def assertLetterTile(self, response, letter):
        # The shared tile renders the letter as the sole text of its span.
        self.assertIn(f'>{letter}</span>'.encode(), response.content)

    def test_initial_property_is_first_letter_upper(self):
        self.assertEqual(self.kwame.profile.initial, 'K')

    def test_initial_property_blank_username_is_question_mark(self):
        self.kwame.username = '   '
        self.assertEqual(self.kwame.profile.initial, '?')

    def test_profile_header_shows_first_letter_when_no_avatar(self):
        res = self.client.get(f'/u/{self.kwame.username}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'bv-avatar')
        self.assertLetterTile(res, 'K')

    def test_profile_header_shows_image_when_avatar_uploaded(self):
        self.kwame.profile.avatar.save('pic.png', make_avatar_file())
        res = self.client.get(f'/u/{self.kwame.username}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'bv-avatar-img')
        self.assertContains(res, self.kwame.profile.avatar.url)

    def test_nav_shows_first_letter_when_no_avatar(self):
        self.client.login(username='kwame', password='pass12345')
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'nav-avatar')
        self.assertLetterTile(res, 'K')

    def test_leaderboard_creators_use_letter_not_brand_icon(self):
        res = self.client.get('/battle/leaderboard/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'bv-avatar')
        self.assertLetterTile(res, 'K')

    def test_followers_tab_shows_letter_tiles(self):
        from users.models import Follow
        zola = User.objects.create_user('zola', password='pass12345', email='z@test.com')
        Follow.objects.create(follower=zola, following=self.kwame)
        res = self.client.get(f'/u/{self.kwame.username}/?tab=followers')
        self.assertEqual(res.status_code, 200)
        self.assertLetterTile(res, 'Z')

    def test_edit_profile_previews_letter_when_no_avatar(self):
        self.client.login(username='kwame', password='pass12345')
        res = self.client.get('/settings/profile/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'bv-avatar')
        self.assertLetterTile(res, 'K')
