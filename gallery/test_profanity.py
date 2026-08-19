"""Public-language gate — comments and every other public write path.

5 Whys: Why a dedicated test module? The filter is a new contract.
A regression that lets a slur through a comment is worse than a
broken chart. These tests pin the matcher, the false-positive
boundary, and every public POST that can show user text.
"""
from django.test import TestCase, override_settings

from gallery.forms import AppUploadForm, CommentForm, ReviewForm
from gallery.models import AppProject, Comment, Notification, Review
from gallery.profanity import (
    PUBLIC_LANGUAGE_ERROR,
    contains_profanity,
    validate_public_text,
)
from gallery.tests import make_category, make_project, make_user
from users.forms import ProfileForm, SignUpForm, TipForm


class MatcherTests(TestCase):
    def test_clean_technical_prose_is_allowed(self):
        for text in (
            'Does this work with class-based views?',
            'I put the password in .env.example',
            'Thanks, your assistant notes helped.',
            'The analysis of the cocktail API looks solid.',
            'Hello from Scunthorpe — great README.',
            'Document the title field and the classic setup.',
            'This is a solid Django app. Cloned and ran it.',
        ):
            self.assertFalse(contains_profanity(text), text)

    def test_plain_and_obfuscated_abuse_is_caught(self):
        for text in (
            'this is fucking broken',
            'f.u.c.k this setup',
            'what a load of sh1t',
            'you are an asshole',
            'piece of shit readme',
            'fuuuuck this',
            'username fuckyou should never ship',
        ):
            self.assertTrue(contains_profanity(text), text)

    def test_empty_is_clean(self):
        self.assertFalse(contains_profanity(''))
        self.assertFalse(contains_profanity(None))

    def test_validate_raises_without_echoing_the_word(self):
        with self.assertRaises(Exception) as ctx:
            validate_public_text('this is fucking broken')
        message = str(ctx.exception)
        self.assertIn('reword', message.lower())
        self.assertNotIn('fuck', message.lower())


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class CommentAndReviewGateTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')
        self.fan = make_user('fan')
        self.project = make_project(self.owner, self.cat, title='Clean vibe')

    def test_clean_comment_posts_and_renders(self):
        self.client.login(username='fan', password='pass12345')
        response = self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': 'Does this work with Django 5 class-based views?'},
        )
        self.assertEqual(response.status_code, 302)
        comment = Comment.objects.get()
        self.assertFalse(comment.is_hidden)
        self.assertIn('class-based', comment.body)
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertContains(page, 'class-based')
        self.assertNotContains(page, PUBLIC_LANGUAGE_ERROR)

    def test_vulgar_comment_is_not_created_or_shown(self):
        self.client.login(username='fan', password='pass12345')
        response = self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': 'this is fucking broken and you know it'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Comment.objects.exists())
        self.assertFalse(Notification.objects.filter(kind='comment').exists())
        self.assertContains(response, 'cannot include')
        self.assertNotContains(response, 'fucking')

    def test_obfuscated_comment_is_also_rejected(self):
        self.client.login(username='fan', password='pass12345')
        self.client.post(
            f'/app/{self.project.slug}/comment/',
            {'body': 'what a load of s.h.i.t honestly'},
        )
        self.assertFalse(Comment.objects.exists())

    def test_orm_write_hides_instead_of_rendering(self):
        comment = Comment.objects.create(
            project=self.project,
            user=self.fan,
            body='you are an asshole for shipping this',
        )
        comment.refresh_from_db()
        self.assertTrue(comment.is_hidden)
        self.assertNotIn('asshole', comment.body_html)
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertNotContains(page, 'asshole')

    def test_hidden_comments_are_not_counted(self):
        Comment.objects.create(
            project=self.project, user=self.fan, body='Looks good, thanks a lot.',
        )
        Comment.objects.create(
            project=self.project, user=self.fan, body='this is fucking broken now',
        )
        page = self.client.get(f'/app/{self.project.slug}/')
        self.assertContains(page, 'Comments • 1')

    def test_vulgar_review_is_rejected(self):
        self.client.login(username='fan', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/star/')
        self.client.post(
            f'/app/{self.project.slug}/review/',
            {'rating': '2', 'text': 'what a load of bullshit honestly'},
        )
        self.assertFalse(Review.objects.exists())

    def test_clean_review_posts(self):
        self.client.login(username='fan', password='pass12345')
        self.client.post(f'/app/{self.project.slug}/star/')
        response = self.client.post(
            f'/app/{self.project.slug}/review/',
            {'rating': '5', 'text': 'Cloned it. Runs clean on Django 5.'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Review.objects.get().text, 'Cloned it. Runs clean on Django 5.')

    def test_comment_form_itself_rejects(self):
        form = CommentForm(data={'body': 'this is fucking broken now'})
        self.assertFalse(form.is_valid())
        self.assertIn('body', form.errors)

    def test_review_form_itself_rejects(self):
        form = ReviewForm(data={'rating': 3, 'text': 'this is fucking broken now'})
        self.assertFalse(form.is_valid())


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class OtherPublicWriteGatesTests(TestCase):
    def setUp(self):
        self.cat = make_category()

    def test_publish_form_rejects_vulgar_title_and_readme(self):
        data = {
            'title': 'My fucking tracker',
            'category': self.cat.id,
            'short_description': 'A short description of this vibe.',
            'readme': '# Heading\n\n' + ('Enough characters in this readme for the form. ' * 3),
            'html_code': '<div>hi</div>',
            'star_cost': 0,
            'price_zar': 0,
        }
        form = AppUploadForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('title', form.errors)

        data['title'] = 'My tracker'
        data['readme'] = '# Heading\n\nthis is a fucking mess of a readme. ' + ('padding text here. ' * 8)
        form = AppUploadForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('readme', form.errors)

    def test_signup_rejects_vulgar_username(self):
        form = SignUpForm(data={
            'username': 'fuckyou',
            'email': 'x@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)

    def test_signup_allows_normal_username(self):
        form = SignUpForm(data={
            'username': 'nolo_ai',
            'email': 'nolo@test.com',
            'password1': 'correcthorse1',
            'password2': 'correcthorse1',
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_profile_form_rejects_vulgar_bio(self):
        form = ProfileForm(data={'bio': 'I build fucking stock tools', 'location': 'Durban, ZA'})
        self.assertFalse(form.is_valid())
        self.assertIn('bio', form.errors)

    def test_profile_form_allows_clean_bio(self):
        form = ProfileForm(data={'bio': 'AI builder • Stock tools • Durban, ZA', 'location': 'Durban, ZA'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_tip_form_rejects_vulgar_note(self):
        form = TipForm(data={'amount': 2, 'message': 'you are an asshole'})
        self.assertFalse(form.is_valid())
        self.assertIn('message', form.errors)

    def test_tip_endpoint_does_not_store_vulgar_note(self):
        sender = make_user('tipster', stars_balance=5)
        make_user('tippee')
        self.client.login(username='tipster', password='pass12345')
        response = self.client.post(
            '/u/tippee/tip/',
            {'amount': '1', 'message': 'you are an asshole'},
        )
        self.assertEqual(response.status_code, 400)
        from users.models import Tip
        self.assertFalse(Tip.objects.exists())
        sender.profile.refresh_from_db()
        self.assertEqual(sender.profile.stars_balance, 5)

    def test_notify_drops_a_vulgar_quote(self):
        owner = make_user('noted')
        from gallery.notify import notify
        note = notify(owner, 'comment', 'this is fucking broken', 'what a load of shit')
        note.refresh_from_db()
        self.assertEqual(note.title, 'New activity on BlaqVibes')
        self.assertEqual(note.body, '')


# --- Local languages, display backstops, ORM gates, social signups --------


class LocalLanguageMatcherTests(TestCase):
    """Durban speaks more than English. The gate must too."""

    def test_local_language_abuse_is_caught(self):
        for text in (
            'jy is n klootzak',
            'what a cuiter honestly',
            'daai hoer steel code',
            'uyisifebe wena',
            'uyisilima ungakhulumi',
            'le app ibhalwe yisidenge',
            'wena sithutha',
            'uyisiphukuphuku',
            'umqundu wakho unuka',
            'stop being such a hotnot',
            'fokken poes',
        ):
            self.assertTrue(contains_profanity(text), text)

    def test_innocent_local_speech_stays_allowed(self):
        for text in (
            # mampara is documented as affectionate ("silly goose") —
            # blocking it kills real speech without stopping abuse.
            'ai mampara, look, this is how we do it',
            'Eish, the build failed again.',
            'sharp sharp — see you at the Durban meetup',
            'isilinganiso means measurement in isiZulu',
            'the tokoloshe is folklore, not abuse',
        ):
            self.assertFalse(contains_profanity(text), text)

    def test_english_sex_education_words_are_not_blocked(self):
        # The gate stops vulgarity and slurs, not safety vocabulary.
        for text in (
            'Our README explains sex education resources for schools.',
            'This app teaches consent and rape prevention.',
            'A porn filter would break this safety toolkit.',
        ):
            self.assertFalse(contains_profanity(text), text)


class DisplayTextBackstopTests(TestCase):
    def test_clean_value_passes_through(self):
        from gallery.profanity import display_text
        self.assertEqual(display_text('Stock tracker', 'x'), 'Stock tracker')
        self.assertEqual(display_text('', 'fallback'), 'fallback')
        self.assertEqual(display_text(None, 'fallback'), 'fallback')

    def test_dirty_value_becomes_placeholder_without_echo(self):
        from gallery.profanity import display_text
        out = display_text('this is fucking broken', 'Untitled vibe')
        self.assertEqual(out, 'Untitled vibe')
        self.assertNotIn('fuck', out)

    def test_template_filters_replace_and_escape(self):
        from django.template import Context, Template
        rendered = Template(
            "{% load safe_display %}{{ t|public_text:'Untitled' }}"
        ).render(Context({'t': 'what a load of bullshit'}))
        self.assertEqual(rendered, 'Untitled')

        rendered = Template(
            "{% load safe_display %}{{ h|public_html|safe }}"
        ).render(Context({'h': '<p>you are an asshole</p>'}))
        self.assertNotIn('asshole', rendered)
        self.assertIn('hidden', rendered)

        # Clean HTML survives untouched (still safe-marked by |safe).
        rendered = Template(
            "{% load safe_display %}{{ h|public_html|safe }}"
        ).render(Context({'h': '<p>nice work</p>'}))
        self.assertEqual(rendered, '<p>nice work</p>')


@override_settings(SEED_DEMO=False)
class ProjectOrmGateTests(TestCase):
    """Admin/shell writes must never publish blocked words."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('owner')

    def test_dirty_title_cannot_be_published_via_orm(self):
        project = AppProject.objects.create(
            owner=self.owner,
            category=self.cat,
            title='My fucking tracker',
            short_description='A short description of this vibe.',
            readme='# H\n\n' + ('enough text for the readme here. ' * 5),
            status='published',
        )
        project.refresh_from_db()
        # Held, not rewritten: raw words stay for moderators, status drops.
        self.assertEqual(project.status, 'pending')
        self.assertEqual(project.title, 'My fucking tracker')
        self.assertIn('language_gate', project.scan_report)
        # Not on the feed, not in the API.
        page = self.client.get('/')
        self.assertNotContains(page, 'fucking')
        api = self.client.get('/api/v1/apps/')
        self.assertNotContains(api, 'fucking')

    def test_dirty_slug_is_never_minted(self):
        project = AppProject.objects.create(
            owner=self.owner,
            category=self.cat,
            title='fuck you all',
            short_description='A short description of this vibe.',
            readme='# H\n\n' + ('enough text for the readme here. ' * 5),
        )
        self.assertNotIn('fuck', project.slug)

    def test_clean_full_save_still_publishes(self):
        project = make_project(self.owner, self.cat, title='Clean title')
        project.refresh_from_db()
        self.assertEqual(project.status, 'published')
        self.assertNotIn('language_gate', project.scan_report or {})

    def test_full_clean_raises_field_errors_for_admin(self):
        from django.core.exceptions import ValidationError
        project = AppProject(
            owner=self.owner,
            category=self.cat,
            title='what a klootzak app',
            short_description='A short description.',
            readme='fine',
        )
        with self.assertRaises(ValidationError) as ctx:
            project.full_clean()
        self.assertIn('title', ctx.exception.message_dict)

    def test_display_backstop_catches_a_shell_edit(self):
        project = make_project(self.owner, self.cat, title='Clean title')
        # Bypass save() entirely, like a raw shell UPDATE.
        AppProject.objects.filter(pk=project.pk).update(title='this is shit')
        page = self.client.get(f'/app/{project.slug}/')
        self.assertNotContains(page, 'this is shit')
        self.assertContains(page, 'Untitled vibe')
        feed = self.client.get('/')
        self.assertNotContains(feed, 'this is shit')
        api = self.client.get(f'/api/v1/apps/{project.slug}/')
        self.assertNotContains(api, 'this is shit')
        self.assertContains(api, 'Untitled vibe')

    def test_changelog_backstop(self):
        from gallery.models import AppVersion
        project = make_project(self.owner, self.cat)
        version = AppVersion(project=project, version='1.1.0', changelog='fokken bullshit release')
        version.save()
        version.refresh_from_db()
        self.assertEqual(version.changelog, 'Update')


@override_settings(SEED_DEMO=False)
class SocialUsernameGateTests(TestCase):
    """GitHub/Google signups never touch SignUpForm — the adapter gates them."""

    def test_clean_username_helper_is_a_noop(self):
        from users.adapters import force_clean_username
        user = make_user('clean_handle')
        self.assertIsNone(force_clean_username(user))
        user.refresh_from_db()
        self.assertEqual(user.username, 'clean_handle')

    def test_dirty_username_is_force_renamed(self):
        from django.contrib.auth.models import User
        from users.adapters import force_clean_username
        user = User.objects.create_user(username='fuckyou', password='pass12345')
        renamed = force_clean_username(user)
        self.assertEqual(renamed, f'user_{user.pk}')
        user.refresh_from_db()
        self.assertEqual(user.username, f'user_{user.pk}')
        self.assertFalse(contains_profanity(user.username))

    def test_rename_avoids_collisions(self):
        from django.contrib.auth.models import User
        from users.adapters import force_clean_username
        dirty = User.objects.create_user(username='poes', password='pass12345')
        User.objects.create_user(username=f'user_{dirty.pk}', password='x')
        renamed = force_clean_username(dirty)
        self.assertEqual(renamed, f'user_{dirty.pk}_1')

    def test_auto_signup_refused_for_dirty_provider_handle(self):
        from types import SimpleNamespace
        from users.adapters import BlaqSocialAccountAdapter
        adapter = BlaqSocialAccountAdapter()
        dirty = SimpleNamespace(user=SimpleNamespace(username='fuckyou'))
        clean = SimpleNamespace(user=SimpleNamespace(username='nolo_ai'))
        self.assertFalse(adapter.is_auto_signup_allowed(None, dirty))
        self.assertTrue(adapter.is_auto_signup_allowed(None, clean))

    def test_account_adapter_clean_username_rejects(self):
        from django.core.exceptions import ValidationError
        from users.adapters import BlaqAccountAdapter
        adapter = BlaqAccountAdapter()
        with self.assertRaises(ValidationError):
            adapter.clean_username('fuckyou')
        self.assertEqual(adapter.clean_username('nolo_ai'), 'nolo_ai')

    def test_social_save_user_renames_and_notifies(self):
        """Full adapter path: a dirty handle becomes user_<pk> + the
        person is told why (no silent rewrite)."""
        from types import SimpleNamespace
        from django.contrib.auth.models import User
        from users.adapters import BlaqSocialAccountAdapter

        adapter = BlaqSocialAccountAdapter()
        user = User(username='fuckyou')
        sociallogin = SimpleNamespace(
            user=user,
            account=SimpleNamespace(provider='github', extra_data={'login': 'fuckyou'}),
        )

        def fake_super_save(self, request, sociallogin, form=None):
            sociallogin.user.set_unusable_password()
            sociallogin.user.save()
            return sociallogin.user

        import users.adapters as adapters_module
        original = adapters_module.DefaultSocialAccountAdapter.save_user
        adapters_module.DefaultSocialAccountAdapter.save_user = fake_super_save
        try:
            saved = adapter.save_user(None, sociallogin)
        finally:
            adapters_module.DefaultSocialAccountAdapter.save_user = original

        saved.refresh_from_db()
        self.assertEqual(saved.username, f'user_{saved.pk}')
        profile = saved.profile
        profile.refresh_from_db()
        # A dirty GitHub handle is never copied onto the public profile.
        self.assertEqual(profile.github, '')
        note = Notification.objects.filter(user=saved, kind='moderation').first()
        self.assertIsNotNone(note)
        self.assertNotIn('fuck', note.body.lower())


@override_settings(SEED_DEMO=False)
class ScrubMigrationTests(TestCase):
    """Existing rows from before the gate get held/renamed, never rewritten."""

    def _run(self, module_name, func_name='_scrub_accounts'):
        import importlib
        from django.apps import apps as global_apps
        module = importlib.import_module(f'gallery.migrations.{module_name}')
        getattr(module, func_name)(global_apps, None)

    def test_accounts_migration_renames_and_notifies(self):
        from django.contrib.auth.models import User
        user = User.objects.create_user(
            username='fuckyou', password='pass12345',
            first_name='Normal', last_name='Name',
        )
        user.profile.github = 'fuckyou-dev'
        user.profile.save(update_fields=['github'])
        clean = User.objects.create_user(username='nolo_ai', password='pass12345')

        self._run('0028_scrub_existing_accounts')

        user.refresh_from_db()
        clean.refresh_from_db()
        self.assertEqual(user.username, f'user_{user.pk}')
        self.assertEqual(clean.username, 'nolo_ai')
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.github, '')
        note = Notification.objects.filter(user=user, kind='moderation').first()
        self.assertIsNotNone(note)
        self.assertIn('username', note.title.lower())
        # The old word must never be echoed back to anyone.
        self.assertNotIn('fuckyou', note.body)

    def test_accounts_migration_blanks_dirty_notification_urls(self):
        owner = make_user('note_owner')
        note = Notification.objects.create(
            user=owner, kind='follow', title='Someone followed you',
            url='/u/fuckyou/',
        )
        self._run('0028_scrub_existing_accounts')
        note.refresh_from_db()
        self.assertEqual(note.url, '')

    def test_vibes_migration_holds_dirty_published_rows(self):
        cat = make_category()
        owner = make_user('owner')
        project = make_project(owner, cat, title='Legacy clean vibe')
        # Simulate a pre-gate shell write that bypassed save().
        AppProject.objects.filter(pk=project.pk).update(
            title='what a load of shit', slug='load-of-shit-app',
        )
        from gallery.models import AppVersion
        version = AppVersion.objects.create(
            project=project, version='1.1.0', changelog='fokken kak release',
        )

        self._run('0027_hold_public_vibe_profanity', '_hold')

        project.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(project.status, 'pending')
        self.assertIn('language_gate', project.scan_report)
        self.assertNotIn('shit', project.slug)
        # Raw text survives for moderators — never silently rewritten.
        self.assertEqual(project.title, 'what a load of shit')
        self.assertEqual(version.changelog, 'Update')


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False, MEDIA_ROOT='/tmp/blaqvibes-tests-changelog')
class ChangelogHonestyTests(TestCase):
    """A vulgar changelog must not be silently rewritten to "Update" —
    the author has to be told why."""

    def setUp(self):
        from gallery.tests import make_zip_file
        self.cat = make_category()
        self.owner = make_user('owner')
        self.project = make_project(self.owner, self.cat, title='Clean vibe')
        self.project.zip_file.save('app.zip', make_zip_file({'app.py': 'print(1)\n'}), save=True)

    def _edit_post(self, changelog):
        self.client.login(username='owner', password='pass12345')
        from gallery.tests import make_zip_file
        return self.client.post(
            f'/app/{self.project.slug}/edit/',
            {
                'title': 'Clean vibe',
                'category': self.cat.pk,
                'short_description': 'A short description of this vibe.',
                'readme': '# Heading\n\n' + ('Enough characters in this readme for the form. ' * 3),
                'star_cost': '0',
                'price_zar': '0',
                'changelog': changelog,
                'zip_file': make_zip_file({'app.py': 'print(2)\n'}),
            },
            follow=True,
        )

    def test_vulgar_changelog_is_flagged_to_the_author(self):
        response = self._edit_post('fokken kak release')
        self.assertContains(response, 'Your changelog was not saved')
        version = self.project.versions.order_by('created_at').first()
        self.assertIsNotNone(version)
        self.assertEqual(version.changelog, 'Update')

    def test_clean_changelog_is_kept(self):
        self._edit_post('Fixed the chart bug')
        version = self.project.versions.order_by('created_at').first()
        self.assertEqual(version.changelog, 'Fixed the chart bug')


@override_settings(SEED_DEMO=False)
class AdminGatesTests(TestCase):
    def test_staff_user_form_rejects_dirty_username(self):
        from django.core.exceptions import ValidationError
        from users.admin import BlaqUserChangeForm
        user = make_user('staff_target')
        form = BlaqUserChangeForm(instance=user)
        form.cleaned_data = {'username': 'fuckyou'}
        with self.assertRaises(ValidationError):
            form.clean_username()
        form.cleaned_data = {'username': 'clean_name'}
        self.assertEqual(form.clean_username(), 'clean_name')

    def test_project_clean_blocks_admin_style_save(self):
        """Django admin validates via full_clean before saving — dirty
        titles must become an error, not a published row."""
        from django.core.exceptions import ValidationError
        cat = make_category()
        owner = make_user('owner2')
        project = make_project(owner, cat, title='Clean title')
        project.title = 'this is shit'
        with self.assertRaises(ValidationError):
            project.full_clean()


@override_settings(SEED_DEMO=False)
class RealAllauthSocialFormTests(TestCase):
    """The actual allauth social signup form — not a mock of it. When
    auto-signup is refused for a dirty handle, THIS form is what the
    person sees, and its username field must run our gate."""

    def _form(self, username):
        from types import SimpleNamespace
        from django.contrib.auth.models import User
        from allauth.socialaccount.forms import SignupForm
        user = User(username=username, email=f'{username}@test.com')
        sociallogin = SimpleNamespace(user=user, email_addresses=[])
        return SignupForm(
            sociallogin=sociallogin,
            data={'username': username, 'email': f'{username}@test.com'},
        )

    def test_dirty_provider_handle_rejected_by_real_form(self):
        form = self._form('fuckyou')
        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)
        self.assertNotIn('fuckyou', str(form.errors['username']))

    def test_clean_handle_accepted_by_real_form(self):
        form = self._form('nolo_ai')
        self.assertTrue(form.is_valid(), form.errors)

    def test_obfuscated_handle_rejected_by_real_form(self):
        form = self._form('fvckyou123')
        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)


@override_settings(SEED_DEMO=False)
class ReadmeRenderBackstopTests(TestCase):
    def test_dirty_readme_html_never_renders(self):
        cat = make_category()
        owner = make_user('readme_owner')
        project = make_project(owner, cat, title='Readme vibe')
        # Simulate a shell write of both the markdown AND the rendered
        # HTML, bypassing save() entirely.
        AppProject.objects.filter(pk=project.pk).update(
            readme='this is a fucking mess',
            readme_html='<p>this is a fucking mess</p>',
        )
        page = self.client.get(f'/app/{project.slug}/')
        self.assertNotContains(page, 'fucking')
        self.assertContains(page, 'The README for this vibe was removed.')
