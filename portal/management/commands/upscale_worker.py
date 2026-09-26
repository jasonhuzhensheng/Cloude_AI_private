from portal.storage_quota import ACCOUNT_BYTES, account_used
"""SeedVR2 worker, serialized with existing media tasks."""
import fcntl
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Sum
from portal.gpu import GPULock
from portal.models import VideoUpscaleJob, VideoJob
from portal.video_upscale import MODEL, ready
from portal.video_limits import DISK_RESERVE, RESULT_BYTES, ACCOUNT_BYTES

class Cancelled(Exception):
    pass

def check(job):
    if VideoUpscaleJob.objects.filter(pk=job.pk,cancel_requested=True).exists():
        raise Cancelled()
    if shutil.disk_usage(settings.DATA).free < DISK_RESERVE:
        raise RuntimeError('Not enough free disk space')

def stage(job,text):
    VideoUpscaleJob.objects.filter(pk=job.pk).update(stage=text)

def run_process(job,args,log,directory):
    process=subprocess.Popen(args,stdout=log,stderr=log,start_new_session=True)
    start=time.monotonic()
    try:
        while process.poll() is None:
            check(job)
            if time.monotonic()-start>21600:
                raise RuntimeError('Upscaling exceeded the six-hour processing limit')
            if sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())>RESULT_BYTES*3:
                raise RuntimeError('Upscale working files exceeded the storage limit')
            time.sleep(2)
        if process.returncode:
            raise RuntimeError('Upscale processing failed; check the server log')
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait()

def command(job,source,output):
    runtime=Path(settings.IMAGE_EDIT_ROOT)/'seedvr2-runtime'
    return [str(runtime/'.venv/bin/python'),str(runtime/'inference_cli.py'),str(source),
            '--output',str(output),'--output_format','mp4','--video_backend','ffmpeg',
            '--model_dir',str(runtime/'models/SEEDVR2'),'--dit_model',MODEL,
            '--resolution',str(job.resolution),'--batch_size','5','--chunk_size','65',
            '--temporal_overlap','4','--uniform_batch_size','--seed','42',
            '--dit_offload_device','cpu','--vae_offload_device','cpu',
            '--vae_encode_tiled','--vae_decode_tiled','--attention_mode','sdpa']

def probe(path):
    result=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],capture_output=True,text=True,check=True,timeout=30)
    return json.loads(result.stdout)

def validate_result(original,result,target):
    before=next(s for s in original['streams'] if s['codec_type']=='video')
    after=next(s for s in result['streams'] if s['codec_type']=='video')
    expected=(target,target*16//9) if before['height']>before['width'] else (target*16//9,target)
    if (after['width'],after['height'])!=expected or min(after['width'],after['height'])<=min(before['width'],before['height']):
        raise RuntimeError('Output resolution verification failed')
    if abs(float(original['format']['duration'])-float(result['format']['duration']))>.15:
        raise RuntimeError('Output duration verification failed')
    if before.get('nb_frames') and after.get('nb_frames') and before['nb_frames']!=after['nb_frames']:
        raise RuntimeError('Output frame count verification failed')
    if any(s['codec_type']=='audio' for s in original['streams']) and not any(s['codec_type']=='audio' for s in result['streams']):
        raise RuntimeError('Output audio verification failed')

def run_job(job):
    root=Path(settings.IMAGE_EDIT_ROOT)
    with (settings.DATA/'media-engine.lock').open('a') as media:
        while True:
            check(job)
            try:fcntl.flock(media,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:time.sleep(2)
        lock=GPULock()
        acquired=False
        try:
            while not acquired:
                check(job);acquired=lock.acquire(blocking=False)
                if not acquired:time.sleep(2)
            check(job)
            # This media-only deployment deliberately does not restart disabled chat.
            if not (root/'CHAT_DISABLED').exists():
                raise RuntimeError('Upscaling currently requires the media-only configuration')
            stage(job,'Loading SeedVR2 7B / upscaling; progress is indeterminate')
            with tempfile.TemporaryDirectory(prefix='video-upscale-',dir=settings.DATA) as folder:
                folder=Path(folder);source=folder/'input.mp4';output=folder/'enhanced';output.mkdir()
                shutil.copyfile(job.source.video.path,source)
                original=probe(source)
                with (root/'logs'/f'upscale-{job.pk}.log').open('ab') as log:
                    run_process(job,command(job,source,output),log,folder)
                    check(job)
                    generated=output/'input.mp4'
                    stage(job,'Preserving original audio and verifying the result')
                    final=folder/'result.mp4'
                    width,height=(job.resolution,job.resolution*16//9) if job.source.orientation=='portrait' else (job.resolution*16//9,job.resolution)
                    run_process(job,['ffmpeg','-y','-v','error','-i',str(generated),'-i',str(source),'-map','0:v:0','-map','1:a?','-vf',f'crop={width}:{height}','-c:v','libx264','-crf','18','-preset','fast','-pix_fmt','yuv420p','-c:a','copy','-movflags','+faststart',str(final)],log,folder)
                validate_result(original,probe(final),job.resolution)
                if final.stat().st_size>RESULT_BYTES:
                    raise RuntimeError('Upscaled result exceeds the 2 GiB limit')
                check(job)
                used=account_used(job.owner, exclude_upscale=job.pk)
                if used+final.stat().st_size>ACCOUNT_BYTES:
                    raise RuntimeError('Generated video storage quota is full')
                with final.open('rb') as handle:
                    job.video.save('upscaled.mp4',File(handle),save=False)
                job.size=final.stat().st_size;job.status='succeeded';job.stage='Upscale completed';job.save()
        finally:
            if acquired:lock.release()
            fcntl.flock(media,fcntl.LOCK_UN)

class Command(BaseCommand):
    help='Process private SeedVR2 video upscale jobs.'
    def handle(self,*args,**options):
        settings.DATA.mkdir(parents=True,exist_ok=True)
        with (settings.DATA/'upscale-worker.lock').open('a') as singleton:
            try:fcntl.flock(singleton,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return
            def stop(*_):raise KeyboardInterrupt()
            signal.signal(signal.SIGTERM,stop)
            VideoUpscaleJob.objects.filter(status='running').update(status='failed',stage='Interrupted; original video is unchanged')
            while True:
                close_old_connections()
                if not ready():time.sleep(3);continue
                job=VideoUpscaleJob.objects.filter(status='queued').order_by('id').first()
                if not job:time.sleep(2);continue
                if not VideoUpscaleJob.objects.filter(pk=job.pk,status='queued').update(status='running'):continue
                job.refresh_from_db()
                try:run_job(job)
                except BaseException as exc:
                    self.stderr.write(f'Upscale {job.pk}: {type(exc).__name__}: {exc}')
                    if job.status!='succeeded':
                        VideoUpscaleJob.objects.filter(pk=job.pk).update(status='cancelled' if isinstance(exc,Cancelled) else 'failed',stage='Cancelled; original video is unchanged' if isinstance(exc,Cancelled) else 'Upscaling failed; original video is unchanged. Contact the administrator.')
                    if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
