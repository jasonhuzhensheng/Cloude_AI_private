from portal.storage_quota import ACCOUNT_BYTES, account_used
from django.db.models import Max
from django.db.models.functions import Greatest, Coalesce
"""Owner-protected local H3 jobs and generated media."""
import json
import fcntl
from functools import wraps
import math
import shutil
from django.utils import timezone
from pathlib import Path
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from . import video_upscale, video_diagnostics
from .video_timeline import validate_timeline, draft_timeline
from .images import is_image, normalize_image
from .models import Conversation, Document, VideoJob
from django.core.files.base import ContentFile
from .video_limits import MAX_DURATION, ACCOUNT_BYTES, RESULT_BYTES, segment_count, RESOLUTIONS, dimensions

def queue_admission(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        # Serialize capacity checks across web processes without taking the GPU lock.
        with (settings.DATA / 'video-queue-admission.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                return view(*args, **kwargs)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    return wrapped

def available():
    return (Path(settings.IMAGE_EDIT_ROOT) / 'H3_READY').is_file()

def timing_profile(owner, mode, orientation, resolution='288p'):
    samples = list(VideoJob.objects.filter(owner=owner, mode=mode, orientation=orientation, resolution=resolution, timed_segments__gt=0).order_by('-id').values_list('segment_seconds', 'timed_segments')[:20])
    count = sum(n for _, n in samples)
    rate = sum(seconds for seconds, _ in samples) / count if count else 85 * (RESOLUTIONS[resolution][0][0] * RESOLUTIONS[resolution][0][1] / (512*288)) ** 1.25
    return {'low': max(1, round(rate * .75)), 'high': max(2, round(rate * 1.4)), 'measured': bool(count)}

def job_data(job):
    data = {key: getattr(job, key) for key in ('id', 'mode', 'prompt', 'timeline', 'status', 'stage', 'error', 'duration_seconds', 'orientation', 'resolution', 'completed_segments', 'pause_requested', 'delete_requested')}
    data['prompt_check'] = video_diagnostics.report(job)
    data['upscales'] = [video_upscale.serialize(u) for u in job.upscales.all()]
    data['upscale_available'] = video_upscale.ready()
    data['continuation_of_id'] = job.continuation_of_id
    data['output_duration_seconds'] = job.duration_seconds + job.prefix_seconds
    data['continue_available'] = (Path(settings.IMAGE_EDIT_ROOT) / 'H3_CONTINUE_READY').is_file()
    data['queue_position'] = (VideoJob.objects.filter(status='running').count() + VideoJob.objects.filter(status='queued',pk__lt=job.pk).count() + 1) if job.status == 'queued' else None
    data['total_segments'] = segment_count(job.duration_seconds)
    profile = timing_profile(job.owner, job.mode, job.orientation, job.resolution)
    if job.timed_segments:
        rate = job.segment_seconds / job.timed_segments
        profile = {'low': max(1, round(rate * .75)), 'high': max(2, round(rate * 1.4)), 'measured': True}
    remaining = max(0, data['total_segments'] - job.completed_segments)
    merge = max(10, (job.duration_seconds + job.prefix_seconds) * .2)
    credit = 0
    if job.status == 'running' and job.stage.startswith('Generating segment'):
        credit = min(max(0, (timezone.now() - job.updated).total_seconds()), profile['low'] * .75)
    data['estimate'] = {'total_low': math.ceil(data['total_segments'] * profile['low'] + merge), 'total_high': math.ceil(data['total_segments'] * profile['high'] + merge), 'remaining_low': math.ceil(max(1, remaining * profile['low'] + merge - credit)), 'remaining_high': math.ceil(max(2, remaining * profile['high'] + merge - credit)), 'measured': profile['measured']}
    if job.status in ('succeeded', 'failed', 'paused') or job.pause_requested:
        data['estimate']['remaining_low'] = data['estimate']['remaining_high'] = None
    data.update(created=job.created.isoformat(), updated=job.updated.isoformat())
    if job.video:
        data['video_url'] = reverse('video-media', args=[job.pk, 'video'])
    if job.audio:
        data['audio_url'] = reverse('video-media', args=[job.pk, 'audio'])
    return data

@login_required
def studio(request):
    if request.method == 'POST' and request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
            rows, note = draft_timeline(data.get('prompt'), data.get('duration_seconds'))
            return JsonResponse({'timeline': rows, 'note': note})
        except (ValueError, TypeError, AttributeError) as exc:
            return JsonResponse({'error': str(exc) or 'Invalid timeline request.'}, status=400)
    if request.method == 'POST':
        upload = request.FILES.get('file')
        if not upload or upload.size > 80 * 1024**2 or not is_image(upload.name):
            return JsonResponse({'error': '请选择不超过 80 MB 的 JPG、PNG 或 WebP 图片 / Choose a JPG, PNG or WebP image up to 80 MB.'}, status=400)
        raw = upload.read()
        try:
            normalize_image(raw, max_pixels=8192*8192, max_edge=8192)  # Validate decoding, format, pixel count and static frame.
        except ValueError as exc:
            return JsonResponse({'error': str(exc)}, status=400)
        used =account_used(request.user)
        if used + len(raw) > ACCOUNT_BYTES:
            return JsonResponse({'error': 'Your file storage limit is 100 GB.'}, status=400)
        with transaction.atomic():
            chat = Conversation.objects.create(owner=request.user, title='Video reference images')
            doc = Document(owner=request.user, conversation=chat, name=Path(upload.name).name[:250], size=len(raw), text='')
            doc.file.save('reference', ContentFile(raw))
        return JsonResponse({'id': doc.pk, 'name': doc.name}, status=201)
    if request.GET.get('image'):
        doc = get_object_or_404(Document, pk=request.GET['image'], owner=request.user)
        if not is_image(doc.name):
            raise Http404
        mime = {'.png': 'image/png', '.webp': 'image/webp'}.get(Path(doc.name).suffix.lower(), 'image/jpeg')
        response = FileResponse(doc.file.open('rb'), content_type=mime)
        response['Cache-Control'] = 'private, no-store'
        return response
    images = [d for d in Document.objects.filter(owner=request.user).order_by('-id') if is_image(d.name)]
    return render(request, 'video_studio.html', {'images': images[:100], 'available': available()})

@login_required
def jobs(request):
    video_upscale.cleanup_deleted(request.user)
    for job in VideoJob.objects.filter(owner=request.user, delete_requested=True).exclude(status__in=['queued', 'running']):
        try:
            remove_job(job)
        except OSError:
            pass  # Keep the record so cleanup can be retried.
    response = JsonResponse({'available': available(), 'queue_full': VideoJob.objects.filter(status__in=['queued','running']).count() >= 4, 'jobs': [job_data(j) for j in VideoJob.objects.filter(owner=request.user).annotate(latest_task=Greatest('created',Coalesce(Max('upscales__created'),'created'))).order_by('-latest_task','-id')[:20]], 'timings': {m+'_'+o+'_'+r: timing_profile(request.user,m,o,r) for m in ('fl2va','ref2va') for o in ('landscape','portrait') for r in RESOLUTIONS}})
    response['Cache-Control'] = 'private, no-store'
    return response

@login_required
@require_POST
@queue_admission
def create(request):
    if not available():
        return JsonResponse({'error': 'H3 is not ready yet.'}, status=503)
    try:
        data = json.loads(request.body)
        prompt = data.get('prompt', '')
        mode = data.get('mode', 'fl2va')
        orientation = data.get('orientation', 'landscape')
        if orientation not in ('landscape', 'portrait'):
            return JsonResponse({'error': 'Choose landscape or portrait.'}, status=400)
        resolution = data.get('resolution', '480p')
        if not isinstance(resolution, str) or resolution not in RESOLUTIONS:
            return JsonResponse({'error': 'Choose 480p, 720p or 1080p.'}, status=400)
        duration = data.get('duration_seconds', 3)
        if type(duration) is not int or not 1 <= duration <= MAX_DURATION:
            return JsonResponse({'error': 'Duration must be a whole number from 1 to 1,800 seconds.'}, status=400)
        if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 40000 or mode not in ('fl2va', 'ref2va'):
            raise ValueError()
        source_id = data.get('source_id')
        if source_id is not None and (type(source_id) is not int or source_id <= 0):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'error': 'Enter a prompt of 1–40,000 characters and a valid generation mode.'}, status=400)
    try:
        timeline = validate_timeline(data.get('timeline', []), duration)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    parent = None
    parent_id = data.get('continuation_of_id')
    if parent_id is not None:
        if type(parent_id) is not int or parent_id <= 0:
            return JsonResponse({'error':'Invalid source generation.'},status=400)
        parent = get_object_or_404(VideoJob,pk=parent_id,owner=request.user)
        if not (Path(settings.IMAGE_EDIT_ROOT) / 'H3_CONTINUE_READY').is_file():
            return JsonResponse({'error':'Continue generation is being prepared. Try again later.'},status=503)
        if parent.status != 'succeeded' or not parent.video or parent.delete_requested:
            return JsonResponse({'error':'Choose a completed video that is not being deleted.'},status=409)
        if parent.duration_seconds + parent.prefix_seconds + duration > MAX_DURATION:
            return JsonResponse({'error':'The combined video cannot exceed 1,800 seconds.'},status=400)
        mode, orientation, resolution = 'fl2va', parent.orientation, parent.resolution
        source_id = None
    source = None
    if source_id:
        source = get_object_or_404(Document, pk=source_id, owner=request.user)
        if not is_image(source.name):
            return JsonResponse({'error': 'Choose an image as the reference.'}, status=400)
    if mode == 'ref2va' and source is None:
        return JsonResponse({'error': 'Reference mode requires an image. Upload one in chat first.'}, status=400)
    if VideoJob.objects.filter(status__in=['queued', 'running']).count() >= 4:
        return JsonResponse({'error': 'The video queue is full. Please try again later.'}, status=429)
    used =account_used(request.user)
    if used + RESULT_BYTES > ACCOUNT_BYTES:
        return JsonResponse({'error': 'Your generated video storage quota is full.'}, status=400)
    try:
        with transaction.atomic():
            if parent and not VideoJob.objects.filter(pk=parent.pk,delete_requested=False,status='succeeded').update(updated=parent.updated):
                return JsonResponse({'error':'The source generation is no longer available.'},status=409)
            job = VideoJob.objects.create(continuation_of=parent, prefix_seconds=parent.duration_seconds + parent.prefix_seconds if parent else 0, owner=request.user, source=source, mode=mode, prompt=prompt.strip(), timeline=timeline, duration_seconds=duration, orientation=orientation, resolution=resolution)
    except IntegrityError:
        return JsonResponse({'error': 'This task could not be queued. Please retry.'}, status=429)
    return JsonResponse(job_data(job), status=202)

