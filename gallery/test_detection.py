"""Regression suite for language + program-kind detection.

Pins the rules documented in docs/specs/BlaqVibes_Detection_Research.md:
the linguist-aligned exclusions in gallery/language.py, the dependency
fingerprints in gallery/kind_detect.py, and the LLM prompt carrying
heuristic markers in gallery/classify.py. Every test here corresponds to a
real-world misclassification that shipped before the rewrite.
"""
from django.test import TestCase, override_settings

from gallery.models import AppFile
from gallery.tests import make_category, make_project, make_user, make_zip_file


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class LanguageStatsLinguistRulesTests(TestCase):
    """gallery/language.py — the exclusions are what make the bar honest."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('langowner')

    def _stats(self, files):
        from gallery.language import detect_languages_from_field
        project = make_project(self.owner, self.cat)
        project.zip_file.save('x.zip', make_zip_file(files), save=True)
        return detect_languages_from_field(project.zip_file)

    def test_lockfile_cannot_rename_a_python_project(self):
        """A 1MB package-lock.json is generated data, not the project."""
        stats = self._stats({
            'app.py': 'print(1)\n' * 200,
            'src/util.py': 'x = 1\n' * 100,
            'package-lock.json': '{"lockfileVersion":3,"packages":' + '"a":{},' * 8000 + '}',
        })
        self.assertEqual(stats.get('Python'), 100)
        self.assertNotIn('JSON', stats)

    def test_node_modules_is_vendored_not_source(self):
        stats = self._stats({
            'app.py': 'print(1)\n' * 200,
            'node_modules/react/index.js': 'x' * 200000,
            'node_modules/lodash/lodash.min.js': 'x' * 140000,
        })
        self.assertEqual(stats.get('Python'), 100)

    def test_minified_bundle_and_docs_do_not_skew(self):
        stats = self._stats({
            'main.py': 'print(1)\n' * 300,
            'docs/guide.md': 'x' * 20000,
            'dist/bundle.min.js': 'x' * 60000,
        })
        self.assertEqual(stats.get('Python'), 100)

    def test_cpp_project_is_recognized(self):
        stats = self._stats({
            'src/main.cpp': 'int main(){}\n' * 400,
            'src/engine.h': '#pragma once\n' * 200,
            'CMakeLists.txt': 'cmake_minimum_required(VERSION 3.10)\n' * 20,
        })
        self.assertIn('C++', stats)
        self.assertGreaterEqual(stats['C++'], 50)

    def test_dart_project_is_recognized(self):
        stats = self._stats({'lib/main.dart': 'void main(){}\n' * 400})
        self.assertEqual(stats.get('Dart'), 100)

    def test_known_filenames_count_without_extensions(self):
        stats = self._stats({
            'Dockerfile': 'FROM python:3.12\n' * 30,
            'Makefile': 'all:\n\tpython build\n' * 30,
            'deploy.py': 'x' * 100,
        })
        self.assertIn('Dockerfile', stats)
        self.assertIn('Makefile', stats)
        self.assertIn('Python', stats)

    def test_shebang_detects_extensionless_scripts(self):
        stats = self._stats({'bin/deploy': '#!/usr/bin/env python3\nimport os\n' * 20})
        self.assertEqual(stats.get('Python'), 100)

    def test_data_only_project_falls_back_instead_of_empty(self):
        """A config-only zip still gets a bar — degrade, never a lie."""
        stats = self._stats({'config.json': 'x' * 500, 'schema.yaml': 'x' * 500})
        self.assertIn('JSON', stats)
        self.assertIn('YAML', stats)

    def test_github_wrapper_folder_is_stripped(self):
        """`repo-main/docs/…` must still count as documentation."""
        stats = self._stats({
            'myrepo-main/app.py': 'print(1)\n' * 200,
            'myrepo-main/docs/guide.md': 'x' * 20000,
        })
        self.assertEqual(stats.get('Python'), 100)

    def test_percentages_always_sum_to_one_hundred(self):
        stats = self._stats({
            'a.py': 'x' * 3000, 'b.js': 'x' * 1700, 'c.html': 'x' * 900,
            'd.css': 'x' * 400, 'e.go': 'x' * 250, 'f.rb': 'x' * 120,
        })
        self.assertEqual(sum(stats.values()), 100)


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class KindManifestFingerprintTests(TestCase):
    """gallery/kind_detect.py — dependencies are the honest signal."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('kindowner2')

    def _with_files(self, files, **kwargs):
        project = make_project(self.owner, self.cat, **kwargs)
        project.zip_file.save(
            f'{project.slug}.zip', make_zip_file(files), save=True
        )
        for p in files:
            AppFile.objects.create(project=project, path=p, size=100)
        return project

    def _kind(self, files, **kwargs):
        from gallery.kind_detect import detect_kind
        return detect_kind(self._with_files(files, **kwargs))

    # -- the misclassifications that shipped ---------------------------------

    def test_gradle_server_is_not_a_mobile_app(self):
        result = self._kind(
            {'build.gradle': '', 'settings.gradle': '',
             'src/main/java/Main.java': '',
             'src/main/resources/application.yml': ''},
            title='Inventory Service', readme='# Inventory Service\nA service.',
        )
        self.assertEqual(result['kind'], 'api_backend')

    def test_gradle_android_still_is_mobile(self):
        result = self._kind(
            {'app/build.gradle': '',
             'app/src/main/AndroidManifest.xml': '',
             'app/src/main/java/com/x/MainActivity.java': ''},
            title='Mzansi Weather',
        )
        self.assertEqual(result['kind'], 'mobile_app')

    def test_electron_dependency_means_desktop(self):
        result = self._kind(
            {'package.json': '{"name":"notesy","main":"main.js",'
                             '"devDependencies":{"electron":"^30.0.0"}}',
             'main.js': 'x'},
            title='Notesy', readme='# Notesy',
        )
        self.assertEqual(result['kind'], 'desktop_app')
        self.assertTrue(any('electron' in e for e in result['evidence']))

    def test_express_dependency_means_api(self):
        result = self._kind(
            {'package.json': '{"name":"todo-api","dependencies":{"express":"^4.18.0"}}',
             'server.js': 'x'},
            title='Todo API', readme='# Todo API',
        )
        self.assertEqual(result['kind'], 'api_backend')
        self.assertTrue(any('express' in e for e in result['evidence']))

    def test_next_dependency_means_web_app(self):
        result = self._kind(
            {'package.json': '{"name":"site","dependencies":'
                             '{"next":"^14.0.0","react":"^18.0.0"}}',
             'pages/index.js': 'x'},
            title='My Site', readme='# My Site',
        )
        self.assertEqual(result['kind'], 'web_app')

    def test_discord_dependency_means_bot(self):
        result = self._kind(
            {'package.json': '{"name":"modbot","dependencies":{"discord.js":"^14.0.0"}}',
             'index.js': 'x'},
            title='ModBot', readme='# ModBot',
        )
        self.assertEqual(result['kind'], 'bot')

    def test_streamlit_dependency_means_data_viz(self):
        result = self._kind(
            {'app.py': 'import streamlit as st\n',
             'requirements.txt': 'streamlit==1.30.0\npandas==2.1.0\n'},
            title='Sales dashboard', readme='# Sales',
        )
        self.assertEqual(result['kind'], 'data_viz')

    def test_torch_dependency_means_ai_ml(self):
        result = self._kind(
            {'train.py': 'import torch\n',
             'requirements.txt': 'torch\ntorchvision\n'},
            title='Churn model', readme='# Churn model',
        )
        self.assertEqual(result['kind'], 'ai_ml')

    def test_selenium_dependency_means_bot(self):
        result = self._kind(
            {'scraper.py': 'from selenium import webdriver\n',
             'requirements.txt': 'selenium\nbeautifulsoup4\n'},
            title='Price watcher', readme='# Price watcher',
        )
        self.assertEqual(result['kind'], 'bot')

    def test_pygame_dependency_means_game(self):
        result = self._kind(
            {'main.py': 'import pygame\n',
             'requirements.txt': 'pygame==2.5.0\n'},
            title='Sphinx dodger', readme='# Sphinx dodger',
        )
        self.assertEqual(result['kind'], 'game')

    def test_kivy_dependency_means_desktop(self):
        result = self._kind(
            {'main.py': 'from kivy.app import App\n',
             'requirements.txt': 'kivy==2.3.0\n'},
            title='Pomodoro timer', readme='# Pomodoro',
        )
        self.assertEqual(result['kind'], 'desktop_app')

    def test_flask_in_requirements_means_api(self):
        result = self._kind(
            {'app.py': 'from flask import Flask\n',
             'requirements.txt': 'flask==3.0.0\ngunicorn==21.2.0\n'},
            title='Notes API', readme='# Notes API',
        )
        self.assertEqual(result['kind'], 'api_backend')

    def test_cargo_axum_means_api(self):
        result = self._kind(
            {'Cargo.toml': '[dependencies]\naxum = "0.7"\ntokio = "1"\n',
             'src/main.rs': 'x'},
            title='shortr', readme='# shortr',
        )
        self.assertEqual(result['kind'], 'api_backend')

    def test_go_cobra_means_cli(self):
        result = self._kind(
            {'go.mod': 'module example.com/gtool\n\ngo 1.22\n\nrequire (\n'
                       '\tgithub.com/spf13/cobra v1.8.0\n)\n',
             'main.go': 'x', 'cmd/root.go': 'x'},
            title='gtool', readme='# gtool\nManage your git repos.',
        )
        self.assertEqual(result['kind'], 'cli_tool')

    def test_pubspec_flutter_section_means_mobile(self):
        result = self._kind(
            {'pubspec.yaml': 'name: weather\n\ndependencies:\n  flutter:\n    sdk: flutter\n',
             'lib/main.dart': 'x'},
            title='Mzansi Weather', readme='# Weather',
        )
        self.assertEqual(result['kind'], 'mobile_app')

    def test_vendored_manifest_cannot_decide_the_kind(self):
        """`node_modules/react/package.json` is a dependency, not a statement."""
        result = self._kind(
            {'app.py': 'from flask import Flask\n',
             'requirements.txt': 'flask==3.0.0\n',
             'node_modules/react/package.json': '{"name":"react"}'},
            title='Notes API', readme='# Notes API',
        )
        self.assertEqual(result['kind'], 'api_backend')

    def test_github_wrapper_does_not_hide_near_proof_files(self):
        result = self._kind(
            {'mygame-main/ProjectSettings/ProjectVersion.txt': '',
             'mygame-main/Assets/Scenes/SampleScene.unity': ''},
            title='MyGame', readme='# MyGame',
        )
        self.assertEqual(result['kind'], 'game')

    # -- the guards ----------------------------------------------------------

    def test_manifest_signal_tables_stay_in_the_taxonomy(self):
        """Producers may not invent kinds — same guard as the other tables."""
        from gallery.kind_detect import (
            _NPM_SIGNALS, _PYPI_SIGNALS, _CARGO_MARKERS, _GO_MARKERS,
            _COMPOSER_MARKERS, _GEMFILE_MARKERS, _PUBSPEC_MARKERS,
        )
        from gallery.taxonomy import KIND_VALUES
        for table in (_NPM_SIGNALS, _PYPI_SIGNALS):
            for _sig, (kind, _w) in table.items():
                self.assertIn(kind, KIND_VALUES, _sig)
        for markers in (_CARGO_MARKERS, _GO_MARKERS, _COMPOSER_MARKERS,
                        _GEMFILE_MARKERS, _PUBSPEC_MARKERS):
            for _m, kind, _w in markers:
                self.assertIn(kind, KIND_VALUES)

    def test_broken_manifest_is_ignored_not_fatal(self):
        """A corrupted package.json must not break the publish path."""
        result = self._kind(
            {'package.json': 'NOT JSON AT ALL {{{',
             'app.py': 'from flask import Flask\n',
             'requirements.txt': 'flask\n'},
            title='Notes API', readme='# Notes API',
        )
        self.assertEqual(result['kind'], 'api_backend')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class ClassifyPromptMarkerTests(TestCase):
    """gallery/classify.py — the LLM refines candidates, it never starts cold."""

    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('promptowner')

    def test_prompt_carries_heuristic_markers(self):
        from gallery.classify import _build_prompt
        project = make_project(self.owner, self.cat, title='Todo API')
        prompt = _build_prompt(project, {'evidence': ['package.json uses express']})
        self.assertIn('Detected markers:', prompt)
        self.assertIn('package.json uses express', prompt)

    def test_prompt_without_heuristic_still_renders(self):
        from gallery.classify import _build_prompt
        project = make_project(self.owner, self.cat, title='Some thing')
        prompt = _build_prompt(project, None)
        self.assertIn('Detected markers: none', prompt)
