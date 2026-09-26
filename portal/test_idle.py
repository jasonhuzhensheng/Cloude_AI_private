import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from portal.idle import Activity, ActivityMiddleware, IDLE_SECONDS, check_idle
from portal.management.commands.idle_watchdog import RunpodStop


class IdleDecisionTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 10000
        self.activity = Activity(self.directory.name, clock=lambda: self.now)
        self.activity.touch()
        self.stop = Mock()

    def tick(self, busy=False, dry=False):
        return check_idle(self.activity, lambda: busy, self.stop, dry_run=dry)

    def test_twenty_minutes_and_dry_run(self):
        self.now += IDLE_SECONDS - 1
        self.assertEqual(self.tick(), 'waiting')
        self.now += 1
        self.assertEqual(self.tick(dry=True), 'would-stop')
        self.stop.assert_not_called()
        self.assertFalse(self.activity.read()['draining'])
        self.assertEqual(self.tick(), 'stopping')
        self.stop.assert_called_once()
        self.assertEqual(self.tick(), 'stopping')
        self.stop.assert_called_once()

    def test_live_request_and_queued_jobs_delay_shutdown(self):
        self.now += IDLE_SECONDS + 10
        with self.activity.gate():
            self.assertEqual(self.tick(), 'busy')
        self.now += IDLE_SECONDS + 10
        self.assertEqual(self.tick(busy=True), 'busy')
        self.assertEqual(self.tick(), 'waiting')
        self.stop.assert_not_called()

    def test_exclusive_decision_blocks_new_requests(self):
        self.now += IDLE_SECONDS
        self.stop.side_effect = lambda: self.assertIsNone(self.activity.gate())
        self.assertEqual(self.tick(), 'stopping')

    def test_uncertain_stop_keeps_admissions_closed(self):
        self.now += IDLE_SECONDS
        self.stop.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            self.tick()
        self.assertTrue(self.activity.read()['draining'])

    def test_future_clock_and_corrupt_state_never_stop(self):
        self.now -= 100
        self.assertEqual(self.tick(), 'waiting')
        (self.activity.root / 'state.json').write_text('broken')
        with self.assertRaises(ValueError):
            self.tick()
        self.stop.assert_not_called()


@override_settings(IDLE_SHUTDOWN_ENABLED=True)
class IdleMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = override_settings(DATA=Path(self.directory.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.activity = Activity()
        self.activity.update(last_activity=1, draining=False)
        self.factory = RequestFactory()

    def request(self, path='/', authenticated=True):
        req = self.factory.get(path)
        req.user = SimpleNamespace(is_authenticated=authenticated)
        return req

    def test_polls_and_anonymous_traffic_do_not_reset_idle(self):
        middleware = ActivityMiddleware(lambda r: HttpResponse('ok'))
        for path in ['/api/gpu/status/', '/api/image-jobs/3/', '/api/images/3/jobs/']:
            middleware(self.request(path))
        middleware(self.request(authenticated=False))
        self.assertEqual(self.activity.read()['last_activity'], 1)
        middleware(self.request())
        self.assertGreater(self.activity.read()['last_activity'], 1)

    def test_stream_is_busy_until_consumed_or_closed_without_iteration(self):
        for consume in [True, False]:
            response = ActivityMiddleware(lambda r: StreamingHttpResponse(iter(['hello'])))(self.request())
            self.assertIsNone(self.activity.gate(exclusive=True))
            if consume:
                self.assertEqual(b''.join(response), b'hello')
            response.close()
            with self.activity.gate(exclusive=True) as handle:
                self.assertIsNotNone(handle)

    def test_shutdown_rejects_new_work(self):
        self.activity.update(draining=True)
        view = Mock()
        self.assertEqual(ActivityMiddleware(view)(self.request()).status_code, 503)
        view.assert_not_called()

    def test_rejected_request_does_not_reset_idle(self):
        ActivityMiddleware(lambda r: HttpResponse(status=403))(self.request())
        self.assertEqual(self.activity.read()['last_activity'], 1)

    def test_exception_releases_request(self):
        def fail(request):
            raise RuntimeError()
        with self.assertRaises(RuntimeError):
            ActivityMiddleware(fail)(self.request())
        with self.activity.gate(exclusive=True) as handle:
            self.assertIsNotNone(handle)


class PresenceTests(TestCase):
    def test_requires_login_post_and_csrf(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post('/api/activity/').status_code, 403)
        user = get_user_model().objects.create_user(username='presence-test')
        client.force_login(user)
        self.assertEqual(client.get('/api/activity/').status_code, 405)
        self.assertEqual(client.post('/api/activity/').status_code, 403)
        client.get('/')
        response = client.post('/api/activity/', HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['idle_seconds'], 1200)

    def test_worker_cannot_stop_without_verified_gateway(self):
        from django.core.management import call_command, CommandError
        with patch.dict('os.environ', {'PORTAL_WAKE_GATEWAY_READY': '0'}), patch('requests.post') as post:
            with self.assertRaises(CommandError):
                call_command('idle_watchdog', once=True)
            post.assert_not_called()


class StopApiTests(SimpleTestCase):
    def test_only_exact_stop_endpoint_and_no_redirects(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / 'key'
            token.write_text('test-token')
            token.chmod(0o600)
            client = RunpodStop('94g82b06vc8dz9', token)
            with patch('requests.post', return_value=Mock(status_code=200)) as post:
                client()
            self.assertEqual(post.call_args.args[0], 'https://rest.runpod.io/v1/pods/94g82b06vc8dz9/stop')
            self.assertFalse(post.call_args.kwargs['allow_redirects'])
