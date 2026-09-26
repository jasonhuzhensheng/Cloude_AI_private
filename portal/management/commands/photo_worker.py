from portal.storage_quota import ACCOUNT_BYTES, account_used
import fcntl,json,os,signal,subprocess,tempfile,time,shutil
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.db import close_old_connections,transaction
from django.db.models import Sum
from django.utils import timezone
from PIL import Image
from portal.models import PhotoJob,Document,Conversation
from portal.gpu import GPULock
from portal import photo_remote
from portal.photo_models import MODELS
from portal.photo_sizes import SIZES, UPSCALES, output_size

ROOT=Path(settings.IMAGE_EDIT_ROOT)
class Cancelled(Exception):pass

def check(job):
    if PhotoJob.objects.filter(pk=job.pk,cancel_requested=True).exists():raise Cancelled()

def run_job(job):
    process=None
    remote=photo_remote.enabled()
    with (settings.DATA/('photo-remote.lock' if remote else 'media-engine.lock')).open('a') as media:
        while True:
            check(job)
            try:fcntl.flock(media,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:time.sleep(1)
        lock=GPULock();acquired=False
        try:
            while not remote and not lock.acquire(blocking=False):
                check(job);time.sleep(1)
            acquired=not remote
            if not (ROOT/'CHAT_DISABLED').exists():raise RuntimeError('Media-only mode required')
            if shutil.disk_usage(settings.DATA).free < 5*1024**3:raise RuntimeError('Not enough free disk space')
            with tempfile.TemporaryDirectory(prefix='qwen21-',dir=settings.DATA) as tmp:
                directory=Path(tmp);output=directory/'output.png';progress=directory/'progress.json'
                width,height=output_size(job.format,job.source,job.model)
                payload={'seed':job.seed,'prompt':job.prompt,'negative_prompt':job.negative_prompt,'width':width,'height':height,'output':str(output),'progress':str(progress)}
                if job.source_id:
                    if job.source.owner_id!=job.owner_id:raise RuntimeError('Invalid source')
                    payload['source']=job.source.file.path
                references=list(job.references.select_related('document'))
                if references:
                    if job.model!='qwen21' or job.format in UPSCALES or len(references)+bool(job.source_id)>3:raise RuntimeError('Invalid references')
                    if any(r.document.owner_id!=job.owner_id for r in references):raise RuntimeError('Invalid reference owner')
                    payload['references']=[r.document.file.path for r in references]
                request=directory/'request.json';request.write_text(json.dumps(payload))
                command=[str(ROOT/'qwen21-runtime/.venv/bin/python'),str(settings.BASE_DIR/('deployment/photo_upscale_infer.py' if job.format in UPSCALES else 'deployment/'+MODELS[job.model]['script'])),str(request)]
                env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
                env.pop('CUDA_MPS_PIPE_DIRECTORY',None);env.pop('CUDA_MPS_ACTIVE_THREAD_PERCENTAGE',None)
                if remote:
                    output=photo_remote.run(job,payload,'upscale' if job.format in UPSCALES else job.model,check)
                else:
                    with (ROOT/'logs'/f'photo-{job.pk}.log').open('ab') as log:
                        process=subprocess.Popen(command,stdout=log,stderr=log,env=env,start_new_session=True)
                        start=time.monotonic()
                        while process.poll() is None:
                            check(job)
                            if time.monotonic()-start>1800:raise RuntimeError('Photo generation timed out')
                            try:
                                state=json.loads(progress.read_text())
                                PhotoJob.objects.filter(pk=job.pk).update(stage=state['stage'][:200],completed_steps=min(40,int(state['steps'])),updated=timezone.now())
                            except (OSError,ValueError,KeyError):pass
                            time.sleep(2)
                        if process.returncode or not output.is_file():raise RuntimeError('Local image engine failed')
                check(job)
                if output.stat().st_size>20*1024**2:raise RuntimeError('Result too large')
                with Image.open(output) as result:
                    result.verify()
                    if result.size!=(width,height):raise RuntimeError('Unexpected output dimensions')
                raw=output.read_bytes()
                with transaction.atomic():
                    used=account_used(job.owner)
                    if used+len(raw)>ACCOUNT_BYTES:raise RuntimeError('Image storage full')
                    conversation=job.source.conversation if job.source_id else Conversation.objects.create(owner=job.owner,title='Generated photos')
                    result=Document(owner=job.owner,conversation=conversation,name=f'{job.model}-{job.pk}.png',size=len(raw),text='')
                    result.file.save(result.name,ContentFile(raw))
                    job.result=result;job.status='succeeded';job.stage='Your image is ready';job.completed_steps=40;job.save(update_fields=['result','status','stage','completed_steps','updated'])
                if remote: shutil.rmtree(output.parent,ignore_errors=True)
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
            if acquired:lock.release()
            fcntl.flock(media,fcntl.LOCK_UN)

class Command(BaseCommand):
    def handle(self,*args,**kwargs):
        with (settings.DATA/'photo-worker.lock').open('a') as singleton:
            try:fcntl.flock(singleton,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            def stop(*_):raise KeyboardInterrupt()
            signal.signal(signal.SIGTERM,stop)
            PhotoJob.objects.filter(status='running').update(status='failed',stage='Interrupted. Create a new task to retry.')
            while True:
                close_old_connections()
                job=PhotoJob.objects.filter(status='queued').order_by('id').first()
                if job is None:time.sleep(2);continue
                if not PhotoJob.objects.filter(pk=job.pk,status='queued').update(status='running',stage='Waiting for the GPU'):continue
                job.refresh_from_db()
                try:run_job(job)
                except BaseException as exc:
                    self.stderr.write(f'Photo {job.pk}: {type(exc).__name__}')
                    PhotoJob.objects.filter(pk=job.pk).exclude(status='succeeded').update(status='cancelled' if isinstance(exc,Cancelled) else 'failed',stage='Cancelled' if isinstance(exc,Cancelled) else 'Photo generation failed. Please retry or contact your administrator.',updated=timezone.now())
                    if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
