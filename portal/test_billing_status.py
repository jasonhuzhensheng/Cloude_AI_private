import tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from .billing_status import snapshot


@override_settings(SECURE_SSL_REDIRECT=False)
class BillingTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        context = override_settings(DATA=Path(folder.name))
        context.enable()
        self.addCleanup(context.disable)

    def test_threshold_topup_low_balance_and_failure(self):
        with patch('portal.billing_status.time.time') as clock, patch('portal.billing_status.fetch_balance') as fetch:
            clock.return_value=1000;fetch.return_value=146.67
            self.assertEqual(snapshot()['balance_usd'],146.67)
            snapshot();self.assertEqual(fetch.call_count,1)
            clock.return_value=1301;fetch.return_value=140
            self.assertEqual(snapshot()['balance_usd'],146.67)
            clock.return_value=1602;fetch.return_value=136.67
            self.assertEqual(snapshot()['balance_usd'],136.67)
            clock.return_value=1903;fetch.return_value=145
            self.assertEqual(snapshot()['balance_usd'],145)
            clock.return_value=2204;fetch.return_value=9
            self.assertEqual(snapshot()['balance_usd'],9)
            clock.return_value=2505;fetch.return_value=8
            self.assertEqual(snapshot()['balance_usd'],8)
            clock.return_value=2806;fetch.side_effect=RuntimeError('secret')
            result=snapshot()
            self.assertTrue(result['stale']);self.assertEqual(result['balance_usd'],8)
            self.assertNotIn('secret',str(result))

    def test_admin_only_even_after_admin_request(self):
        admin=get_user_model().objects.create_user('admin',is_staff=True)
        user=get_user_model().objects.create_user('normal')
        with patch('portal.billing_status.snapshot',return_value={'balance_usd':100}) as fetch, patch('portal.gpu_status.collect',return_value={'available':False}):
            self.client.force_login(admin)
            self.assertIn('billing',self.client.get('/api/gpu/status/').json())
            self.client.force_login(user)
            self.assertNotIn('billing',self.client.get('/api/gpu/status/').json())
            self.assertEqual(fetch.call_count,1)
            self.client.logout()
            self.assertEqual(self.client.get('/api/gpu/status/').status_code,302)
