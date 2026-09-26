import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from django.test import TestCase,override_settings
from django.contrib.auth import get_user_model
from . import gpu_status

@override_settings(SECURE_SSL_REDIRECT=False)
class GPUStatusTests(TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        context=override_settings(DATA=Path(folder.name))
        context.enable()
        self.addCleanup(context.disable)
        gpu_status._sample=None;gpu_status._sample_time=0
        self.user=get_user_model().objects.create_user('gpu_test_user')
    def test_login_required(self):
        with patch('portal.gpu_status.collect') as collect:
            self.assertEqual(self.client.get('/api/gpu/status/').status_code,302);collect.assert_not_called()
    def test_metrics_cached_and_unknown_values(self):
        self.client.force_login(self.user)
        output=Mock(stdout='NVIDIA RTX A6000, 27, 26000, 49140, 54, [N/A]\n')
        with patch('portal.gpu_status.subprocess.run',return_value=output) as run,patch('portal.gpu_status.requests.get',return_value=Mock(status_code=200)):
            first=self.client.get('/api/gpu/status/');second=self.client.get('/api/gpu/status/')
        data=first.json();self.assertEqual(data['utilization'],27);self.assertEqual(data['memory_total_mib'],49140);self.assertIsNone(data['power_w']);self.assertEqual(data['state'],'GPU active');self.assertEqual(run.call_count,1);self.assertEqual(data,second.json());self.assertEqual(first['Cache-Control'],'private, no-store')
    def test_failure_does_not_expose_diagnostics(self):
        self.client.force_login(self.user)
        with patch('portal.gpu_status.collect',side_effect=RuntimeError('private process data')):r=self.client.get('/api/gpu/status/')
        self.assertFalse(r.json()['available']);self.assertNotIn('private process data',r.content.decode())
