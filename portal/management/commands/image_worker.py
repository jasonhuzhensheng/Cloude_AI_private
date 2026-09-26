from portal.storage_quota import ACCOUNT_BYTES, account_used
import fcntl,io,os,signal,subprocess,tempfile,time
from pathlib import Path
import requests
from PIL import Image
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections,transaction
from django.db.models import Sum
from django.core.files.base import ContentFile
from portal.models import ImageEditJob,Document
from portal.gpu import GPULock
from portal.media_lock import media_slot

ROOT=Path(settings.IMAGE_EDIT_ROOT)
def chat_pids():
    expected=str((ROOT/'llama.cpp/build/bin/llama-server').resolve());pids=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            if str((p/'exe').resolve())==expected:pids.append(int(p.name))
        except (OSError,RuntimeError):pass
    return pids

def restore_chat():
    if (ROOT/'CHAT_DISABLED').exists():
        return
    if not chat_pids():
        with (ROOT/'logs/chat.log').open('ab') as log:
            subprocess.Popen(['bash',str(ROOT/'start-chat.sh')],stdout=log,stderr=log,start_new_session=True)
    for _ in range(100):
        try:
            if requests.get('http://127.0.0.1:8080/proxy/absolute/8080/health',timeout=2).status_code==200:return
        except requests.RequestException:pass
        time.sleep(1)
    raise RuntimeError('Chat model did not become ready')

def stop_chat():
    for pid in chat_pids():
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
    for _ in range(60):
        if not chat_pids():return
        time.sleep(.5)
    raise RuntimeError('Chat model could not release GPU')

def stage(job,text):
    job.stage=text;job.save(update_fields=['stage','updated'])

def run_job(job):
    concurrent=(ROOT/'CONCURRENT_GPU_READY').is_file()
    lock=GPULock();process=None
    if not concurrent:
        while not lock.acquire(blocking=False):time.sleep(1)
    try:
        stage(job,'Preparing the local image model; chat remains available' if concurrent else 'Preparing the local image model; chat is temporarily paused')
        if concurrent:
            subprocess.run(['bash',str(ROOT/'start-mps.sh')],check=True,timeout=15)
        else:stop_chat()
        with tempfile.TemporaryDirectory(prefix='image-edit-',dir=settings.DATA) as directory:
            directory=Path(directory);source=directory/'input.png';output=directory/'output.png'
            with job.source.file.open('rb') as f,Image.open(f) as original:
                width,height=original.size
                scale=768/max(width,height);width=max(256,round(width*scale/64)*64);height=max(256,round(height*scale/64)*64)
                # The Qwen vision engine rejects some odd-sized reference grids.
                original.convert('RGB').resize((width,height),Image.Resampling.LANCZOS).save(source)
            models=ROOT/'image-models'
            command=[str(ROOT/'image-runtime/build/bin/sd-cli'),'--diffusion-model',str(models/'edit.gguf'),'--vae',str(models/'vae.safetensors'),'--llm',str(models/'encoder.gguf'),'--llm_vision',str(models/'vision.gguf'),'--cfg-scale','2.5','--sampling-method','euler','--steps','20','--offload-to-cpu','--diffusion-fa','--flow-shift','3','--model-args','qwen_image_zero_cond_t=true','-W',str(width),'-H',str(height),'-r',str(source),'-p',job.prompt,'-o',str(output),'--seed','42']
            stage(job,'Editing your image locally. This may take several minutes.')
            with (ROOT/'logs'/f'image-job-{job.id}.log').open('wb') as log:
                env=os.environ.copy()
                if concurrent:env.update(CUDA_MPS_PIPE_DIRECTORY='/tmp/private-ai-mps',CUDA_MPS_ACTIVE_THREAD_PERCENTAGE='90')
                process=subprocess.Popen(command,stdout=log,stderr=log,start_new_session=True,env=env)
                try:code=process.wait(timeout=900)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGTERM);process.wait(timeout=20);raise RuntimeError('Image generation timed out')
            if code!=0 or not output.is_file():raise RuntimeError('Image engine failed')
            if output.stat().st_size>20*1024*1024:raise RuntimeError('Result too large')
            with Image.open(output) as result:
                if result.width*result.height>24_000_000:raise RuntimeError('Invalid result dimensions')
                result.load();buffer=io.BytesIO();result.convert('RGB').save(buffer,format='PNG');raw=buffer.getvalue()
            with transaction.atomic():
                total=account_used(job.owner)
                if total+len(raw)>ACCOUNT_BYTES:raise RuntimeError('Storage quota reached')
                result=Document(owner=job.owner,conversation=job.source.conversation,name=Path(job.source.name).stem[:210]+'-edited.png',size=len(raw),text='',truncated=False)
                result.file.save('edited.png',ContentFile(raw),save=True)
                job.result=result;job.save(update_fields=['result','updated'])
    finally:
        try:
            if process is not None and process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=20)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
            if not concurrent:
                stage(job,'Restoring the chat model')
                restore_chat()
        finally:
            if not concurrent:lock.release()

class Command(BaseCommand):
    help='Process private, local Qwen image edits one at a time.'
    def handle(self,*args,**options):
        settings.DATA.mkdir(parents=True,exist_ok=True)
        singleton=(settings.DATA/'image-worker.lock').open('a')
        try:fcntl.flock(singleton,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        def stop(*_):raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM,stop)
        ImageEditJob.objects.filter(status='running').update(status='failed',stage='Interrupted',error='The server restarted during this edit. The original image is unchanged.')
        # Recover a chat model interrupted by a worker or server crash.
        lock=GPULock()
        if lock.acquire(blocking=False):
            try:restore_chat()
            finally:lock.release()
        while True:
            close_old_connections()
            job=ImageEditJob.objects.filter(status='queued').order_by('id').first()
            if job is None:time.sleep(2);continue
            if not ImageEditJob.objects.filter(pk=job.pk,status='queued').update(status='running'):continue
            job.refresh_from_db()
            try:
                with media_slot():
                    run_job(job)
                job.status='succeeded';job.stage='Your edited image is ready';job.save(update_fields=['status','stage','updated'])
            except BaseException as exc:
                self.stderr.write(f'Image job {job.id} failed: {type(exc).__name__}: {exc}')
                job.status='failed';job.stage='Edit did not complete';job.error='The local edit did not complete. Your original image is unchanged. Please try again or contact your administrator.';job.save(update_fields=['status','stage','error','updated'])
                if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
