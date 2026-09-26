"""Main-server lifecycle supervisor. Only its allowlisted photo pod may be changed."""
import fcntl, json, os, stat, time
from pathlib import Path
import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from portal.photo_remote import atomic, config_path

PROTECTED = {'liokr6kbd9vlwk', '94g82b06vc8dz9', 'ng5y0mjyosl248', '0ohxnztjx7agwg'}


def validate(config, pod):
    identity = config['pod_id']
    if not identity or identity in PROTECTED or pod.get('id') != identity:
        raise ValueError('Protected or mismatched pod')
    if pod.get('name') != 'private-ai-photo-ondemand' or pod.get('networkVolumeId') != config['volume_id']:
        raise ValueError('Unexpected photo pod identity')
    if int(pod.get('gpuCount', 0)) != 1:
        raise ValueError('Unexpected GPU count')
    return float(pod.get('costPerHr') or 999) <= float(config['max_hourly'])


class Supervisor:
    def __init__(self, config):
        self.config = config
        key = Path('/root/.config/private-ai/runpod-photo.key')
        if stat.S_IMODE(key.stat().st_mode) != 0o600:
            raise ValueError('Unsafe credential permissions')
        self.session = requests.Session()
        self.session.headers['Authorization'] = 'Bearer ' + key.read_text().strip()
        self.url = 'https://rest.runpod.io/v1/pods/' + config['pod_id']
        self.last_balance_check = 0
        self.balance = None

    def request(self, method, path='', **kwargs):
        r = self.session.request(method, self.url + path, timeout=30, **kwargs)
        r.raise_for_status()
        return r.json() if r.content else {}

    def budget(self, spool):
        now = time.time()
        if now - self.last_balance_check < 60:
            return self.balance
        r = self.session.post('https://api.runpod.io/graphql', json={'query':'query { myself { clientBalance } }'}, timeout=30)
        r.raise_for_status()
        balance = float(r.json()['data']['myself']['clientBalance'])
        ledger_path = spool / 'budget.json'
        try: ledger = json.loads(ledger_path.read_text())
        except FileNotFoundError:
            ledger = {'last_balance': self.config['initial_balance'], 'remaining': self.config['remaining_budget']}
        ledger['remaining'] -= max(0, ledger['last_balance'] - balance)
        ledger['last_balance'] = balance
        atomic(ledger_path, ledger)
        self.balance = min(balance, ledger['remaining'])
        self.last_balance_check = now
        return self.balance

    def tick(self, spool):
        config = json.loads(config_path().read_text())
        if config.get('pod_id') != self.config['pod_id']:
            raise ValueError('Configuration identity changed; restart supervisor')
        pod = self.request('GET')
        affordable = validate(self.config, pod)
        try: demand = time.time() - json.loads((spool/'demand.json').read_text())['time'] < 30
        except (OSError, ValueError, KeyError): demand = False
        from portal.models import PhotoJob
        from django.db import close_old_connections
        close_old_connections()
        active = PhotoJob.objects.filter(status__in=['queued','running']).exists()
        # DB success is committed only after the result has been saved.
        wanted = config.get('enabled') is True and demand and active
        blocked = False
        reason = ''
        if wanted:
            try:
                if self.budget(spool) < 10:
                    blocked, reason = True, '预算不足 / Budget reserve reached'
            except Exception:
                blocked, reason = True, '无法确认余额 / Balance check unavailable'
            if not affordable:
                blocked, reason = True, 'GPU 价格超过上限 / GPU price exceeds limit'
        state = pod.get('desiredStatus')
        if not wanted or blocked:
            if state == 'RUNNING':
                self.request('POST','/stop')
                state = 'STOPPING'
            stage = reason or ('图片 GPU 已停止 / Photo GPU stopped' if state == 'EXITED' else '正在停止图片 GPU / Stopping photo GPU')
        else:
            if state == 'EXITED':
                self.request('POST','/start')
            stage = '启动或加载图片 GPU / Starting or loading photo GPU'
            try:
                heartbeat=json.loads((spool/'executor.json').read_text())
                if heartbeat.get('pod')==self.config['pod_id'] and time.time()-heartbeat['time']<30:
                    stage='图片 GPU 运行中 / Photo GPU running'
            except (OSError,ValueError,KeyError): pass
        atomic(spool/'controller.json', {'time':time.time(),'stage':stage,'blocked':blocked,'provider_status':state,'pod_id':self.config['pod_id']})

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        root = Path(settings.IMAGE_EDIT_ROOT)
        spool = root/'photo-spool'; spool.mkdir(exist_ok=True)
        with open('/tmp/private-ai-photo-controller.lock','w') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: return
            supervisor = Supervisor(json.loads(config_path().read_text()))
            while True:
                try: supervisor.tick(spool)
                except Exception as exc:
                    # Do not leak API response bodies or headers into application logs.
                    self.stderr.write('Photo GPU controller: '+type(exc).__name__)
                    atomic(spool/'controller.json',{'time':time.time(),'stage':'图片 GPU 控制失败 / Photo GPU control unavailable','blocked':True})
                time.sleep(10)
