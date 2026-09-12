"""PUBLISH FIRST. BUILD THE PROOF LATER.

Tests for the simplified publish flow: a creator publishes with a name, a
one-line description, their project (ZIP or snippet) and ONE build-method
tap. README is scaffolded, category is automatic, pricing defaults to free,
and detailed AI provenance is invited afterwards — never demanded here.
"""
import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from gallery.forms import QuickPublishForm
from gallery.models import AppProject, Category
from gallery.tests import make_category, make_user


def make_zip_file(files, name='app.zip'):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    return SimpleUploadedFile(name, buf.getvalue(), content_type='application/zip')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class QuickPublishFormTests(TestCase):
    def setUp(self):
        self.cat = make_category()

    def base_data(self, **overrides):
        data = {
            'title': 'Inventory Tracker',
            'short_description': 'A simple inventory system for small businesses.',
            'build_method': 'human',
            'html_code': '<main><h1>It runs</h1></main>',
            'css_code': '',
            'js_code': '',
            'zip_file': '',
            'readme': '',
            'tech_stack': '',
            'creator_kind': '',
            'star_cost': '',
            'price_zar': '',
        }
        data.update(overrides)
        return data

    def save_project(self, form):
        """A bare ModelForm has no owner — publish() supplies one."""
        form.instance.owner = make_user('quickowner')
        return form.save()

    def test_minimal_form_is_valid_without_readme_ai_details_or_category(self):
        """The whole point: four inputs, everything else optional."""
        form = QuickPublishForm(data=self.base_data())
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_publishing_without_a_readme_scaffolds_one(self):
        form = QuickPublishForm(data=self.base_data())
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = form.save(commit=False)
        self.assertIn('# Inventory Tracker', project.readme)
        self.assertIn('starter scaffold', project.readme)

    def test_scaffold_is_detected_so_the_strengthen_step_can_offer_more(self):
        form = QuickPublishForm(data=self.base_data())
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertTrue(project.has_scaffold_readme)
        project.readme = '# My own story\n\nReal docs written by a human.'
        project.save()
        self.assertFalse(project.has_scaffold_readme)

    def test_category_is_chosen_automatically(self):
        form = QuickPublishForm(data=self.base_data())
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertIsNotNone(project.category_id)

    def test_pricing_defaults_to_free(self):
        form = QuickPublishForm(data=self.base_data())
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertEqual(project.star_cost, 0)
        self.assertEqual(project.price_zar, 0)

    def test_build_method_assisted_is_recorded_as_the_label(self):
        form = QuickPublishForm(data=self.base_data(build_method='ai_assisted'))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertEqual(project.build_method, 'ai_assisted')
        self.assertEqual(project.build_method_label, 'AI-assisted')
        self.assertFalse(project.ai_generated)

    def test_build_method_generated_sets_the_flag(self):
        form = QuickPublishForm(data=self.base_data(build_method='ai_generated'))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertEqual(project.build_method, 'ai_generated')
        self.assertTrue(project.ai_generated)

    def test_build_method_human_leaves_nothing_to_explain(self):
        form = QuickPublishForm(data=self.base_data(build_method='human'))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertEqual(project.build_method, 'human_built')

    def test_remixed_without_a_parent_is_refused_with_direction(self):
        form = QuickPublishForm(data=self.base_data(build_method='remixed'))
        self.assertFalse(form.is_valid())
        self.assertIn('build_method', form.errors)
        self.assertIn('Remix', form.errors['build_method'][0])

    def test_zip_or_snippet_is_still_required(self):
        form = QuickPublishForm(data=self.base_data(html_code='', zip_file=''))
        self.assertFalse(form.is_valid())
        self.assertTrue(form.non_field_errors() or 'zip_file' in form.errors or 'html_code' in form.errors)

    def test_zip_upload_is_valid_without_any_prose(self):
        upload = make_zip_file({'app.py': 'print(1)\n'})
        form = QuickPublishForm(data=self.base_data(html_code=''), files={'zip_file': upload})
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertTrue(project.zip_file)

    def test_legacy_ai_checkbox_without_build_method_still_maps(self):
        """Old clients (and old tests) post the plain ai_generated checkbox."""
        form = QuickPublishForm(data=self.base_data(build_method='', ai_generated='on'))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertEqual(project.build_method, 'ai_generated')

    def test_studio_payload_round_trip(self):
        """Studio posts readme/tech_stack/star/price through the same path."""
        form = QuickPublishForm(data=self.base_data(
            readme='# Starter\n\n' + ('Real starter readme content. ' * 4),
            tech_stack='HTML, CSS',
            star_cost='0',
            price_zar='0',
        ))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertTrue(project.readme.startswith('# Starter'))
        self.assertEqual(project.tech_stack, 'HTML, CSS')
        self.assertFalse(project.has_scaffold_readme)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class PublishFlowTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.user = make_user('quickpublisher')
        self.client.force_login(self.user)
        from django.core.cache import cache
        cache.clear()

    def payload(self, **extra):
        data = {
            'title': 'My tiny dashboard',
            'short_description': 'Charts for a small shop.',
            'build_method': 'ai_assisted',
            'html_code': '<p>ok</p>',
            'css_code': '',
            'js_code': '',
            'zip_file': '',
            'readme': '',
            'tech_stack': '',
            'creator_kind': '',
            'star_cost': '',
            'price_zar': '',
        }
        data.update(extra)
        return data

    def test_publish_redirects_to_the_success_state(self):
        response = self.client.post('/publish/', self.payload())
        project = AppProject.objects.get(title='My tiny dashboard')
        self.assertRedirects(
            response, reverse('publish_success', args=[project.slug]),
            target_status_code=200,
        )

    def test_success_page_shows_the_win_and_the_optional_next_steps(self):
        self.client.post('/publish/', self.payload())
        project = AppProject.objects.get(title='My tiny dashboard')
        response = self.client.get(reverse('publish_success', args=[project.slug]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('PROJECT PUBLISHED', body)
        self.assertIn('Make it stronger', body)
        self.assertIn('Maybe later', body)
        self.assertIn('Add AI details', body)          # the AI transparency invite
        self.assertIn('Add a real README', body)       # scaffold detected
        self.assertIn('Name your AI tool', body)

    def test_success_page_links_point_at_real_pages(self):
        self.client.post('/publish/', self.payload())
        project = AppProject.objects.get(title='My tiny dashboard')
        response = self.client.get(reverse('publish_success', args=[project.slug]))
        body = response.content.decode()
        self.assertIn(f'/app/{project.slug}/edit/', body)
        self.assertIn('/skills/new/', body)

    def test_success_page_is_owner_only_and_strangers_bounce_to_the_project(self):
        self.client.post('/publish/', self.payload())
        project = AppProject.objects.get(title='My tiny dashboard')
        stranger = make_user('stranger')
        self.client.force_login(stranger)
        response = self.client.get(reverse('publish_success', args=[project.slug]))
        self.assertRedirects(response, project.get_absolute_url(), target_status_code=200)

    def test_zip_publish_lands_on_the_scan_state(self):
        upload = make_zip_file({'index.py': 'print(1)\n'})
        self.client.post('/publish/', self.payload(html_code='', zip_file=upload, title='Zip vibe'))
        project = AppProject.objects.get(title='Zip vibe')
        response = self.client.get(reverse('publish_success', args=[project.slug]))
        body = response.content.decode()
        self.assertIn('safety scan running', body)
        self.assertNotIn('PROJECT PUBLISHED', body)
        self.assertEqual(project.status, 'pending')

    def test_the_publish_page_is_four_inputs_not_a_questionnaire(self):
        response = self.client.get('/publish/')
        body = response.content.decode()
        self.assertIn('name="title"', body)
        self.assertIn('name="short_description"', body)
        self.assertIn('name="build_method"', body)
        self.assertIn('name="zip_file"', body)
        self.assertIn('How did you build it?', body)
        for absent in ('problem_statement', 'human_did', 'ai_got_wrong',
                       'remix_changed', 'ai_tool', 'ai_prompt', 'tech_stack',
                       'star_cost', 'price_zar', 'name="readme"'):
            self.assertNotIn(f'name="{absent}"' if absent != 'name="readme"' else 'name="readme"', body)

    def test_all_four_build_methods_are_offered(self):
        response = self.client.get('/publish/')
        body = response.content.decode()
        for method in ('Human-built', 'AI-assisted', 'AI-generated', 'Remixed'):
            self.assertIn(method, body)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class BuildMethodModelTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('methodowner')

    def test_old_rows_keep_deriving_the_label(self):
        """Projects published before build_choice existed are untouched."""
        p = AppProject.objects.create(
            owner=self.owner, category=self.cat, title='Legacy',
            short_description='x', readme='# Legacy\n\ncontent', status='published',
        )
        self.assertEqual(p.build_method, 'human_built')
        p.ai_tool = 'Claude'
        self.assertEqual(p.build_method, 'ai_assisted')
        p.ai_generated = True
        self.assertEqual(p.build_method, 'ai_generated')

    def test_creator_claim_beats_derivation_upwards(self):
        p = AppProject.objects.create(
            owner=self.owner, category=self.cat, title='Claimed',
            short_description='x', readme='# C\n\ncontent', status='published',
            build_choice='ai_assisted',
        )
        self.assertEqual(p.build_method, 'ai_assisted')

    def test_a_named_tool_still_surfaces_ai_even_if_the_pick_was_human(self):
        p = AppProject.objects.create(
            owner=self.owner, category=self.cat, title='Evidence wins',
            short_description='x', readme='# E\n\ncontent', status='published',
            build_choice='human', ai_tool='Gemini',
        )
        self.assertEqual(p.build_method, 'ai_assisted')

    def test_lineage_fact_beats_every_claim(self):
        original = AppProject.objects.create(
            owner=self.owner, category=self.cat, title='Origin',
            short_description='x', readme='# O\n\ncontent', status='published',
        )
        child = AppProject.objects.create(
            owner=self.owner, category=self.cat, title='Child',
            short_description='x', readme='# C\n\ncontent', status='published',
            forked_from=original, build_choice='ai_generated',
        )
        self.assertEqual(child.build_method, 'remixed')

    def test_claims_ai_covers_all_three_records(self):
        p = AppProject.objects.create(
            owner=self.owner, category=self.cat, title='Claims',
            short_description='x', readme='# C\n\ncontent', status='published',
        )
        self.assertFalse(p.claims_ai)
        p.build_choice = 'ai_assisted'
        self.assertTrue(p.claims_ai)
        p.build_choice = ''
        p.ai_generated = True
        self.assertTrue(p.claims_ai)
        p.ai_generated = False
        p.ai_tool = 'Claude'
        self.assertTrue(p.claims_ai)

    def test_proof_checks_name_the_ai_gap_for_a_choice_based_claim(self):
        p = AppProject.objects.create(
            owner=self.owner, category=self.cat, title='Gap',
            short_description='x', readme='# G\n\ncontent', status='published',
            build_choice='ai_assisted',
        )
        labels = {row['label']: row['ok'] for row in p.proof_checks()}
        self.assertFalse(labels['AI tool named'])
        self.assertFalse(labels['AI workflow note'])


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class EditRoundTripTests(TestCase):
    """The edit page is where proof is added later — it must never silently
    wipe AI records, snippet code, or the build-method claim."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('editer')
        self.client.force_login(self.owner)
        self.project = AppProject.objects.create(
            owner=self.owner, category=self.cat, title='Editable',
            short_description='x', readme='# E\n\n' + ('Existing readme. ' * 6),
            status='published', html_code='<p>keep me</p>',
            ai_generated=True, build_choice='ai_generated', ai_tool='Claude',
        )

    def test_edit_without_touching_ai_fields_preserves_the_records(self):
        response = self.client.post(f'/app/{self.project.slug}/edit/', {
            'title': 'Editable',
            'category': self.cat.id,
            'short_description': 'x',
            'readme': self.project.readme,
            'tech_stack': '',
            'creator_kind': '',
            'build_choice': 'ai_generated',
            'ai_tool': 'Claude',
            'ai_prompt': 'Prompted a first draft.',
            'problem_statement': '',
            'human_did': '',
            'ai_got_wrong': '',
            'remix_changed': '',
            'remix_why': '',
            'html_code': '<p>keep me</p>',
            'css_code': '',
            'js_code': '',
            'star_cost': '0',
            'price_zar': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertTrue(self.project.ai_generated)
        self.assertEqual(self.project.build_choice, 'ai_generated')
        self.assertEqual(self.project.ai_tool, 'Claude')
        self.assertEqual(self.project.html_code, '<p>keep me</p>')

    def test_edit_with_blank_build_choice_does_not_unmark_an_ai_project(self):
        response = self.client.post(f'/app/{self.project.slug}/edit/', {
            'title': 'Editable',
            'category': self.cat.id,
            'short_description': 'x',
            'readme': self.project.readme,
            'tech_stack': '',
            'creator_kind': '',
            'build_choice': '',
            'ai_tool': 'Claude',
            'ai_prompt': '',
            'problem_statement': '',
            'human_did': '',
            'ai_got_wrong': '',
            'remix_changed': '',
            'remix_why': '',
            'html_code': '<p>keep me</p>',
            'css_code': '',
            'js_code': '',
            'star_cost': '0',
            'price_zar': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertTrue(self.project.ai_generated)
        self.assertEqual(self.project.build_method, 'ai_generated')

    def test_edit_page_renders_every_proof_field(self):
        response = self.client.get(f'/app/{self.project.slug}/edit/')
        body = response.content.decode()
        for name in ('name="ai_tool"', 'name="ai_prompt"', 'name="problem_statement"',
                     'name="human_did"', 'name="build_choice"', 'name="html_code"',
                     'name="star_cost"', 'name="price_zar"', 'name="thumbnail"'):
            self.assertIn(name, body)
