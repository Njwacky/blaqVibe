"""DATABASE_URL handling — the difference between a durable deploy and an empty one.

Three behaviours are pinned here, each one a failure that has actually bitten a
deployment:

1. The query string of DATABASE_URL is OBEYED (it used to be dropped whole, so
   `?sslmode=require` silently became libpq's `prefer`).
2. A URL copied out of a hosting dashboard never crashes the boot: parameters
   meant for other clients are dropped, and near-miss sslmode spellings are
   mapped onto real libpq modes.
3. `security_check` treats "production on SQLite" as an ERROR, because on a
   container host that combination deletes the database on every deploy.
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from blaqvibes import settings as project_settings
from blaqvibes.settings import _db_from_url, _pg_options_from_query

SUPABASE_URL = (
    'postgresql://postgres.abcdefghijkl:secret@aws-0-eu-west-1.pooler.supabase.com'
    ':5432/postgres?sslmode=require'
)


class QueryStringParsingTests(SimpleTestCase):
    """`_pg_options_from_query` — strict, and never fatal."""

    def test_libpq_parameters_are_passed_through(self):
        options = _pg_options_from_query('sslmode=require&connect_timeout=10&application_name=blaqvibes')
        self.assertEqual(options['sslmode'], 'require')
        self.assertEqual(options['connect_timeout'], '10')
        self.assertEqual(options['application_name'], 'blaqvibes')

    def test_unknown_parameters_are_dropped_not_forwarded(self):
        """psycopg aborts the connection on an unknown option — a pasted URL
        must not be able to take the process down at boot."""
        options = _pg_options_from_query('sslmode=require&wat=1&foo=bar')
        self.assertEqual(options, {'sslmode': 'require'})

    def test_provider_hints_are_dropped(self):
        """Supabase appends `supa=`; Prisma-style URLs carry `pgbouncer=true`."""
        options = _pg_options_from_query('sslmode=require&supa=base-pooler.x&pgbouncer=true')
        self.assertEqual(options, {'sslmode': 'require'})

    def test_ssl_true_becomes_sslmode_require(self):
        self.assertEqual(_pg_options_from_query('ssl=true')['sslmode'], 'require')
        self.assertEqual(_pg_options_from_query('ssl=1')['sslmode'], 'require')

    def test_explicit_sslmode_beats_the_ssl_shorthand(self):
        options = _pg_options_from_query('ssl=false&sslmode=require')
        self.assertEqual(options['sslmode'], 'require')

    def test_near_miss_sslmode_spellings_are_mapped(self):
        self.assertEqual(_pg_options_from_query('sslmode=no-verify')['sslmode'], 'require')
        self.assertEqual(_pg_options_from_query('sslmode=verify-full')['sslmode'], 'verify-full')

    def test_garbage_sslmode_is_ignored_rather_than_fatal(self):
        self.assertEqual(_pg_options_from_query('sslmode=sort-of'), {})

    def test_authority_cannot_be_overridden_from_the_query(self):
        """dbname/user/password/host/port come from the URL's authority only."""
        options = _pg_options_from_query('dbname=other&user=root&host=evil.example&port=1')
        self.assertEqual(options, {})

    def test_blank_and_malformed_query_strings_are_survivable(self):
        self.assertEqual(_pg_options_from_query(''), {})
        self.assertEqual(_pg_options_from_query('&&&'), {})
        self.assertEqual(_pg_options_from_query(None), {})


