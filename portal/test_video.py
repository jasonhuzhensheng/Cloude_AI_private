import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from .models import Conversation, Document, VideoJob
from .management.commands.video_worker import command

class VideoTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.folder.name, IMAGE_EDIT_ROOT=self.folder.name, DATA=Path(self.folder.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        Path(self.folder.name, 'H3_READY').touch()
        self.user = User.objects.create_user('video-owner', password='test-video-password')
        self.other = User.objects.create_user('video-other', password='test-other-password')
        self.chat = Conversation.objects.create(owner=self.user)
        self.doc = Document.objects.create(owner=self.user, conversation=self.chat, name='reference.png', size=3, text='')
        self.client.force_login(self.user)

    def post(self, data):
        return self.client.post('/api/video/create/', data, content_type='application/json')

    def test_accepts_next_video_while_current_one_runs(self):
        self.assertEqual(self.post({'prompt': 'A red ball bouncing.'}).status_code, 202)
        VideoJob.objects.update(status='running')
        second=self.post({'prompt': 'A blue ball bouncing.'})
        self.assertEqual(second.status_code,202)
        self.assertEqual(second.json()['queue_position'],2)
        self.assertEqual(VideoJob.objects.count(),2)
        self.assertEqual(self.post({'prompt':'Third'}).status_code,202)
        self.assertEqual(self.post({'prompt':'Fourth'}).status_code,202)
        self.assertEqual(self.post({'prompt':'Fifth'}).status_code,429)
        queued=VideoJob.objects.filter(status='queued').order_by('id').first()
        self.client.post(f'/api/video/{queued.pk}/control/',{'action':'pause'},content_type='application/json')
        self.assertEqual(self.post({'prompt':'Replacement'}).status_code,202)

    def test_reference_owner_validation_and_required_source(self):
        self.assertEqual(self.post({'prompt': 'Animate', 'mode': 'ref2va'}).status_code, 400)
        self.client.force_login(self.other)
        self.assertEqual(self.post({'prompt': 'Animate', 'mode': 'ref2va', 'source_id': self.doc.pk}).status_code, 404)
        self.client.force_login(self.user)
        response = self.post({'prompt': 'Animate', 'mode': 'ref2va', 'source_id': self.doc.pk})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(VideoJob.objects.get().source_id, self.doc.pk)

    def test_outputs_and_job_list_are_private(self):
        job = VideoJob.objects.create(owner=self.user, prompt='Example', status='succeeded')
        job.video.save('video.mp4', ContentFile(b'private-video'))
        job.audio.save('audio.wav', ContentFile(b'private-audio'))
        response = self.client.get(f'/api/video/{job.pk}/video/')
        self.assertEqual(response['Content-Type'], 'video/mp4')
        self.assertEqual(b''.join(response.streaming_content), b'private-video')
        response = self.client.get(f'/api/video/{job.pk}/audio/?download=1')
        self.assertIn('attachment', response['Content-Disposition'])
        response.close()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get('/api/video/jobs/').json()['jobs'], [])
        self.assertEqual(self.client.get(f'/api/video/{job.pk}/video/').status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(f'/api/video/{job.pk}/video/').status_code, 302)

    def test_unready_and_invalid_requests(self):
        for payload in ([], {'prompt': ''}, {'prompt': 'a', 'mode': '../bad'}, {'prompt': 'a', 'source_id': True}):
            self.assertEqual(self.post(payload).status_code, 400)
        Path(self.folder.name, 'H3_READY').unlink()
        self.assertEqual(self.post({'prompt': 'A ball'}).status_code, 503)

    def test_studio_escapes_prompt_and_requires_login(self):
        self.assertEqual(self.client.get('/video/').status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get('/video/').status_code, 302)

    def test_command_passes_prompt_as_data_and_selects_reference_mode(self):
        job = SimpleNamespace(mode='ref2va', prompt='$(touch /tmp/not-a-command)')
        args = command(job, Path('/tmp/result.mp4'), Path('/tmp/reference.png'))
        self.assertEqual(args[args.index('-p') + 1], job.prompt)
        self.assertIn('--audio-vae', args)
        self.assertIn('--ref-image', args)
        self.assertNotIn('--init-img', args)

    def test_duration_bounds_and_progress(self):
        for duration in (0, 1801, 2.5, True, '30'):
            self.assertEqual(self.post({'prompt': 'Test', 'duration_seconds': duration}).status_code, 400)
        r = self.post({'prompt': 'Test', 'duration_seconds': 1800})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()['total_segments'], 772)
        self.assertEqual(r.json()['duration_seconds'], 1800)

    def test_pause_resume_owner_and_active_constraint(self):
        j = VideoJob.objects.create(owner=self.user, prompt='Test', duration_seconds=6)
        url = f'/api/video/{j.pk}/control/'
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {'action': 'pause'}, content_type='application/json').status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url, {'action': 'pause'}, content_type='application/json').json()['status'], 'paused')
        other = VideoJob.objects.create(owner=self.user, prompt='Other')
        self.assertEqual(self.client.post(url, {'action': 'resume'}, content_type='application/json').status_code, 200)
        other.status = 'succeeded'; other.save()
        self.assertEqual(self.client.post(url, {'action': 'resume'}, content_type='application/json').json()['status'], 'queued')

    def test_resumes_completed_segments_and_saves_final_result(self):
        from contextlib import nullcontext
        from .management.commands import video_worker as worker
        root = Path(self.folder.name)
        (root / 'logs').mkdir()
        job = VideoJob.objects.create(owner=self.user, prompt='Test', duration_seconds=6)
        checkpoint = root / 'video-checkpoints' / str(self.user.pk) / str(job.pk)
        checkpoint.mkdir(parents=True)
        (checkpoint / 'segment-0000.mp4').write_bytes(b'completed-before-restart')
        engine_calls = []
        def fake_run(job, args, log, timeout, env=None):
            if '-M' in args:
                engine_calls.append(args)
                Path(args[args.index('-o') + 1]).write_bytes(b'generated')
            elif args[-1].endswith('continuation.png'):
                from PIL import Image
                Image.new('RGB',(32,32),'blue').save(args[-1])
            else:
                Path(args[-1]).write_bytes(b'converted')
        def fake_probe(path):
            if not path.exists():
                raise RuntimeError('Missing')
            return 6 if path.name == 'output.mp4' else 56 / 24
        with override_settings(DATA=root), patch.object(worker, 'ROOT', root), patch.object(worker, 'media_slot', return_value=nullcontext()), patch.object(worker, 'GPULock'), patch.object(worker, 'stop_chat'), patch.object(worker, 'restore_chat'), patch.object(worker.subprocess, 'run'), patch.object(worker, 'probe', side_effect=fake_probe), patch.object(worker, 'run_process', side_effect=fake_run), patch.object(worker.shutil, 'disk_usage', return_value=SimpleNamespace(free=10*1024**3)):
            worker.run_job(job)
        self.assertTrue(all('--init-img' in a and a[a.index('--init-img')+1].endswith('continuation.png') for a in engine_calls))
        self.assertEqual(len(engine_calls), 2)  # Three segments total; first reused.
        self.assertEqual([a[a.index('--seed')+1] for a in engine_calls], ['43', '44'])
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded')
        self.assertEqual(job.completed_segments, 3)
        self.assertTrue(job.video.storage.exists(job.video.name))
        self.assertTrue(job.audio.storage.exists(job.audio.name))
        self.assertFalse(checkpoint.exists())

    def test_pause_preserves_completed_segments(self):
        from contextlib import nullcontext
        from .management.commands import video_worker as worker
        root = Path(self.folder.name)
        job = VideoJob.objects.create(owner=self.user, prompt='Test', pause_requested=True, duration_seconds=6)
        checkpoint = root / 'video-checkpoints' / str(self.user.pk) / str(job.pk)
        checkpoint.mkdir(parents=True)
        saved = checkpoint / 'segment-0000.mp4'; saved.write_bytes(b'saved')
        with override_settings(DATA=root), patch.object(worker, 'media_slot', return_value=nullcontext()), patch.object(worker, 'GPULock'), patch.object(worker, 'restore_chat'), patch.object(worker, 'run_process') as run, patch.object(worker.shutil, 'disk_usage', return_value=SimpleNamespace(free=10*1024**3)):
            with self.assertRaises(worker.Paused):
                worker.run_job(job)
        run.assert_not_called()
        self.assertEqual(saved.read_bytes(), b'saved')

    def test_orientation_validation_and_engine_dimensions(self):
        for value in ('square', '', [], None):
            self.assertEqual(self.post({'prompt': 'Test', 'orientation': value}).status_code, 400)
        result = self.post({'prompt': 'Test', 'orientation': 'portrait', 'duration_seconds': 6, 'resolution':'288p'})
        self.assertEqual(result.status_code, 202)
        self.assertEqual(result.json()['orientation'], 'portrait')
        job = VideoJob.objects.get()
        for orientation, size in [('portrait', ('288', '512')), ('landscape', ('512', '288'))]:
            job.orientation = orientation
            for index in (0, 1, 771):
                args = command(job, Path('/tmp/result.avi'), index=index)
                self.assertEqual((args[args.index('-W')+1], args[args.index('-H')+1]), size)

    def test_measured_eta_and_stop_feedback(self):
        from .video_views import job_data
        j = VideoJob.objects.create(owner=self.user, prompt='Test', duration_seconds=6, status='running', completed_segments=1, timed_segments=1, segment_seconds=80, stage='Generating segment 2 of 3')
        estimate = job_data(j)['estimate']
        self.assertTrue(estimate['measured'])
        self.assertLess(estimate['remaining_high'], estimate['total_high'])
        response = self.client.post(f'/api/video/{j.pk}/control/', {'action':'pause'}, content_type='application/json')
        self.assertTrue(response.json()['pause_requested'])
        self.assertIn('Stopping', response.json()['stage'])
        self.assertIsNone(response.json()['estimate']['remaining_high'])
        j.status='paused'; j.save()
        self.assertIsNone(job_data(j)['estimate']['remaining_low'])

    def test_native_resolution_sizes_and_validation(self):
        from .video_limits import dimensions
        from .video_views import timing_profile
        for resolution in ('4k', [], None):
            self.assertEqual(self.post({'prompt':'Test','resolution':resolution}).status_code,400)
        for resolution, native, export in [('480p',(864,480),(854,480)),('720p',(1280,736),(1280,720)),('1080p',(1920,1088),(1920,1080))]:
            for orientation in ('landscape','portrait'):
                job=SimpleNamespace(mode='fl2va',prompt='Test',resolution=resolution,orientation=orientation)
                args=command(job,Path('/tmp/result.avi'))
                expected=native if orientation=='landscape' else native[::-1]
                self.assertEqual((int(args[args.index('-W')+1]),int(args[args.index('-H')+1])),expected)
                self.assertEqual(dimensions(resolution,orientation),export if orientation=='landscape' else export[::-1])
        VideoJob.objects.create(owner=self.user,prompt='Test',resolution='480p',segment_seconds=50,timed_segments=1,status='succeeded')
        self.assertTrue(timing_profile(self.user,'fl2va','landscape','480p')['measured'])
        self.assertFalse(timing_profile(self.user,'fl2va','landscape','1080p')['measured'])

    def test_delete_completed_files_preserves_source_and_checks_owner(self):
        self.doc.file.save('source.png', ContentFile(b'original'))
        job = VideoJob.objects.create(owner=self.user, source=self.doc, prompt='Test', status='succeeded')
        job.video.save('result.mp4', ContentFile(b'video'))
        job.audio.save('result.wav', ContentFile(b'audio'))
        paths = [Path(job.video.path), Path(job.audio.path)]
        url = f'/api/video/{job.pk}/control/'
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(url, {'action': 'delete'}, content_type='application/json').status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 405)
        from django.test import Client
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(protected.post(url, {'action': 'delete'}, content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post(url, {'action': 'delete'}, content_type='application/json').status_code, 200)
        self.assertFalse(VideoJob.objects.filter(pk=job.pk).exists())
        self.assertTrue(all(not p.exists() for p in paths))
        self.assertTrue(Path(self.doc.file.path).exists())
        self.assertEqual(self.client.get(f'/api/video/{job.pk}/video/').status_code, 404)

    def test_delete_unfinished_jobs(self):
        for state in ('queued', 'running', 'paused', 'failed'):
            job = VideoJob.objects.create(owner=self.user, prompt='Test', status=state)
            checkpoint=Path(self.folder.name)/'video-checkpoints'/str(self.user.pk)/str(job.pk)
            checkpoint.mkdir(parents=True)
            (checkpoint/'segment-0000.mp4').write_bytes(b'saved segment')
            response=self.client.post(f'/api/video/{job.pk}/control/', {'action': 'delete'}, content_type='application/json')
            self.assertEqual(response.status_code, 202 if state=='running' else 200)
            if state=='running':
                job.refresh_from_db()
                self.assertTrue(job.pause_requested and job.delete_requested)
                job.status='paused';job.save()
                self.client.get('/api/video/jobs/')
            self.assertFalse(VideoJob.objects.filter(pk=job.pk).exists())
            self.assertFalse(checkpoint.exists())
            job.delete()

    def test_video_reference_upload_preview_and_ownership(self):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = io.BytesIO()
        Image.new('RGB', (32, 24), 'blue').save(data, format='PNG')
        raw = data.getvalue()
        response = self.client.post('/video/', {'file': SimpleUploadedFile('blue.png', raw, content_type='image/png')})
        self.assertEqual(response.status_code, 201)
        doc = Document.objects.get(pk=response.json()['id'])
        self.assertEqual(doc.file.read(), raw)
        preview = self.client.get(f'/video/?image={doc.pk}')
        self.assertEqual(preview['Content-Type'], 'image/png')
        self.assertEqual(b''.join(preview.streaming_content), raw)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f'/video/?image={doc.pk}').status_code, 404)
        self.assertEqual(self.client.post('/video/', {'file': SimpleUploadedFile('bad.png', b'bad')}).status_code, 400)

    def test_8k_upload_preserves_original_and_accepts_over_20mb(self):
        import io
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = io.BytesIO()
        Image.new('RGB', (7680, 4320), 'blue').save(data, format='JPEG')
        raw = data.getvalue() + b'\0' * (21 * 1024**2)
        response = self.client.post('/video/', {'file': SimpleUploadedFile('8k.jpg', raw)})
        self.assertEqual(response.status_code, 201)
        doc = Document.objects.get(pk=response.json()['id'])
        self.assertEqual(doc.file.read(), raw)
        data = io.BytesIO()
        Image.new('RGB', (8193, 1)).save(data, format='PNG')
        response = self.client.post('/video/', {'file': SimpleUploadedFile('wide.png', data.getvalue())})
        self.assertEqual(response.status_code, 400)
        self.assertIn('8192', response.json()['error'])

    def test_gpu_encoder_is_opt_in_and_reversible(self):
        with patch('portal.management.commands.video_worker.ROOT', Path(self.folder.name)):
            job=SimpleNamespace(mode='fl2va',prompt='Blue ball')
            args=command(job,Path('/tmp/out.avi'))
            self.assertEqual(args[args.index('--backend')+1], 'te=cpu')
            marker=Path(self.folder.name)/'H3_GPU_ENCODER_READY'
            marker.touch()
            args=command(job,Path('/tmp/out.avi'))
            self.assertEqual(args[args.index('--backend')+1], 'te=cuda0')
            self.assertIn('--offload-to-cpu',args)
            marker.unlink()
            args=command(job,Path('/tmp/out.avi'))
            self.assertEqual(args[args.index('--backend')+1], 'te=cpu')

    def test_reference_continuation_uses_first_frame_model(self):
        job=SimpleNamespace(mode='ref2va',prompt='Movement')
        args=command(job,Path('/tmp/out.avi'),Path('/tmp/last.png'),index=1,continuation=True)
        self.assertIn('--init-img',args)
        self.assertNotIn('--ref-image',args)
        self.assertTrue(args[args.index('--diffusion-model')+1].endswith('minimax_h3_fl2va_pruned-Q4_K.gguf'))

    def test_timeline_persistence_validation_and_segment_selection(self):
        from .video_timeline import segment_prompt, validate_timeline
        rows = [{'start': 3, 'end': 6, 'action': 'Walk forward'}, {'start': 0, 'end': 2, 'action': 'Wave once'}]
        response = self.post({'prompt': 'A person in a garden.', 'duration_seconds': 8, 'timeline': rows})
        self.assertEqual(response.status_code, 202)
        job = VideoJob.objects.get()
        self.assertEqual(response.json()['timeline'], job.timeline)
        self.assertEqual(job.timeline[0]['action'], 'Wave once')
        self.assertIn('Wave once', segment_prompt(job, 0))
        self.assertNotIn('Walk forward', segment_prompt(job, 0))
        self.assertNotIn('Wave once', segment_prompt(job, 1))
        self.assertIn('0.667–2.333s: Begin this action: Walk forward', segment_prompt(job, 1))
        self.assertIn('Continue the action already underway: Walk forward', segment_prompt(job, 2))
        self.assertNotIn('Walk forward', segment_prompt(job, 3))
        args = command(job, Path('/tmp/out.avi'), index=1)
        self.assertEqual(args[args.index('-p')+1], segment_prompt(job, 1))
        for invalid in [None, {}, [{'start': True, 'end': 2, 'action': 'a'}], [{'start': float('nan'), 'end': 2, 'action': 'a'}], [{'start': 0, 'end': 9, 'action': 'a'}], [{'start': 2, 'end': 2, 'action': 'a'}], [{'start': 0, 'end': 2, 'action': ' '}], [{'start': 0, 'end': 2, 'action': 'a'*40001}], [{'start': 0, 'end': 3, 'action': 'a'}, {'start': 2, 'end': 4, 'action': 'b'}]]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_timeline(invalid, 8)
                self.assertEqual(self.post({'prompt': 'Scene', 'duration_seconds': 8, 'timeline': invalid}).status_code, 400)
        self.assertEqual(segment_prompt(SimpleNamespace(prompt='Legacy'), 2), 'Legacy')

    def test_timeline_draft_explicit_and_sequential(self):
        from .video_timeline import draft_timeline
        rows, _ = draft_timeline('首先挥手，然后转身，最后走开', 9)
        self.assertEqual(rows, [{'start':0,'end':3,'action':'挥手'}, {'start':3,'end':6,'action':'转身'}, {'start':6,'end':9,'action':'走开'}])
        rows, _ = draft_timeline('0–2s: Wave; 2–6s: Turn', 9)
        self.assertEqual(rows[-1], {'start':2,'end':6,'action':'Turn'})
        self.assertEqual(len(draft_timeline('Wave and smile', 3)[0]), 1)
        for text in ['0-5s: Wave; 4-6s: Turn', '0-10s: Walk', '0-3s:', 'Scene. 0-3s: Walk']:
            with self.assertRaises(ValueError):
                draft_timeline(text, 9)
        response = self.client.post('/video/', {'prompt':'First wave, then turn', 'duration_seconds':6}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['timeline']), 2)
        self.assertFalse(VideoJob.objects.exists())
        self.client.logout()
        self.assertEqual(self.client.post('/video/', {}, content_type='application/json').status_code, 302)

    def test_long_prompt_roundtrip(self):
        prompt = '景' * 40000
        response = self.post({'prompt': prompt, 'duration_seconds': 3})
        self.assertEqual(response.status_code, 202)
        job = VideoJob.objects.get(pk=response.json()['id'])
        self.assertEqual(job.prompt, prompt)
        args = command(job, Path('/tmp/out.avi'))
        self.assertEqual(args[args.index('-p') + 1], prompt)
        self.assertEqual(self.post({'prompt': '景' * 40001}).status_code, 400)
