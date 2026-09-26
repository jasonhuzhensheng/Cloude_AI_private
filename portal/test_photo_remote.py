import json, tempfile, time
from pathlib import Path
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from portal.management.commands.photo_gpu_controller import validate, Supervisor
from portal.photo_remote import atomic

class PhotoGPUControlTests(SimpleTestCase):
    def setUp(self):
        self.config={'pod_id':'photo123','volume_id':'volume','max_hourly':2.10,'enabled':True,'initial_balance':130,'remaining_budget':130}
        self.pod={'id':'photo123','name':'private-ai-photo-ondemand','networkVolumeId':'volume','gpuCount':1,'costPerHr':2.09,'desiredStatus':'RUNNING'}
    def test_primary_and_old_pods_cannot_be_controlled(self):
        for identity in ['liokr6kbd9vlwk','94g82b06vc8dz9','ng5y0mjyosl248','0ohxnztjx7agwg']:
            with self.assertRaises(ValueError):validate(dict(self.config,pod_id=identity),dict(self.pod,id=identity))
    def test_identity_and_price_guards(self):
        self.assertTrue(validate(self.config,self.pod))
        self.assertFalse(validate(self.config,dict(self.pod,costPerHr=3)))
        for change in [{'name':'other'},{'networkVolumeId':'other'},{'gpuCount':2},{'id':'other'}]:
            with self.assertRaises(ValueError):validate(self.config,dict(self.pod,**change))
    def tick(self, active, demand_age=0, balance=100, state='RUNNING', enabled=True):
        with tempfile.TemporaryDirectory() as tmp:
            spool=Path(tmp);config=spool/'config.json';atomic(config,dict(self.config,enabled=enabled));atomic(spool/'demand.json',{'time':time.time()-demand_age})
            supervisor=object.__new__(Supervisor);supervisor.config=self.config
            supervisor.request=Mock(return_value=dict(self.pod,desiredStatus=state));supervisor.budget=Mock(return_value=balance)
            with patch('portal.management.commands.photo_gpu_controller.config_path',return_value=config),patch('portal.models.PhotoJob.objects') as jobs:
                jobs.filter.return_value.exists.return_value=active
                supervisor.tick(spool)
            return supervisor,json.loads((spool/'controller.json').read_text())
    def test_starts_only_with_live_demand(self):
        s,result=self.tick(True,state='EXITED');s.request.assert_any_call('POST','/start')
        s,result=self.tick(True,state='EXITED',demand_age=90);self.assertEqual(s.request.call_count,1)
    def test_stops_after_database_result_committed(self):
        s,result=self.tick(False);s.request.assert_any_call('POST','/stop')
        s,result=self.tick(True);self.assertEqual(s.request.call_count,1)
    def test_stops_on_coordinator_crash_or_disabled(self):
        for kwargs in [{'demand_age':90},{'enabled':False}]:
            s,result=self.tick(True,**kwargs);s.request.assert_any_call('POST','/stop')
    def test_budget_blocks_start_and_stops_running_gpu(self):
        s,result=self.tick(True,balance=9);s.request.assert_any_call('POST','/stop');self.assertTrue(result['blocked'])
    def test_topup_does_not_increase_approved_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=object.__new__(Supervisor);s.config=self.config;s.last_balance_check=0;s.session=Mock()
            r=s.session.post.return_value;r.json.return_value={'data':{'myself':{'clientBalance':120}}}
            self.assertEqual(s.budget(Path(tmp)),120)
            s.last_balance_check=0;r.json.return_value={'data':{'myself':{'clientBalance':220}}}
            self.assertEqual(s.budget(Path(tmp)),120)

from django.test import TestCase
from django.contrib.auth import get_user_model

class GPUPageTests(TestCase):
    def test_both_studios_label_two_gpus(self):
        user=get_user_model().objects.create_user(username='gpuviewer',password='Test-only-password')
        self.client.force_login(user)
        for url in ['/photos/','/video/']:
            response=self.client.get(url,secure=True)
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'视频 GPU / Video GPU')
            self.assertContains(response,'图片 GPU / Photo GPU')

class PhotoMetricTests(SimpleTestCase):
    def test_stopped_gpu_does_not_reuse_live_or_stale_metrics(self):
        from portal.photo_remote import public_status
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);spool=root/'photo-spool';spool.mkdir()
            atomic(root/'photo-gpu.json',{'enabled':True})
            atomic(spool/'controller.json',{'time':time.time(),'stage':'Stopped','provider_status':'EXITED','pod_id':'photo123'})
            atomic(spool/'executor.json',{'time':time.time(),'pod':'photo123','utilization':99,'memory_used_mib':40000,'memory_total_mib':96000})
            with self.settings(IMAGE_EDIT_ROOT=str(root)):
                d=public_status();self.assertNotIn('utilization',d);self.assertNotIn('pod_id',d)
                atomic(spool/'controller.json',{'time':time.time(),'stage':'Running','provider_status':'RUNNING','pod_id':'photo123'})
                self.assertEqual(public_status()['utilization'],99)
                atomic(spool/'executor.json',{'time':time.time()-90,'pod':'photo123','utilization':99})
                self.assertNotIn('utilization',public_status())

class ExecutorHeartbeatTests(SimpleTestCase):
    def test_heartbeat_is_written_even_without_gpu_metrics(self):
        import importlib.util
        from django.conf import settings
        spec=importlib.util.spec_from_file_location('photo_executor_test',settings.BASE_DIR/'deployment/photo_gpu_executor.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp,patch.object(module.subprocess,'check_output',side_effect=OSError('no driver')):
            module.SPOOL=Path(tmp)
            module.heartbeat()
            d=json.loads((Path(tmp)/'executor.json').read_text())
            self.assertIn('time',d)
            self.assertNotIn('utilization',d)
