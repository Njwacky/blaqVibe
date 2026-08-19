"""Public-language gate — comments and every other public write path.

5 Whys: Why a dedicated test module? The filter is a new contract.
A regression that lets a slur through a comment is worse than a
broken chart. These tests pin the matcher, the false-positive
boundary, and every public POST that can show user text.
"""
from django.test import TestCase, override_settings

from gallery.forms import AppUploadForm, CommentForm, ReviewForm
from gallery.models import Comment, Notification, Review
from gallery.profanity import (
    PUBLIC_LANGUAGE_ERROR,
    contains_profanity,
    validate_public_text,
)
from gallery.tests import make_category, make_project, make_user
from users.forms import ProfileForm, SignUpForm, TipForm


class MatcherTests(TestCase):
    def test_clean_technical_prose_is_allowed(self):
        for text in (
            'Does this work with class-based views?',
            'I put the password in .env.example',
            'Thanks, your assistant notes helped.',
            'The analysis of the cocktail API looks solid.',
            'Hello from Scunthorpe — great README.',
            'Document the title field and the classic setup.',
            'This is a solid Django app. Cloned and ran it.',
        ):
            self.assertFalse(contains_profanity(text), text)

    def test_plain_and_obfuscated_abuse_is_caught(self):
        for text in (
            'this is fucking broken',
            'f.u.c.k this setup',
            'what a load of sh1t',
            'you are an asshole',
            'piece of shit readme',
            'fuuuuck this',
            'username fuckyou should never ship',
        ):
            self.assertTrue(contains_profanity(text), text)

    def test_empty_is_clean(self):
        self.assertFalse(contains_profanity(''))
        self.assertFalse(contains_profanity(None))

    def test_validate_raises_without_echoing_the_word(self):
        with self.assertRaises(Exception) as ctx:
            validate_public_text('this is fucking broken')
        message = str(ctx.exception)
        self.assertIn('reword', message.lower())
        self.assertNotIn('fuck', message.lower())


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class CommentAndReviewGateTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.fan = make_user('fan')
        self.project = make_project(self.owner, self.cat, title='Clean vibe')

    def test_clean_comment_posts_and_renders(self):
        self.client.login(username='fan', password='pass12345')
        response = self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': 'Does this work with Django 5 class-based views?'},
        )
        self.assertEqual(response.status_code, 302)
        comment = Comment.objects.get()
        self.assertFalse(comment.is_hidden)
        self.assertIn('class-based', comment.body)
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertContains(page, 'class-based')
        self.assertNotContains(page, PUBLIC_LANGUAGE_ERROR)

    def test_vulgar_comment_is_not_created_or_shown(self):
        self.client.login(username='fan', password='pass12345')
        response = self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': 'this is fucking broken and you know it'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(Notification.objects.filter(kind='comment').exists())
        self.assertContains(response, 'cannot include')
        self.assertNotContains(response, 'fucking')

    def test_obfuscated_comment_is_also_rejected(self):
        self.client.login(username='fan', password='pass12345')
        self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': 'what a load of s.h.i.t honestly'},
        )
        self.assertFalse(Comment.objects.exists())

    def test_orm_write_hides_instead_of_rendering(self):
        comment = Comment.objects.create(
            project=self.project,
            user=self.fan,
            body='you are an asshole for shipping this',
        )
        comment.refresh_from_db()
        self.assertTrue(comment.is_hidden)
        self.assertNotIn('asshole', comment.body_html)
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertNotContains(page, 'asshole')

    def test_hidden_comments_are_not_counted(self):
        Comment.objects.create(
            project=self.project, user=self.fan, body='Looks good, thanks a lot.',
        )
        Comment.objects.create(
            project=self.project, user=self.fan, body='this is fucking broken now',
        )
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertContains(page, 'Comments • 1')

    def test_vulgar_review_is_rejected(self):
        self.client.login(username='fan', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/star/')
        self.client.post(
            f'/app/{self.project.slug}/review/',
            {'rating': '2', 'text': 'what a load of bullshit honestly'},
        )
        self.assertFalse(Review.objects.exists())

    def test_clean_review_posts(self):
        self.client.login(username='fan', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/star/')
        response = self.client.post(
            f'/app/{self.project.slug}/review/',
            {'rating': '5', 'text': 'Cloned it. Runs clean on Django 5.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Review.objects.get().text, 'Cloned it. Runs clean on Django 5.')

    def test_comment_form_itself_rejects(self):
        form = CommentForm(data={'body': 'this is fucking broken now'})
        self.assertFalse(form.is_valid())
        self.assertIn('body', form.errors)

    def test_review_form_itself_rejects(self):
        form = ReviewForm(data={'rating': 3, 'text': 'this is fucking broken now'})
        self.assertFalse(form.is_valid())


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class OtherPublicWriteGatesTests(TestCase):
    def setUp(self):
        self.cat = make_category()

    def test_publish_form_rejects_vulgar_title_and_readme(self):
        data = {
            'title': 'My fucking tracker',
            'category': self.cat.id,
            'short_description': 'A short description of this vibe.',
            'readme': '# Heading\n\n' + ('Enough characters in this readme for the form. ' * 3),
            'html_code': '<div>hi</div>',
            'star_cost': 0,
            'price_zar': 0,
        }
        form = AppUploadForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('title', form.errors)

        data['title'] = 'My tracker'
        data['readme'] = '# Heading\n\nthis is a fucking mess of a readme. ' + ('padding text here. ' * 8)
        form = AppUploadForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('readme', form.errors)

    def test_signup_rejects_vulgar_username(self):
        form = SignUpForm(data={
            'username': 'fuckyou',
            'email': 'x@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)

    def test_signup_allows_normal_username(self):
        form = SignUpForm(data={
            'username': 'nolo_ai',
            'email': 'nolo@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_profile_form_rejects_vulgar_bio(self):
        form = ProfileForm(data={'bio': 'I build fucking stock tools', 'location': 'Durban, ZA'})
        self.assertFalse(form.is_valid())
        self.assertIn('bio', form.errors)

    def test_profile_form_allows_clean_bio(self):
        form = ProfileForm(data={'bio': 'AI builder • Stock tools • Durban, ZA', 'location': 'Durban, ZA'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_tip_form_rejects_vulgar_note(self):
        form = TipForm(data={'amount': 2, 'message': 'you are an asshole'})
        self.assertFalse(form.is_valid())
        self.assertIn('message', form.errors)

    def test_tip_endpoint_does_not_store_vulgar_note(self):
        sender = make_user('tipster', stars_balance=5)
        make_user('tippee')
        self.client.login(username='tipster', password='pass12345')
        response = self.client.post(
            '/u/tippee/tip/',
            {'amount': '1', 'message': 'you are an asshole'},
        )
        self.assertEqual(response.status_code, 400)
        from users.models import Tip
        self.assertFalse(Tip.objects.exists())
        sender.profile.refresh_from_db()
        self.assertEqual(sender.profile.stars_balance, 5)

    def test_notify_drops_a_vulgar_quote(self):
        owner = make_user('noted')
        from gallery.notify import notify
        note = notify(owner, 'comment', 'this is fucking broken', 'what a load of shit')
        note.refresh_from_db()
        self.assertEqual(note.title, 'New activity on BlaqVibes')
        self.assertEqual(note.body, '')
