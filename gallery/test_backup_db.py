"""Tests for `manage.py backup_db` (file-backed SQLite path).

The snapshot test uses a standalone SQLite file rather than Django's in-memory
test DB: the command deliberately refuses in-memory databases (backup against
Django's shared-cache connection deadlocks), so the production shape — a file
on disk — is exactly what gets exercised here.
"""
import sqlite3
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from gallery.management.commands.backup_db import Command


@override_settings(BASE_DIR=tempfile.gettempdir())
class BackupDbTests(SimpleTestCase):

    def _make_source_db(self, path: Path):
        conn = sqlite3.connect(path)
        conn.execute(
            'CREATE TABLE gallery_appproject (id INTEGER PRIMARY KEY, title TEXT NOT NULL)'
        )
        conn.execute("INSERT INTO gallery_appproject (title) VALUES ('A vibe')")
        conn.commit()
        conn.close()

    def _sqlite_settings(self, path: Path):
        return {'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': str(path),
        }}

    def test_sqlite_backup_is_a_restorable_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'source.sqlite3'
            self._make_source_db(src)
            out = Path(tmp) / 'snapshot.sqlite3'

            with override_settings(DATABASES=self._sqlite_settings(src)):
                call_command('backup_db', output=str(out), keep=0)

            self.assertTrue(out.exists())
            conn = sqlite3.connect(out)
            try:
                # The snapshot is a real, openable database containing our row.
                (ok,) = conn.execute('PRAGMA integrity_check').fetchone()
                self.assertEqual(ok, 'ok')
                (title,) = conn.execute(
                    'SELECT title FROM gallery_appproject'
                ).fetchone()
                self.assertEqual(title, 'A vibe')
            finally:
                conn.close()

    def test_refuses_in_memory_database_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'snapshot.sqlite3'
            with override_settings(DATABASES=self._sqlite_settings(':memory:')):
                with self.assertRaisesMessage(Exception, 'in-memory'):
                    call_command('backup_db', output=str(out), keep=0)
            self.assertFalse(out.exists())

    def test_prune_keeps_newest_N(self):
        base = Path(tempfile.gettempdir()) / 'backups'
        base.mkdir(parents=True, exist_ok=True)
        oldest = base / 'sqlite3-20200101T000000Z.sqlite3'
        oldest.touch()
        middle = base / 'sqlite3-20200102T000000Z.sqlite3'
        middle.touch()
        fresh = base / 'sqlite3-20260101T000000Z.sqlite3'
        fresh.touch()
        try:
            removed = Command()._prune(keep=1)
            self.assertIn(oldest.name, removed)
            self.assertIn(middle.name, removed)
            self.assertFalse(oldest.exists())
            self.assertFalse(middle.exists())
            self.assertTrue(fresh.exists(), 'newest backup should survive')
        finally:
            for f in base.glob('sqlite3-*'):
                f.unlink()
