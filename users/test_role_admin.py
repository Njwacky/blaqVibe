""""Find someone, change their role" — search, guards, and the audit trail.

The bug these cover: `/admin/roles/` rendered every account in the database
with an armed `<select>` + button per row. It did not scale (the page cost grew
with the company), it had no way to find a person, and nothing stopped a typo
in the URL from promoting the wrong one.
"""
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from gallery.models import Notification
from .models import AdminLog, Profile
from .roles import apply_role_change, superadmin_count
from .user_search import (
    RESULT_LIMIT, role_totals, search_users, users_with_role,
)

PW = 'RoleAdmin@2026'
LOCMEM = 'django.core.mail.backends.locmem.EmailBackend'


def make_user(username, role='user', email=None, **extra):
    """A user with an explicit profile role. Profiles are created by the
    post_save signal, so update_or_create is what the rest of the suite does."""
    user = User.objects.create_user(
        username, email or f'{username}@example.com', PW, **extra)
    Profile.objects.update_or_create(user=user, defaults={'role': role})
    user.refresh_from_db()
    return user


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class UserSearchTests(TestCase):
    def setUp(self):
        self.kwame = make_user('kwame')
        self.kwame2 = make_user('kwame2')
        self.skwame = make_user('skwame')
        self.other = make_user('thando', email='thando@vibes.co.za')

    def test_exact_username_ranks_first(self):
        results = search_users('kwame')
        self.assertEqual(results.users[0], self.kwame)
        self.assertEqual(results.users[0].match_label, 'exact username')

    def test_leading_at_sign_is_stripped(self):
        results = search_users('@kwame')
        self.assertEqual(results.query, 'kwame')
        self.assertEqual(results.users[0], self.kwame)

    def test_prefix_ranks_above_interior_match(self):
        usernames = [u.username for u in search_users('kwame').users]
        self.assertLess(usernames.index('kwame2'), usernames.index('skwame'))

    def test_email_exact_match_finds_the_person(self):
        results = search_users('thando@vibes.co.za')
        self.assertEqual(results.users[0], self.other)
        self.assertEqual(results.users[0].match_label, 'exact email')
        self.assertEqual(results.single_exact, self.other)

    def test_confirmed_alternative_email_matches(self):
        """allauth holds several addresses per user; User.email holds one."""
        EmailAddress.objects.create(user=self.kwame, email='kwame@alt.dev',
                                    verified=True, primary=False)
        results = search_users('kwame@alt.dev')
        self.assertEqual(results.users[0], self.kwame)
        self.assertEqual(results.users[0].match_label, 'verified email address')
        self.assertEqual(results.single_exact, self.kwame)

    def test_two_matches_is_not_an_exact_hit(self):
        results = search_users('kwame')
        self.assertGreaterEqual(results.total, 2)
        self.assertIsNone(results.single_exact)

    def test_query_shorter_than_two_characters_returns_nothing(self):
        results = search_users('k')
        self.assertTrue(results.too_short)
        self.assertEqual(results.users, [])

    def test_blank_query_makes_no_query(self):
        with self.assertNumQueries(0):
            results = search_users('   ')
        self.assertFalse(results.too_short)
        self.assertEqual(results.users, [])

    def test_results_are_bounded_and_say_so(self):
        for i in range(RESULT_LIMIT + 5):
            make_user(f'bulk{i:02d}')
        results = search_users('bulk')
        self.assertEqual(len(results.users), RESULT_LIMIT)
        self.assertEqual(results.total, RESULT_LIMIT + 5)
        self.assertFalse(results.exhausted)

    def test_two_terms_must_both_match(self):
        ghana = make_user('kwame.ghana')
        make_user('kwame.za')
        results = search_users('kwame ghana')
        self.assertEqual([u.username for u in results.users], [ghana.username])

    def test_search_falls_back_when_the_database_cannot_do_trigrams(self):
        """A missing pg_trgm extension must cost a warning, not a 500."""
        from users import user_search
        with patch.dict(user_search._TRIGRAM, {'checked': True, 'ok': True}):
            results = search_users('kwame')
        self.assertTrue(any(u.username == 'kwame' for u in results.users))
        self.assertFalse(user_search._TRIGRAM['ok'])  # remembered for next time

    def test_no_email_is_shown_for_a_user_without_one(self):
        make_user('noemail', email='')
        results = search_users('noemail')
        self.assertEqual(results.users[0].username, 'noemail')
        self.assertEqual(results.users[0].match_label, 'exact username')


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False)
class RoleListingTests(TestCase):
    def setUp(self):
        self.admin = make_user('blaq', role='admin')
        self.mod = make_user('sbu', role='moderator')
        self.plain = make_user('thando')

    def test_users_with_role_is_bounded(self):
        rows, total = users_with_role('moderator')
        self.assertEqual([u.username for u in rows], ['sbu'])
        self.assertEqual(total, 1)

    def test_unknown_role_lists_nobody(self):
        self.assertEqual(users_with_role('wizard'), ([], 0))

    def test_role_totals_counts_every_role(self):
        totals = role_totals()
        self.assertEqual(totals['admin'], 1)
        self.assertEqual(totals['moderator'], 1)
        self.assertEqual(totals['user'], 1)
        self.assertEqual(totals['superadmin'], 0)


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False, EMAIL_BACKEND=LOCMEM)
class ApplyRoleChangeTests(TestCase):
    def setUp(self):
        self.boss = make_user('nolo.ai', role='superadmin')
        self.person = make_user('kwame')

    def apply(self, new_role, **kw):
        args = dict(actor=self.boss, target=self.person, new_role=new_role)
        args.update(kw)
        return apply_role_change(**args)

    def test_promotion_needs_a_reason(self):
        result = self.apply('moderator', reason='no')
        self.assertFalse(result.changed)
        self.assertIn('why', result.message.lower())

    def test_promotion_needs_the_typed_username(self):
        result = self.apply('moderator', reason='reports queue help')
        self.assertFalse(result.changed)
        self.assertIn('@kwame', result.message)
        self.assertEqual(self.person.profile.role, 'user')

    def test_promotion_with_reason_and_confirmation_lands(self):
        with self.captureOnCommitCallbacks(execute=True):
            result = self.apply('moderator', reason='reports queue help',
                                confirm='@kwame')
        self.assertTrue(result.changed)
        self.person.refresh_from_db()
        self.assertEqual(self.person.profile.role, 'moderator')
        self.assertEqual(Notification.objects.filter(
            user=self.person, kind='role').count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('moderator', mail.outbox[0].subject)

    def test_demotion_needs_no_confirmation(self):
        self.apply('admin', reason='triage reports', confirm='kwame')  # escalate first
        with self.captureOnCommitCallbacks(execute=True):
            result = self.apply('user', reason='left the team')
        self.assertTrue(result.changed)
        self.person.refresh_from_db()
        self.assertEqual(self.person.profile.role, 'user')

    def test_admin_keeps_django_staff_flag(self):
        self.apply('admin', reason='triage reports', confirm='kwame')
        self.person.refresh_from_db()
        self.assertTrue(self.person.is_staff)
        self.apply('user', reason='no longer an admin')
        self.person.refresh_from_db()
        self.assertFalse(self.person.is_staff)

    def test_django_superuser_flag_is_never_cleared_quietly(self):
        django_admin = make_user('legacy', role='admin', is_staff=True, is_superuser=True)
        result = apply_role_change(actor=self.boss, target=django_admin,
                                   new_role='user', reason='role cleanup')
        django_admin.refresh_from_db()
        self.assertTrue(result.changed)
        self.assertEqual(django_admin.profile.role, 'user')
        self.assertTrue(django_admin.is_superuser)
        self.assertIn('Django superuser', result.message)
        log = AdminLog.objects.filter(action='set_role').first()
        self.assertIn('django-superuser kept', log.target)

    def test_audit_row_carries_actor_change_and_reason(self):
        self.apply('moderator', reason='moderating reports', confirm='kwame')
        log = AdminLog.objects.get(action='set_role')
        self.assertEqual(log.actor, self.boss)
        self.assertEqual(log.target, '@kwame: user→moderator — moderating reports')

    def test_same_role_is_a_no_op_with_no_audit_row(self):
        result = self.apply('user', reason='nothing to do here')
        self.assertFalse(result.changed)
        self.assertIn('already', result.message)
        self.assertFalse(AdminLog.objects.exists())

    def test_self_change_is_refused_and_recorded(self):
        result = apply_role_change(actor=self.boss, target=self.boss,
                                   new_role='admin', reason='stepping down')
        self.assertFalse(result.changed)
        self.boss.refresh_from_db()
        self.assertEqual(self.boss.profile.role, 'superadmin')
        self.assertTrue(AdminLog.objects.filter(action='set_role_denied').exists())

    def test_last_superadmin_cannot_be_demoted(self):
        """The concurrent case: the pre-check saw two superadmins, the locked
        re-check sees one — the write must stop."""
        other = make_user('second', role='superadmin')
        self.assertEqual(superadmin_count(), 2)
        with patch('users.roles.superadmin_count', return_value=1):
            result = apply_role_change(actor=self.boss, target=other,
                                       new_role='user', reason='cleanup')
        self.assertFalse(result.changed)
        other.refresh_from_db()
        self.assertEqual(other.profile.role, 'superadmin')
        self.assertIn('only Super Admin', result.message)

    def test_unknown_role_is_refused_and_recorded(self):
        result = self.apply('wizard', reason='typo in a crafted POST')
        self.assertFalse(result.changed)
        self.assertEqual(self.person.profile.role, 'user')
        self.assertTrue(AdminLog.objects.filter(action='set_role_denied').exists())

    def test_a_non_superadmin_cannot_change_roles(self):
        """Defense in depth: the guard is in the engine, not only the view."""
        helper = make_user('adminish', role='admin')
        result = apply_role_change(actor=helper, target=self.person,
                                   new_role='admin', reason='lateral move',
                                   confirm='kwame')
        self.assertFalse(result.changed)
        self.person.refresh_from_db()
        self.assertEqual(self.person.profile.role, 'user')

    def test_notification_and_email_survive_a_broken_mail_server(self):
        with patch('users.emails.send_generic_email', side_effect=RuntimeError('smtp down')):
            with self.captureOnCommitCallbacks(execute=True):
                result = apply_role_change(actor=self.boss, target=self.person,
                                           new_role='moderator',
                                           reason='reports queue', confirm='kwame')
        self.assertTrue(result.changed)  # the change stands, the mail was best effort
        self.assertEqual(Notification.objects.filter(user=self.person, kind='role').count(), 1)


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False, EMAIL_BACKEND=LOCMEM)
class ManageRolesViewTests(TestCase):
    def setUp(self):
        self.boss = make_user('nolo.ai', role='superadmin')
        self.mod = make_user('sbu', role='moderator')
        self.plain = make_user('thando')
        self.plain2 = make_user('thandi')  # two people share the prefix 'thand'
        self.client.force_login(self.boss)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        response = self.client.get(reverse('manage_roles'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_admin_role_is_forbidden(self):
        self.client.force_login(make_user('blaq', role='admin'))
        self.assertEqual(self.client.get(reverse('manage_roles')).status_code, 403)

    def test_search_finds_the_person(self):
        response = self.client.get(reverse('manage_roles'), {'q': 'thand'})
        self.assertEqual(response.status_code, 200)          # two matches → list
        self.assertContains(response, '@thando')
        self.assertContains(response, '@thandi')
        self.assertContains(response, 'Change role')

    def test_exact_match_goes_straight_to_the_person(self):
        response = self.client.get(reverse('manage_roles'), {'q': '@thando'})
        self.assertRedirects(response, reverse('manage_user_role', args=['thando']))

    def test_landing_page_never_lists_the_whole_network(self):
        response = self.client.get(reverse('manage_roles'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '@thando')   # 1 of 10,000 ordinary users
        self.assertContains(response, '@sbu')         # elevated access is listed
        self.assertContains(response, 'Roles in the network')

    def test_role_filter_lists_that_role_only(self):
        response = self.client.get(reverse('manage_roles'), {'role': 'moderator'})
        self.assertContains(response, '@sbu')
        self.assertNotContains(response, '@thando')

    def test_unknown_role_filter_falls_back_to_the_landing_page(self):
        response = self.client.get(reverse('manage_roles'), {'role': 'wizard'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Roles in the network')

    def test_no_match_explains_itself(self):
        response = self.client.get(reverse('manage_roles'), {'q': 'nobodyhere'})
        self.assertContains(response, 'No account matches')

    def test_detail_page_shows_the_account_state(self):
        response = self.client.get(reverse('manage_user_role', args=['thando']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'thando@example.com')
        self.assertContains(response, 'Why is this changing?')

    def test_unknown_person_is_a_404_not_a_role_error(self):
        """A deleted username must not render 'It's not you, it's me' — the
        superadmin did nothing wrong, the account simply is not there."""
        self.assertEqual(
            self.client.get(reverse('manage_user_role', args=['ghost'])).status_code, 404)

    def test_post_promotes_and_redirects_to_the_person(self):
        response = self.client.post(reverse('manage_user_role', args=['thando']), {
            'role': 'moderator', 'reason': 'runs the reports queue', 'confirm': 'thando',
        })
        self.assertRedirects(response, reverse('manage_user_role', args=['thando']))
        self.plain.refresh_from_db()
        self.assertEqual(self.plain.profile.role, 'moderator')

    def test_refused_post_re_renders_with_the_reason_intact(self):
        response = self.client.post(reverse('manage_user_role', args=['thando']), {
            'role': 'moderator', 'reason': 'runs the reports queue',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'runs the reports queue')
        self.plain.refresh_from_db()
        self.assertEqual(self.plain.profile.role, 'user')

    def test_old_set_role_url_still_resolves(self):
        url = reverse('set_role', args=['thando'])
        self.assertEqual(url, '/admin/roles/thando/set/')
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_own_page_refuses_the_form(self):
        response = self.client.get(reverse('manage_user_role', args=['nolo.ai']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'This is <b>you</b>')
        self.assertContains(response, 'cannot change your own role')

    def test_non_superadmin_cannot_post(self):
        self.client.force_login(make_user('blaq', role='admin'))
        response = self.client.post(reverse('manage_user_role', args=['thando']), {
            'role': 'admin', 'reason': 'lateral', 'confirm': 'thando',
        })
        self.assertEqual(response.status_code, 403)
        self.plain.refresh_from_db()
        self.assertEqual(self.plain.profile.role, 'user')
