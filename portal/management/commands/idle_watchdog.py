import fcntl
import os
import re
import time
import uuid
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from portal.idle import Activity, check_idle
from portal.models import ImageEditJob, VideoJob


class RunpodStop:
    def __init__(self, pod_id, token_path):
        if not re.fullmatch(r'[a-z0-9]+', pod_id or ''):
            raise CommandError('A valid current Runpod Pod ID is required.')
        path = Path(token_path)
        if path.stat().st_mode & 0o077:
            raise CommandError('The Runpod token file must be readable only by its owner (0600).')
        self.token = path.read_text().strip()
        if not self.token:
            raise CommandError('The Runpod token file is empty.')
        self.url = 'https://rest.runpod.io/v1/pods/' + pod_id

    def __call__(self):
        response = requests.post(self.url + '/stop', headers={'Authorization': 'Bearer ' + self.token}, timeout=(5, 20), allow_redirects=False)
        if response.status_code != 200:
            raise RuntimeError('Runpod stop did not return success.')

    def is_running(self):
        response = requests.get(self.url, headers={'Authorization': 'Bearer ' + self.token}, timeout=(5, 20), allow_redirects=False)
        response.raise_for_status()
        return response.json()['desiredStatus'] == 'RUNNING'


class Command(BaseCommand):
    help = 'Stop the current Runpod Pod after 20 minutes without active work.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--once', action='store_true')

    def handle(self, *args, **options):
        dry = options['dry_run']
        if not dry and (not settings.IDLE_SHUTDOWN_ENABLED or os.environ.get('PORTAL_WAKE_GATEWAY_READY') != '1'):
            raise CommandError('Configure and verify the wake-up gateway before enabling automatic shutdown.')
        activity = Activity()
        singleton = (activity.root / 'watchdog.lock').open('a')
        try:
            fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            singleton.close()
            raise CommandError('An idle watchdog is already running.')
        try:
            stop = None if dry else RunpodStop(os.environ.get('RUNPOD_POD_ID'), os.environ.get('RUNPOD_STOP_TOKEN_FILE', '/workspace/private-ai/runpod-stop.key'))
            # /tmp is recreated with the container; the data volume survives.
            # Restarting just the watchdog must not reset the idle countdown.
            boot = Path('/tmp/private-ai-activity-boot')
            if not dry:
                if not boot.exists():
                    boot.write_text(uuid.uuid4().hex)
                boot_id = boot.read_text()
                if activity.read().get('boot_id') != boot_id:
                    activity.update(boot_id=boot_id, draining=False, last_activity=activity.clock())
            elif not (activity.root / 'state.json').exists():
                activity.touch()
            while True:
                close_old_connections()
                try:
                    if not dry and activity.read().get('draining'):
                        if stop.is_running():
                            # Retry without reopening admissions: a timed-out
                            # earlier stop may still be in flight at the provider.
                            stop()
                        self.stdout.write('stopping')
                        return
                    state = check_idle(activity, lambda: ImageEditJob.objects.filter(status__in=['queued', 'running']).exists() or VideoJob.objects.filter(status__in=['queued', 'running']).exists(), stop, dry_run=dry)
                    self.stdout.write(state)
                    if state == 'stopping':
                        return
                except Exception:
                    # Do not log provider responses or credential-bearing errors.
                    self.stderr.write('Idle shutdown check failed; shutdown is not confirmed.')
                    if options['once']:
                        raise CommandError('Idle shutdown could not be verified.')
                if options['once']:
                    return
                time.sleep(15)
        finally:
            singleton.close()
