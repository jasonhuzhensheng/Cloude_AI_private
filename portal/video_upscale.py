from portal.storage_quota import ACCOUNT_BYTES, account_used
"""Private result upscaling; no source file is overwritten."""
from pathlib import Path
import math,re
from functools import lru_cache
from datetime import datetime,timedelta,timezone as dt_timezone
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.http import JsonResponse, FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .models import VideoUpscaleJob, VideoJob
from .video_limits import ACCOUNT_BYTES, RESULT_BYTES, dimensions

MODEL = 'seedvr2_ema_7b_fp16.safetensors'
TARGETS = (720, 1080, 2160)

def ready():
    root = Path(settings.IMAGE_EDIT_ROOT)
    return (root / 'SEEDVR2_READY').is_file()

@lru_cache(maxsize=16)
def _chunk_log(path, mtime, size):
    """Bounded, stat-keyed cache: status polls never repeatedly scan unchanged logs."""
    if size > 8 * 1024 * 1024:
        return None
    text = Path(path).read_text(errors='replace')
    chunks, first, last, day, previous = [], None, None, 0, None
    for line in text.splitlines():
        stamp = re.search(r'\[(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?\]', line)
        if not stamp:
            continue
        seconds = int(stamp[1])*3600+int(stamp[2])*60+int(stamp[3])
        if previous is not None and seconds < previous-43200:
            day += 86400
        previous = seconds
        clock = day+seconds
        first = clock if first is None else first
        last = clock
        chunk = re.search(r'Chunk (\d+)/(\d+): (\d+) new \+ (\d+) context frames', line)
        if chunk:
            index,total,new,context = map(int,chunk.groups())
            if index != len(chunks)+1 or total < index or new <= 0:
                return None
            chunks.append(dict(index=index,total=total,frames=new+context,start=clock,end=None))
        elif 'Output assembled:' in line and chunks and chunks[-1]['end'] is None:
            chunks[-1]['end'] = clock
    return dict(first=first,last=last,chunks=chunks) if first is not None else None


def estimate(job):
    duration=job.source.duration_seconds+job.source.prefix_seconds
    baseline={720:134.63,1080:209.6,2160:592.28}[job.resolution]*duration
    low,high=math.ceil(baseline*.75),math.ceil(baseline*2.5)
    elapsed=None
    measured=False
    detail=''
    stale=False
    progress=None
    if job.status=='running':
        try:
            path=Path(settings.IMAGE_EDIT_ROOT)/'logs'/f'upscale-{job.pk}.log'
            stat=path.stat()
            progress=_chunk_log(str(path),stat.st_mtime_ns,stat.st_size)
            if progress:
                clock=progress['first']
                start=datetime.combine(job.created.astimezone(dt_timezone.utc).date(),datetime.min.time(),tzinfo=dt_timezone.utc)+timedelta(seconds=clock)
                if start<job.created-timedelta(seconds=30):start+=timedelta(days=1)
                now=timezone.now()
                if start<=now and (now-job.created).total_seconds()<86400:
                    elapsed=max(0,(now-start).total_seconds())
                    stale=elapsed-(progress['last']-clock)>180
        except (OSError,ValueError):
            pass
    if elapsed is not None and progress and progress['chunks']:
        chunks=progress['chunks']
        current=chunks[-1]
        completed=[c for c in chunks if c['end'] is not None]
        detail=f"Chunks complete: {len(completed)}/{current['total']}"
        if current['end'] is None:
            detail+=f" · Processing chunk {current['index']}/{current['total']}"
        else:
            detail+=' · Preparing next chunk or exporting'
        if completed:
            # Actual overlapping-frame work, including encoding, inference and decoding.
            rates=[(c['end']-c['start'])/c['frames'] for c in completed if c['end']>c['start']]
            if rates:
                remaining_frames=(current['total']-current['index'])*max(c['frames'] for c in chunks)
                spent=0
                if current['end'] is None:
                    remaining_frames+=current['frames']
                    spent=max(0,elapsed-(current['start']-progress['first']))
                # Keep a margin for chunk transitions and final video/audio export.
                low=math.ceil(elapsed+max(0,min(rates)*remaining_frames*.8-spent)+30)
                high=math.ceil(elapsed+max(0,max(rates)*remaining_frames*1.3-spent)+180)
                measured=True
                # A slow/stalled chunk must not turn into a permanent near-zero countdown.
                if current['end'] is None and spent>max(rates)*current['frames']*1.3+180:
                    stale=True
    active=job.status in ('queued','running') and not job.cancel_requested and not job.delete_requested
    overdue=stale or (elapsed is not None and elapsed>=high)
    return dict(total_low=low,total_high=high,elapsed_seconds=round(elapsed) if elapsed is not None else None,
                remaining_low=math.ceil(max(0,low-(elapsed or 0))) if active and not overdue else None,
                remaining_high=math.ceil(max(0,high-(elapsed or 0))) if active and not overdue else None,
                measured=measured,detail=detail,
                overdue=overdue,waiting=job.status=='queued' or (job.status=='running' and elapsed is None))