def remove_job(job):
    if job.continuations.exists() or job.upscales.exists():
        raise OSError('Dependent results must be removed first')
    for field in (job.video, job.audio):
        if field.name:
            field.storage.delete(field.name)
    directory = settings.DATA / 'video-checkpoints' / str(job.owner_id) / str(job.pk)
    if directory.exists():
        shutil.rmtree(directory)
    job.delete()

@login_required
@require_POST
@queue_admission
def control(request, pk):
    job = get_object_or_404(VideoJob, pk=pk, owner=request.user)
    try:
        data = json.loads(request.body)
        action = data.get('action')
    except (ValueError, AttributeError):
        action = None
    if action in ('upscale', 'cancel-upscale', 'delete-upscale'):
        return video_upscale.control(request, job, data)
    if action == 'delete':
        # Claim queued/inactive jobs atomically; the worker only claims queued jobs.
        with transaction.atomic():
            VideoJob.objects.filter(pk=pk).update(updated=job.updated)
            if job.continuations.exists():
                return JsonResponse({'error':'Delete the continuation tasks/results first. Their source video is preserved.'},status=409)
            if job.upscales.exists():
                return JsonResponse({'error':'Delete this video’s upscale tasks/results first. The original is still needed by those results.'},status=409)
            VideoJob.objects.filter(pk=pk).update(delete_requested=True, pause_requested=True)
            VideoJob.objects.filter(pk=pk, status='queued').update(status='paused')
            job.refresh_from_db()
        if job.status == 'running':
            return JsonResponse(job_data(job), status=202)
        try:
            remove_job(job)
        except OSError:
            return JsonResponse({'error': 'Unable to remove generated files. Please retry.'}, status=503)
        return JsonResponse({'deleted': pk})
    if job.delete_requested:
        return JsonResponse({'error': 'This generation is being deleted.'}, status=409)
    if action == 'pause':
        VideoJob.objects.filter(pk=pk, status='queued').update(status='paused', stage='Paused; completed segments are saved', pause_requested=True)
        VideoJob.objects.filter(pk=pk, status='running').update(pause_requested=True, stage='Stopping; completed segments will be kept', updated=timezone.now())
    elif action == 'resume':
        if VideoJob.objects.filter(status__in=['queued', 'running']).count() >= 4:
            return JsonResponse({'error': 'The video queue is full.'}, status=429)
        try:
            with transaction.atomic():
                VideoJob.objects.filter(pk=pk, delete_requested=False, status__in=['paused', 'failed']).update(status='queued', stage='Resuming saved segments', error='', pause_requested=False)
        except IntegrityError:
            return JsonResponse({'error': 'This task could not be queued. Please retry.'}, status=429)
    else:
        return JsonResponse({'error': 'Choose pause or resume.'}, status=400)
    job.refresh_from_db()
    return JsonResponse(job_data(job))

@login_required
def media(request, pk, kind):
    job = get_object_or_404(VideoJob, pk=pk, owner=request.user)
    if kind == 'prompt-log':
        return video_diagnostics.download(job)
    if kind == 'upscale':
        return video_upscale.media(request, job)
    if kind not in ('video', 'audio'):
        raise Http404
    field = getattr(job, kind)
    if not field:
        raise Http404
    ext, mime = ('mp4', 'video/mp4') if kind == 'video' else ('wav', 'audio/wav')
    response = FileResponse(field.open('rb'), as_attachment=request.GET.get('download') == '1', filename=f'h3-{pk}.{ext}', content_type=mime)
    response['Cache-Control'] = 'private, no-store'
    return response
