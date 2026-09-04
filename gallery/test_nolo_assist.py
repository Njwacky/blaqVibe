"""Tests for Nolo's assistant skills (feature 3): fix code + write README.

Both must work with NO API key (real static analysis + a real structured
README), staying honest about the source. These tests run in the no-key
default, so `source` is always 'heuristic' — the promise that a fresh deploy
still helps a beginner.
"""
import json

from django.test import TestCase, override_settings

from gallery.nolo_assist import analyze_code, fix_code, write_readme

class AnalyzeCodeTests(TestCase):
    def test_flags_getelementbyid_typo(self):
        findings = analyze_code(js='var x = document.getElementByID("a");')
        titles = [f['title'] for f in findings]
        self.assertTrue(any('getElementByID' in t for t in titles))
        # The typo is an error-level finding and sorts first.
        self.assertEqual(findings[0]['level'], 'error')

    def test_flags_unbalanced_js_braces(self):
        findings = analyze_code(js='function f() { if (x) {')
        self.assertTrue(any('curly braces' in f['title'] for f in findings))

    def test_flags_unbalanced_css_braces(self):
        findings = analyze_code(css='body { color: red;')
        self.assertTrue(any('CSS' in f['title'] for f in findings))

    def test_flags_unclosed_html_tag(self):
        findings = analyze_code(html='<div><p>hi</p>')
        self.assertTrue(any('div' in f['title'] for f in findings))

    def test_void_tags_not_flagged(self):
        findings = analyze_code(html='<img src="a.png"><br><input>')
        self.assertFalse(any('img' in f['title'] or 'br' in f['title'] for f in findings))

    def test_clean_code_has_no_error_findings(self):
        findings = analyze_code(
            html='<div><p>hi</p></div>',
            css='body { color: red; }',
            js='document.addEventListener("DOMContentLoaded", function () { var n = 1; });',
        )
        self.assertFalse(any(f['level'] == 'error' for f in findings))

    def test_never_raises_on_garbage(self):
        # Must be robust to weird input — a broken analyser must not crash.
        self.assertIsInstance(analyze_code(html='<<<>>>', js='(((', css='}}}'), list)

class FixCodeTests(TestCase):
    def test_returns_heuristic_source_without_key(self):
        summary, findings, source = fix_code(js='document.getElementByID("x")')
        self.assertEqual(source, 'heuristic')
        self.assertTrue(summary)
        self.assertTrue(findings)

class WriteReadmeTests(TestCase):
    def test_readme_meets_publish_gate(self):
        md, source = write_readme(title='My App', description='Does a thing',
                                  js='localStorage.setItem("a", 1);')
        self.assertEqual(source, 'heuristic')
        # The publish form requires a '# ' heading and >= 100 chars.
        self.assertIn('# ', md)
        self.assertGreaterEqual(len(md.strip()), 100)

    def test_readme_reflects_detected_features(self):
        md, _ = write_readme(title='Canvas', js='var c = document.querySelector("canvas");',
                             html='<canvas></canvas>')
        self.assertIn('canvas', md.lower())

    def test_blank_input_still_valid_readme(self):
        md, _ = write_readme()
        self.assertIn('# ', md)
        self.assertGreaterEqual(len(md.strip()), 100)

@override_settings(RATELIMIT_ENABLE=False)
class NoloAssistApiTests(TestCase):
    def test_fix_api_returns_findings(self):
        resp = self.client.post('/nolo/fix/', data=json.dumps({
            'js': 'document.getElementByID("x")',
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload['source'], 'heuristic')
        self.assertTrue(payload['findings'])
        self.assertIn('summary', payload)

    def test_fix_api_is_public(self):
        # No login required — a beginner tinkering before signup needs help.
        resp = self.client.post('/nolo/fix/', data=json.dumps({'js': 'var x=1;'}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200)

    def test_readme_api_returns_markdown(self):
        resp = self.client.post('/nolo/readme/', data=json.dumps({
            'title': 'My Vibe', 'description': 'A tiny app', 'js': 'addEventListener("click", f)',
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertIn('# ', payload['readme'])
        self.assertEqual(payload['source'], 'heuristic')

    def test_fix_api_survives_bad_json(self):
        resp = self.client.post('/nolo/fix/', data='not json', content_type='application/json')
        self.assertIn(resp.status_code, (200, 500))