def cleanup_deleted(owner):
    for job in VideoUpscaleJob.objects.filter(owner=owner,delete_requested=True).exclude(status__in=['queued','running']):
        try:
            with transaction.atomic():
                job.video.delete(save=False)
                job.delete()
        except OSError:
            pass

def serialize(job):
    return dict(id=job.pk, created=job.created.isoformat(), resolution=job.resolution, status=job.status, stage=job.stage,
                cancel_requested=job.cancel_requested,delete_requested=job.delete_requested,
                estimate=estimate(job),
                video_url=f'/api/video/{job.source_id}/upscale/?result={job.pk}' if job.video else None)

def control(request, source, data):
    action = data.get('action')
    if action in ('delete-upscale','cancel-upscale') and type(data.get('upscale_id')) is not int:
        return JsonResponse({'error':'Invalid upscale task.'},status=400)
    if action == 'delete-upscale':
        with transaction.atomic():
            job=get_object_or_404(VideoUpscaleJob,pk=data.get('upscale_id'),owner=request.user,source=source)
            VideoUpscaleJob.objects.filter(pk=job.pk).update(delete_requested=True,cancel_requested=True)
            VideoUpscaleJob.objects.filter(pk=job.pk,status='queued').update(status='cancelled',stage='Cancelled',updated=timezone.now())
            job.refresh_from_db()
        if job.status=='running':
            return JsonResponse({'ok':True,'deleting':True},status=202)
        cleanup_deleted(request.user)
        return JsonResponse({'ok':True})
    if action == 'cancel-upscale':
        job = get_object_or_404(VideoUpscaleJob, pk=data.get('upscale_id'), owner=request.user, source=source)
        VideoUpscaleJob.objects.filter(pk=job.pk,status='queued').update(status='cancelled',stage='Cancelled',cancel_requested=True,updated=timezone.now())
        VideoUpscaleJob.objects.filter(pk=job.pk,status='running').update(cancel_requested=True,stage='Stopping upscale…',updated=timezone.now())
        return JsonResponse({'ok':True})
    if not ready():
        return JsonResponse({'error':'Video upscaling is not ready. The model must pass a server test first.'},status=503)
    target = data.get('resolution')
    if type(target) is not int or target not in TARGETS:
        return JsonResponse({'error':'Choose 720p, 1080p or 4K.'},status=400)
    if source.status != 'succeeded' or not source.video or source.delete_requested:
        return JsonResponse({'error':'Only completed video results can be upscaled.'},status=409)
    if target <= min(dimensions(source.resolution, source.orientation)):
        return JsonResponse({'error':'Choose a resolution higher than the original video.'},status=400)
    used =account_used(request.user)
    if used+RESULT_BYTES>ACCOUNT_BYTES:
        return JsonResponse({'error':'Your generated video storage quota is full.'},status=400)
    if VideoUpscaleJob.objects.filter(status__in=['queued','running']).count()>=4:
        return JsonResponse({'error':'The upscale queue is full.'},status=429)
    try:
        with transaction.atomic():
            # Lock the source via a write; source deletion uses the same database transaction.
            if not VideoJob.objects.filter(pk=source.pk,delete_requested=False).update(updated=source.updated):
                return JsonResponse({'error':'This generation is being deleted.'},status=409)
            job=VideoUpscaleJob.objects.create(owner=request.user,source=source,resolution=target)
    except IntegrityError:
        return JsonResponse({'error':'Wait for your current upscale task to finish.'},status=429)
    return JsonResponse(serialize(job),status=202)

def media(request, source):
    identifier=request.GET.get('result','')
    if not identifier.isdecimal() or len(identifier)>18:
        raise Http404
    job=get_object_or_404(VideoUpscaleJob,pk=int(identifier),source=source,owner=request.user,status='succeeded')
    if not job.video:
        raise Http404
    response=FileResponse(job.video.open('rb'),as_attachment=request.GET.get('download')=='1',filename=f'h3-{source.pk}-{job.resolution}p.mp4',content_type='video/mp4')
    response['Cache-Control']='private, no-store'
    return response
