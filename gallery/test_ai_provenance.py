from django.contrib.auth.models import User
from django.test import TestCase

from .forms import AppUploadForm, QuickPublishForm
from .models import AppProject, Category


class AIProvenanceFormTests(TestCase):
    """AI transparency without publishing friction.

    The CLASSIFICATION is mandatory and honest (Human / AI-assisted /
    AI-generated / Remixed — one tap). The DETAILS (tool, prompt, what AI
    got wrong, what the human did) are optional everywhere: the proof
    checklist and the strengthen nudges invite them afterwards.
    """

    def setUp(self):
        self.category = Category.objects.create(
            name='Full App',
            slug='full-app',
            type='full_app',
        )

    def save_project(self, form):
        form.instance.owner = User.objects.create_user('provowner', 'p@test.com', 'pass12345')
        return form.save()

    def valid_data(self, **overrides):
        data = {
            'title': 'A real project',
            'category': self.category.pk,
            'creator_kind': '',
            'short_description': 'A small project built and published for other builders to inspect.',
            'readme': '# A real project\n\n' + ('This project explains how it works and how to run it. ' * 4),
            'tech_stack': 'Python',
            'ai_generated': False,
            'ai_tool': '',
            'ai_prompt': '',
            'html_code': '<main>hello</main>',
            'css_code': '',
            'js_code': '',
            'star_cost': 0,
            'price_zar': 0,
        }
        data.update(overrides)
        return data

    # -- the publish form: one tap, no questionnaire --------------------------

    def test_publish_form_ai_assisted_needs_no_tool_or_prompt(self):
        form = QuickPublishForm(data={
            'title': 'Assisted',
            'short_description': 'Built with AI helping.',
            'build_method': 'ai_assisted',
            'html_code': '<main>hello</main>',
            'zip_file': '',
            'readme': '',
            'tech_stack': '',
            'creator_kind': '',
            'star_cost': '',
            'price_zar': '',
        })
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertEqual(project.build_method_label, 'AI-assisted')
        self.assertEqual(project.build_choice, 'ai_assisted')

    def test_publish_form_ai_generated_needs_no_tool_or_prompt(self):
        form = QuickPublishForm(data={
            'title': 'Generated',
            'short_description': 'AI produced most of it.',
            'build_method': 'ai_generated',
            'html_code': '<main>hello</main>',
            'zip_file': '',
            'readme': '',
            'tech_stack': '',
            'creator_kind': '',
            'star_cost': '',
            'price_zar': '',
        })
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertEqual(project.build_method_label, 'AI-generated')

    def test_publish_form_human_built_needs_no_ai_metadata(self):
        form = QuickPublishForm(data={
            'title': 'Handmade',
            'short_description': 'Written by hand.',
            'build_method': 'human',
            'html_code': '<main>hello</main>',
            'zip_file': '',
            'readme': '',
            'tech_stack': '',
            'creator_kind': '',
            'star_cost': '',
            'price_zar': '',
        })
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = self.save_project(form)
        self.assertEqual(project.build_method_label, 'Human-built')
        self.assertFalse(project.claims_ai)

    # -- the edit form: details stay optional there too ----------------------

    def test_human_built_project_does_not_require_ai_metadata(self):
        form = AppUploadForm(data=self.valid_data())
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_ai_assisted_project_is_valid_with_provenance(self):
        form = AppUploadForm(
            data=self.valid_data(
                ai_generated=True,
                ai_tool='Claude',
                ai_prompt='Used AI to draft the first implementation, then tested and edited the result.',
            )
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_edit_form_ai_claim_no_longer_blocks_on_details(self):
        """Details are invited by the proof checklist, not demanded by the
        form — otherwise the later step would become the new gate."""
        form = AppUploadForm(data=self.valid_data(ai_generated=True))
        self.assertTrue(form.is_valid(), form.errors.as_json())

    def test_edit_form_build_choice_radio_maps_the_same_way(self):
        form = AppUploadForm(data=self.valid_data(
            ai_generated=False, build_choice='ai_assisted'))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = form.save(commit=False)
        self.assertEqual(project.build_choice, 'ai_assisted')
        self.assertEqual(project.build_method_label, 'AI-assisted')

    def test_edit_form_build_choice_human_clears_the_ai_flag(self):
        form = AppUploadForm(data=self.valid_data(
            ai_generated=True, build_choice='human'))
        self.assertTrue(form.is_valid(), form.errors.as_json())
        project = form.save(commit=False)
        self.assertFalse(project.ai_generated)
        self.assertEqual(project.build_method_label, 'Human-built')

    def test_build_choice_honours_a_real_remix_parent(self):
        owner = User.objects.create_user('remixer', 'r@test.com', 'pass12345')
        original = AppProject.objects.create(
            owner=owner, category=self.category, title='Origin',
            short_description='The original build.', readme='# Origin\n\n' + ('Original docs. ' * 8),
        )
        form = AppUploadForm(data=self.valid_data(
            build_choice='remixed',
            remix_changed='Added low-stock alerts and a REST endpoint.'))
        form.instance.forked_from = original
        self.assertTrue(form.is_valid(), form.errors.as_json())
        self.assertEqual(form.instance.build_method_label, 'Remixed')
