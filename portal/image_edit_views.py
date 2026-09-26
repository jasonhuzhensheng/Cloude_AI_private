import json
from pathlib import Path
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import JsonResponse,Http404
from django.shortcuts import render,get_object_or_404
from django.views.decorators.http import require_POST
from .models import Document,ImageEditJob
from .images import is_image

def available():
    return Path(settings.IMAGE_EDIT_READY).is_file()

def source(request,pk):
    doc=get_object_or_404(Document,pk=pk,owner=request.user)
    if not is_image(doc.name):raise Http404
    return doc

def job_data(job):
    from .chat_files import file_data
    result=file_data(job.result) if job.result else None
    return {'file':result,'id':job.id,'status':job.status,'stage':job.stage,'error':job.error,'prompt':job.prompt,'result_id':job.result_id,'created':job.created.isoformat(),'updated':job.updated.isoformat()}

@login_required
def editor(request,pk):
    doc=source(request,pk)
    return render(request,'image_editor.html',{'document':doc,'available':available(),'concurrent_gpu':(Path(settings.IMAGE_EDIT_ROOT)/'CONCURRENT_GPU_READY').is_file()})

@login_required
def jobs(request,pk):
    doc=source(request,pk)
    return JsonResponse({'jobs':[job_data(j) for j in ImageEditJob.objects.filter(owner=request.user,source=doc)[:10]],'available':available()})

@login_required
@require_POST
def create(request,pk):
    doc=source(request,pk)
    if not available(): return JsonResponse({'error':'The local image editor is not ready yet. Please try again later.'},status=503)
    try:
        data=json.loads(request.body);prompt=data.get('instruction','')
        if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=2000:raise ValueError()
    except (ValueError,AttributeError,TypeError):return JsonResponse({'error':'Describe the image changes in 1–2,000 characters.'},status=400)
    if ImageEditJob.objects.filter(status__in=['queued','running']).count()>=4:return JsonResponse({'error':'The image editing queue is full. Please try again later.'},status=429)
    try:job=ImageEditJob.objects.create(owner=request.user,source=doc,prompt=prompt.strip())
    except IntegrityError:return JsonResponse({'error':'You already have an image edit in progress. Wait for it to finish before starting another.'},status=429)
    return JsonResponse(job_data(job),status=202)

@login_required
def status(request,pk):
    job=get_object_or_404(ImageEditJob,pk=pk,owner=request.user)
    return JsonResponse(job_data(job))
