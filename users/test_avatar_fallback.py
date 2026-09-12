"""Profile pictures: first letter when no photo, star-rank frame always.

The shared tile (templates/users/_avatar.html) renders the uploaded photo
when there is one, otherwise the FIRST LETTER of the username (uppercase) —
and the ring around it is the creator's star rank: bronze → silver → gold
→ platinum. Comments and reviews show no avatars at all.
"""
import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from PIL import Image

from gallery.models import Comment
from gallery.tests import make_category, make_project, make_user


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

    def test_frame_tier_follows_star_rank(self):
        self.assertEqual(self.kwame.profile.frame_tier, 'bronze')
        cat = make_category()
        gold = make_user('goldbuilder')
        project = make_project(gold, cat)
        project.stars = 60
        project.save()
        self.assertEqual(gold.profile.frame_tier, 'gold')

    def test_profile_header_shows_first_letter_when_no_avatar(self):
        res = self.client.get(f'/u/{self.kwame.username}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'bv-avatar')
        self.assertContains(res, 'bv-frame-bronze')
        self.assertLetterTile(res, 'K')

    def test_profile_header_frame_matches_rank(self):
        cat = make_category()
        gold = make_user('goldbuilder')
        project = make_project(gold, cat)
        project.stars = 60
        project.save()
        res = self.client.get(f'/u/{gold.username}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'bv-frame-gold')
        self.assertContains(res, 'title="Gold creator"')

    def test_profile_header_shows_image_when_avatar_uploaded(self):
        self.kwame.profile.avatar.save('pic.png', make_avatar_file())
        res = self.client.get(f'/u/{self.kwame.username}/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'bv-avatar-img')
        self.assertContains(res, 'bv-frame-bronze')
        self.assertContains(res, self.kwame.profile.avatar.url)

    def test_explicit_frame_overrides_auto_tier(self):
        html = render_to_string('users/_avatar.html', {'u': self.kwame, 'frame': 'platinum'})
        self.assertIn('bv-frame-platinum', html)
        self.assertIn('>K</span>', html)
        html = render_to_string('users/_avatar.html', {'u': self.kwame, 'frame': 'none'})
        self.assertNotIn('bv-frame-', html)

    def test_nav_shows_first_letter_when_no_avatar(self):
        self.client.login(username='kwame', password='pass12345')
        res = self.client.get('/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'nav-avatar')
        self.assertContains(res, 'bv-frame-bronze')
        self.assertLetterTile(res, 'K')

    def test_leaderboard_creators_use_letter_not_brand_icon(self):
        res = self.client.get('/battle/leaderboard/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'bv-avatar')
        self.assertContains(res, 'bv-frame-bronze')
        self.assertLetterTile(res, 'K')

    def test_followers_tab_shows_letter_tiles(self):
        from users.models import Follow
        zola = User.objects.create_user('zola', password='pass12345', email='z@test.com')
        Follow.objects.create(follower=zola, following=self.kwame)
        res = self.client.get(f'/u/{self.kwame.username}/?tab=followers')
        self.assertEqual(res.status_code, 200)
        self.assertLetterTile(res, 'Z')
        self.assertContains(res, 'bv-frame-bronze')

    def test_edit_profile_previews_letter_when_no_avatar(self):
        self.client.login(username='kwame', password='pass12345')
        res = self.client.get('/settings/profile/')
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'bv-avatar')
        self.assertContains(res, 'bv-frame-bronze')
        self.assertLetterTile(res, 'K')

    def test_comments_and_reviews_show_no_avatars(self):
        cat = make_category()
        owner = make_user('vibeowner')
        project = make_project(owner, cat, slug='framed-vibe')
        Comment.objects.create(project=project, user=self.kwame, body='Looks great, thanks for sharing!')
        res = self.client.get(project.get_absolute_url())
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, 'Looks great, thanks for sharing!')
        # Anonymous page: no nav tile either, so no avatar markup at all.
        self.assertNotContains(res, 'bv-avatar')
