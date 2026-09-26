import io,tempfile
from pathlib import Path
from django.test import TestCase,Client,override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from PIL import Image
from .models import Document,Conversation,ImageEditJob
from .gpu import GPULock

@override_settings(SECURE_SSL_REDIRECT=False)
class ImageEditTests(TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.env=override_settings(MEDIA_ROOT=self.temp.name,IMAGE_EDIT_READY=str(Path(self.temp.name)/'READY'));self.env.enable();self.addCleanup(self.env.disable)
        Path(self.temp.name,'READY').touch()
        self.a=get_user_model().objects.create_user('image_alice',password='test-password-239');self.b=get_user_model().objects.create_user('image_bob',password='test-password-789')
        self.chat=Conversation.objects.create(owner=self.a)
        img=io.BytesIO();Image.new('RGB',(64,64),'red').save(img,format='PNG')
        self.doc=Document.objects.create(owner=self.a,conversation=self.chat,name='test.png',size=len(img.getvalue()),file=SimpleUploadedFile('test.png',img.getvalue()),text='')
        self.client.force_login(self.a)
    def test_enqueue_and_duplicate_prevention(self):
        r=self.client.post(f'/api/images/{self.doc.id}/edit/',{'instruction':'Change red to green'},content_type='application/json')
        self.assertEqual(r.status_code,202)
        job=ImageEditJob.objects.get(pk=r.json()['id']);self.assertEqual(job.source,self.doc);self.assertEqual(job.owner,self.a)
        self.assertEqual(self.client.post(f'/api/images/{self.doc.id}/edit/',{'instruction':'Another edit'},content_type='application/json').status_code,429)
    def test_authentication_csrf_and_ownership(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.a)
        self.assertEqual(client.post(f'/api/images/{self.doc.id}/edit/',{'instruction':'test'},content_type='application/json').status_code,403)
        job=ImageEditJob.objects.create(owner=self.a,source=self.doc,prompt='private')
        self.client.force_login(self.b)
        for url in [f'/images/{self.doc.id}/',f'/api/images/{self.doc.id}/jobs/',f'/api/image-jobs/{job.id}/']:
            self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.post(f'/api/images/{self.doc.id}/edit/',{'instruction':'test'},content_type='application/json').status_code,404)
    def test_unavailable_and_invalid_input(self):
        self.assertEqual(self.client.post(f'/api/images/{self.doc.id}/edit/',{'instruction':''},content_type='application/json').status_code,400)
        Path(self.temp.name,'READY').unlink()
        self.assertEqual(self.client.post(f'/api/images/{self.doc.id}/edit/',{'instruction':'test'},content_type='application/json').status_code,503)
    def test_gpu_lock_is_shared_across_instances(self):
        first=GPULock();second=GPULock();self.assertTrue(first.acquire(False))
        try:self.assertFalse(second.acquire(False))
        finally:first.release()
        self.assertTrue(second.acquire(False));second.release()
    def test_worker_failure_restores_chat_and_releases_gpu(self):
        from unittest.mock import patch
        from portal.management.commands.image_worker import run_job
        job=ImageEditJob.objects.create(owner=self.a,source=self.doc,prompt='Change red to green',status='running')
        with patch('portal.management.commands.image_worker.stop_chat',side_effect=RuntimeError('test failure')),patch('portal.management.commands.image_worker.restore_chat') as restore:
            with self.assertRaises(RuntimeError):run_job(job)
            restore.assert_called_once()
        lock=GPULock();self.assertTrue(lock.acquire(False));lock.release()
    def test_odd_reference_dimensions_are_aligned_before_engine(self):
        from unittest.mock import patch,Mock
        from portal.management.commands.image_worker import run_job
        odd=io.BytesIO();Image.new('RGB',(447,672),'red').save(odd,format='PNG')
        self.doc.file.save('odd.png',SimpleUploadedFile('odd.png',odd.getvalue()))
        job=ImageEditJob.objects.create(owner=self.a,source=self.doc,prompt='Change red to green',status='running')
        root=Path(self.temp.name);(root/'logs').mkdir()
        def launch(command,**kwargs):
            with Image.open(command[command.index('-r')+1]) as ref:
                self.assertEqual(ref.width%64,0);self.assertEqual(ref.height%64,0)
                self.assertEqual(ref.size,(512,768))
                ref.save(command[command.index('-o')+1])
            process=Mock();process.wait.return_value=0;process.poll.return_value=0;return process
        with patch('portal.management.commands.image_worker.ROOT',root),patch('portal.management.commands.image_worker.stop_chat'),patch('portal.management.commands.image_worker.restore_chat'),patch('portal.management.commands.image_worker.subprocess.Popen',side_effect=launch):run_job(job)
        job.refresh_from_db();self.assertIsNotNone(job.result_id)
    def test_concurrent_mode_keeps_chat_and_does_not_take_chat_lock(self):
        from unittest.mock import patch
        from portal.management.commands.image_worker import run_job
        root=Path(self.temp.name);(root/'CONCURRENT_GPU_READY').touch()
        job=ImageEditJob.objects.create(owner=self.a,source=self.doc,prompt='test',status='running')
        lock=GPULock();self.assertTrue(lock.acquire(False))
        try:
            with patch('portal.management.commands.image_worker.ROOT',root),patch('portal.management.commands.image_worker.subprocess.run',side_effect=RuntimeError('MPS unavailable')),patch('portal.management.commands.image_worker.stop_chat') as stop,patch('portal.management.commands.image_worker.restore_chat') as restore:
                with self.assertRaises(RuntimeError):run_job(job)
                stop.assert_not_called();restore.assert_not_called()
        finally:lock.release()
