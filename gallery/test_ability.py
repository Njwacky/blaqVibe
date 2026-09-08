"""Phase 3 — demonstrated skills from published Builds, not self-tags."""
from django.test import TestCase, override_settings

from gallery.ability import ai_maturity, demonstrated_skills
from gallery.tests import make_category, make_project, make_user


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class DemonstratedAbilityTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('abilityowner')

    def test_unpublished_does_not_count(self):
        make_project(self.owner, self.cat, title='Draft', status='pending', tech_stack='Django')
        live = make_project(self.owner, self.cat, title='Live', tech_stack='Django, PostgreSQL')
        skills = demonstrated_skills(
            list(type(live).objects.filter(owner=self.owner))
        )
        names = {s['name']: s['count'] for s in skills}
        self.assertEqual(names.get('Django'), 1)
        self.assertEqual(names.get('PostgreSQL'), 1)

    def test_remix_and_ai_are_skills_with_evidence(self):
        original = make_project(self.owner, self.cat, title='Parent Ability')
        other = make_user('abilityremix')
        make_project(
            other, self.cat, title='Child Ability', forked_from=original,
            remix_changed='Added SMS alerts',
            ai_tool='Claude', ai_prompt='Build stock alerts',
            ai_got_wrong='Invented a pip package',
            problem_statement='Shops lose stock',
            human_did='Spec and tests',
            tech_stack='Python',
        )
        skills = demonstrated_skills(list(type(original).objects.filter(owner=other, status='published')))
        names = {s['name'] for s in skills}
        self.assertIn('Python', names)
        self.assertIn('Remix / continuation', names)
        self.assertIn('AI orchestration', names)
        mat = ai_maturity(list(type(original).objects.filter(owner=other, status='published')))
        self.assertEqual(mat['stage'], 7)
        self.assertEqual(mat['label'], 'Continuer')

    def test_empty_builder_has_no_stage(self):
        mat = ai_maturity([])
        self.assertEqual(mat['stage'], 0)

    def test_profile_shows_demonstrated_chips(self):
        make_project(
            self.owner, self.cat, title='Stack Proof',
            tech_stack='Django',
            human_did='Wrote the models',
        )
        response = self.client.get(f'/u/{self.owner.username}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Django')
        self.assertContains(response, 'AI-era stage')
        skills_tab = self.client.get(f'/u/{self.owner.username}/?tab=skills')
        self.assertContains(skills_tab, 'Demonstrated from published Builds')
        self.assertContains(skills_tab, 'No single')
