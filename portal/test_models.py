import json
from unittest.mock import patch, Mock
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from .models import Conversation, Message
from .model_registry import DEFAULT_MODEL, FLASH_MODEL

@override_settings(SECURE_SSL_REDIRECT=False)
class ModelSelectionTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('modeltester')
        self.client.force_login(self.user)
        self.chat=Conversation.objects.create(owner=self.user)
        self.url=f'/api/chats/{self.chat.id}/send/'
    def send(self,model,stream=False):
        return self.client.post(self.url,json.dumps({'message':'hello','model_id':model}),content_type='application/json',HTTP_ACCEPT='application/x-ndjson' if stream else 'application/json')
    def test_unknown_and_uninstalled_do_not_call_upstream(self):
        with patch('portal.model_registry.Path.is_file',return_value=False),patch('portal.views.requests.post') as call:
            for model in ['not-a-model',FLASH_MODEL,[],None]:
                self.assertEqual(self.send(model).status_code,400)
            call.assert_not_called()
        self.assertFalse(self.chat.messages.exists())
    def test_flash_routes_and_persists_choice(self):
        upstream=Mock();upstream.json.return_value={'choices':[{'message':{'content':'answer'},'finish_reason':'stop'}]}
        with patch('portal.model_registry.Path.is_file',return_value=True),patch('portal.views.requests.post',return_value=upstream) as call:
            self.assertEqual(self.send(FLASH_MODEL).status_code,200)
        self.assertEqual(call.call_args.kwargs['json']['model'],FLASH_MODEL)
        self.chat.refresh_from_db();self.assertEqual(self.chat.model_id,FLASH_MODEL)
        self.assertEqual(self.chat.messages.last().metadata['model_id'],FLASH_MODEL)
        self.assertEqual(self.client.get(f'/api/chats/{self.chat.id}/').json()['model_id'],FLASH_MODEL)
    def test_stream_routes_and_persists_choice(self):
        upstream=Mock();upstream.iter_lines.return_value=iter([b'data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}',b'data: [DONE]'])
        with patch('portal.model_registry.Path.is_file',return_value=True),patch('portal.streaming.requests.post',return_value=upstream) as call:
            response=self.send(FLASH_MODEL,True)
            events=b''.join(response.streaming_content)
        self.assertIn(b'Qwen3.8 Flash Next',events)
        self.assertEqual(call.call_args.kwargs['json']['model'],FLASH_MODEL)
        self.chat.refresh_from_db();self.assertEqual(self.chat.model_id,FLASH_MODEL)
    def test_failed_switch_does_not_change_saved_selection(self):
        with patch('portal.model_registry.Path.is_file',return_value=True),patch('portal.views.requests.post',side_effect=RuntimeError('offline')):
            self.assertEqual(self.send(FLASH_MODEL).status_code,503)
        self.chat.refresh_from_db();self.assertEqual(self.chat.model_id,DEFAULT_MODEL)
    def test_model_choices_are_visible_and_disabled_until_ready(self):
        with patch('portal.model_registry.Path.is_file',return_value=False):
            response=self.client.get('/')
        self.assertContains(response,'id="model-select"')
        self.assertContains(response,'Not installed')
