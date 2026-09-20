"""Provider preference order for every LLM-backed feature.

The rule these tests pin: OpenRouter (or an OpenAI key) is consulted FIRST,
then Claude, Gemini, Groq, and finally the honest built-in helper. They also
pin the Render quirk — the production key lives under the name `openai_key`
and is an OpenRouter key (`sk-or-…`), so it must be routed to openrouter.ai,
not api.openai.com. No test here touches the network.
"""
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings

ALL_KEYS = (
    'OPENROUTER_API_KEY', 'OPENAI_API_KEY', 'OPENAI_KEY', 'openai_key',
    'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'GROQ_API_KEY',
)
NO_KEYS = {name: '' for name in ALL_KEYS if name.isupper()}


def _chat_response(text):
    fake = Mock()
    fake.raise_for_status = Mock()
    fake.json.return_value = {'choices': [{'message': {'role': 'assistant', 'content': text}}]}
    return fake


def _stub_project(**overrides):
    fields = {
        'title': 'Taxi rank queue', 'short_description': 'Take a number at the rank.',
        'tech_stack': 'Django', 'language_stats': {'Python': 80.0}, 'file_count': 7,
        'readme': '# Taxi rank queue\n\n' + ('Take a number, see your place in line. ' * 8),
        'file_tree': {'app/views.py': {}, 'requirements.txt': {}}, 'slug': 'taxi-rank-queue',
        'creator_kind': '', 'html_code': '', 'zip_file': None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class _EnvIsolation:
    """Strip every provider key from os.environ so a developer's shell cannot
    make these tests pass or fail by accident."""

    def setUp(self):
        super().setUp()
        self._saved = {k: os.environ.pop(k, None) for k in ALL_KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
        super().tearDown()


@override_settings(**NO_KEYS)
class ProviderResolutionTests(_EnvIsolation, SimpleTestCase):
    def test_no_keys_means_no_live_model_anywhere(self):
        from gallery.ai_providers import openai_compatible_config, openai_compatible_text, preferred_backend
        from gallery.classify import llm_available
        from gallery.nolo_ai import configured_ai_backend
        self.assertIsNone(openai_compatible_config())
        self.assertEqual(preferred_backend(), '')
        self.assertEqual(openai_compatible_text('hi'), '')
        self.assertEqual(configured_ai_backend(), 'heuristic')
        self.assertFalse(llm_available())

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render', OPENROUTER_MODEL='openai/gpt-4o-mini',
                       ANTHROPIC_API_KEY='sk-ant-x', GEMINI_API_KEY='g', GROQ_API_KEY='q')
    def test_openrouter_beats_every_other_key(self):
        from gallery.ai_providers import openai_compatible_config, preferred_backend
        from gallery.classify import llm_available
        from gallery.nolo_ai import configured_ai_backend
        config = openai_compatible_config()
        self.assertEqual(config['source'], 'openrouter')
        self.assertEqual(config['base_url'], 'https://openrouter.ai/api/v1')
        self.assertEqual(config['model'], 'openai/gpt-4o-mini')
        self.assertEqual(preferred_backend(), 'openrouter')
        self.assertEqual(configured_ai_backend(), 'openrouter')
        self.assertTrue(llm_available())

    @override_settings(OPENAI_API_KEY='sk-or-v1-added-as-openai-key')
    def test_openrouter_key_stored_under_openai_name_is_routed_to_openrouter(self):
        from gallery.ai_providers import openai_compatible_config
        config = openai_compatible_config()
        self.assertEqual(config['source'], 'openrouter')
        self.assertEqual(config['base_url'], 'https://openrouter.ai/api/v1')
        self.assertEqual(config['key'], 'sk-or-v1-added-as-openai-key')

    @override_settings(OPENAI_API_KEY='sk-proj-real-openai', OPENAI_MODEL='gpt-4o-mini')
    def test_real_openai_key_uses_openai(self):
        from gallery.ai_providers import openai_compatible_config
        from gallery.nolo_ai import configured_ai_backend
        config = openai_compatible_config()
        self.assertEqual(config['source'], 'openai')
        self.assertEqual(config['base_url'], 'https://api.openai.com/v1')
        self.assertEqual(config['model'], 'gpt-4o-mini')
        self.assertEqual(configured_ai_backend(), 'openai')

    @override_settings(OPENAI_API_KEY='sk-proj-real-openai', OPENROUTER_API_KEY='sk-or-v1-both')
    def test_openrouter_wins_when_both_are_set(self):
        from gallery.ai_providers import openai_compatible_config
        self.assertEqual(openai_compatible_config()['source'], 'openrouter')

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render', SITE_URL='https://blaqvibes.co.za')
    def test_openrouter_request_shape(self):
        from gallery.ai_providers import openai_compatible_text
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response('  Stars unlock ZIPs.  ')) as posted:
            text = openai_compatible_text('How do stars work?', system='Be brief.', max_output_tokens=99, temperature=0.2)
        self.assertEqual(text, 'Stars unlock ZIPs.')
        self.assertEqual(posted.call_args.args[0], 'https://openrouter.ai/api/v1/chat/completions')
        headers = posted.call_args.kwargs['headers']
        self.assertEqual(headers['Authorization'], 'Bearer sk-or-v1-render')
        self.assertEqual(headers['HTTP-Referer'], 'https://blaqvibes.co.za')
        self.assertEqual(headers['X-Title'], 'BlaqVibes Nolo')
        body = posted.call_args.kwargs['json']
        self.assertEqual(body['model'], 'openai/gpt-4o-mini')
        self.assertEqual(body['max_tokens'], 99)
        self.assertEqual(body['temperature'], 0.2)
        self.assertEqual(body['messages'][0], {'role': 'system', 'content': 'Be brief.'})
        self.assertEqual(body['messages'][1]['role'], 'user')
        self.assertIn('How do stars work?', body['messages'][1]['content'])

    @override_settings(OPENAI_API_KEY='sk-proj-real-openai')
    def test_openai_request_has_no_openrouter_attribution_headers(self):
        from gallery.ai_providers import openai_compatible_text
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response('ok')) as posted:
            openai_compatible_text('hi')
        self.assertEqual(posted.call_args.args[0], 'https://api.openai.com/v1/chat/completions')
        self.assertNotIn('HTTP-Referer', posted.call_args.kwargs['headers'])

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render')
    def test_content_parts_are_joined(self):
        from gallery.ai_providers import openai_compatible_text
        fake = _chat_response('')
        fake.json.return_value = {'choices': [{'message': {'content': [{'type': 'text', 'text': 'Hello '}, {'type': 'text', 'text': 'Durban'}]}}]}
        with patch('gallery.ai_providers.requests.post', return_value=fake):
            self.assertEqual(openai_compatible_text('hi'), 'Hello Durban')


