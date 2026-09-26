from pathlib import Path
from unittest.mock import patch
from django.core.files.base import ContentFile
from django.test import TestCase
from . import test_video
from .models import VideoJob, VideoUpscaleJob
from .management.commands.upscale_worker import command, validate_result

class UpscaleTests(TestCase):
    def setUp(self):
        test_video.VideoTests.setUp(self)
        self.source=VideoJob.objects.create(owner=self.user,prompt='Synthetic',status='succeeded',resolution='480p')
        self.source.video.save('original.mp4',ContentFile(b'original'))
    def control(self,data):
        return self.client.post(f'/api/video/{self.source.pk}/control/',data,content_type='application/json')
    def test_readiness_target_ownership_queue_and_cancel(self):
        self.assertEqual(self.control({'action':'upscale','resolution':1080}).status_code,503)
        Path(self.folder.name,'SEEDVR2_READY').touch()
        self.assertEqual(self.control({'action':'upscale','resolution':480}).status_code,400)
        self.assertEqual(self.control({'action':'upscale','resolution':1080}).status_code,202)
        job=VideoUpscaleJob.objects.get()
        self.assertEqual(self.control({'action':'upscale','resolution':2160}).status_code,429)
        self.assertEqual(self.control({'action':'delete'}).status_code,409)
        self.client.force_login(self.other)
        self.assertEqual(self.control({'action':'cancel-upscale','upscale_id':job.pk}).status_code,404)
        self.client.force_login(self.user)
        self.assertEqual(self.control({'action':'cancel-upscale','upscale_id':job.pk}).status_code,200)
        job.refresh_from_db();self.assertEqual(job.status,'cancelled')
        self.assertEqual(self.control({'action':'delete-upscale','upscale_id':job.pk}).status_code,200)
        self.assertTrue(Path(self.source.video.path).exists())
    def test_result_is_private_and_deleted_separately(self):
        job=VideoUpscaleJob.objects.create(owner=self.user,source=self.source,status='succeeded',resolution=1080)
        job.video.save('upscaled.mp4',ContentFile(b'upscaled'))
        url=f'/api/video/{self.source.pk}/upscale/?result={job.pk}'
        self.assertEqual(self.client.get(url).status_code,200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code,404)
        self.client.force_login(self.user)
        path=job.video.path
        self.control({'action':'delete-upscale','upscale_id':job.pk})
        self.assertFalse(Path(path).exists());self.assertTrue(Path(self.source.video.path).exists())
    def test_cli_and_result_validation(self):
        job=VideoUpscaleJob(source=self.source,resolution=1080)
        args=command(job,Path('/tmp/source.mp4'),Path('/tmp/output'))
        self.assertIn('seedvr2_ema_7b_fp16.safetensors',args)
        self.assertIn('--chunk_size',args)
        before={'streams':[{'codec_type':'video','width':854,'height':480,'nb_frames':'72'},{'codec_type':'audio'}],'format':{'duration':'3'}}
        after={'streams':[{'codec_type':'video','width':1920,'height':1080,'nb_frames':'72'},{'codec_type':'audio'}],'format':{'duration':'3'}}
        validate_result(before,after,1080)
        after['streams'][0]['nb_frames']='60'
        with self.assertRaises(RuntimeError):validate_result(before,after,1080)
    def test_worker_publishes_verified_result_and_preserves_original(self):
        from .management.commands import upscale_worker as worker
        Path(self.folder.name,'CHAT_DISABLED').touch()
        Path(self.folder.name,'logs').mkdir()
        job=VideoUpscaleJob.objects.create(owner=self.user,source=self.source,resolution=1080,status='running')
        before={'streams':[{'codec_type':'video','width':854,'height':480,'nb_frames':'72'},{'codec_type':'audio'}],'format':{'duration':'3'}}
        after={'streams':[{'codec_type':'video','width':1920,'height':1080,'nb_frames':'72'},{'codec_type':'audio'}],'format':{'duration':'3'}}
        def fake_process(job,args,log,directory):
            if '--output' in args:
                Path(args[args.index('--output')+1],'input.mp4').write_bytes(b'enhanced')
            else:Path(args[-1]).write_bytes(b'muxed')
        with patch.object(worker,'run_process',side_effect=fake_process),patch.object(worker,'probe',side_effect=[before,after]):
            worker.run_job(job)
        job.refresh_from_db();self.assertEqual(job.status,'succeeded')
        self.assertEqual(Path(job.video.path).read_bytes(),b'muxed')
        self.assertEqual(Path(self.source.video.path).read_bytes(),b'original')

    def test_delete_running_stops_then_cleans_only_derived_copy(self):
        from .video_upscale import cleanup_deleted
        source=self.source
        job=VideoUpscaleJob.objects.create(owner=self.user,source=source,resolution=1080,status='running')
        url=f'/api/video/{source.pk}/control/'
        response=self.client.post(url,{'action':'delete-upscale','upscale_id':job.pk},content_type='application/json')
        self.assertEqual(response.status_code,202)
        job.refresh_from_db()
        self.assertTrue(job.cancel_requested);self.assertTrue(job.delete_requested)
        cleanup_deleted(self.user)
        self.assertTrue(VideoUpscaleJob.objects.filter(pk=job.pk).exists())
        job.status='cancelled';job.save()
        cleanup_deleted(self.user)
        self.assertFalse(VideoUpscaleJob.objects.filter(pk=job.pk).exists())
        self.assertTrue(source.video.storage.exists(source.video.name))

    def test_estimate_uses_processing_clock_and_handles_overdue(self):
        from datetime import timedelta
        from django.utils import timezone
        from .video_upscale import estimate
        job=VideoUpscaleJob.objects.create(owner=self.user,source=self.source,resolution=2160,status='queued')
        queued=estimate(job)
        self.assertTrue(queued['waiting'])
        self.assertGreater(queued['total_high'],queued['total_low'])
        root=Path(self.folder.name)
        (root/'logs').mkdir(exist_ok=True)
        start=timezone.now()-timedelta(minutes=2)
        job.created=start;job.status='running';job.save()
        (root/'logs'/f'upscale-{job.pk}.log').write_text('['+start.strftime('%H:%M:%S')+'.000] Loading model')
        value=estimate(job)
        self.assertFalse(value['waiting'])
        self.assertGreaterEqual(value['elapsed_seconds'],120)
        self.assertLess(value['remaining_high'],value['total_high'])
        with patch('portal.video_upscale.timezone.now',return_value=start+timedelta(seconds=queued['total_high']+10)):
            self.assertTrue(estimate(job)['overdue'])
            self.assertIsNone(estimate(job)['remaining_high'])

    def test_live_chunks_override_short_clip_estimate_and_handle_midnight(self):
        from datetime import datetime, timezone, timedelta
        from .video_upscale import estimate
        start=datetime(2026,9,20,23,0,tzinfo=timezone.utc)
        job=VideoUpscaleJob.objects.create(owner=self.user,source=self.source,resolution=2160,status='running')
        job.created=start
        logs=Path(self.folder.name,'logs');logs.mkdir(exist_ok=True)
        log=logs/f'upscale-{job.pk}.log'
        log.write_text('[23:00:00.000] Loading\n'
            '[23:01:00.000] Chunk 1/3: 65 new + 0 context frames\n'
            '[23:30:00.000] Output assembled: 65 frames\n'
            '[23:30:30.000] Chunk 2/3: 65 new + 4 context frames\n'
            '[00:00:00.000] Output assembled: 69 frames\n'
            '[00:00:30.000] Chunk 3/3: 14 new + 4 context frames\n'
            '[00:02:00.000] Upscaling batch 1/14\n')
        with patch('portal.video_upscale.timezone.now',return_value=start+timedelta(minutes=62)):
            result=estimate(job)
        self.assertTrue(result['measured'])
        self.assertIn('2/3',result['detail'])
        self.assertEqual(result['elapsed_seconds'],3720)
        self.assertGreater(result['remaining_high'],400)
        self.assertLess(result['remaining_high'],900)
        with patch('portal.video_upscale.timezone.now',return_value=start+timedelta(minutes=70)):
            self.assertIsNone(estimate(job)['remaining_high'])
        job.cancel_requested=True
        with patch('portal.video_upscale.timezone.now',return_value=start+timedelta(minutes=62)):
            self.assertIsNone(estimate(job)['remaining_high'])

    def test_inner_batches_do_not_count_as_completed_chunks(self):
        from .video_upscale import _chunk_log
        logs=Path(self.folder.name,'logs');logs.mkdir(exist_ok=True)
        log=logs/'partial.log'
        log.write_text('[12:00:00] Chunk 1/3: 65 new + 0 context frames\n[12:01:00] Decoding batch 61/61\n')
        result=_chunk_log(str(log),1,log.stat().st_size)
        self.assertIsNone(result['chunks'][0]['end'])
        log.write_text(log.read_text()+'[12:02:00] Output assembled: 65 frames\n')
        result=_chunk_log(str(log),2,log.stat().st_size)
        self.assertIsNotNone(result['chunks'][0]['end'])
        self.assertIsNone(_chunk_log(str(log),3,9*1024*1024))

    def test_new_upscale_promotes_old_video_and_sorts_children(self):
        for _ in range(21):VideoJob.objects.create(owner=self.user,prompt='Newer',status='succeeded')
        first=VideoUpscaleJob.objects.create(owner=self.user,source=self.source,status='succeeded')
        second=VideoUpscaleJob.objects.create(owner=self.user,source=self.source,status='succeeded')
        data=self.client.get('/api/video/jobs/').json()['jobs']
        self.assertEqual(data[0]['id'],self.source.pk)
        self.assertEqual([u['id'] for u in data[0]['upscales']],[second.pk,first.pk])
        self.assertIn('created',data[0]['upscales'][0])
