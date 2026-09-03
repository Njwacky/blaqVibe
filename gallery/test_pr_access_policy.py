"""Focused tests for the centralized PR visibility policy.

These tests complement the broader regression suite by locking the policy
itself: PR source bytes are sensitive, so a caller must never gain access
merely by knowing a target slug and sequential PR id.
"""
from django.test import TestCase, override_settings

from gallery.access import user_can_review_pr
from gallery.models import PullRequest
from gallery.tests import make_category, make_project, make_user


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class PullRequestAccessPolicyTests(TestCase):
    def setUp(self):
        self.category = make_category()
        self.owner = make_user('targetowner')
        self.fork_owner = make_user('forkowner')
        self.stranger = make_user('stranger')
        self.moderator = make_user('moderator', role='moderator')

        self.target = make_project(
            self.owner, self.category, title='Published target', status='published'
        )
        self.pending_source = make_project(
            self.fork_owner,
            self.category,
            title='Pending source',
            status='pending',
            forked_from=self.target,
        )
        self.pr = PullRequest.objects.create(
            source=self.pending_source,
            target=self.target,
            author=self.fork_owner,
            title='Review this fork',
            status='open',
        )

    def test_anonymous_is_denied_pending_source(self):
        self.assertFalse(user_can_review_pr(self._anonymous(), self.pr))

    def test_unrelated_user_is_denied_pending_source(self):
        self.assertFalse(user_can_review_pr(self.stranger, self.pr))

    def test_fork_owner_can_review_own_pending_source(self):
        self.assertTrue(user_can_review_pr(self.fork_owner, self.pr))

    def test_target_owner_can_review_pending_source(self):
        self.assertTrue(user_can_review_pr(self.owner, self.pr))

    def test_moderator_can_review_pending_source(self):
        self.assertTrue(user_can_review_pr(self.moderator, self.pr))

    def test_pending_target_denies_even_the_source_owner(self):
        self.target.status = 'pending'
        self.target.save(update_fields=['status'])
        self.assertFalse(user_can_review_pr(self.fork_owner, self.pr))
        self.assertFalse(user_can_review_pr(self.owner, self.pr))
        self.assertFalse(user_can_review_pr(self.moderator, self.pr))

    def test_public_source_is_reviewable_for_everyone_when_target_is_public(self):
        self.pending_source.status = 'published'
        self.pending_source.save(update_fields=['status'])
        self.assertTrue(user_can_review_pr(self._anonymous(), self.pr))
        self.assertTrue(user_can_review_pr(self.stranger, self.pr))

    def _anonymous(self):
        from django.contrib.auth.models import AnonymousUser
        return AnonymousUser()
