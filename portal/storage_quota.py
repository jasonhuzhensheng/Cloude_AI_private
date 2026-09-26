"""Account-wide quota for saved uploads and generated results."""
from django.db.models import Sum
ACCOUNT_BYTES = 100 * 1024**3

def account_used(owner, exclude_video=None, exclude_upscale=None):
    from .models import Document, VideoJob, VideoUpscaleJob
    documents=Document.objects.filter(owner=owner)
    videos=VideoJob.objects.filter(owner=owner)
    upscales=VideoUpscaleJob.objects.filter(owner=owner)
    if exclude_video is not None:videos=videos.exclude(pk=exclude_video)
    if exclude_upscale is not None:upscales=upscales.exclude(pk=exclude_upscale)
    return sum(q.aggregate(total=Sum('size'))['total'] or 0 for q in (documents,videos,upscales))
