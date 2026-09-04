"""Tests for the static-site live runner (gallery/runner.py + run_static view).

The runner extends the proven snippet-preview model: user code runs ONLY
inside the sandboxed, opaque-origin iframe, behind the same signed token,
with the same CSP. These tests pin the two promises that keep it honest:
  * detection never claims a run it cannot deliver (build/source ZIPs stay
    'files');
  * the served document never leaks our origin (no per-file serving, valid
    token required, sandbox CSP present).
"""
import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from gallery.models import AppProject
from gallery.runner import assemble_runnable_document, detect_static_runnable
from gallery.taxonomy import RUNNABLE_PREVIEW_MODES, preview_mode_for

from .tests import make_category, make_project, make_user

def _zip(files, name='site.zip'):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for path, content in files.items():
            if isinstance(content, str):
                content = content.encode('utf-8')
            zf.writestr(path, content)
    return SimpleUploadedFile(name, buf.getvalue(), content_type='application/zip')

_PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 24

class DetectStaticRunnableTests(TestCase):
    def test_root_index_is_runnable(self):
        runnable, entry = detect_static_runnable(['index.html', 'style.css', 'app.js'])
        self.assertTrue(runnable)
        self.assertEqual(entry, 'index.html')

    def test_nested_index_when_no_root(self):
        runnable, entry = detect_static_runnable(['public/index.html', 'public/a.css'])
        self.assertTrue(runnable)
        self.assertEqual(entry, 'public/index.html')

    def test_shallowest_html_when_no_index(self):
        runnable, entry = detect_static_runnable(['docs/deep/x.html', 'home.html'])
        self.assertTrue(runnable)
        self.assertEqual(entry, 'home.html')

    def test_build_marker_is_not_runnable(self):
        # package.json means "needs a build" — rendering src would be a fake preview.
        self.assertEqual(detect_static_runnable(['index.html', 'package.json', 'src/App.jsx']), (False, ''))

    def test_python_server_is_not_runnable(self):
        self.assertEqual(detect_static_runnable(['index.html', 'manage.py', 'app/views.py']), (False, ''))

    def test_source_heavy_tree_is_not_runnable(self):
        # Mostly .jsx/.ts → source, not a finished static site.
        paths = ['index.html', 'a.jsx', 'b.jsx', 'c.tsx', 'd.ts']
        self.assertEqual(detect_static_runnable(paths), (False, ''))

    def test_no_html_is_not_runnable(self):
        self.assertEqual(detect_static_runnable(['a.js', 'b.css']), (False, ''))

    def test_empty_is_not_runnable(self):
        self.assertEqual(detect_static_runnable([]), (False, ''))

    def test_game_export_is_runnable(self):
        runnable, entry = detect_static_runnable(['index.html', 'game.js', 'assets/sprite.png'])
        self.assertTrue(runnable)
        self.assertEqual(entry, 'index.html')

@override_settings(MEDIA_ROOT='/tmp/blaqvibes-runner-tests')
class AssembleDocumentTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('runnerowner')

    def _project_with_zip(self, files):
        p = make_project(self.owner, self.cat, title='Static site')
        p.zip_file.save('site.zip', _zip(files), save=True)
        return p

    def test_inlines_local_css_js_images(self):
        p = self._project_with_zip({
            'index.html': (
                '<html><head><link rel="stylesheet" href="style.css"></head>'
                '<body><h1>Hi</h1><img src="logo.png">'
                '<script src="app.js"></script></body></html>'
            ),
            'style.css': 'h1{color:tomato}',
            'app.js': 'console.log("live")',
            'logo.png': _PNG,
        })
        doc = assemble_runnable_document(p.zip_file, 'index.html')
        self.assertIn('<style', doc)
        self.assertIn('color:tomato', doc)
        self.assertIn('console.log("live")', doc)
        self.assertIn('data:image/png;base64,', doc)
        # No file is referenced by its archive path any more — nothing to fetch.
        self.assertNotIn('href="style.css"', doc)
        self.assertNotIn('src="app.js"', doc)
        self.assertNotIn('src="logo.png"', doc)

    def test_remote_refs_are_left_untouched(self):
        p = self._project_with_zip({
            'index.html': (
                '<html><head><link rel="stylesheet" href="https://cdn.example/x.css">'
                '<script src="https://cdn.tailwindcss.com"></script></head>'
                '<body>hi</body></html>'
            ),
        })
        doc = assemble_runnable_document(p.zip_file, 'index.html')
        self.assertIn('https://cdn.example/x.css', doc)
        self.assertIn('https://cdn.tailwindcss.com', doc)

    def test_script_close_tag_cannot_break_out(self):
        # A user script containing </script> must not terminate our <script>.
        p = self._project_with_zip({
            'index.html': '<html><body><script src="a.js"></script></body></html>',
            'a.js': 'var x = "</script><script>alert(1)</script>";',
        })
        doc = assemble_runnable_document(p.zip_file, 'index.html')
        self.assertNotIn('</script><script>alert(1)', doc)

    def test_missing_entry_returns_empty(self):
        p = self._project_with_zip({'index.html': '<html></html>'})
        self.assertEqual(assemble_runnable_document(p.zip_file, 'nope.html'), '')

    def test_module_type_preserved(self):
        p = self._project_with_zip({
            'index.html': '<html><body><script type="module" src="m.js"></script></body></html>',
            'm.js': 'export const a = 1;',
        })
        doc = assemble_runnable_document(p.zip_file, 'index.html')
        self.assertIn('type="module"', doc)
        self.assertIn('export const a = 1;', doc)

