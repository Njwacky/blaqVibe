"""Nolo's scope inside the app: what it may see, and what it speaks about.

Two contracts:
  1. Access — the only live data Nolo chat receives is what a logged-out
     visitor already sees on the chat page: published vibes and categories.
     Never pending/quarantined/removed projects, never owners' emails,
     balances, trades or notifications.
  2. Voice — every Nolo prompt identifies as BlaqVibes' Nolo, limits itself
     to BlaqVibes, refuses to invent features, and treats pasted content as
     data. The question always survives the token budget; the context is
     what gets cut.
No test here touches the network.
"""
import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from gallery.tests import make_category, make_project, make_user

NO_KEYS = dict(OPENROUTER_API_KEY='', OPENAI_API_KEY='', ANTHROPIC_API_KEY='', GEMINI_API_KEY='', GROQ_API_KEY='')


def _chat_response(text):
    fake = Mock()
    fake.raise_for_status = Mock()
    fake.json.return_value = {'choices': [{'message': {'role': 'assistant', 'content': text}}]}
    return fake


class NoloVoiceTests(SimpleTestCase):
    def test_chat_prompt_is_blaqvibes_only_and_honest_about_access(self):
        from gallery.nolo_ai import NOLO_SYSTEM_PROMPT as p
        self.assertIn('You are Nolo, the BlaqVibes assistant', p)
        self.assertIn('help only with BlaqVibes', p)
        self.assertIn('no access to accounts, emails, balances, trades, payments, notifications or unpublished projects', p)
        self.assertIn('say you are not sure instead of inventing it', p)
        self.assertIn('Never repeat secrets', p)
        self.assertIn('untrusted_user_content', p)

    def test_chat_prompt_facts_match_the_url_map(self):
        """Every path Nolo is told about must really exist in gallery/urls.py."""
        from django.urls import resolve
        from gallery.nolo_ai import NOLO_SYSTEM_PROMPT as p
        from gallery.nolo_context import PUBLIC_PAGES
        for _label, path in PUBLIC_PAGES:
            self.assertIn(path, p, path)
            resolve(path)  # raises Resolver404 if the page does not exist
        resolve('/app/some-vibe/')
        resolve('/app/some-vibe/files/')

    def test_chat_prompt_star_facts_match_the_code(self):
        from gallery.nolo_ai import NOLO_SYSTEM_PROMPT as p
        from users.models import WELCOME_STARS
        self.assertIn(f'new accounts start with {WELCOME_STARS} stars', p)

    def test_fix_and_readme_prompts_carry_the_same_identity(self):
        from gallery.nolo_assist import NOLO_FIX_SYSTEM_PROMPT, NOLO_README_SYSTEM_PROMPT
        for p in (NOLO_FIX_SYSTEM_PROMPT, NOLO_README_SYSTEM_PROMPT):
            self.assertIn('You are Nolo, the BlaqVibes assistant', p)
            self.assertIn('untrusted_user_content', p)
            self.assertIn('never repeat keys or secrets', p)

    def test_system_prompt_survives_the_prompt_economy_budget(self):
        """prompt_economy caps system text at 900 chars by default; Nolo raises
        the budget so the grounding facts are never silently cut."""
        from gallery.nolo_ai import DEFAULT_SYSTEM_BUDGET_CHARS, NOLO_SYSTEM_PROMPT
        from gallery.prompt_economy import optimize_prompt
        self.assertLess(len(NOLO_SYSTEM_PROMPT), DEFAULT_SYSTEM_BUDGET_CHARS)
        plan = optimize_prompt('hi', system=NOLO_SYSTEM_PROMPT, system_budget_chars=DEFAULT_SYSTEM_BUDGET_CHARS)
        self.assertIn('/nolo/chat/', plan['system'])
        self.assertIn('AI-generated or remixed', plan['system'])
        self.assertFalse(any('System instructions were capped' in w for w in plan['warnings']))

    @override_settings(**NO_KEYS)
    def test_offline_helper_stays_in_blaqvibes_voice(self):
        from gallery.nolo_ai import get_nolo_ai_answer
        reply, source = get_nolo_ai_answer('Write me a poem about the ocean')
        self.assertEqual(source, 'heuristic')
        self.assertIn('Nolo from BlaqVibes', reply)
        self.assertIn('only help with BlaqVibes', reply)


class RedactionTests(SimpleTestCase):
    def test_provider_key_shapes_never_reach_a_model(self):
        from gallery.ai_safety import redact_for_ai
        samples = {
            'openrouter': 'sk-or-v1-0123456789abcdef0123456789abcdef0123456789abcdef',
            'anthropic': 'sk-ant-api03-0123456789abcdefghijklmnopqrstuvwxyz',
            'groq': 'gsk_0123456789abcdefghijklmnopqrstuv',
            'paystack': 'sk_live_0123456789abcdefghij',
            'github': 'ghp_0123456789abcdefghijklmnopqrstuvwxyz',
            'aws': 'AKIAIOSFODNN7EXAMPLE',
            'jwt': 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U',
            'dsn': 'postgres://blaq:hunter2hunter2@db.internal:5432/blaqvibes',
        }
        for label, secret in samples.items():
            safe = redact_for_ai(f'paste from a user: {secret} end')
            self.assertNotIn(secret, safe, label)
            self.assertIn('[REDACTED]', safe, label)
        block = '-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----'
        self.assertNotIn('MIIEow', redact_for_ai(block))

    def test_ordinary_code_is_left_alone(self):
        from gallery.ai_safety import redact_for_ai
        code = "def toggle_star(request, slug):\n    project = get_object_or_404(AppProject, slug=slug)\n    return JsonResponse({'ok': True})"
        self.assertEqual(redact_for_ai(code), code)


