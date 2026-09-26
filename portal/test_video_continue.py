import hashlib
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from django.test import TestCase
from django.core.files.base import ContentFile
from .test_video import VideoTests
from .models import VideoJob
from .management.commands import video_worker as worker


class ContinueTests(TestCase):
    def setUp(self):
        VideoTests.setUp(self)
        Path(self.folder.name,'H3_CONTINUE_READY').touch()
        self.parent=VideoJob.objects.create(owner=self.user,prompt='Original',status='succeeded',duration_seconds=5,resolution='720p',orientation='portrait')
        self.parent.video.save('original.mp4',ContentFile(b'original'))

    def create(self, **extra):
        return self.client.post('/api/video/create/',dict(prompt='Turn and wave',duration_seconds=3,continuation_of_id=self.parent.pk,**extra),content_type='application/json')

    def test_inherits_output_and_protects_source(self):
        response=self.create()
        self.assertEqual(response.status_code,202)
        child=VideoJob.objects.get(pk=response.json()['id'])
        self.assertEqual((child.prefix_seconds,child.duration_seconds,child.mode,child.resolution,child.orientation),(5,3,'fl2va','720p','portrait'))
        self.assertEqual(response.json()['output_duration_seconds'],8)
        self.assertEqual(self.client.post(f'/api/video/{self.parent.pk}/control/',{'action':'delete'},content_type='application/json').status_code,409)
        self.assertEqual(Path(self.parent.video.path).read_bytes(),b'original')
        self.assertEqual(self.client.post(f'/api/video/{child.pk}/control/',{'action':'delete'},content_type='application/json').status_code,200)
        self.assertTrue(Path(self.parent.video.path).exists())

    def test_continuation_timeline_is_relative_to_new_section(self):
        from .video_timeline import segment_prompt
        rows=[dict(start=0,end=2,action='Wave'),dict(start=2,end=3,action='Turn')]
        response=self.create(timeline=rows)
        self.assertEqual(response.status_code,202)
        child=VideoJob.objects.get(pk=response.json()['id'])
        self.assertEqual(child.timeline,rows)
        self.assertEqual(child.prefix_seconds,5)
        self.assertIn('Wave',segment_prompt(child,0))
        self.assertNotIn('Wave',segment_prompt(child,1))
        self.assertIn('Turn',segment_prompt(child,1))
        self.assertEqual(self.create(timeline=[dict(start=5,end=7,action='Wrong original offset')]).status_code,400)

    def test_ownership_readiness_length_and_queue(self):
        self.client.force_login(self.other)
        self.assertEqual(self.create().status_code,404)
        self.client.force_login(self.user)
        self.parent.prefix_seconds=1793;self.parent.save()
        self.assertEqual(self.create().status_code,400)
        self.parent.prefix_seconds=0;self.parent.status='running';self.parent.save()
        self.assertEqual(self.create().status_code,409)
        self.parent.status='succeeded';self.parent.save()
        for _ in range(4):
            self.assertEqual(self.create().status_code,202)
        self.assertEqual(self.create().status_code,429)

    @skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required for real media verification')
    def test_real_last_frame_and_combined_media_without_gpu(self):
        root=Path(self.folder.name);(root/'logs').mkdir()
        source=root/'source.mp4'
        run=subprocess.run
        run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=size=512x288:rate=24','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)],check=True)
        original=source.read_bytes()
        self.parent.video.save('source.mp4',ContentFile(original))
        self.parent.duration_seconds=1;self.parent.prefix_seconds=0;self.parent.resolution='288p';self.parent.orientation='landscape';self.parent.save()
        child=VideoJob.objects.create(owner=self.user,continuation_of=self.parent,prefix_seconds=1,duration_seconds=1,prompt='New movement',resolution='288p')
        expected=root/'last.png'
        run(['ffmpeg','-y','-v','error','-i',self.parent.video.path,'-vf','reverse','-frames:v','1',str(expected)],check=True)
        engine=[]
        def process(job,args,log,timeout,env=None):
            worker.check_pause(job)
            if '-M' in args:
                from PIL import Image
                engine.append(args)
                frame=Path(args[args.index('--init-img')+1])
                self.assertEqual(Image.open(frame).tobytes(),Image.open(expected).tobytes())
                self.assertEqual(args[args.index('-p')+1],'New movement')
                raw=args[args.index('-o')+1]
                run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=size=512x288:rate=24','-f','lavfi','-i','sine=frequency=880:sample_rate=48000','-t',str(56/24),'-c:v','mpeg4','-c:a','pcm_s16le',raw],check=True)
            else:
                run(args,stdout=log,stderr=log,timeout=timeout,check=True)
        def startup(args,**kwargs):
            if args[0] in ('bash','nvidia-cuda-mps-control'):
                return None
            return run(args,**kwargs)
        with patch.object(worker,'ROOT',root),patch.object(worker,'media_slot',return_value=nullcontext()),patch.object(worker,'GPULock'),patch.object(worker,'stop_chat'),patch.object(worker,'restore_chat'),patch.object(worker,'run_process',side_effect=process),patch.object(worker.subprocess,'run',side_effect=startup):
            worker.run_job(child)
        child.refresh_from_db()
        self.assertEqual(len(engine),1)
        self.assertEqual(child.status,'succeeded')
        self.assertAlmostEqual(worker.probe(Path(child.video.path)),2,delta=.15)
        self.assertEqual(hashlib.sha256(Path(self.parent.video.path).read_bytes()).digest(),hashlib.sha256(original).digest())
        self.assertTrue(child.audio.size>0)
