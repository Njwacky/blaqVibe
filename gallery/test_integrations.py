from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from .ai_safety import redact_for_ai
from .anypost import AnyPostError, share_project
from .tests import make_category, make_project, make_user


class AiSafetyTests(SimpleTestCase):
    def test_redacts_common_credentials_and_private_paths(self):
        value = 'api_key=secret123 password:letmein C:\\Users\\alice\\notes.txt'
        safe = redact_for_ai(value)
        self.assertNotIn('secret123', safe)
        self.assertNotIn('letmein', safe)
        self.assertNotIn('C:\\Users\\alice', safe)

    def test_clips_prompt_content(self):
        self.assertEqual(len(redact_for_ai('x' * 100, limit=20)), 20)


@override_settings(
    ANYPOST_BASE_URL='https://share.example.test',
    ANYPOST_ENDPOINT='/v1/posts',
    ANYPOST_API_KEY='server-only-key',
    ANYPOST_TIMEOUT_SECONDS='7',
)
class AnyPostClientTests(SimpleTestCase):
    @mock.patch('gallery.anypost.requests.post')
    def test_uses_configured_endpoint_and_never_returns_provider_data(self, post):
        post.return_value.status_code = 201
        self.assertTrue(share_project(title='Vibe', text='A project', url='https://site.test/app/vibe/'))
        request = post.call_args
        self.assertEqual(request.args[0], 'https://share.example.test/v1/posts')
        self.assertEqual(request.kwargs['timeout'], 7)
        self.assertEqual(request.kwargs['headers']['Authorization'], 'Bearer server-only-key')
        self.assertNotIn('server-only-key', str(request.kwargs['json']))

    @override_settings(ANYPOST_BASE_URL='', ANYPOST_ENDPOINT='', ANYPOST_API_KEY='')
    def test_requires_explicit_configuration(self):
        with self.assertRaises(AnyPostError):
            share_project(title='Vibe', text='A project', url='https://site.test/app/vibe/')


@override_settings(RATELIMIT_ENABLE=False, MEDIA_ROOT='/tmp/blaqvibes-anypost-tests')
class AnyPostViewTests(TestCase):
    def test_only_owner_can_share(self):
        owner = make_user('anypostowner')
        other = make_user('anypostother')
        project = make_project(owner, make_category())
        self.client.force_login(other)
        response = self.client.post(f'/app/{project.slug}/share/anypost/')
        self.assertEqual(response.status_code, 404)

    @mock.patch('gallery.anypost.share_project')
    @override_settings(ANYPOST_ENABLED=True)
    def test_owner_can_share_public_metadata(self, share):
        owner = make_user('anypostpublisher')
        project = make_project(owner, make_category())
        self.client.force_login(owner)
        response = self.client.post(f'/app/{project.slug}/share/anypost/')
        self.assertEqual(response.status_code, 302)
        share.assert_called_once()
        self.assertEqual(share.call_args.kwargs['title'], project.title)
