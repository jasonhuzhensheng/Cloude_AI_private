import tempfile
from pathlib import Path
from unittest.mock import patch,Mock
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from PIL import Image
import io
from .models import PhotoJob,Document,Conversation
from .management.commands import photo_worker

@override_settings(SECURE_SSL_REDIRECT=False)
class PhotoTests(TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        context=override_settings(DATA=self.root,MEDIA_ROOT=self.root/'uploads',IMAGE_EDIT_ROOT=self.root)
        context.enable();self.addCleanup(context.disable)
        self.user=get_user_model().objects.create_user('photo-owner')
        self.other=get_user_model().objects.create_user('photo-other')
        self.client.force_login(self.user)
        (self.root/'QWEN21_READY').touch()
        conversation=Conversation.objects.create(owner=self.user)
        self.source=Document.objects.create(owner=self.user,conversation=conversation,name='original.png',size=3,text='')
        self.source.file.save('original.png',ContentFile(b'original'))

    def post(self,**data):
        return self.client.post('/api/photos/create/',dict(prompt='A blue mug',**data),content_type='application/json')

    def test_generate_edit_owner_and_queue(self):
        first=self.post();self.assertEqual(first.status_code,202);self.assertIsNone(first.json()['source_id'])
        self.assertEqual(self.post(source_id=self.source.pk).status_code,202)
        self.client.force_login(self.other)
        self.assertEqual(self.post(source_id=self.source.pk).status_code,404)
        self.assertEqual(self.client.get('/api/photos/').json()['jobs'],[])
        self.assertEqual(self.client.post('/api/photos/'+str(first.json()['id'])+'/cancel/').status_code,404)
        self.client.force_login(self.user)
        self.assertEqual(self.post().status_code,202);self.assertEqual(self.post().status_code,202)
        self.assertEqual(self.post().status_code,429)
        self.assertEqual(self.client.post('/api/photos/'+str(first.json()['id'])+'/cancel/').json()['status'],'cancelled')
        self.assertEqual(self.post().status_code,202)

    def test_readiness_validation_and_login(self):
        (self.root/'QWEN21_READY').unlink()
        self.assertEqual(self.post().status_code,503)
        (self.root/'QWEN21_READY').touch()
        self.assertEqual(self.post(format='invalid').status_code,400)
        self.assertEqual(self.post(source_id=True).status_code,400)
        self.client.logout()
        self.assertEqual(self.client.get('/photos/').status_code,302)

    def test_worker_saves_private_png_and_keeps_original(self):
        (self.root/'logs').mkdir();(self.root/'CHAT_DISABLED').touch()
        job=PhotoJob.objects.create(owner=self.user,source=self.source,prompt='Change color',status='running')
        def start(args,**kwargs):
            import json
            request=json.loads(Path(args[-1]).read_text())
            self.assertEqual(request['source'],self.source.file.path)
            self.assertEqual(request['prompt'],'Change color')
            Image.new('RGBA',(1024,1024),(0,100,255,128)).save(request['output'])
            return Mock(poll=lambda:0,returncode=0)
        with patch.object(photo_worker,'ROOT',self.root),patch.object(photo_worker.subprocess,'Popen',side_effect=start):
            photo_worker.run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status,'succeeded');self.assertEqual(job.result.owner,self.user)
        self.assertEqual(Path(self.source.file.path).read_bytes(),b'original')
        with Image.open(job.result.file.path) as result:self.assertEqual(result.mode,'RGBA')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/api/files/{job.result_id}/').status_code,404)

    def test_native_sizes_and_upscale_limits(self):
        from .photo_sizes import output_size
        self.assertEqual(output_size('square1536'),(1536,1536))
        self.assertEqual(output_size('wide'),(1344,768))
        image=Image.new('RGB',(1024,768));data=io.BytesIO();image.save(data,format='PNG')
        self.source.file.save('source.png',ContentFile(data.getvalue()))
        self.assertEqual(output_size('upscale2',self.source),(2048,1536))
        self.assertEqual(output_size('upscale4',self.source),(4096,3072))
        self.assertEqual(self.post(format='upscale2',source_id=self.source.pk).status_code,503)
        (self.root/'PHOTO_UPSCALE_READY').touch()
        self.assertEqual(self.post(format='upscale2').status_code,400)
        self.assertEqual(self.post(format='upscale2',source_id=self.source.pk).status_code,202)
        image=Image.new('RGB',(1536,1024));data=io.BytesIO();image.save(data,format='PNG')
        self.source.file.save('large.png',ContentFile(data.getvalue()))
        with self.assertRaises(ValueError):output_size('upscale4',self.source)

    def test_prompt_check_log_privacy_and_states(self):
        from .photo_diagnostics import report
        job=PhotoJob.objects.create(owner=self.user,prompt='Safety: blocked',format='square')
        self.assertEqual(report(job)['state'],'unavailable')
        log=self.root/'logs'/f'photo-{job.pk}.log';log.parent.mkdir()
        log.write_text('prompt: Safety: blocked\nSafety: not blocked\n')
        self.assertEqual(report(job)['state'],'no_explicit_signal')
        log.write_text('[12:00:00] WARNING: Safety filter: blocked\nSECRET_PATH=/private/test\n')
        response=self.client.get(f'/api/photos/?prompt_log={job.pk}')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['state'],'review')
        self.assertNotIn('SECRET_PATH',response.content.decode())
        self.assertIn('private',response['Cache-Control'])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/api/photos/?prompt_log={job.pk}').status_code,404)
        job.format='upscale2'
        self.assertEqual(report(job)['state'],'not_applicable')

    def test_model_selection_and_edit_capabilities(self):
        self.assertEqual(self.post(model='unknown').status_code,400)
        self.assertEqual(self.post(model='zimage').status_code,503)
        (self.root/'ZIMAGE_READY').touch()
        self.assertEqual(self.post(model='zimage',source_id=self.source.pk).status_code,400)
        response=self.post(model='zimage')
        self.assertEqual(response.status_code,202)
        job=PhotoJob.objects.get(pk=response.json()['id'])
        self.assertEqual(job.model,'zimage')
        self.assertEqual(self.client.get(f'/api/photos/?prompt_log={job.pk}').json()['model'],'Z-Image')
        self.assertTrue(next(m for m in self.client.get('/api/photos/').json()['models'] if m['id']=='zimage')['ready'])
        (self.root/'QWEN21_READY').unlink()
        self.assertEqual(self.post(model='zimage').status_code,202)

    def test_worker_dispatches_selected_model(self):
        (self.root/'logs').mkdir();(self.root/'CHAT_DISABLED').touch()
        job=PhotoJob.objects.create(owner=self.user,model='zimage',prompt='Fashion portrait',status='running',format='square512')
        def start(args,**kwargs):
            import json
            self.assertTrue(args[1].endswith('/deployment/zimage_infer.py'))
            request=json.loads(Path(args[-1]).read_text())
            self.assertNotIn('source',request)
            Image.new('RGB',(512,512),'blue').save(request['output'])
            return Mock(poll=lambda:0,returncode=0)
        with patch.object(photo_worker,'ROOT',self.root),patch.object(photo_worker.subprocess,'Popen',side_effect=start):photo_worker.run_job(job)
        job.refresh_from_db()
        self.assertEqual(job.status,'succeeded');self.assertTrue(job.result.name.startswith('zimage-'))

    def test_model_native_maximum_sizes(self):
        from .photo_sizes import output_size,NATIVE_MAX
        for model in NATIVE_MAX:
            for format,dims in NATIVE_MAX[model].items():
                self.assertEqual(output_size(format,model=model),dims)
                self.assertTrue(all(n%32==0 for n in dims))
                if model=='zimage':self.assertLessEqual(dims[0]*dims[1],2048**2)
        self.assertEqual(self.post(format='max_square').status_code,202)
        (self.root/'ZIMAGE_READY').touch()
        self.assertEqual(self.post(format='max_169',model='zimage').status_code,202)
        models=self.client.get('/api/photos/').json()['models']
        self.assertEqual(next(s for s in models[0]['sizes'] if s['id']=='max_square')['width'],2048)

    def test_upscale_queue_preserves_active_and_owner_results(self):
        active=PhotoJob.objects.create(owner=self.user,format='upscale2',source=self.source,prompt='AI image upscale')
        for _ in range(31):PhotoJob.objects.create(owner=self.user,prompt='test',status='succeeded')
        PhotoJob.objects.create(owner=self.other,format='upscale2',prompt='private')
        data=self.client.get('/api/photos/').json()
        self.assertIn(active.pk,[j['id'] for j in data['jobs']])
        self.assertEqual([j['id'] for j in data['upscales']],[active.pk])
        active.status='succeeded';active.result=self.source;active.save()
        self.assertEqual(self.client.get('/api/photos/').json()['upscales'][0]['result_id'],self.source.pk)

    def test_photo_delete_owner_files_and_dependencies(self):
        result=Document.objects.create(owner=self.user,conversation=self.source.conversation,name='result.png',size=3,text='')
        result.file.save('result.png',ContentFile(b'png'))
        path=Path(result.file.path)
        job=PhotoJob.objects.create(owner=self.user,source=self.source,result=result,prompt='test',status='succeeded')
        url=f'/api/photos/{job.pk}/cancel/?action=delete'
        self.client.force_login(self.other);self.assertEqual(self.client.post(url).status_code,404)
        self.client.force_login(self.user)
        child=PhotoJob.objects.create(owner=self.user,source=result,prompt='dependent')
        self.assertEqual(self.client.post(url).status_code,409)
        child.delete()
        with self.captureOnCommitCallbacks(execute=True):self.assertTrue(self.client.post(url).json()['deleted'])
        self.assertFalse(path.exists());self.assertTrue(Path(self.source.file.path).exists())
        self.assertFalse(PhotoJob.objects.filter(pk=job.pk).exists())

    def test_delete_running_waits_for_worker_and_queued_deletes(self):
        job=PhotoJob.objects.create(owner=self.user,prompt='test',status='running')
        response=self.client.post(f'/api/photos/{job.pk}/cancel/?action=delete')
        self.assertTrue(response.json()['pending']);job.refresh_from_db()
        self.assertTrue(job.cancel_requested);self.assertTrue(job.delete_requested)
        self.assertEqual(self.client.get('/api/photos/').json()['jobs'][0]['id'],job.pk)
        PhotoJob.objects.filter(pk=job.pk).update(status='cancelled')
        self.assertEqual(self.client.get('/api/photos/').json()['jobs'],[])
        queued=PhotoJob.objects.create(owner=self.user,prompt='test')
        self.assertTrue(self.client.post(f'/api/photos/{queued.pk}/cancel/?action=delete').json()['deleted'])

    def test_negative_prompt_saved_validated_and_private(self):
        response=self.post(negative_prompt='extra arms, extra fingers')
        self.assertEqual(response.status_code,202)
        job=PhotoJob.objects.get(pk=response.json()['id'])
        self.assertEqual(job.negative_prompt,'extra arms, extra fingers')
        self.assertEqual(self.client.get(f'/api/photos/?prompt_log={job.pk}').json()['saved_negative_prompt'],job.negative_prompt)
        self.assertEqual(self.post(negative_prompt=['bad']).status_code,400)
        self.assertEqual(self.post(negative_prompt='x'*1001).status_code,400)

    def test_references_order_ownership_limit_and_model(self):
        from .models import PhotoReference
        extra=Document.objects.create(owner=self.user,conversation=self.source.conversation,name='second.png',size=3,text='')
        third=Document.objects.create(owner=self.user,conversation=self.source.conversation,name='third.png',size=3,text='')
        response=self.post(source_id=self.source.pk,reference_ids=[third.pk,extra.pk])
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()['reference_ids'],[third.pk,extra.pk])
        self.assertEqual(self.client.get(f"/api/photos/?prompt_log={response.json()['id']}").json()['reference_ids'],[third.pk,extra.pk])
        for ids in [[True],[extra.pk,extra.pk],[self.source.pk],[extra.pk,third.pk,999],None]:
            self.assertEqual(self.post(source_id=self.source.pk,reference_ids=ids).status_code,400)
        self.assertEqual(self.post(reference_ids=[999999]).status_code,404)
        self.assertEqual(self.post(reference_ids=[extra.pk],format='upscale2').status_code,400)
        self.assertEqual(self.post(reference_ids=[extra.pk],model='zimage').status_code,400)
        self.client.force_login(self.other)
        self.assertEqual(self.post(reference_ids=[extra.pk]).status_code,404)
        self.assertEqual(PhotoReference.objects.count(),2)

    def test_worker_passes_references_in_order_and_protects_result(self):
        from .models import PhotoReference
        (self.root/'logs').mkdir();(self.root/'CHAT_DISABLED').touch()
        extra=Document.objects.create(owner=self.user,conversation=self.source.conversation,name='second.png',size=3,text='')
        extra.file.save('second.png',ContentFile(b'original two'))
        job=PhotoJob.objects.create(owner=self.user,source=self.source,prompt='Use image 1 and 2',status='running')
        PhotoReference.objects.create(job=job,document=extra,position=0)
        def start(args,**kwargs):
            import json
            data=json.loads(Path(args[-1]).read_text())
            self.assertEqual(data['source'],self.source.file.path)
            self.assertEqual(data['references'],[extra.file.path])
            Image.new('RGB',(1024,1024),'blue').save(data['output'])
            return Mock(poll=lambda:0,returncode=0)
        with patch.object(photo_worker,'ROOT',self.root),patch.object(photo_worker.subprocess,'Popen',side_effect=start):photo_worker.run_job(job)
        job.refresh_from_db();self.assertEqual(job.status,'succeeded')
        parent=PhotoJob.objects.create(owner=self.user,result=extra,prompt='prior',status='succeeded')
        self.assertEqual(self.client.post(f'/api/photos/{parent.pk}/cancel/?action=delete').status_code,409)
        self.assertEqual(Path(extra.file.path).read_bytes(),b'original two')
        extra.owner=self.other;extra.save()
        with patch.object(photo_worker,'ROOT',self.root),patch.object(photo_worker.subprocess,'Popen') as start:
            with self.assertRaisesRegex(RuntimeError,'reference owner'):photo_worker.run_job(job)
            start.assert_not_called()

    def test_variations_atomic_capacity_owner_and_seeds(self):
        parent=PhotoJob.objects.create(owner=self.user,result=self.source,status='succeeded',prompt='original',negative_prompt='extra arms',format='portrait')
        response=self.post(variant_of=parent.pk,count=3)
        self.assertEqual(response.status_code,202)
        jobs=response.json()['jobs'];self.assertEqual(len(jobs),3)
        self.assertEqual(len({j['seed'] for j in jobs}),3)
        self.assertEqual(len({j['prompt'] for j in jobs}),3)
        self.assertTrue(all(j['source_id']==self.source.pk and j['model']=='qwen21' and j['format']=='portrait' and j['negative_prompt']=='extra arms' for j in jobs))
        self.assertEqual(self.post(variant_of=parent.pk,count=2).status_code,429)
        self.assertEqual(PhotoJob.objects.filter(status='queued').count(),3)
        for count in [0,5,True,1.5,'2',None]:self.assertEqual(self.post(variant_of=parent.pk,count=count).status_code,400)
        self.client.force_login(self.other)
        self.assertEqual(self.post(variant_of=parent.pk,count=1).status_code,404)
        self.client.force_login(self.user)
        parent.status='failed';parent.save()
        self.assertEqual(self.post(variant_of=parent.pk,count=1).status_code,400)

    def test_variations_storage_readiness_and_worker_seed(self):
        parent=PhotoJob.objects.create(owner=self.user,result=self.source,status='succeeded',prompt='original')
        (self.root/'QWEN21_READY').unlink()
        self.assertEqual(self.post(variant_of=parent.pk,count=1).status_code,503)
        (self.root/'QWEN21_READY').touch()
        self.source.size=100*1024**3;self.source.save()
        self.assertEqual(self.post(variant_of=parent.pk,count=1).status_code,400)
        self.assertEqual(PhotoJob.objects.count(),1)
        self.source.size=3;self.source.save()
        response=self.post(variant_of=parent.pk,count=1)
        job=PhotoJob.objects.get(pk=response.json()['jobs'][0]['id'])
        (self.root/'logs').mkdir();(self.root/'CHAT_DISABLED').touch()
        def start(args,**kwargs):
            import json
            data=json.loads(Path(args[-1]).read_text());self.assertEqual(data['seed'],job.seed)
            Image.new('RGB',(1024,1024),'blue').save(data['output']);return Mock(poll=lambda:0,returncode=0)
        with patch.object(photo_worker,'ROOT',self.root),patch.object(photo_worker.subprocess,'Popen',side_effect=start):photo_worker.run_job(job)
        job.refresh_from_db();self.assertEqual(job.status,'succeeded')

    def test_variation_degree_validation_and_saved_instruction(self):
        parent=PhotoJob.objects.create(owner=self.user,result=self.source,status='succeeded',prompt='original')
        for degree,label in [('low','Low'),('medium','Medium'),('high','High')]:
            response=self.post(variant_of=parent.pk,count=1,variation_degree=degree)
            self.assertEqual(response.status_code,202)
            self.assertEqual(response.json()['variation_degree'],degree)
            self.assertIn(label,response.json()['jobs'][0]['prompt'])
        for invalid in ['extreme',None,True,[]]:
            self.assertEqual(self.post(variant_of=parent.pk,count=1,variation_degree=invalid).status_code,400)
        self.assertEqual(PhotoJob.objects.filter(status='queued').count(),3)

    def test_account_quota_100g_counts_documents_and_media(self):
        from .storage_quota import ACCOUNT_BYTES,account_used
        from .models import VideoJob,VideoUpscaleJob
        self.assertEqual(ACCOUNT_BYTES,100*1024**3)
        self.source.size=200*1024**2;self.source.save()
        self.assertEqual(self.post().status_code,202)
        video=VideoJob.objects.create(owner=self.user,prompt='test',size=2*1024**3)
        upscale=VideoUpscaleJob.objects.create(owner=self.user,source=video,size=1024**3,status='succeeded')
        self.assertEqual(account_used(self.user),200*1024**2+3*1024**3)
        self.assertEqual(account_used(self.other),0)
        self.assertEqual(account_used(self.user,exclude_video=video.pk),200*1024**2+1024**3)
        self.assertEqual(account_used(self.user,exclude_upscale=upscale.pk),200*1024**2+2*1024**3)
