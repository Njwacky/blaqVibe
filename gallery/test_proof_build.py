"""Phase 1 Proof Card — BUILD. VERIFY. PROVE. SHARE."""
from django.test import TestCase, override_settings

from gallery.forms import AppUploadForm
from gallery.tests import make_category, make_project, make_user


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class ProofCardTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('proofowner')

    def test_human_built_proof_checks_include_readme_and_publish(self):
        p = make_project(
            self.owner, self.cat,
            problem_statement='Spaza shops lose stock counts.',
            human_did='Spec, models, and tests. AI drafted the list view.',
        )
        labels = {row['label']: row['ok'] for row in p.proof_checks()}
        self.assertTrue(labels['README'])
        self.assertTrue(labels['Published in public record'])
        self.assertGreaterEqual(p.proof_ok_count(), 4)

    def test_remix_without_delta_fails_that_check(self):
        original = make_project(self.owner, self.cat, title='Origin Proof')
        other = make_user('proofremix')
        fork = make_project(other, self.cat, title='Child Proof', forked_from=original)
        labels = {row['label']: row['ok'] for row in fork.proof_checks()}
        self.assertFalse(labels['Remix delta stated'])
        fork.remix_changed = 'Added low-stock SMS alerts.'
        fork.save(update_fields=['remix_changed'])
        labels = {row['label']: row['ok'] for row in fork.proof_checks()}
        self.assertTrue(labels['Remix delta stated'])

    def test_detail_renders_proof_card_and_whatsapp(self):
        p = make_project(
            self.owner, self.cat,
            problem_statement='Track stock without a spreadsheet.',
            human_did='Architecture and auth.',
            ai_got_wrong='AI invented a pip package that does not exist.',
        )
        response = self.client.get(f'/app/{p.slug}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'PROOF CARD')
        self.assertContains(response, 'Track stock without a spreadsheet.')
        self.assertContains(response, 'Architecture and auth.')
        self.assertContains(response, 'AI invented a pip package')
        self.assertContains(response, 'wa.me/')
        self.assertContains(response, 'Copy proof link')

    def test_form_accepts_proof_fields(self):
        form = AppUploadForm(data={
            'title': 'Proof Form Vibe',
            'category': self.cat.id,
            'short_description': 'A short description of this vibe.',
            'readme': '# Heading\n\n' + ('Enough characters in this readme for the form. ' * 3),
            'html_code': '<div>hi</div>',
            'problem_statement': 'Need a booking page.',
            'human_did': 'Wrote the spec and reviewed diffs.',
            'star_cost': 0,
            'price_zar': 0,
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['problem_statement'], 'Need a booking page.')

    def test_publish_form_lists_proof_fields(self):
        self.assertIn('problem_statement', AppUploadForm.base_fields)
        self.assertIn('human_did', AppUploadForm.base_fields)
        self.assertNotIn('trust', AppUploadForm.base_fields)

    def _form_data(self, **extra):
        data = {
            'title': 'Remix Form Vibe',
            'category': self.cat.id,
            'short_description': 'A short description of this vibe.',
            'readme': '# Heading\n\n' + ('Enough characters in this readme for the form. ' * 3),
            'html_code': '<div>hi</div>',
            'star_cost': 0,
            'price_zar': 0,
        }
        data.update(extra)
        return data

    def test_remix_edit_requires_delta(self):
        original = make_project(self.owner, self.cat, title='Need Delta Parent')
        other = make_user('needdelta')
        fork = make_project(other, self.cat, title='Need Delta Child', forked_from=original, html_code='<div>hi</div>')
        form = AppUploadForm(data=self._form_data(), instance=fork)
        self.assertFalse(form.is_valid())
        self.assertIn('remix_changed', form.errors)
        form = AppUploadForm(data=self._form_data(remix_changed='Added SMS stock alerts.'), instance=fork)
        self.assertTrue(form.is_valid(), form.errors)

    def test_review_witness_flags_persist(self):
        from gallery.models import Review, Star
        p = make_project(self.owner, self.cat)
        fan = make_user('witnessfan')
        Star.objects.create(user=fan, project=p)
        self.client.force_login(fan)
        response = self.client.post(f'/app/{p.slug}/review/', {
            'rating': '4',
            'text': 'README got me running.',
            'ran_it': 'on',
            'readme_clear': 'on',
        })
        self.assertEqual(response.status_code, 302)
        review = Review.objects.get(user=fan, project=p)
        self.assertTrue(review.ran_it)
        self.assertTrue(review.readme_clear)
        page = self.client.get(f'/app/{p.slug}/')
        self.assertContains(page, 'Ran it')
        self.assertContains(page, 'README clear')