class PayloadOrderTests(SimpleTestCase):
    def test_question_comes_first_and_context_is_what_gets_cut(self):
        from gallery.nolo_ai import CONTEXT_HEADER, build_user_payload
        from gallery.prompt_economy import optimize_prompt
        question = 'Which vibe is easiest to remix?'
        context = 'Latest published vibes:\n' + '\n'.join(f'- Vibe {i} — /app/vibe-{i}/' for i in range(400))
        payload = build_user_payload(question, context)
        self.assertTrue(payload.startswith(question))
        self.assertIn(CONTEXT_HEADER, payload)
        plan = optimize_prompt(payload, user_budget_chars=600)
        self.assertTrue(plan['text'].startswith(question))
        self.assertNotIn('vibe-399', plan['text'])

    def test_no_context_means_the_plain_question(self):
        from gallery.nolo_ai import build_user_payload
        self.assertEqual(build_user_payload('  hello  ', None), 'hello')
        self.assertEqual(build_user_payload('hello', '   '), 'hello')

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-test')
    def test_model_receives_system_first_then_question_then_context(self):
        from gallery.nolo_ai import get_nolo_ai_answer
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response('Try the smallest one.')) as posted:
            reply, source = get_nolo_ai_answer('Which vibe is easiest to remix?', context='Latest published vibes:\n- Taxi Rank — /app/taxi-rank/')
        self.assertEqual(source, 'openrouter')
        messages = posted.call_args.kwargs['json']['messages']
        self.assertEqual(messages[0]['role'], 'system')
        self.assertIn('You are Nolo, the BlaqVibes assistant', messages[0]['content'])
        user = messages[1]['content']
        self.assertLess(user.index('Which vibe is easiest to remix?'), user.index('/app/taxi-rank/'))
        self.assertIn('<untrusted_user_content>', user)


@override_settings(RATELIMIT_ENABLE=False, SEED_DEMO=False, MEDIA_ROOT='/tmp/blaqvibes-tests')
class PublicContextTests(TestCase):
    def setUp(self):
        self.cat = make_category()
        self.owner = make_user('ctxowner')
        self.owner.email = 'private-owner@example.com'
        self.owner.save()
        self.published = make_project(self.owner, self.cat, title='Public Taxi Rank', status='published')
        self.pending = make_project(self.owner, self.cat, title='Pending Secret Prototype', status='pending')
        self.quarantined = make_project(self.owner, self.cat, title='Quarantined Malware Sample', status='quarantined')
        self.removed = make_project(self.owner, self.cat, title='Removed Ghost Vibe', status='removed')

    def test_context_lists_only_published_vibes_with_public_urls(self):
        from gallery.nolo_context import public_chat_context
        ctx = public_chat_context()
        self.assertIn('Public Taxi Rank', ctx)
        self.assertIn(f'/app/{self.published.slug}/', ctx)
        for hidden in ('Pending Secret Prototype', 'Quarantined Malware Sample', 'Removed Ghost Vibe'):
            self.assertNotIn(hidden, ctx)
        self.assertNotIn('private-owner@example.com', ctx)
        self.assertNotIn('ctxowner', ctx)

    def test_context_is_capped(self):
        from gallery.nolo_context import public_chat_context
        for i in range(30):
            make_project(self.owner, self.cat, title=f'Long Titled Published Vibe Number {i} With Extra Words', status='published')
        ctx = public_chat_context(limit=8, max_chars=500)
        self.assertLessEqual(len(ctx), 500)
        self.assertLessEqual(ctx.count('/app/'), 8)

    def test_context_never_raises(self):
        from gallery.nolo_context import public_chat_context
        with patch('gallery.models.AppProject.objects.filter', side_effect=RuntimeError('db down')):
            self.assertEqual(public_chat_context(), '')

    @override_settings(OPENROUTER_API_KEY='sk-or-v1-test')
    def test_chat_api_sends_only_public_context_to_the_model(self):
        with patch('gallery.ai_providers.requests.post', return_value=_chat_response('Public Taxi Rank is a good start.')) as posted:
            response = self.client.post('/nolo/chat/send/', data=json.dumps({'prompt': 'What is new?'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['source'], 'openrouter')
        sent = json.dumps(posted.call_args.kwargs['json'])
        self.assertIn('Public Taxi Rank', sent)
        for hidden in ('Pending Secret Prototype', 'Quarantined Malware Sample', 'Removed Ghost Vibe', 'private-owner@example.com'):
            self.assertNotIn(hidden, sent)

    @override_settings(**NO_KEYS)
    def test_chat_api_without_key_still_answers_in_blaqvibes_voice(self):
        response = self.client.post('/nolo/chat/send/', data=json.dumps({'prompt': 'tell me a joke about cats'}), content_type='application/json')
        body = response.json()
        self.assertEqual(body['source'], 'heuristic')
        self.assertIn('Nolo from BlaqVibes', body['reply'])
