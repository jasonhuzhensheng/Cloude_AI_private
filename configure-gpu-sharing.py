"""Run inside the existing server virtualenv, only after worker files deployed."""
import os,signal,time,subprocess
from pathlib import Path
os.environ.setdefault('DJANGO_SETTINGS_MODULE','portal.settings')
import django
django.setup()
from portal.models import ImageEditJob
from portal.gpu import GPULock
from portal.management.commands.image_worker import stop_chat,restore_chat
root=Path('/workspace/private-ai');start=root/'start-chat.sh'
if ImageEditJob.objects.filter(status__in=['queued','running']).exists():
    raise SystemExit('BUSY: wait for existing image jobs before enabling sharing')
original=start.read_text();backup=root/('start-chat.before-mps-'+str(int(time.time()))+'.sh');backup.write_text(original)
# Stop only this application's idle worker; it will be restarted below.
for p in Path('/proc').iterdir():
    if not p.name.isdigit():continue
    try:
        args=(p/'cmdline').read_bytes().split(b'\0')
        if b'image_worker' in args and any(a.endswith(b'manage.py') for a in args):os.kill(int(p.name),signal.SIGTERM)
    except (OSError,ProcessLookupError):pass
lock=GPULock();lock.acquire()
try:
    subprocess.run(['bash',str(root/'start-mps.sh')],check=True,timeout=15)
    lines=original.splitlines();lines[1:1]=['bash /workspace/private-ai/start-mps.sh','export CUDA_MPS_PIPE_DIRECTORY=/tmp/private-ai-mps','export CUDA_MPS_ACTIVE_THREAD_PERCENTAGE=100']
    start.write_text('\n'.join(lines)+'\n')
    stop_chat();restore_chat()
    (root/'CONCURRENT_GPU_READY').touch()
    print('GPU_SHARING_ENABLED: image active threads 90%, chat may use idle resources',flush=True)
except BaseException:
    (root/'CONCURRENT_GPU_READY').unlink(missing_ok=True);start.write_text(original)
    stop_chat();restore_chat();raise
finally:
    lock.release()
    time.sleep(2)
    with (root/'logs/image-worker.log').open('ab') as log:
        subprocess.Popen([str(root/'web/.venv/bin/python'),str(root/'web/manage.py'),'image_worker'],stdout=log,stderr=log,start_new_session=True)