class DatabaseUrlTests(SimpleTestCase):
    """`_db_from_url` — engine, authority and options."""

    def test_postgres_url_parses_authority_and_options(self):
        config = _db_from_url(SUPABASE_URL)
        self.assertEqual(config['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(config['HOST'], 'aws-0-eu-west-1.pooler.supabase.com')
        self.assertEqual(config['PORT'], '5432')
        self.assertEqual(config['NAME'], 'postgres')
        # Supabase's pooler username is `postgres.<project-ref>` — one token.
        self.assertEqual(config['USER'], 'postgres.abcdefghijkl')
        self.assertEqual(config['PASSWORD'], 'secret')
        self.assertEqual(config['OPTIONS']['sslmode'], 'require')

    def test_a_url_without_a_query_string_gains_no_options(self):
        config = _db_from_url('postgresql://u:p@db.example.com:5432/blaqvibes')
        self.assertNotIn('OPTIONS', config)

    def test_url_encoded_password_is_decoded(self):
        config = _db_from_url('postgresql://postgres.ab:p%40ss%2Fword@host:5432/postgres')
        self.assertEqual(config['PASSWORD'], 'p@ss/word')

    def test_db_sslmode_applies_to_a_remote_host(self):
        with mock.patch.object(project_settings, 'DB_SSLMODE', 'require'):
            config = _db_from_url('postgresql://u:p@db.example.com:5432/blaqvibes')
        self.assertEqual(config['OPTIONS']['sslmode'], 'require')

    def test_db_sslmode_never_forces_tls_on_a_local_or_compose_host(self):
        for host in ('localhost', '127.0.0.1', 'db'):
            with mock.patch.object(project_settings, 'DB_SSLMODE', 'require'):
                config = _db_from_url(f'postgresql://u:p@{host}:5432/blaqvibes')
            self.assertNotIn('OPTIONS', config, host)

    def test_url_sslmode_wins_over_the_env_override(self):
        with mock.patch.object(project_settings, 'DB_SSLMODE', 'verify-full'):
            config = _db_from_url(SUPABASE_URL)
        self.assertEqual(config['OPTIONS']['sslmode'], 'require')

    def test_sqlite_urls_keep_their_path_semantics(self):
        """Relative stays relative, absolute stays absolute — an eaten leading
        slash would silently point Django at a different file."""
        self.assertEqual(
            _db_from_url('sqlite:///db.sqlite3')['NAME'], 'db.sqlite3')
        self.assertEqual(
            _db_from_url('sqlite:////srv/data/db.sqlite3')['NAME'], '/srv/data/db.sqlite3')
        self.assertEqual(
            _db_from_url('sqlite:///:memory:')['NAME'], ':memory:')
        self.assertEqual(
            _db_from_url('sqlite:///db.sqlite3')['ENGINE'], 'django.db.backends.sqlite3')


class SecurityCheckDatabasePostureTests(SimpleTestCase):
    """`manage.py security_check` must refuse to bless an ephemeral database."""

    def _findings(self, production=True):
        from gallery.management.commands.security_check import Command
        errors, warnings = Command().collect(production=production)
        return errors, warnings

    def test_production_on_sqlite_is_an_error(self):
        errors, _warnings = self._findings()
        self.assertTrue(
            any('the database is SQLite' in item for item in errors),
            f'expected a SQLite data-loss finding, got: {errors}',
        )

    def test_the_error_names_the_consequence_and_the_fix(self):
        errors, _warnings = self._findings()
        finding = next(item for item in errors if 'the database is SQLite' in item)
        self.assertIn('EMPTY', finding)
        self.assertIn('DATABASE_URL', finding)

    def test_a_managed_postgres_url_clears_the_finding(self):
        with override_settings(DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': 'postgres', 'USER': 'u', 'PASSWORD': 'p',
                'HOST': 'aws-0-eu-west-1.pooler.supabase.com', 'PORT': '5432',
            },
        }):
            errors, _warnings = self._findings()
        self.assertFalse(any('SQLite' in item for item in errors), errors)

    def test_acknowledged_sqlite_downgrades_to_a_warning(self):
        """An operator running SQLite on a persistent volume can say so once."""
        with mock.patch.dict('os.environ', {'DB_SQLITE_ACK': '1'}):
            errors, warnings = self._findings()
        self.assertFalse(any('the database is SQLite' in item for item in errors))
        self.assertTrue(any('persistent volume' in item for item in warnings), warnings)

    def test_a_dev_posture_says_nothing_about_sqlite(self):
        """SQLite on a laptop is the normal way to run this app — nagging
        about it every local run would train people to ignore the finding."""
        errors, warnings = self._findings(production=False)
        self.assertFalse(any('the database is SQLite' in item for item in errors))
        self.assertFalse(any('the database is SQLite' in item for item in warnings))


class DbCheckCommandTests(TestCase):
    """`manage.py dbcheck` — the one command an operator runs after switching."""

    def test_it_reports_the_engine_and_does_not_crash_on_sqlite(self):
        from io import StringIO
        from django.core.management import call_command

        out = StringIO()
        call_command('dbcheck', stdout=out)
        body = out.getvalue()
        self.assertIn('engine', body)
        self.assertIn('sqlite3', body)
        self.assertIn('migrations', body)

    def test_it_never_prints_the_password(self):
        from io import StringIO
        from django.core.management import call_command

        with override_settings(DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'NAME': 'postgres', 'USER': 'postgres.abcdefghijkl',
                'PASSWORD': 'super-secret-password',
                'HOST': 'aws-0-eu-west-1.pooler.supabase.com', 'PORT': '5432',
            },
        }):
            out = StringIO()
            try:
                call_command('dbcheck', stdout=out)
            except Exception:
                # No server at that host in CI — the connection error must
                # still not leak the password into the report.
                pass
        self.assertNotIn('super-secret-password', out.getvalue())
