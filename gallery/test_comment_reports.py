"""Comment reports — the report button + CommentReport the spec promised.

5 Whys: Why test the whole loop? A report feature that creates rows but
never reaches a moderator is a lie with extra steps. These tests pin:
visitor can report → moderator sees it in the in-app queue → hide
removes the words from the page → dismiss keeps them. Plus the access
rules around the queue.
"""
from django.test import TestCase, override_settings

from gallery.models import Comment, CommentReport
from gallery.tests import make_category, make_project, make_user


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class CommentReportTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.author = make_user('author')
        self.visitor = make_user('visitor')
        self.mod = make_user('moderator', role='moderator')
        self.project = make_project(self.owner, self.cat, title='Clean vibe')
        self.comment = Comment.objects.create(
            project=self.project, user=self.author,
            body='Honestly this vibe is trash and so are you.',
        )

    def _report_url(self):
        return f'/app/{self.project.slug}/comments/{self.comment.pk}/report/'

    def test_report_button_is_on_the_page(self):
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertContains(page, 'Report comment')

    def test_visitor_can_report_like_they_can_report_a_vibe(self):
        response = self.client.post(
            self._report_url(), {'reason': 'abusive', 'details': 'personal attack'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        report = CommentReport.objects.get()
        self.assertEqual(report.comment_id, self.comment.pk)
        self.assertIsNone(report.reporter)
        self.assertEqual(report.reason, 'abusive')
        # The comment stays up until a moderator decides.
        self.assertFalse(Comment.objects.get(pk=self.comment.pk).is_hidden)

    def test_logged_in_reporter_is_recorded_and_deduplicated(self):
        self.client.login(username='visitor', password='pass12345')
        self.client.post(self._report_url(), {'reason': 'harassment'})
        self.client.post(self._report_url(), {'reason': 'harassment'})
        self.assertEqual(CommentReport.objects.count(), 1)
        self.assertEqual(CommentReport.objects.get().reporter, self.visitor)

    def test_bad_reason_falls_back_to_other(self):
        self.client.post(self._report_url(), {'reason': 'javascript'})
        self.assertEqual(CommentReport.objects.get().reason, 'other')

    def test_moderator_sees_the_report_in_the_app_queue(self):
        self.client.post(self._report_url(), {'reason': 'abusive', 'details': 'look closer'})
        self.client.login(username='moderator', password='pass12345')
        queue = self.client.get('/moderation/queue/')
        self.assertContains(queue, 'Comment Reports')
        # Moderators must see the raw words to judge them.
        self.assertContains(queue, 'trash and so are you')
        self.assertContains(queue, 'look closer')

    def test_moderator_hide_removes_the_words_from_the_page(self):
        report = CommentReport.objects.create(comment=self.comment, reason='abusive')
        self.client.login(username='moderator', password='pass12345')
        response = self.client.post(
            f'/moderation/comments/{report.pk}/', {'action': 'hide'}, follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.comment.refresh_from_db()
        report.refresh_from_db()
        self.assertTrue(self.comment.is_hidden)
        self.assertTrue(report.resolved)
        self.assertNotIn('trash', self.comment.body_html)
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertNotContains(page, 'trash and so are you')

    def test_moderator_dismiss_keeps_the_comment(self):
        report = CommentReport.objects.create(comment=self.comment, reason='spam')
        self.client.login(username='moderator', password='pass12345')
        self.client.post(f'/moderation/comments/{report.pk}/', {'action': 'dismiss'})
        self.comment.refresh_from_db()
        report.refresh_from_db()
        self.assertFalse(self.comment.is_hidden)
        self.assertTrue(report.resolved)
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertContains(page, 'trash and so are you')

    def test_moderator_can_unhide_a_false_positive(self):
        self.comment.is_hidden = True
        self.comment.body_html = '<p>hidden</p>'
        self.comment.save(update_fields=['is_hidden', 'body_html'])
        self.client.login(username='moderator', password='pass12345')
        self.client.post(f'/moderation/comment/{self.comment.pk}/', {'action': 'unhide'})
        self.comment.refresh_from_db()
        self.assertFalse(self.comment.is_hidden)
        self.assertIn('trash', self.comment.body_html)

    def test_queue_is_moderators_only(self):
        report = CommentReport.objects.create(comment=self.comment, reason='spam')
        # Anonymous
        self.assertEqual(self.client.get('/moderation/queue/').status_code, 403)
        # Logged in but not staff
        self.client.login(username='visitor', password='pass12345')
        self.assertEqual(self.client.get('/moderation/queue/').status_code, 403)
        self.assertEqual(
            self.client.post(f'/moderation/comments/{report.pk}/', {'action': 'hide'}).status_code,
            403,
        )
        self.assertFalse(report.comment.is_hidden)

    def test_report_rate_limit_kicks_in(self):
        with override_settings(RATELIMIT_ENABLE=True):
            from django.core.cache import cache
            cache.clear()
            for _ in range(11):
                self.client.post(self._report_url(), {'reason': 'spam'})
            # 10/h cap — the 11th must not create a row.
            self.assertLessEqual(CommentReport.objects.count(), 10)