@override_settings(**NO_KEYS)
class NoloChainOrderTests(_EnvIsolation, SimpleTestCase):
    # `requests` is one shared module, so a single patch of requests.post sees
    # the OpenRouter call AND the Claude call — the URL tells them apart.

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render', ANTHROPIC_API_KEY='sk-ant-test', GROQ_API_KEY='gsk')
    def test_nolo_answers_from_openrouter_before_claude(self):
        from gallery.nolo_ai import get_nolo_ai_answer
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response('Preview is an in-app page.')) as posted, \
                patch('gallery.ai_providers.groq_text') as groq:
            reply, source = get_nolo_ai_answer('What is preview files?')
        self.assertEqual(source, 'openrouter')
        self.assertIn('in-app page', reply)
        self.assertEqual(posted.call_count, 1)
        self.assertEqual(posted.call_args.args[0], 'https://openrouter.ai/api/v1/chat/completions')
        groq.assert_not_called()

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render', ANTHROPIC_API_KEY='sk-ant-test')
    def test_openrouter_failure_falls_through_to_claude(self):
        from gallery.nolo_ai import get_nolo_ai_answer
        claude_reply = Mock()
        claude_reply.raise_for_status = Mock()
        claude_reply.json.return_value = {'content': [{'type': 'text', 'text': 'Claude here.'}]}
        urls = []

        def by_url(url, **kwargs):
            urls.append(url)
            if 'openrouter.ai' in url:
                raise RuntimeError('502 from router')
            return claude_reply

        with patch('gallery.ai_providers.requests.post', side_effect=by_url):
            reply, source = get_nolo_ai_answer('Hello?')
        self.assertEqual(source, 'claude')
        self.assertEqual(reply, 'Claude here.')
        self.assertEqual(urls, ['https://openrouter.ai/api/v1/chat/completions', 'https://api.anthropic.com/v1/messages'])

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render', GROQ_API_KEY='gsk')
    def test_openrouter_empty_answer_falls_through_to_groq(self):
        from gallery.nolo_ai import get_nolo_ai_answer
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response('')), \
                patch('gallery.ai_providers.groq_text', return_value='Groq here.'):
            reply, source = get_nolo_ai_answer('Hello?')
        self.assertEqual(source, 'groq')
        self.assertEqual(reply, 'Groq here.')

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render')
    def test_openrouter_failure_with_no_other_key_is_honest_heuristic(self):
        from gallery.nolo_ai import get_nolo_ai_answer
        with patch('gallery.ai_providers.requests.post', side_effect=RuntimeError('down')):
            reply, source = get_nolo_ai_answer('How do I trade stars?')
        self.assertEqual(source, 'heuristic')
        self.assertIn('Stars', reply)

    def test_heuristic_copy_names_the_new_keys(self):
        from gallery.nolo_ai import get_nolo_ai_answer
        reply, source = get_nolo_ai_answer('something unrelated to the canned answers')
        self.assertEqual(source, 'heuristic')
        self.assertIn('OPENROUTER_API_KEY', reply)


