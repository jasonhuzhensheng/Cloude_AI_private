"""Live-model acceptance test. Uses an isolated temporary database, never production accounts."""
import os, tempfile, json
from pathlib import Path
with tempfile.TemporaryDirectory(prefix='portal-acceptance-') as directory:
    os.environ['PORTAL_DATA']=directory
    os.environ['PORTAL_INSECURE_LOCAL']='1'
    os.environ['ALLOWED_HOSTS']='testserver,localhost,127.0.0.1'
    os.environ['DJANGO_SETTINGS_MODULE']='portal.settings'
    import django
    django.setup()
    from django.core.management import call_command
    from django.contrib.auth import get_user_model
    from django.test import Client
    from django.core.files.uploadedfile import SimpleUploadedFile
    call_command('migrate',verbosity=0)
    user=get_user_model().objects.create_user('acceptance_test',password='Temporary-fixture-only-9842')
    client=Client()
    assert client.login(username='acceptance_test',password='Temporary-fixture-only-9842')
    chat=client.post('/api/chats/').json()['id']
    upload=client.post(f'/api/chats/{chat}/upload/',{'file':SimpleUploadedFile('test-plan.txt','项目代号：青竹。交付日期：2026年10月8日。预算：2800美元。'.encode())})
    assert upload.status_code==200,upload.content
    reply=client.post(f'/api/chats/{chat}/send/',json.dumps({'message':'文件里的项目代号和预算分别是什么？请用一句话回答。'}),content_type='application/json')
    assert reply.status_code==200,reply.content
    answer=reply.json()['answer']
    assert '青竹' in answer and ('2800' in answer or '2,800' in answer),answer
    print(json.dumps({'live_model_file_test':'PASSED','answer':answer},ensure_ascii=False))
