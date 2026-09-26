from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock
import json
from django.test import SimpleTestCase, RequestFactory
from .codex_api import gateway

class CodexGatewayTests(SimpleTestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.key = Path(self.tmp.name) / 'key'
        self.key.write_text('test-inference-token')
        self.mock_key = patch('portal.codex_api.KEY_FILE', self.key)
        self.mock_key.start()
        self.addCleanup(self.mock_key.stop)
        self.factory = RequestFactory()

    @patch('portal.codex_api.requests.request')
    def test_anonymous_cannot_reach_backend(self, upstream):
        r = gateway(self.factory.post('/codex/v1/responses', '{}', content_type='application/json'), 'responses')
        self.assertEqual(r.status_code, 401)
        upstream.assert_not_called()

    @patch('portal.codex_api.requests.request')
    def test_namespace_tool_and_stream_roundtrip(self, upstream):
        reply=Mock(status_code=200, headers={'Content-Type':'text/event-stream'})
        reply.iter_content.return_value=iter([b'data: {"type":"response.completed"}\n\n'])
        upstream.return_value=reply
        body={'tools':[{'type':'namespace','name':'functions','tools':[{'type':'function','name':'exec_command','parameters':{'type':'object'}}]}]}
        request=self.factory.post('/codex/v1/responses', json.dumps(body), content_type='application/json',HTTP_AUTHORIZATION='Bearer test-inference-token')
        r=gateway(request,'responses')
        self.assertIn(b'response.completed',b''.join(r.streaming_content))
        sent=json.loads(upstream.call_args.kwargs['data'])
        self.assertEqual(sent['tools'][0]['name'],'functions.exec_command')
        reply.close.assert_called_once()

    @patch('portal.codex_api.requests.request')
    def test_unknown_path_is_not_a_general_proxy(self, upstream):
        request=self.factory.get('/codex/v1/admin',HTTP_AUTHORIZATION='Bearer test-inference-token')
        self.assertEqual(gateway(request,'admin').status_code,404)
        upstream.assert_not_called()
