"""Capability discovery — people ranked by demonstrated Builds, not popularity."""
from django.test import TestCase, override_settings

from gallery.capability import (
    capabilities_for_project,
    project_capability_rows,
    search_capability,
)
from gallery.tests import make_category, make_project, make_user


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-capability-tests')
class CapabilitySearchTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        # Developer B — few "followers" (not modelled), many Django builds
        self.builder_b = make_user('dev_b_proof')
        for i in range(3):
            make_project(
                self.builder_b, self.cat,
                title=f'Inventory {i}',
                tech_stack='Python, Django, PostgreSQL',
                problem_statement='Shops lose stock at night',
                human_did='Designed models and API',
                status='published',
            )
        # Developer A — one weak claim project, different stack noise
        self.builder_a = make_user('dev_a_popular')
        make_project(
            self.builder_a, self.cat,
            title='Hello World',
            tech_stack='HTML, CSS',
            status='published',
        )
        # Unpublished Django must not count
        make_project(
            self.builder_a, self.cat,
            title='Secret Django',
            tech_stack='Django',
            status='pending',
        )

    def test_search_returns_projects_and_people_with_match_reason(self):
        result = search_capability('Django')
        self.assertEqual(result['q'], 'Django')
        self.assertGreaterEqual(len(result['projects']), 3)
        usernames = [row['user'].username for row in result['people']]
        self.assertIn('dev_b_proof', usernames)
        self.assertNotIn('dev_a_popular', usernames)  # no published Django
        top = result['people'][0]
        self.assertEqual(top['user'].username, 'dev_b_proof')
        self.assertEqual(top['count'], 3)
        self.assertIn('Matched because', top['match_reason'])
        self.assertIn('3 published', top['match_reason'])
        self.assertIn('Django', top['match_reason'])

    def test_hardest_test_proof_beats_popularity_noise(self):
        """Builder B with 3 Django projects ranks above anyone with fewer."""
        # Give A a single Django build — still loses to B's three.
        make_project(
            self.builder_a, self.cat,
            title='One Django',
            tech_stack='Django',
            status='published',
        )
        result = search_capability('Django')
        people = result['people']
        self.assertEqual(people[0]['user'].username, 'dev_b_proof')
        self.assertEqual(people[0]['count'], 3)
        self.assertEqual(people[1]['user'].username, 'dev_a_popular')
        self.assertEqual(people[1]['count'], 1)

    def test_empty_query_is_honest(self):
        result = search_capability('')
        self.assertEqual(result['projects'], [])
        self.assertEqual(result['people'], [])
        self.assertTrue(result['suggestions'] or result['suggestions'] == [])

    def test_capabilities_on_project_and_detail_page(self):
        p = make_project(
            self.builder_b, self.cat,
            title='API Service',
            tech_stack='Django, REST API',
            human_did='Auth + endpoints',
            status='published',
        )
        caps = capabilities_for_project(p)
        self.assertTrue(any('Django' in c or 'django' in c.lower() for c in caps))
        rows = project_capability_rows(p)
        self.assertTrue(rows)
        response = self.client.get(p.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CAPABILITIES DEMONSTRATED')
        self.assertContains(response, 'capability')

    def test_capability_page_renders_and_talks(self):
        response = self.client.get('/capability/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Find builders by what they actually built')
        self.assertContains(response, 'New here?')
        found = self.client.get('/capability/', {'q': 'Django'})
        self.assertEqual(found.status_code, 200)
        self.assertContains(found, 'dev_b_proof')
        self.assertContains(found, 'Matched because')
        self.assertContains(found, 'PROJECTS THAT USE')

    def test_demo_repo_is_not_blaqvibe(self):
        from gallery.repo_import import DEMO_REPO_URL
        self.assertNotIn('blaqVibe', DEMO_REPO_URL)
        self.assertNotIn('Njwacky', DEMO_REPO_URL)
        self.assertIn('github.com/', DEMO_REPO_URL)
