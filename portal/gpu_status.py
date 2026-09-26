"""Authenticated, cached GPU telemetry with no process or user information."""
import csv,io,math,subprocess,threading,time
from pathlib import Path
from django.conf import settings
from datetime import datetime,timezone
import requests
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from .gpu import GPULock
from .photo_remote import enabled as remote_photos_enabled, public_status
from .models import ImageEditJob, VideoJob, VideoUpscaleJob, PhotoJob
_guard=threading.Lock()
_sample=None
_sample_time=0

def number(value):
    try:
        n=float(value.strip());return n if math.isfinite(n) and n>=0 else None
    except (ValueError,TypeError):return None

def collect():
    out=subprocess.run(['nvidia-smi','--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=2,check=True)
    rows=list(csv.reader(io.StringIO(out.stdout)))
    if not rows or len(rows[0])!=6:raise ValueError('Unavailable GPU sample')
    row=rows[0];util,used,total,temp,power=map(number,row[1:])
    jobs=ImageEditJob.objects.filter(status__in=['queued','running'])
    running=jobs.filter(status='running').first()
    queued=jobs.filter(status='queued').count()
    video = VideoJob.objects.filter(status='running').first()
    upscale = VideoUpscaleJob.objects.filter(status='running').first()
    photo = None if remote_photos_enabled() else PhotoJob.objects.filter(status='running').first()
    queued += PhotoJob.objects.filter(status='queued').count()
    if photo and not upscale and not video and not running:
        state='Qwen-Image-2.1 photos'
    elif upscale and not video and not running:
        state='SeedVR2 video upscaling'
    elif video:
        state = 'Restoring chat' if video.stage == 'Restoring chat' else 'H3 video / audio'
    elif running:
        state='Restoring chat' if running.stage=='Restoring the chat model' else 'Image editing'
    else:
        lock=GPULock();acquired=lock.acquire(blocking=False)
        if acquired:lock.release()
        if not acquired:state='Chat / document processing'
        else:
            try:
                health=requests.get('http://127.0.0.1:8080/proxy/absolute/8080/health',timeout=.7)
                state='Ready' if health.status_code==200 else 'Loading chat'
            except requests.RequestException:state='Ready (chat disabled)' if (Path(settings.IMAGE_EDIT_ROOT)/'CHAT_DISABLED').exists() else 'Chat unavailable'
            if state=='Ready' and util is not None and util>10:state='GPU active'
    return {'available':True,'name':row[0].strip(),'utilization':util,'memory_used_mib':used,'memory_total_mib':total,'temperature_c':temp,'power_w':power,'state':state,'queued_images':queued,'sampled_at':datetime.now(timezone.utc).isoformat()}

@login_required
def status(request):
    global _sample,_sample_time
    with _guard:
        if _sample is None or time.monotonic()-_sample_time>=5:
            try:_sample=collect()
            except Exception:_sample={'available':False,'state':'GPU status unavailable','sampled_at':datetime.now(timezone.utc).isoformat()}
            _sample_time=time.monotonic()
        data=dict(_sample)
    data['photo_gpu']=public_status()
    if request.user.is_staff:
        from .billing_status import snapshot
        try:data["billing"]=snapshot()
        except Exception:data["billing"]={"stale":True}
    response=JsonResponse(data)
    response['Cache-Control']='private, no-store'
    return response
