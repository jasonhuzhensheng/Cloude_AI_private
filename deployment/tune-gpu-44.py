"""One-time, reversible Flash offload tuning on the existing Runpod server."""
import os, sys, shutil, time
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portal.settings')
os.environ.setdefault('PORTAL_DATA', '/workspace/private-ai/web-data')
sys.path.insert(0, '/workspace/private-ai/web')
import django
django.setup()
from portal.gpu import GPULock
from portal.models import ImageEditJob
from portal.management.commands.image_worker import stop_chat, restore_chat
root = Path('/workspace/private-ai')
config = root / 'models.ini'
marker = root / 'CONCURRENT_GPU_READY'
paused = root / 'CONCURRENT_GPU_TUNING'
lock = GPULock()
assert not paused.exists(), 'An earlier tuning operation needs inspection'
assert marker.exists(), 'Expected concurrent image mode'
old = config.read_text()
assert old.count('n-gpu-layers = 32') == 1, 'Unexpected model preset'
backup = root / ('models.before-44-' + str(int(time.time())) + '.ini')
shutil.copy2(config, backup)
lock.acquire()
try:
    marker.rename(paused)
    if ImageEditJob.objects.filter(status__in=['queued', 'running']).exists():
        raise RuntimeError('Image work is pending; retry after completion')
    stop_chat()
    try:
        config.write_text(old.replace('n-gpu-layers = 32', 'n-gpu-layers = 44'))
        restore_chat()
    except BaseException:
        config.write_text(old)
        stop_chat()
        restore_chat()
        raise
    print('GPU_LAYERS_44_READY', flush=True)
finally:
    if paused.exists():
        paused.rename(marker)
    lock.release()
