"""Tests for the starter gallery + in-browser Studio (feature 2).

The Studio is an on-ramp for a beginner with nothing built: pick a starter,
edit it live client-side, publish through the ONE publish path. These tests
pin the two promises:
  * the gallery and studio load starters honestly (data, public, blank option);
  * publishing from Studio flows through the real publish path, so the result
    is a normal snippet AppProject with the user's edits — no shortcut around
    scan/classify/trust.
"""
from django.test import TestCase, override_settings

from gallery.models import AppProject
from gallery.starters import STARTERS, STARTERS_BY_SLUG, get_starter

from .tests import make_category, make_user


class StarterDataTests(TestCase):
    def test_every_starter_is_self_contained_and_complete(self):
        for s in STARTERS:
            self.assertTrue(s['slug'])
            self.assertTrue(s['name'])
            self.assertTrue(s['html'].strip(), s['slug'])
            # README must clear the publish form's own gate (100 chars + heading).
            self.assertGreaterEqual(len(s['readme'].strip()), 100, s['slug'])
            self.assertIn('# ', s['readme'], s['slug'])

    def test_get_starter_returns_none_for_unknown(self):
        self.assertIsNone(get_starter('nope'))
        self.assertIsNone(get_starter(''))

    def test_slugs_are_unique(self):
        slugs = [s['slug'] for s in STARTERS]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertEqual(len(STARTERS_BY_SLUG), len(STARTERS))


class StarterGalleryViewTests(TestCase):
    def test_gallery_is_public_and_lists_starters(self):
        resp = self.client.get('/start/')
        self.assertEqual(resp.status_code, 200)
        for s in STARTERS:
            self.assertContains(resp, s['name'])
        # The blank on-ramp is always offered.
        self.assertContains(resp, 'Blank canvas')


class StudioViewTests(TestCase):
    def test_studio_loads_starter_into_editors(self):
        resp = self.client.get('/studio/hello-landing/')
        self.assertEqual(resp.status_code, 200)
        starter = get_starter('hello-landing')
        # The starter HTML is pre-loaded into the editor textarea.
        self.assertContains(resp, 'people vibing')
        self.assertEqual(resp.context['starter']['slug'], starter['slug'])

    def test_blank_studio_loads(self):
        resp = self.client.get('/studio/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['starter'])

    def test_unknown_starter_is_404(self):
        self.assertEqual(self.client.get('/studio/not-real/').status_code, 404)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-studio-tests')
class StudioPublishTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.user = make_user('studiouser')
        self.client.force_login(self.user)

    def test_publish_from_studio_creates_snippet_project(self):
        resp = self.client.post('/studio/hello-landing/', {
            'title': 'My First Vibe',
            'category': self.cat.id,
            'short_description': 'A landing page I made in Studio.',
            'readme': '# My First Vibe\n\n' + ('Built in the BlaqVibes Studio from a starter. ' * 4),
            'tech_stack': 'HTML, CSS',
            'html_code': '<h1>Edited in studio</h1>',
            'css_code': 'h1{color:hotpink}',
            'js_code': 'console.log("mine")',
            'star_cost': 0,
            'price_zar': 0,
        })
        # publish() redirects to the new vibe on success.
        self.assertEqual(resp.status_code, 302)
        project = AppProject.objects.get(owner=self.user, title='My First Vibe')
        # The user's edits — not the starter defaults — were saved.
        self.assertIn('Edited in studio', project.html_code)
        self.assertIn('hotpink', project.css_code)
        # It is a snippet (no ZIP), owned by the publisher.
        self.assertFalse(project.zip_file)
        self.assertEqual(project.owner, self.user)

    def test_publish_from_blank_studio(self):
        resp = self.client.post('/studio/', {
            'title': 'Blank Start Vibe',
            'category': self.cat.id,
            'short_description': 'Started from a blank canvas.',
            'readme': '# Blank Start Vibe\n\n' + ('Made from scratch in the Studio blank canvas. ' * 4),
            'tech_stack': 'HTML',
            'html_code': '<main>hello world</main>',
            'css_code': 'main{padding:20px}',
            'js_code': '',
            'star_cost': 0,
            'price_zar': 0,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(AppProject.objects.filter(owner=self.user, title='Blank Start Vibe').exists())

    def test_studio_publish_requires_login(self):
        self.client.logout()
        resp = self.client.post('/studio/hello-landing/', {
            'title': 'Should Not Publish',
            'category': self.cat.id,
            'short_description': 'x',
            'readme': '# x\n\n' + ('y ' * 60),
            'html_code': '<h1>x</h1>',
            'star_cost': 0,
            'price_zar': 0,
        })
        # login_required on the underlying publish view → redirect to login.
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)
        self.assertFalse(AppProject.objects.filter(title='Should Not Publish').exists())
