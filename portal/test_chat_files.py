import io,json,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from django.test import TestCase,override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from docx import Document as Word
from .tests import PortalTests
from .test_office import word_fixture
from .models import Document,Message,ImageEditJob
from .chat_files import file_data

@override_settings(SECURE_SSL_REDIRECT=False)
class ChatFilesTests(TestCase):
    setUp=PortalTests.setUp
    def upload(self,name,raw):
        r=self.client.post(f'/api/chats/{self.chat.id}/upload/',{'file':SimpleUploadedFile(name,raw)});self.assertEqual(r.status_code,200,r.content);return r.json()['id']
    def send(self,prompt,**extras):return self.client.post(f'/api/chats/{self.chat.id}/send/',json.dumps({'message':prompt,**extras}),content_type='application/json',HTTP_ACCEPT='application/x-ndjson')
    def test_word_download_persisted_in_history(self):
        pk=self.upload('source.docx',word_fixture())
        response=Mock();response.json.return_value={'choices':[{'message':{'content':json.dumps({'operations':[{'type':'replace_text','find':'USD 100','replace':'USD 250'}]})}}]}
        with patch('portal.chat_files.model_json',return_value={'action':'edit','file_id':pk,'instruction':'Replace USD 100 with USD 250'}),patch('portal.office_views.requests.post',return_value=response):r=self.send('修改 Word 金额为 USD 250')
        self.assertEqual(r.status_code,200,r.content);result=r.json()['files'][0]['id']
        self.assertEqual(Word(io.BytesIO(Document.objects.get(pk=result).file.read())).paragraphs[1].text,'Amount: USD 250')
        detail=self.client.get(f'/api/chats/{self.chat.id}/').json();self.assertEqual(detail['messages'][-1]['files'][0]['id'],result)
        self.client.force_login(self.b);self.assertEqual(self.client.get(f'/api/files/{result}/').status_code,404)
    def test_router_cannot_access_other_files(self):
        pk=self.upload('source.docx',word_fixture())
        with patch('portal.chat_files.model_json',return_value={'action':'edit','file_id':99999}):r=self.send('edit the file')
        self.assertEqual(r.status_code,400);self.assertEqual(Document.objects.count(),1)
        r=self.send('edit the file',file_id=99999);self.assertEqual(r.status_code,400)
    def test_text_edit_and_json_validation(self):
        pk=self.upload('settings.json',b'{"title":"Old"}')
        with patch('portal.chat_files.model_json',side_effect=[{'action':'edit','file_id':pk,'instruction':'Change Old to New'},{'operations':[{'find':'Old','replace':'New'}]}]):r=self.send('Change Old to New')
        self.assertEqual(r.status_code,200,r.content);d=Document.objects.get(pk=r.json()['files'][0]['id']);self.assertEqual(json.loads(d.file.read()),{'title':'New'})
        with patch('portal.chat_files.model_json',side_effect=[{'action':'edit','file_id':pk},{'operations':[{'find':'Old','replace':'"'}]}]):r=self.send('Change the JSON')
        self.assertEqual(r.status_code,503);self.assertEqual(Document.objects.count(),2)
    def test_clarification_does_not_create_file(self):
        self.upload('source.docx',word_fixture())
        with patch('portal.chat_files.model_json',return_value={'action':'clarify','reply':'Which paragraph should I change?'}):r=self.send('修改一下')
        self.assertEqual(r.status_code,200);self.assertEqual(r.json()['files'],[]);self.assertEqual(Document.objects.count(),1)
    def test_image_job_persists_and_returns_owned_result(self):
        from PIL import Image
        b=io.BytesIO();Image.new('RGB',(32,32),'red').save(b,format='PNG');pk=self.upload('source.png',b.getvalue())
        with tempfile.NamedTemporaryFile() as ready,override_settings(IMAGE_EDIT_READY=ready.name),patch('portal.chat_files.model_json',return_value={'action':'edit','file_id':pk,'instruction':'Change red to green'}):r=self.send('Change red to green')
        self.assertEqual(r.status_code,200,r.content);job=ImageEditJob.objects.get(pk=r.json()['job']['id'])
        result=Document.objects.create(owner=self.a,conversation=self.chat,name='edited.png',size=3,text='');result.file.save('edited.png',SimpleUploadedFile('edited.png',b.getvalue()))
        job.result=result;job.status='running';job.stage='Restoring the chat model';job.save()
        for state in ['running','failed','succeeded']:
            job.status=state;job.save()
            history=self.client.get(f'/api/chats/{self.chat.id}/').json()
            self.assertEqual(history['messages'][-1]['files'][0]['id'],result.pk)
            self.assertEqual(self.client.get(f'/api/image-jobs/{job.pk}/').json()['file']['id'],result.pk)
        job.status='succeeded';job.save()
        history=self.client.get(f'/api/chats/{self.chat.id}/').json();self.assertEqual(history['messages'][-1]['files'][0]['id'],result.pk)
        self.assertEqual(self.client.get(f'/api/image-jobs/{job.pk}/').json()['file']['id'],result.pk)
    def test_conversion_reuses_authenticated_tool(self):
        pk=self.upload('source.docx',word_fixture())
        from django.http import JsonResponse
        with patch('portal.chat_files.model_json',return_value={'action':'convert','file_id':pk}),patch('portal.office_views.convert',return_value=JsonResponse({'id':pk})) as converter:r=self.send('Word转PDF')
        self.assertEqual(r.status_code,200);self.assertEqual(converter.call_args.args[1],pk);self.assertEqual(converter.call_args.args[0].user,self.a)
    def test_selected_file_wins_router_guess_with_duplicate_names(self):
        from django.http import JsonResponse
        first=self.upload('source.docx',word_fixture())
        second=self.upload('source.docx',word_fixture())
        with patch('portal.chat_files.model_json',return_value={'action':'edit','file_id':first,'instruction':'Correct errors'}),patch('portal.office_views.edit',return_value=JsonResponse({'id':second})) as editor:
            response=self.send('检查错误并修改',file_id=second)
        self.assertEqual(response.status_code,200)
        self.assertEqual(editor.call_args.args[1],second)
    def test_router_failure_does_not_blame_file_selection(self):
        pk=self.upload('source.docx',word_fixture())
        with patch('portal.chat_files.model_json',side_effect=RuntimeError('model unavailable')):
            response=self.send('检查错误并修改',file_id=pk)
        self.assertEqual(response.status_code,503)
        self.assertIn('selected file is retained',response.json()['error'])
        self.assertEqual(Document.objects.count(),1)
    def test_json_tools_specify_available_model(self):
        from .chat_files import model_json
        response=Mock();response.json.return_value={'choices':[{'message':{'content':'{"action":"edit"}'}}]}
        with patch('portal.model_registry.Path.is_file',return_value=True),patch('portal.chat_files.requests.post',return_value=response) as call:
            self.assertEqual(model_json('classify',{}),{'action':'edit'})
        self.assertEqual(call.call_args.kwargs['json']['model'],'Qwen3.8-Flash-Next')