@override_settings(MEDIA_ROOT='/tmp/blaqvibes-runner-tests')
class PreviewModeTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('modeowner')

    def test_static_zip_mode_from_classifier(self):
        from gallery.classify import classify_project
        p = make_project(self.owner, self.cat, title='Static')
        p.zip_file.save('s.zip', _zip({'index.html': '<html><body>x</body></html>', 'a.css': 'body{}'}), save=True)
        # AppFile rows are what _project_paths reads first.
        from gallery.models import AppFile
        AppFile.objects.create(project=p, path='index.html', size=10)
        AppFile.objects.create(project=p, path='a.css', size=5)
        classify_project(p, allow_llm=False)
        p.refresh_from_db()
        self.assertEqual(p.preview_mode, 'static_zip')
        self.assertEqual(p.static_entry, 'index.html')
        self.assertTrue(p.can_run_preview)
        self.assertIn(p.preview_mode, RUNNABLE_PREVIEW_MODES)

    def test_source_zip_stays_files(self):
        from gallery.classify import classify_project
        from gallery.models import AppFile
        p = make_project(self.owner, self.cat, title='Source')
        p.zip_file.save('src.zip', _zip({'package.json': '{}', 'src/App.jsx': 'x'}), save=True)
        AppFile.objects.create(project=p, path='package.json', size=2)
        AppFile.objects.create(project=p, path='src/App.jsx', size=1)
        classify_project(p, allow_llm=False)
        p.refresh_from_db()
        self.assertEqual(p.preview_mode, 'files')
        self.assertEqual(p.static_entry, '')
        self.assertFalse(p.can_run_preview)

    def test_snippet_still_wins(self):
        self.assertEqual(preview_mode_for('web_app', has_html=True, has_zip=True, static_runnable=True), 'snippet')

@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-runner-tests')
class RunStaticViewTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('viewowner')
        self.p = make_project(self.owner, self.cat, title='Runnable site', slug='runnable-site')
        self.p.zip_file.save('r.zip', _zip({
            'index.html': '<html><body><h1>Live</h1><script src="a.js"></script></body></html>',
            'a.js': 'console.log(1)',
        }), save=True)
        AppProject.objects.filter(pk=self.p.pk).update(
            preview_mode='static_zip', static_entry='index.html')
        self.p.refresh_from_db()

    def _token(self):
        from gallery.preview_token import issue_snippet_token
        return issue_snippet_token(self.p.slug)

    def test_requires_token(self):
        resp = self.client.get(f'/app/{self.p.slug}/run-static/')
        self.assertEqual(resp.status_code, 403)

    def test_framed_with_token_serves_assembled_doc(self):
        resp = self.client.get(
            f'/app/{self.p.slug}/run-static/?t={self._token()}',
            HTTP_SEC_FETCH_DEST='iframe',
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('Live', body)
        self.assertIn('console.log(1)', body)   # js inlined
        self.assertNotIn('src="a.js"', body)     # not served per-file

    def test_sandbox_csp_present(self):
        resp = self.client.get(
            f'/app/{self.p.slug}/run-static/?t={self._token()}',
            HTTP_SEC_FETCH_DEST='iframe',
        )
        csp = resp['Content-Security-Policy']
        self.assertIn('sandbox allow-scripts', csp)
        self.assertIn("default-src 'none'", csp)

    def test_document_dest_is_refused_even_with_token(self):
        # Opening the URL as a top-level document must never run user JS first-party.
        resp = self.client.get(
            f'/app/{self.p.slug}/run-static/?t={self._token()}',
            HTTP_SEC_FETCH_DEST='document',
        )
        self.assertEqual(resp.status_code, 403)

    def test_non_static_zip_is_404(self):
        AppProject.objects.filter(pk=self.p.pk).update(preview_mode='files', static_entry='')
        resp = self.client.get(
            f'/app/{self.p.slug}/run-static/?t={self._token()}',
            HTTP_SEC_FETCH_DEST='iframe',
        )
        self.assertEqual(resp.status_code, 404)

    def test_preview_shell_points_iframe_at_run_static(self):
        resp = self.client.get(f'/app/{self.p.slug}/preview/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('/run-static/', resp.content.decode())
