"""Tests for the BlaqVibes product architecture delivered in this change.

Grouped by the section of the plan each one pins:

* §4  — navigation is five destinations, utilities live in the account menu.
* §7  — BUILD is a real workflow with three honest entry points.
* §3/§18 — remix is a first-class relationship with real family statistics.
* §16/§17 — Builder Skills have immutable versions, and a project records
  the exact version it was built from.
* §9  — a project knows when it was first published.

Every test asserts *behaviour a visitor can see* or *data another feature
can rely on* — never an implementation detail.
"""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import AppProject, ProjectEvent
from .skill_models import Skill, SkillUse, SkillVersion
from .tests import make_category, make_project, make_user


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class PrimaryNavigationTests(TestCase):
    """§4 — the first thing users see is what people are building."""

    def setUp(self):
        self.cat = make_category()
        self.user = make_user('navigator')

    def _nav(self, body):
        return body[body.index('<div class="nav-links"'):body.index('</nav>')]

    def test_anonymous_nav_has_exactly_the_five_destinations(self):
        nav = self._nav(self.client.get('/').content.decode())
        for href in ('href="/"', 'href="/discover/"', 'href="/skills/"',
                     'href="/challenges/"', 'href="/build/"'):
            self.assertIn(href, nav)
        # Utilities must NOT compete with the five.
        for utility in ('/launch/', '/battle/', '/saved/', '/trades/', '/sales/', '/settings/'):
            self.assertNotIn(utility, nav)

    def test_utilities_move_into_the_account_menu(self):
        self.client.login(username='navigator', password='pass12345')
        body = self.client.get('/').content.decode()
        menu = body[body.index('<div class="nav-menu"'):body.index('</nav>')]
        for utility in ('/saved/', '/inbox/', '/battle/', '/launch/', '/trades/',
                        '/sales/', '/settings/', '/publish/'):
            self.assertIn(utility, menu)

    def test_legacy_prompt_skills_urls_still_resolve(self):
        skill = Skill.objects.create(
            creator=self.user, title='Legacy link', summary='Still reachable.',
            problem='Old links must not rot.', workflow='Redirect, never 404.',
        )
        self.assertRedirects(self.client.get('/prompt-skills/'), '/skills/',
                             status_code=301)
        self.assertRedirects(
            self.client.get(f'/prompt-skills/{skill.slug}/'),
            f'/skills/{skill.slug}/', status_code=301,
        )

    def test_project_page_renders_no_literal_template_markup(self):
        """A multi-line {# #} is not a comment — it renders as text."""
        project = make_project(self.user, self.cat, title='Markup Guard')
        body = self.client.get(f'/app/{project.slug}/').content.decode()
        self.assertNotIn('{#', body)
        self.assertNotIn('{%', body)
        self.assertNotIn('{{', body)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class BuildWorkflowTests(TestCase):
    """§7 — BUILD SOMETHING: scratch / remix / skill, then the workflow."""

    def setUp(self):
        self.cat = make_category()
        self.builder = make_user('buildhub')
        self.other = make_user('someone-else')

    def test_build_hub_offers_three_entry_points_to_anyone(self):
        response = self.client.get('/build/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Start from scratch')
        self.assertContains(response, 'Remix a project')
        self.assertContains(response, 'Use a Builder Skill')
        for step in ('CREATE', 'UPLOAD', 'SHOW', 'FEEDBACK', 'IMPROVE', 'PUBLISH'):
            self.assertContains(response, step)

    def test_remixable_rail_excludes_your_own_work(self):
        make_project(self.builder, self.cat, title='My Own Thing')
        make_project(self.other, self.cat, title='Someone Elses Thing')
        self.client.login(username='buildhub', password='pass12345')
        response = self.client.get('/build/')
        titles = [p.title for p in response.context['remixable']]
        self.assertIn('Someone Elses Thing', titles)
        self.assertNotIn('My Own Thing', titles)

    def test_using_a_skill_sends_the_builder_into_the_build_flow(self):
        skill = Skill.objects.create(
            creator=self.other, title='Ship a landing page',
            summary='A repeatable path to a live landing page.',
            problem='Landing pages stall at the copy stage.',
            workflow='Outline, then build the hero, then the proof section.',
        )
        self.client.login(username='buildhub', password='pass12345')
        response = self.client.post(f'/skills/{skill.slug}/use/')
        self.assertRedirects(response, f'/build/?skill={skill.slug}',
                             fetch_redirect_response=False)
        # The open loop is visible on the Build page until a project claims it.
        page = self.client.get('/build/')
        self.assertEqual(page.context['pending_skill_use'].skill_id, skill.pk)
        self.assertContains(page, 'Ship a landing page')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class DiscoverAndRemixStatsTests(TestCase):
    """§3/§18 — remix families are real data, and Discover shows them."""

    def setUp(self):
        self.cat = make_category()
        self.origin = make_user('origin')
        self.remixer = make_user('remixer')
        self.deep = make_user('deepremixer')
        self.root = make_project(self.origin, self.cat, title='Origin Idea')
        self.remix = make_project(self.remixer, self.cat, title='First Remix',
                                  forked_from=self.root)
        self.remix2 = make_project(self.deep, self.cat, title='Remix Of A Remix',
                                   forked_from=self.remix)
        self.hidden = make_project(self.remixer, self.cat, title='Hidden Remix',
                                   forked_from=self.root, status='pending')

    def test_discover_renders_for_a_stranger(self):
        response = self.client.get('/discover/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Origin Idea')
        self.assertContains(response, 'Most remixed')

    def test_totals_count_originals_remixes_and_depth(self):
        totals = self.client.get('/discover/').context['totals']
        self.assertEqual(totals['projects'], 3)
        self.assertEqual(totals['originals'], 1)
        self.assertEqual(totals['remixes'], 2)
        self.assertEqual(totals['deepest_generation'], 2)

    def test_unpublished_remixes_never_leak_into_the_stats(self):
        response = self.client.get('/discover/')
        self.assertNotContains(response, 'Hidden Remix')
        self.assertEqual(response.context['totals']['remixes'], 2)

    def test_top_remixers_ignore_remixing_your_own_work(self):
        selfish = make_user('selfremixer')
        own = make_project(selfish, self.cat, title='Own Root')
        make_project(selfish, self.cat, title='Own Remix', forked_from=own)
        usernames = [u.username for u in self.client.get('/discover/').context['top_remixers']]
        self.assertIn('remixer', usernames)
        self.assertNotIn('selfremixer', usernames)

    def test_growing_families_credit_the_original_not_the_middle_node(self):
        families = self.client.get('/discover/').context['growing_families']
        self.assertTrue(families)
        top = families[0]
        self.assertEqual(top['project'].pk, self.root.pk)
        self.assertEqual(top['new_remixes'], 2)  # both descendants are recent

    def test_remix_generation_and_root_are_derived_from_the_chain(self):
        self.assertEqual(self.root.remix_generation, 0)
        self.assertEqual(self.remix.remix_generation, 1)
        self.assertEqual(self.remix2.remix_generation, 2)
        self.assertEqual(self.remix2.root_project.pk, self.root.pk)
        self.assertFalse(self.root.is_remix)
        self.assertTrue(self.remix2.is_remix)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class SkillVersionTests(TestCase):
    """§16/§17 — SKILL → SKILL VERSION → USE → BUILD → PROJECT → PROOF."""

    def setUp(self):
        self.cat = make_category()
        self.author = make_user('skillauthor2')
        self.builder = make_user('skillbuilder2')
        self.skill = Skill.objects.create(
            creator=self.author,
            title='Debug a failing deploy',
            summary='Find the real cause of a broken deploy in minutes.',
            problem='Deploys fail and the logs are noise.',
            workflow='Read the last successful build, diff it, bisect the change.',
        )

    def test_a_new_skill_is_born_with_version_one(self):
        head = self.skill.current_version
        self.assertIsNotNone(head)
        self.assertEqual(head.version, 1)
        self.assertEqual(head.label, 'v1')
        self.assertEqual(head.workflow, self.skill.workflow)

    def test_versions_are_immutable(self):
        head = self.skill.current_version
        head.workflow = 'Rewritten history'
        with self.assertRaises(ValueError):
            head.save()
        head.refresh_from_db()
        self.assertNotEqual(head.workflow, 'Rewritten history')

    def test_editing_a_skill_publishes_a_new_version_and_keeps_the_old_text(self):
        self.client.login(username='skillauthor2', password='pass12345')
        response = self.client.post(f'/skills/{self.skill.slug}/update/', {
            'title': self.skill.title,
            'summary': self.skill.summary,
            'problem': self.skill.problem,
            'workflow': 'Now with a rollback step before the bisect.',
            'difficulty': 'intermediate',
        })
        self.assertEqual(response.status_code, 302)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.version_count, 2)
        self.assertEqual(self.skill.current_version.version, 2)
        first = SkillVersion.objects.get(skill=self.skill, version=1)
        self.assertIn('bisect the change', first.workflow)
        self.assertNotIn('rollback', first.workflow)

    def test_an_edit_that_changes_nothing_does_not_inflate_the_version(self):
        self.client.login(username='skillauthor2', password='pass12345')
        self.client.post(f'/skills/{self.skill.slug}/update/', {
            'title': self.skill.title,
            'summary': self.skill.summary,
            'problem': self.skill.problem,
            'workflow': self.skill.workflow,
            'difficulty': self.skill.difficulty,
        })
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.version_count, 1)

    def test_only_the_author_can_publish_a_new_version(self):
        self.client.login(username='skillbuilder2', password='pass12345')
        response = self.client.post(f'/skills/{self.skill.slug}/update/', {
            'title': 'Hijacked', 'summary': 'Hijacked', 'workflow': 'Hijacked',
        })
        self.assertEqual(response.status_code, 404)
        self.skill.refresh_from_db()
        self.assertEqual(self.skill.title, 'Debug a failing deploy')

    def test_project_records_the_exact_version_it_was_built_from(self):
        self.client.login(username='skillbuilder2', password='pass12345')
        self.client.post(f'/skills/{self.skill.slug}/use/')
        project = make_project(self.builder, self.cat, title='Deploy Doctor')
        project.refresh_from_db()
        self.assertEqual(project.source_skill_id, self.skill.pk)
        self.assertEqual(project.source_skill_version.version, 1)
        body = self.client.get(f'/app/{project.slug}/').content.decode()
        self.assertIn('Built using Builder Skill', body)
        self.assertIn('(v1)', body)

    def test_a_later_skill_edit_cannot_rewrite_an_existing_project_proof(self):
        self.client.login(username='skillbuilder2', password='pass12345')
        self.client.post(f'/skills/{self.skill.slug}/use/')
        project = make_project(self.builder, self.cat, title='Proof Anchor')
        self.client.logout()
        self.client.login(username='skillauthor2', password='pass12345')
        self.client.post(f'/skills/{self.skill.slug}/update/', {
            'title': self.skill.title,
            'summary': self.skill.summary,
            'problem': self.skill.problem,
            'workflow': 'A completely different workflow.',
            'difficulty': 'advanced',
        })
        project.refresh_from_db()
        self.assertEqual(project.source_skill_version.version, 1)
        self.assertIn('bisect the change', project.source_skill_version.workflow)

    def test_skill_page_shows_version_history_and_proof_count(self):
        self.client.login(username='skillbuilder2', password='pass12345')
        self.client.post(f'/skills/{self.skill.slug}/use/')
        make_project(self.builder, self.cat, title='Counted Proof')
        response = self.client.get(f'/skills/{self.skill.slug}/')
        self.assertContains(response, 'Version history')
        self.assertContains(response, 'Produced 1 published project')
        self.assertContains(response, 'Counted Proof')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class ProjectPublishedAtTests(TestCase):
    """§9/§18 — a project knows when it first became public."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('historian')

    def test_publishing_stamps_published_at_once(self):
        project = make_project(self.owner, self.cat, title='Timeline', status='pending')
        self.assertIsNone(project.published_at)
        project.status = 'published'
        project.save()
        project.refresh_from_db()
        first_stamp = project.published_at
        self.assertIsNotNone(first_stamp)

        # A re-queue → re-publish cycle is a new VERSION, not a new birthday.
        project.status = 'pending'
        project.save()
        project.status = 'published'
        project.save()
        project.refresh_from_db()
        self.assertEqual(project.published_at, first_stamp)
        self.assertTrue(
            ProjectEvent.objects.filter(project=project, kind='version').exists()
        )

    def test_project_page_shows_the_publish_date(self):
        project = make_project(self.owner, self.cat, title='Dated Project')
        project.refresh_from_db()
        response = self.client.get(f'/app/{project.slug}/')
        self.assertContains(response, 'Published')
        self.assertContains(response, project.published_at.strftime('%b %Y').lstrip('0'))


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class RemixStatsWindowTests(TestCase):
    """Growth is measured in a window — old families are not 'growing'."""

    def setUp(self):
        self.cat = make_category()
        self.a = make_user('oldorigin')
        self.b = make_user('oldremixer')

    def test_old_remixes_do_not_count_as_growth(self):
        from . import remix_stats
        root = make_project(self.a, self.cat, title='Ancient Idea')
        old_remix = make_project(self.b, self.cat, title='Ancient Remix', forked_from=root)
        AppProject.objects.filter(pk=old_remix.pk).update(
            created_at=timezone.now() - timedelta(days=90)
        )
        self.assertEqual(remix_stats.fastest_growing_families(), [])
        # …but it still counts as a remix and as "most remixed".
        self.assertEqual(remix_stats.remix_totals()['remixes'], 1)
        self.assertEqual(remix_stats.most_remixed()[0].pk, root.pk)
