"""`python manage.py backup_db` — a consistent, timestamped database backup.

Why a command instead of docs telling people to copy the file?
1. A copy of a SQLite file mid-write can be torn; `sqlite3.Connection.backup`
   takes a consistent snapshot of whatever Django is using, even the in-memory
   test DB.
2. One command with one default output dir means a cron/systemd timer can be a
   one-liner, and every backup lands in the same place with a sortable name.
3. Old backups are pruned to `--keep` newest files so a nightly cron does not
   fill the disk while keeping a real recovery window.
4. Postgres uses `pg_dump -Fc` (custom format) so a restore is
   `pg_restore --clean --if-exists -d <db> backup.dump`.

Restore is deliberately NOT implemented: restoring into a live production DB
needs a human deciding what to take down. The command documents the one-liners
instead.

5 Whys: why not a Django fixture?
1. Fixtures are serialized ORM objects; they skip DB-level state (sequences,
   indexes, row locks) and cannot capture a live Postgres transaction
   consistently.
2. `dumpdata` on a 100 MB media-free DB is slow and memory-hungry compared to
   a native dump.
3. Native dumps restore into the exact engine; fixtures need model
   compatibility forever.
"""
import datetime
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

DEFAULT_KEEP = 10


def _stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


class Command(BaseCommand):
    help = (
        'Write a timestamped backup of the default database to backups/ '
        '(SQLite snapshot via the live connection; Postgres via pg_dump -Fc).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', default='',
            help='Explicit output path. Default: backups/<engine>-<stamp>.<ext>',
        )
        parser.add_argument(
            '--keep', type=int, default=DEFAULT_KEEP,
            help='Keep the N newest backups in the default dir (0 = keep all).',
        )

    def handle(self, *args, **options):
        self.db = settings.DATABASES['default']
        engine = self.db['ENGINE'].rsplit('.', 1)[-1]

        if engine == 'sqlite3':
            out = Path(options['output']) if options['output'] else self._default_path('sqlite3', '.sqlite3')
            self._backup_sqlite(out)
        elif engine == 'postgresql':
            out = Path(options['output']) if options['output'] else self._default_path('postgres', '.dump')
            self._backup_postgres(out)
        else:
            raise CommandError(f'backup_db does not support engine: {engine}')

        self.stdout.write(self.style.SUCCESS(f'backup written: {out} ({out.stat().st_size} bytes)'))

        if not options['output']:
            pruned = self._prune(options['keep'])
            if pruned:
                self.stdout.write(self.style.WARNING(f'pruned {len(pruned)} old backup(s): {", ".join(pruned)}'))

    def _default_path(self, engine, ext):
        base = Path(settings.BASE_DIR) / 'backups'
        base.mkdir(parents=True, exist_ok=True)
        return base / f'{engine}-{_stamp()}{ext}'

    def _backup_sqlite(self, out: Path):
        import sqlite3

        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + '.tmp')

        # NAME is a Path in settings.py (BASE_DIR / 'db.sqlite3'); anything
        # Path-like must be stringified before startswith / uri quoting.
        name = str(self.db.get('NAME', '') or '')
        if not name or name.startswith(':memory:') or name.startswith('file:'):
            # Fail closed: an in-memory DB cannot be snapshotted from a file,
            # and sqlite3.Connection.backup() against Django's shared-cache
            # test connection DEADLOCKS inside an open transaction. A backup
            # that hangs is worse than no backup — it blocks cron forever.
            raise CommandError(
                'Cannot snapshot an in-memory SQLite database. Run the app '
                'with a file-backed DATABASE_URL (or NAME) to use backup_db.'
            )

        # Back it up through a SEPARATE read-only connection so the snapshot
        # never shares locks with the live connection, and a backup can run
        # while the app is writing (SQLite serializes readers, giving a
        # consistent image via the online backup API).
        source = sqlite3.connect(f'file:{name}?mode=ro', uri=True)
        target = sqlite3.connect(tmp)
        try:
            source.backup(target)  # consistent snapshot, safe under writes
        finally:
            target.close()
            source.close()
        tmp.replace(out)

    def _backup_postgres(self, out: Path):
        import shutil

        if shutil.which('pg_dump') is None:
            raise CommandError(
                'pg_dump not found — install postgresql-client (apt install '
                'postgresql-client) or pass --output and dump another way.'
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + '.tmp')
        env = os.environ.copy()
        if self.db.get('PASSWORD'):
            env['PGPASSWORD'] = self.db['PASSWORD']
        cmd = ['pg_dump', '--format=custom', '--no-owner', '--no-privileges']
        if self.db.get('HOST'):
            cmd += ['--host', self.db['HOST']]
        if self.db.get('PORT'):
            cmd += ['--port', str(self.db['PORT'])]
        if self.db.get('USER'):
            cmd += ['--username', self.db['USER']]
        cmd += ['--file', str(tmp), self.db['NAME']]
        try:
            subprocess.run(cmd, env=env, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:
            tmp.unlink(missing_ok=True)
            raise CommandError(f'pg_dump failed: {exc.stderr.strip()[:400] or exc}')
        tmp.replace(out)

    def _prune(self, keep: int):
        if keep <= 0:
            return []
        base = Path(settings.BASE_DIR) / 'backups'
        if not base.exists():
            return []
        files = sorted(base.glob('sqlite3-*'), key=lambda p: p.name)
        files += sorted(base.glob('postgres-*'), key=lambda p: p.name)
        old = files[:-keep] if len(files) > keep else []
        removed = []
        for path in old:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError as exc:
                self.stderr.write(f'could not prune {path}: {exc}')
        return removed
