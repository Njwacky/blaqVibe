"""gallery/ziputil.py — the storage-API branch only real deploys take.

The suite runs on FileSystemStorage, where ziputil._local_path() finds a
filesystem path and never touches the FieldFile. Every S3/R2-backed deploy
takes the other branch, and that branch is where publishing used to die: it
streamed the archive through the storage API and closed the FieldFile on the
way out, while AppProject.save() was still holding that same unsaved upload
in order to write it. These tests pin the fixed behaviour by putting a
storage backend with no path() in front of the real code.
"""
import io

from django.core.files.base import File
from django.core.files.storage import Storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from gallery.models import AppProject
from gallery.tests import make_category, make_user, make_zip_bytes, make_zip_file


class InMemoryRemoteStorage(Storage):
    """An object-store stand-in: bytes live in a dict, never at a path.

    Storage.path() raising NotImplementedError is what sends
    ziputil._local_path() down the storage-API branch — the branch every
    S3/R2-backed deploy runs. Keeping the bytes in a dict lets a test assert
    exactly what landed in "the bucket" without touching a real one.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.blobs = {}

    def path(self, name):
        raise NotImplementedError('this backend has no path()')

    def _open(self, name, mode='rb'):
        return File(io.BytesIO(self.blobs[name]), name)

    def _save(self, name, content):
        name = name.replace('\\', '/')
        self.blobs[name] = content.read()
        return name

    def exists(self, name):
        return name in self.blobs

    def size(self, name):
        return len(self.blobs[name])

    def url(self, name):
        return f'/remote-media/{name}'

    def delete(self, name):
        self.blobs.pop(name, None)


@override_settings(
    RATELIMIT_ENABLE=False,
    STORAGES={'default': {'BACKEND': 'gallery.test_ziputil.InMemoryRemoteStorage'}},
)
class RemoteStorageZipAccessTests(TestCase):
    """Reading a ZIP through the storage API must not consume the upload."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('zipowner')

    def _unsaved_project(self, files):
        project = AppProject(
            owner=self.owner,
            category=self.cat,
            title='Remote Zip',
            short_description='A vibe saved onto storage without a path.',
            status='pending',
        )
        project.zip_file = SimpleUploadedFile(
            'app.zip', make_zip_bytes(files), content_type='application/zip')
        return project

    def test_reading_the_zip_for_language_stats_leaves_the_upload_writable(self):
        """The regression: publish 500'd because the write read a closed file.

        AppProject.save() reads the archive for language stats first and then
        writes the same, still unsaved upload through storage.save(). On a
        backend with no path() the read used to close the FieldFile, so the
        write raised "ValueError: I/O operation on closed file" out of an
        unguarded super().save() — a bare 500 on POST /publish/ and on
        POST /import/github/ alike.
        """
        payload = make_zip_bytes({'main.py': 'print("hello")\n'})
        project = self._unsaved_project({'main.py': 'print("hello")\n'})

        project.save()  # used to raise ValueError

        self.assertEqual(project.language_stats, {'Python': 100})
        self.assertEqual(project.zip_file.size, len(payload))
        self.assertEqual(project.zip_file.storage.blobs[project.zip_file.name], payload)

    def test_open_zip_leaves_the_field_file_open_and_rewound(self):
        """Unit level: the caller may keep reading after the archive closes."""
        from gallery.ziputil import open_zip

        payload = make_zip_bytes({'app.py': 'x = 1\n'})
        project = self._unsaved_project({'app.py': 'x = 1\n'})

        with open_zip(project.zip_file) as zf:
            self.assertEqual(zf.namelist(), ['app.py'])

        self.assertFalse(project.zip_file.closed)
        self.assertEqual(project.zip_file.read(), payload)

    def test_open_zip_still_reads_a_saved_remote_file(self):
        """The seek-0 contract must not break post-save readers (build_tree)."""
        from gallery.ziputil import open_zip

        project = self._unsaved_project({'requirements.txt': 'django\n'})
        project.zip_file.save('saved.zip', make_zip_file({'requirements.txt': 'django\n'}), save=True)

        with open_zip(project.zip_file) as zf:
            self.assertEqual(zf.namelist(), ['requirements.txt'])
        with open_zip(project.zip_file) as zf:
            self.assertIn('requirements.txt', zf.namelist())
        self.assertEqual(project.zip_file.read(), make_zip_bytes({'requirements.txt': 'django\n'}))

    def test_broken_archive_still_reports_no_languages(self):
        """A corrupt upload is a quiet empty result, never an exception."""
        from gallery.language import detect_languages_from_field

        project = self._unsaved_project({'main.py': 'print(1)\n'})
        project.zip_file = SimpleUploadedFile(
            'broken.zip', b'this is not a zip archive', content_type='application/zip')

        self.assertEqual(detect_languages_from_field(project.zip_file), {})
        self.assertFalse(project.zip_file.closed)
