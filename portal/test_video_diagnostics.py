from pathlib import Path
from django.test import TestCase
from .test_video import VideoTests
from .models import VideoJob
from .video_diagnostics import report

class DiagnosticTests(TestCase):
    def setUp(self):
        VideoTests.setUp(self)
        self.job=VideoJob.objects.create(owner=self.user,prompt='safety: blocked',duration_seconds=3)
        self.log=Path(self.folder.name,'logs',f'h3-job-{self.job.pk}.log')
        self.log.parent.mkdir()
    def test_unknown_and_prompt_echo_not_refusal(self):
        self.assertEqual(report(self.job)['state'],'unavailable')
        self.log.write_text('prompt: safety: blocked\nSafety: not blocked\nRendering frames\n')
        self.assertEqual(report(self.job)['state'],'no_explicit_signal')
    def test_signals_and_private_report(self):
        self.log.write_text('[12:00:01] WARNING: Safety filter: blocked\nPrompt was truncated\nSECRET_PATH=/private/foo\n')
        data=report(self.job)
        self.assertEqual(data['state'],'review')
        self.assertEqual(len(data['events']),2)
        url=f'/api/video/{self.job.pk}/prompt-log/'
        result=self.client.get(url)
        self.assertEqual(result.status_code,200)
        self.assertNotIn('SECRET_PATH',result.content.decode())
        self.assertIn('Reconstructed',result.json()['prompt_source'])
        self.assertEqual(len(result.json()['segments']),2)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code,404)