@override_settings(**NO_KEYS)
class BackgroundFeatureOrderTests(_EnvIsolation, SimpleTestCase):
    """Kind classification, Nolo review, AI README and challenge drafts all
    consult the first-preference provider before Gemini/Groq."""

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render', GEMINI_API_KEY='g', GROQ_API_KEY='q')
    def test_classify_labels_the_verdict_with_openrouter(self):
        from gallery.classify import llm_classify
        answer = json.dumps({'kind': 'game', 'confidence': 0.9, 'appeal': 70, 'why': 'arcade loop'})
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response(answer)), \
                patch('gallery.classify._call_gemini') as gemini, \
                patch('gallery.classify._call_groq') as groq:
            verdict = llm_classify(_stub_project(title='Pygame shooter'))
        self.assertIsNotNone(verdict)
        self.assertEqual(verdict['source'], 'openrouter')
        self.assertEqual(verdict['kind'], 'game')
        gemini.assert_not_called()
        groq.assert_not_called()

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render', GROQ_API_KEY='q')
    def test_classify_falls_through_when_openrouter_errors(self):
        from gallery.classify import llm_classify
        with patch('gallery.ai_providers.requests.post', side_effect=RuntimeError('boom')), \
                patch('gallery.classify._call_gemini', return_value=None), \
                patch('gallery.classify._call_groq', return_value={'kind': 'tool', 'confidence': 0.7, 'appeal': 40, 'why': 'cli'}):
            verdict = llm_classify(_stub_project())
        self.assertEqual(verdict['source'], 'groq')
        self.assertEqual(verdict['kind'], 'tool')

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render', GEMINI_API_KEY='g')
    def test_nolo_review_prefers_openrouter(self):
        from gallery.nolo_review import nolo_review
        answer = json.dumps({'score': 8, 'fixes': ['Add tests', 'Pin deps', 'Add screenshots'], 'pros': ['Clear README', 'Small', 'Runs']})
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response(answer)):
            review = nolo_review(_stub_project())
        self.assertEqual(review['source'], 'openrouter')
        self.assertEqual(review['score'], 8)
        self.assertEqual(review['fixes'][0], 'Add tests')

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render')
    def test_nolo_review_unparseable_answer_degrades_to_heuristic(self):
        from gallery.nolo_review import nolo_review
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response('no json here')):
            review = nolo_review(_stub_project())
        self.assertEqual(review['source'], 'heuristic')

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render')
    def test_ai_readme_uses_openrouter(self):
        from gallery.ai_readme import generate_ai_readme
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response('# Taxi rank queue\n\n## What is this?\nQueue app.')) as posted:
            readme = generate_ai_readme(_stub_project())
        self.assertTrue(readme.startswith('# Taxi rank queue'))
        self.assertEqual(posted.call_count, 1)

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render')
    def test_challenge_drafts_use_openrouter(self):
        from gallery.challenge_ai import generate_challenge_candidates
        ideas = json.dumps([
            {'title': 'Kota Shop Orders', 'description': 'Order a kota on WhatsApp.', 'bounty_stars': 10, 'tag': 'challenge-week-40'},
            {'title': 'Stokvel Ledger', 'description': 'Track stokvel contributions.', 'bounty_stars': 10, 'tag': 'challenge-week-41'},
            {'title': 'Gogo Pension Reminder', 'description': 'SMS pension day reminders.', 'bounty_stars': 10, 'tag': 'challenge-week-42'},
        ])
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response(ideas)):
            cands = generate_challenge_candidates(['Spaza Shop Stock Tracker'], n=3)
        self.assertEqual([c['title'] for c in cands], ['Kota Shop Orders', 'Stokvel Ledger', 'Gogo Pension Reminder'])


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False, **NO_KEYS)
class NoloChatPageLabelTests(_EnvIsolation, TestCase):
    @override_settings(OPENROUTER_API_KEY='sk-or-v1-render')
    def test_chat_page_says_openrouter(self):
        page = self.client.get('/nolo/chat/')
        self.assertContains(page, 'Answers come from OpenRouter')

    @override_settings(OPENAI_API_KEY='sk-proj-real')
    def test_chat_page_says_openai(self):
        page = self.client.get('/nolo/chat/')
        self.assertContains(page, 'Answers come from OpenAI')

    def test_chat_page_without_keys_is_honest(self):
        page = self.client.get('/nolo/chat/')
        self.assertContains(page, 'No AI key is set')
