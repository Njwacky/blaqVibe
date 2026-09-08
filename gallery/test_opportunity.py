"""Phase 4 — opportunity via projects, not a Jobs tab."""
from django.test import TestCase, override_settings

from gallery.opportunity import proof_cv, similar_builds
from gallery.tests import make_category, make_project, make_user


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class OpportunityTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.a = make_user('oppowner')
        self.b = make_user('oppremixer')

    def test_similar_matches_shared_problem_words(self):
        stock = make_project(
            self.a, self.cat, title='Stock Tracker',
            problem_statement='Spaza shops lose stock counts overnight.',
        )
        cousin = make_project(
            self.b, self.cat, title='Shop Counts',
            problem_statement='Spaza shops lose stock without a paper trail.',
        )
        other = make_project(
            self.b, self.cat, title='Chess Clock',
            problem_statement='Players forget whose turn it is.',
        )
        related = similar_builds(stock, [stock, cousin, other])
        slugs = [p.slug for p in related]
        self.assertIn(cousin.slug, slugs)
        self.assertNotIn(other.slug, slugs)

    def test_proof_cv_ignores_drafts(self):
        live = make_project(self.a, self.cat, title='Live CV', problem_statement='Need a booking page.')
        make_project(self.a, self.cat, title='Draft CV', status='pending', problem_statement='Secret draft.')
        cv = proof_cv(self.a, list(type(live).objects.filter(owner=self.a)))
        self.assertEqual(len(cv['builds']), 1)
        self.assertEqual(cv['problems'][0].title, 'Live CV')

    def test_proof_page_and_problems_have_no_jobs_tab(self):
        make_project(
            self.a, self.cat, title='Open Quest',
            problem_statement='Clinics lose patient files between rooms.',
        )
        proof = self.client.get(f'/u/{self.a.username}/proof/')
        self.assertEqual(proof.status_code, 200)
        self.assertContains(proof, 'PROOF OF WORK')
        self.assertContains(proof, 'Open Quest')
        self.assertNotContains(proof, 'Apply for a job')
        problems = self.client.get('/problems/')
        self.assertEqual(problems.status_code, 200)
        self.assertContains(problems, 'Open problems')
        self.assertContains(problems, 'Clinics lose patient files')
        self.assertNotContains(problems, 'Apply now')
        self.assertNotContains(problems, 'Hiring')

    def test_detail_shows_similar_builds(self):
        stock = make_project(
            self.a, self.cat, title='Stock Alpha',
            problem_statement='Spaza shops lose stock counts overnight.',
        )
        make_project(
            self.b, self.cat, title='Stock Beta',
            problem_statement='Spaza shops lose stock counts without wifi.',
        )
        response = self.client.get(f'/app/{stock.slug}/')
        self.assertContains(response, 'OTHERS SOLVING A SIMILAR PROBLEM')
        self.assertContains(response, 'Stock Beta')
