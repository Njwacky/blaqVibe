"""The 3-step approval stepper on My Vibes cards.

Spec (change of plan, 2026-09-29): three BORDERED rings — not filled dots,
no glow, no blinking. State is carried by the ring colour alone:

    pending     ✔ white · orange ring (empty, still) · gray ring
    quarantined ✔ white · red ring with ✕ · gray ring
    published   ✔ white · ✔ orange · ✔ green

The REJECT reason never appears on the card — the owner finds that message
in their inbox (the scan/approval flows already send it).
"""
from pathlib import Path

from django.test import TestCase, override_settings

from gallery.models import Notification
from gallery.tests import make_category, make_project, make_user


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class ApprovalStepsTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('stepsowner')

    def _page(self, **project_kwargs):
        make_project(self.owner, self.cat, **project_kwargs)
        self.client.login(username='stepsowner', password='pass12345')
        return self.client.get('/my-vibes/')

    def test_pending_vibe_shows_uploaded_done_review_active_approved_gray(self):
        response = self._page(title='Waiting vibe', status='pending')
        html = response.content.decode()
        self.assertContains(response, 'approval-step--done', status_code=200)      # step 1 ✔
        self.assertContains(response, 'approval-step--active')                     # step 2 orange
        self.assertContains(response, 'approval-dot--warn')
        self.assertContains(response, 'In review')
        self.assertNotIn('approval-step--blocked', html)                           # not rejected
        self.assertNotIn('approval-step--approved', html)                          # not approved yet
        self.assertNotIn('Rejected', html)
        # Step 2 while waiting is an EMPTY ring — no check inside it.
        self.assertContains(response, 'Waiting for approval')

    def test_quarantined_vibe_shows_red_rejected_step(self):
        response = self._page(title='Bad vibe', status='quarantined')
        html = response.content.decode()
        self.assertContains(response, 'approval-step--blocked', status_code=200)   # red ring
        self.assertContains(response, 'Rejected')
        self.assertNotIn('approval-step--active', html)                            # not "in review"
        self.assertNotIn('approval-step--approved', html)
        # The reason is in the inbox, never on the card (see test below).

    def test_published_vibe_shows_green_approved_step(self):
        response = self._page(title='Live vibe', status='published')
        html = response.content.decode()
        self.assertContains(response, 'approval-step--approved', status_code=200)  # green ring
        self.assertContains(response, 'Approved')
        self.assertNotIn('approval-step--active', html)
        self.assertNotIn('approval-step--blocked', html)
        self.assertNotIn('approval-step--todo', html)

    def test_removed_vibe_has_no_stepper(self):
        response = self._page(title='Archived vibe', status='removed')
        # Match the element, not the CSS link (?v=approval-steps-…) in <head>.
        self.assertNotContains(response, 'class="approval-steps"', status_code=200)

    def test_rings_are_static_no_glow_no_animation(self):
        """The change of plan: no glowing/blinking orange — rings are still."""
        css = Path('static/gallery/css/my-vibes.css').read_text()
        block = css[css.index('.approval-steps{'):]
        self.assertNotIn('animation', block)
        self.assertNotIn('keyframes', block)
        self.assertNotIn('box-shadow', block)

    def test_reject_reason_is_not_on_the_card_but_is_in_the_inbox(self):
        """Card shows the STATE (red ✕); the REASON arrives as a notification."""
        project = make_project(self.owner, self.cat, title='Flagged vibe',
                               status='quarantined')
        # The exact call the scan task makes when it quarantines a project.
        from gallery.notify import notify
        notify(project.owner, 'quarantined', f'"{project.title}" was quarantined',
               'Virus or blocked secret found. Edit and re-upload a clean ZIP.',
               project.get_absolute_url())
        self.client.login(username='stepsowner', password='pass12345')
        html = self.client.get('/my-vibes/').content.decode()
        # The card says the state in one word, never the reason.
        self.assertIn('Rejected', html)
        self.assertNotIn('Virus or blocked secret', html)
        # The inbox row carries the message the owner needs.
        self.assertTrue(
            Notification.objects.filter(
                user=self.owner, kind='quarantined',
                body__contains='Virus or blocked secret',
            ).exists()
        )

    def test_stepper_appears_once_per_card_for_mixed_statuses(self):
        """A workshop with several vibes: every non-archived card gets one."""
        make_project(self.owner, self.cat, title='A waiting', status='pending')
        make_project(self.owner, self.cat, title='B live', status='published')
        self.client.login(username='stepsowner', password='pass12345')
        html = self.client.get('/my-vibes/').content.decode()
        self.assertEqual(html.count('class="approval-steps"'), 2)
