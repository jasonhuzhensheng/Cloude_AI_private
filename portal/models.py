import uuid
from django.conf import settings
from django.db import models
def upload_path(instance, name):
    return f'{instance.owner_id}/{uuid.uuid4().hex}'
class Conversation(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=100, default='New chat')
    model_id = models.CharField(max_length=100, default='Qwen3.8-27B-Uncensored')
    created = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-id']
        verbose_name = 'conversation'
class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=12)
    content = models.TextField()
    metadata = models.JSONField(default=dict,blank=True)
    created = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ['id']
class Document(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='documents')
    name = models.CharField(max_length=250)
    file = models.FileField(upload_to=upload_path)
    size = models.PositiveIntegerField()
    text = models.TextField()
    truncated = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

class ImageEditJob(models.Model):
    owner=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.CASCADE)
    source=models.ForeignKey(Document,on_delete=models.CASCADE,related_name='image_edits')
    prompt=models.TextField()
    status=models.CharField(max_length=16,default='queued')
    stage=models.CharField(max_length=200,default='Waiting for the GPU')
    result=models.ForeignKey(Document,null=True,blank=True,on_delete=models.SET_NULL,related_name='image_edit_results')
    error=models.TextField(blank=True)
    created=models.DateTimeField(auto_now_add=True)
    updated=models.DateTimeField(auto_now=True)
    class Meta:
        ordering=['-id']
        constraints=[models.UniqueConstraint(fields=['owner'],condition=models.Q(status__in=['queued','running']),name='one_active_image_edit_per_user')]

class VideoJob(models.Model):
    continuation_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='continuations')
    prefix_seconds = models.PositiveIntegerField(default=0)
    timeline = models.JSONField(default=list, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL)
    mode = models.CharField(max_length=16, default='fl2va')
    prompt = models.TextField()
    orientation = models.CharField(max_length=16, default='landscape', choices=[('landscape', 'Landscape'), ('portrait', 'Portrait')])
    resolution = models.CharField(max_length=8, default='288p')
    duration_seconds = models.PositiveIntegerField(default=3)
    completed_segments = models.PositiveIntegerField(default=0)
    pause_requested = models.BooleanField(default=False)
    delete_requested = models.BooleanField(default=False)
    segment_seconds = models.FloatField(default=0)
    timed_segments = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, default='queued')
    stage = models.CharField(max_length=200, default='Waiting for the GPU')
    video = models.FileField(upload_to=upload_path, blank=True)
    audio = models.FileField(upload_to=upload_path, blank=True)
    size = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-id']
        constraints = [models.UniqueConstraint(fields=['owner'], condition=models.Q(status='running'), name='one_running_video_per_user')]

class VideoUpscaleJob(models.Model):
    delete_requested = models.BooleanField(default=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source = models.ForeignKey(VideoJob, on_delete=models.PROTECT, related_name='upscales')
    resolution = models.PositiveIntegerField(default=1080)
    status = models.CharField(max_length=16, default='queued')
    stage = models.CharField(max_length=200, default='Waiting for the GPU')
    cancel_requested = models.BooleanField(default=False)
    video = models.FileField(upload_to=upload_path, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-id']
        constraints = [models.UniqueConstraint(fields=['owner'], condition=models.Q(status__in=['queued','running']), name='one_active_upscale_per_user')]

class PhotoJob(models.Model):
    seed = models.PositiveIntegerField(default=42)
    negative_prompt = models.TextField(blank=True, default="")
    delete_requested = models.BooleanField(default=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    source = models.ForeignKey(Document, null=True, blank=True, on_delete=models.PROTECT, related_name='photo_sources')
    result = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL, related_name='photo_results')
    model = models.CharField(max_length=32, default='qwen21')
    prompt = models.TextField()
    format = models.CharField(max_length=16, default='square')
    status = models.CharField(max_length=16, default='queued')
    stage = models.CharField(max_length=200, default='Waiting for the GPU')
    cancel_requested = models.BooleanField(default=False)
    completed_steps = models.PositiveIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-id']


class PhotoReference(models.Model):
    job = models.ForeignKey(PhotoJob, on_delete=models.CASCADE, related_name='references')
    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='photo_references')
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['job', 'document'], name='photo_unique_reference'),
                       models.UniqueConstraint(fields=['job', 'position'], name='photo_reference_position')]
