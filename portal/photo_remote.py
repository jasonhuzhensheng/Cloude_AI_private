"""Shared-volume transport; original server remains the only database writer."""
import json, os, time, uuid, shutil
from pathlib import Path
from django.conf import settings
from django.utils import timezone


def config_path():
    return Path(settings.IMAGE_EDIT_ROOT) / 'photo-gpu.json'


def enabled():
    try: return json.loads(config_path().read_text()).get('enabled') is True
    except (OSError, ValueError): return False


def atomic(path, data):
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def run(job, payload, engine, check):
    from portal.models import PhotoJob
    root = Path(settings.IMAGE_EDIT_ROOT)
    spool = root / 'photo-spool'
    spool.mkdir(exist_ok=True)
    folder = spool / ('job-' + str(job.pk) + '-' + uuid.uuid4().hex)
    folder.mkdir()
    output = folder / 'output.png'
    payload = dict(payload, output=str(output), progress=str(folder / 'progress.json'))
    atomic(folder / 'lease.json', {'time': time.time()})
    atomic(folder / 'request.json', {'engine': engine, 'payload': payload})
    start = time.monotonic()
    try:
        while True:
            check(job)
            now = time.time()
            atomic(folder / 'lease.json', {'time': now})
            atomic(spool / 'demand.json', {'time': now, 'job': job.pk})
            if not (folder / 'progress.json').exists() and time.monotonic() - start > 600:
                raise RuntimeError('Photo GPU startup timed out')
            if time.monotonic() - start > 2700:
                raise RuntimeError('Remote photo GPU timed out')
            try:
                control = json.loads((spool / 'controller.json').read_text())
                if now - control['time'] < 60 and control.get('blocked'):
                    raise RuntimeError('Photo GPU unavailable: ' + control['stage'])
                stage = control['stage']
            except (OSError, ValueError, KeyError):
                stage = '等待图片 GPU 控制器 / Waiting for photo GPU controller'
            steps = 0
            try:
                progress = json.loads((folder / 'progress.json').read_text())
                stage, steps = progress['stage'], max(0, min(40, int(progress['steps'])))
            except (OSError, ValueError, KeyError): pass
            PhotoJob.objects.filter(pk=job.pk).update(stage=stage[:200], completed_steps=steps, updated=timezone.now())
            if (folder / 'done.json').exists():
                done = json.loads((folder / 'done.json').read_text())
                if not done.get('ok') or not output.is_file():
                    raise RuntimeError('Remote image engine failed')
                shutil.copyfile(folder / 'engine.log', root / 'logs' / f'photo-{job.pk}.log')
                return output
            time.sleep(2)
    except BaseException:
        (folder / 'cancel').touch()
        raise


def public_status():
    try: config=json.loads(config_path().read_text())
    except (OSError,ValueError): return {'enabled':False,'configured':False}
    spool = Path(settings.IMAGE_EDIT_ROOT) / 'photo-spool'
    state = {'enabled': config.get('enabled') is True, 'configured':True, 'stage': '图片 GPU 状态不可用 / Photo GPU status unavailable'}
    try:
        control = json.loads((spool / 'controller.json').read_text())
        if time.time() - control['time'] < 40:
            state['stage'] = control['stage']
            if not state['enabled'] and config.get('fallback_reason'):
                state['stage'] += ' · '+config['fallback_reason']
            state['provider_status'] = control.get('provider_status')
            metrics = json.loads((spool / 'executor.json').read_text())
            if state['provider_status'] == 'RUNNING' and metrics.get('pod') == control.get('pod_id') and time.time() - metrics['time'] < 30:
                state.update({k: metrics[k] for k in ['name','utilization','memory_used_mib','memory_total_mib'] if k in metrics})
    except (OSError, ValueError, KeyError): pass
    return state
