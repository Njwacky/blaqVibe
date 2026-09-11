"""`python manage.py dbcheck` — where does this deployment's data actually live?

Written for one moment: the minute after somebody switches production from
SQLite to a managed Postgres (or pastes a Supabase URL and waits nervously).
It answers, without reading logs:

  * which engine am I on, and WHAT host/database/role — the three strings
    that are wrong 90% of the time when a fresh URL "connects but is empty";
  * is the connection actually ENCRYPTED (a libpq `sslmode` that silently
    fell back to plaintext is invisible from the app side — `pg_stat_ssl`
    answers it from the server side),
  * how many sessions am I holding against the server's limit (a free-tier
    pooler is the first thing a gunicorn + celery fleet exhausts),
  * how much data is in here, and are migrations applied.

Exit code: 0 when the database answers, 1 when it cannot be reached — so it
is usable as a deploy smoke test. It never prints credentials.
"""
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def _brief_version(raw):
    """'PostgreSQL 17.4 on aarch64-unknown-linux-gnu, compiled by gcc ...' → 'PostgreSQL 17.4'."""
    text = (raw or '').strip().splitlines()[0] if raw else ''
    parts = text.split()
    return ' '.join(parts[:2]) if parts else 'unknown'


class Command(BaseCommand):
    help = ('Report which database this process is really using: engine, host, TLS, '
            'session count, migration state and row counts.')

    def handle(self, *args, **options):
        from django.db import connection

        db = settings.DATABASES['default']
        engine = db.get('ENGINE', '')
        is_postgres = engine.endswith('postgresql')

        self.stdout.write(self.style.MIGRATE_HEADING('BlaqVibes database check'))
        self._row('engine', engine)
        if is_postgres:
            self._row('host', f"{db.get('HOST') or '?'}:{db.get('PORT') or 5432}")
            self._row('database', db.get('NAME') or '?')
            self._row('user', db.get('USER') or '?')
            opts = db.get('OPTIONS') or {}
            self._row('options', ', '.join(f'{k}={v}' for k, v in sorted(opts.items())) or '(none)')
            self._row('conn_max_age', db.get('CONN_MAX_AGE', 0))
        else:
            self._row('file', str(db.get('NAME') or '?'))
            try:
                size_mb = os.path.getsize(str(db.get('NAME'))) / (1024 * 1024)
                self._row('file size', f'{size_mb:.2f} MB')
            except OSError:
                self._row('file size', 'unknown (in-memory or not created yet)')

        # Connectivity first: everything below is best-effort decoration.
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
        except Exception as exc:
            raise CommandError(
                f'the database is NOT reachable: {type(exc).__name__}. '
                f'Check DATABASE_URL (host, password, and — on Supabase — that '
                f'you copied the pooler host, not the IPv6-only direct one).'
            )

        if is_postgres:
            self._row('server', self._scalar('SELECT version()', transform=_brief_version))
            self._row('tls', self._tls_line())
            self._row('sessions', self._sessions_line())
        else:
            self._row('server', self._scalar('SELECT sqlite_version()', transform=lambda v: f'SQLite {v}'))
            self._row('tls', 'n/a — a file, not a network connection')

        self._row('migrations', self._migrations_line())
        self._row('data', self._data_line())
        self._verdict(is_postgres)

    # -- helpers ----------------------------------------------------------
    def _row(self, label, value):
        self.stdout.write(f'  {label:<12} {value}')

    def _scalar(self, sql, transform=None, params=None):
        from django.db import connection
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params or [])
                row = cursor.fetchone()
            value = row[0] if row else None
            return transform(value) if transform else value
        except Exception:
            return 'unknown'

    def _tls_line(self):
        """Ask the SERVER whether this session is encrypted.

        Client-side settings can be wrong in the direction that still works
        (sslmode=prefer silently upgrades), so the honest answer comes from
        pg_stat_ssl for our own backend pid.
        """
        from django.db import connection
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT ssl, version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid()'
                )
                row = cursor.fetchone()
            if not row:
                return 'unknown (pg_stat_ssl has no row for this session)'
            ssl_on, version, cipher = row[0], row[1], row[2]
            if ssl_on:
                return f'yes — {version or "TLS"} / {cipher or "cipher n/a"}'
            return 'NO — this session is plaintext'
        except Exception:
            return 'unknown (pg_stat_ssl not readable)'

    def _sessions_line(self):
        from django.db import connection
        used = self._scalar(
            'SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()'
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute('SHOW max_connections')
                row = cursor.fetchone()
            limit = row[0] if row else '?'
        except Exception:
            limit = '?'
        return f'{used} in this database, server limit {limit}'

    def _migrations_line(self):
        """Applied vs pending, straight from Django's own migration graph."""
        from django.db import connection
        try:
            from django.db.migrations.executor import MigrationExecutor
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            pending = [migration for migration, backwards in plan if not backwards]
            applied = len(executor.loader.applied_migrations)
            if pending:
                names = ', '.join(f'{m.app_label}.{m.name}' for m in pending[:3])
                extra = '' if len(pending) <= 3 else f' (+{len(pending) - 3} more)'
                return f'{applied} applied, {len(pending)} PENDING — run `manage.py migrate` ({names}{extra})'
            return f'{applied} applied, 0 pending'
        except Exception:
            return 'unknown (run `manage.py migrate` first)'

    def _data_line(self):
        try:
            from django.contrib.auth import get_user_model
            from gallery.models import AppProject
            users = get_user_model().objects.count()
            published = AppProject.objects.filter(status='published').count()
            total = AppProject.objects.count()
            return f'{users} users, {total} projects ({published} published)'
        except Exception:
            return 'unknown'

    def _verdict(self, is_postgres):
        dev = bool(getattr(settings, 'DEBUG', False) or getattr(settings, 'LOCAL_DEV', False))
        if is_postgres:
            self.stdout.write(self.style.SUCCESS(
                '  verdict      ok — durable Postgres, reachable'
            ))
            return
        if dev:
            self.stdout.write(
                '  verdict      dev posture — SQLite is expected here'
            )
            return
        self.stdout.write(self.style.ERROR(
            '  verdict      WARNING — this is a FILE inside the container. On a host that '
            'replaces the filesystem on deploy (Render/Fly/Heroku/Cloud Run) all of this '
            'data is deleted on the next deploy. Set DATABASE_URL.'
        ))
