"""Cross-process activity leases for the optional Runpod idle shutdown worker."""
import fcntl
import json
import os
import threading
import time
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST

IDLE_SECONDS = 20 * 60


def context(request):
    return {'idle_shutdown_enabled': settings.IDLE_SHUTDOWN_ENABLED}


class Activity:
    def __init__(self, root=None, clock=time.time):
        self.root = Path(root or settings.DATA) / 'activity'
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock

    def gate(self, exclusive=False):
        handle = (self.root / 'requests.lock').open('a')
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return handle

    def read(self):
        try:
            return json.loads((self.root / 'state.json').read_text())
        except FileNotFoundError:
            return {'last_activity': self.clock(), 'draining': False}

    def update(self, **changes):
        with (self.root / 'state.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self.read()
            state.update(changes)
            temporary = self.root / 'state.tmp'
            temporary.write_text(json.dumps(state))
            temporary.chmod(0o600)
            os.replace(temporary, self.root / 'state.json')
            return state

    def touch(self):
        self.update(last_activity=self.clock())


class Lease:
    def __init__(self, activity, handle):
        self.activity, self.handle = activity, handle
        self.lock = threading.Lock()
        self.record = False

    def close(self):
        with self.lock:
            if self.handle is not None:
                try:
                    if self.record:
                        self.activity.touch()
                finally:
                    self.handle.close()
                    self.handle = None


def is_poll(request):
    path = request.path_info
    return request.method == 'GET' and (
        path == '/api/gpu/status/'
        or path == '/api/video/jobs/'
        or path.startswith('/api/image-jobs/')
        or (path.startswith('/api/images/') and path.endswith('/jobs/'))
        or path.startswith('/static/')
    )


class ActivityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.IDLE_SHUTDOWN_ENABLED or not request.user.is_authenticated or is_poll(request):
            return self.get_response(request)
        activity = Activity()
        handle = activity.gate()
        if handle is None:
            return self.sleeping()
        lease = Lease(activity, handle)
        try:
            if activity.read().get('draining'):
                handle.close()
                lease.handle = None
                return self.sleeping()
            response = self.get_response(request)
            lease.record = response.status_code < 400
            if response.streaming:
                content = response.streaming_content

                def stream():
                    try:
                        yield from content
                    finally:
                        lease.close()

                response.streaming_content = stream()
                # Also release when a client disconnects before consuming any bytes.
                response._resource_closers.append(lease.close)
            else:
                lease.close()
            return response
        except BaseException:
            lease.close()
            raise

    @staticmethod
    def sleeping():
        response = JsonResponse({'error': 'The server is going to sleep. Please reopen the wake-up page.'}, status=503)
        response['Retry-After'] = '30'
        response['Cache-Control'] = 'no-store'
        return response


@login_required
@require_POST
def presence(request):
    # ActivityMiddleware records authenticated, CSRF-checked user interactions.
    response = JsonResponse({'enabled': settings.IDLE_SHUTDOWN_ENABLED, 'idle_seconds': IDLE_SECONDS})
    response['Cache-Control'] = 'no-store'
    return response


def check_idle(activity, has_jobs, stop, dry_run=False):
    """Exclude new work atomically while deciding and submitting a stop."""
    gate = activity.gate(exclusive=True)
    if gate is None:
        activity.touch()
        return 'busy'
    with gate:
        if has_jobs():
            activity.touch()
            return 'busy'
        state = activity.read()
        if state.get('draining'):
            return 'stopping'
        elapsed = activity.clock() - float(state['last_activity'])
        if elapsed < 0:
            activity.touch()
            return 'waiting'
        if elapsed < IDLE_SECONDS:
            return 'waiting'
        if dry_run:
            return 'would-stop'
        activity.update(draining=True)
        try:
            stop()
        except Exception:
            # The API may have accepted an uncertain request. Leave admissions
            # closed until the controller checks desiredStatus before retrying.
            raise
        return 'stopping'
