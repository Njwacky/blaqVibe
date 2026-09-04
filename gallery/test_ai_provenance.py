from django.contrib.auth.models import User
from django.test import TestCase

from .forms import AppUploadForm
from .models import Category


class AIProvenanceFormTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(
            name='Full App',
            slug='full-app',
            type='full_app',
        )

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

    def test_ai_assisted_requires_tool_and_creation_note(self):
        form = AppUploadForm(data=self.valid_data(ai_generated=True))

        self.assertFalse(form.is_valid())
        self.assertIn('ai_tool', form.errors)
        self.assertIn('ai_prompt', form.errors)

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
