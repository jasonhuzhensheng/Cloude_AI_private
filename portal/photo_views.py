from portal.photo_remote import public_status
from portal.storage_quota import ACCOUNT_BYTES, account_used
import json,secrets
from pathlib import Path
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Sum,Q
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.utils import timezone
from .models import PhotoJob, Document, ImageEditJob, VideoJob, PhotoReference
from .images import is_image
from . import photo_diagnostics, photo_models
from .video_views import queue_admission

from .photo_sizes import SIZES, UPSCALES, output_size, native_sizes

def ready():
    return (Path(settings.IMAGE_EDIT_ROOT)/'QWEN21_READY').is_file()

def serialize(job):
    return dict(seed=job.seed,reference_ids=list(job.references.values_list("document_id",flat=True)),id=job.pk,delete_requested=job.delete_requested,model=job.model,model_label=photo_models.MODELS.get(job.model,{}).get("label",job.model),prompt_check=photo_diagnostics.report(job),prompt=job.prompt,negative_prompt=job.negative_prompt,status=job.status,stage=job.stage,
                completed_steps=job.completed_steps,result_id=job.result_id,
                format=job.format,is_upscale=job.format in UPSCALES,source_id=job.source_id,created=job.created.isoformat(),updated=job.updated.isoformat())

@login_required
def studio(request):
    return render(request,'photos.html',{'images':[d for d in Document.objects.filter(owner=request.user).order_by('-id') if is_image(d.name)][:100]})

@login_required
def jobs(request):
    if "prompt_log" in request.GET:
        identifier=request.GET["prompt_log"]
        if not identifier.isdecimal() or len(identifier)>18:
            return JsonResponse({"error":"无效任务编号 / Invalid task ID."},status=400)
        return photo_diagnostics.download(get_object_or_404(PhotoJob,pk=int(identifier),owner=request.user))
    for pending in PhotoJob.objects.filter(owner=request.user,delete_requested=True).exclude(status__in=['queued','running']):
        try:delete_finished(pending.pk,request.user)
        except ValueError:
            PhotoJob.objects.filter(pk=pending.pk).update(delete_requested=False,stage='无法删除：图片被其他任务使用 / Cannot delete: image used by another task')
    owned=PhotoJob.objects.filter(owner=request.user)
    recent=list(owned.values_list('pk',flat=True)[:30])
    upscale=owned.filter(format__in=UPSCALES)
    recent_upscale=list(upscale.values_list('pk',flat=True)[:30])
    response=JsonResponse({'photo_gpu':public_status(),'upscales':[serialize(j) for j in upscale.filter(Q(pk__in=recent_upscale)|Q(status__in=['queued','running']))], 'models':photo_models.choices(),'ready':ready(),'upscale_ready':(Path(settings.IMAGE_EDIT_ROOT)/'PHOTO_UPSCALE_READY').is_file(),'setup_failed':(Path(settings.IMAGE_EDIT_ROOT)/'QWEN21_FAILED').is_file(),'jobs':[serialize(j) for j in owned.filter(Q(pk__in=recent)|Q(status__in=['queued','running']))]})
    response['Cache-Control']='private, no-store'
    return response

@login_required
@require_POST
@queue_admission
def create(request):
    try:
        data=json.loads(request.body)
        if isinstance(data,dict) and 'variant_of' in data:return create_variants(request,data)
        prompt=data.get('prompt');format=data.get('format','max_square');model=data.get('model','qwen21')
        if not isinstance(model,str) or model not in photo_models.MODELS:raise ValueError()
        if not isinstance(prompt,str) or not 1<=len(prompt.strip())<=2000 or format not in native_sizes(model) and format not in UPSCALES:raise ValueError()
        negative=data.get('negative_prompt','')
        if not isinstance(negative,str) or len(negative)>1000:
            return JsonResponse({'error':'负面提示词最多 1000 字符 / Negative prompt must be at most 1000 characters.'},status=400)
        reference_ids=data.get('reference_ids',[])
        if not isinstance(reference_ids,list) or len(reference_ids)>3 or any(type(i) is not int or i<=0 for i in reference_ids):raise ValueError()
        if len(set(reference_ids))!=len(reference_ids):raise ValueError()
        source_id=data.get('source_id')
        if source_id is not None and (type(source_id) is not int or source_id<=0):raise ValueError()
    except (ValueError,TypeError,AttributeError):
        return JsonResponse({'error':'Enter a prompt of 1–2,000 characters and a valid image format.'},status=400)
    if source_id in reference_ids or len(reference_ids)+bool(source_id)>3:
        return JsonResponse({'error':'最多 3 张不同参考照片（含原图） / Choose up to 3 distinct images, including the source.'},status=400)
    if reference_ids and format in UPSCALES:
        return JsonResponse({'error':'超分只使用一张原图 / Upscaling accepts one source only.'},status=400)
    references=[get_object_or_404(Document,pk=pk,owner=request.user) for pk in reference_ids]
    if any(not is_image(d.name) for d in references):
        return JsonResponse({'error':'参考文件必须是照片 / References must be images.'},status=400)
    if references and not photo_models.MODELS[model]['edit']:
        return JsonResponse({'error':'多图参考请使用 Qwen / Use Qwen for image references.'},status=400)
    if format not in UPSCALES and not photo_models.ready(model):
        return JsonResponse({'error':'所选模型尚未就绪 / Selected model is not ready.'},status=503)
    source=get_object_or_404(Document,pk=source_id,owner=request.user) if source_id else None
    if source and not is_image(source.name):
        return JsonResponse({'error':'Choose an image to edit.'},status=400)
    if source and format not in UPSCALES and not photo_models.MODELS[model]['edit']:
        return JsonResponse({'error':'Z-Image 仅支持文字生成，请使用 Qwen 编辑照片 / Z-Image is text-to-image only; use Qwen to edit.'},status=400)
    if format in UPSCALES:
        if not (Path(settings.IMAGE_EDIT_ROOT)/'PHOTO_UPSCALE_READY').is_file():
            return JsonResponse({'error':'图片超分正在验证 / Image upscaling is being verified.'},status=503)
        try:output_size(format,source)
        except (ValueError,OSError) as exc:return JsonResponse({'error':str(exc)},status=400)
    if PhotoJob.objects.filter(status__in=['queued','running']).count()>=4:
        return JsonResponse({'error':'The photo queue is full (4 tasks).'},status=429)
    used=account_used(request.user)
    if used+20*1024**2>ACCOUNT_BYTES:
        return JsonResponse({'error':'Not enough image storage. Free space before generating.'},status=400)
    with transaction.atomic():
        job=PhotoJob.objects.create(owner=request.user,source=source,prompt=prompt.strip(),format=format,model=model,negative_prompt=negative.strip() if format not in UPSCALES else '')
        PhotoReference.objects.bulk_create([PhotoReference(job=job,document=d,position=i) for i,d in enumerate(references)])
    return JsonResponse(serialize(job),status=202)

def create_variants(request,data):
    # Called only within the authenticated POST create endpoint's admission lock.
    degree=data.get('variation_degree','medium')
    if not isinstance(degree,str) or degree not in ['low','medium','high']:
        return JsonResponse({'error':'请选择低、中或高差异度 / Choose low, medium or high variation.'},status=400)
    pk,count=data.get('variant_of'),data.get('count')
    if type(pk) is not int or pk<=0 or type(count) is not int or not 1<=count<=4:
        return JsonResponse({'error':'请输入 1–4 的整数 / Enter a whole number from 1 to 4.'},status=400)
    parent=get_object_or_404(PhotoJob,pk=pk,owner=request.user)
    if parent.status!='succeeded' or not parent.result_id or parent.delete_requested:
        return JsonResponse({'error':'请使用已完成且有结果的任务 / Choose a completed task with a result.'},status=400)
    if parent.result.owner_id!=request.user.pk:
        return JsonResponse({'error':'Result unavailable.'},status=404)
    if not photo_models.ready('qwen21'):
        return JsonResponse({'error':'Qwen 尚未就绪 / Qwen is not ready.'},status=503)
    slots=max(0,4-PhotoJob.objects.filter(status__in=['queued','running']).count())
    if count>slots:
        return JsonResponse({'error':f'队列仅剩 {slots} 个空位，请减少数量或稍后再试 / Only {slots} queue slots available; reduce the count or try later.'},status=429)
    used=account_used(request.user)
    if used+count*20*1024**2>ACCOUNT_BYTES:
        return JsonResponse({'error':'存储空间不足，请减少数量或清理结果 / Not enough storage; reduce the count or remove unneeded results.'},status=400)
    format=parent.format if parent.format not in UPSCALES else 'max_square'
    if parent.format in UPSCALES:
        from PIL import Image
        with Image.open(parent.result.file.path) as image:
            ratio=image.width/image.height
        format=min(native_sizes('qwen21'),key=lambda key:abs(native_sizes('qwen21')[key][0]/native_sizes('qwen21')[key][1]-ratio)+abs(native_sizes('qwen21')[key][0]*native_sizes('qwen21')[key][1]-2048**2)/1e9)
    levels={
        'low':('低 / Low','Keep the pose, scene, clothing and framing close to the reference. Make only small but visible changes.',[
            'Slightly shift the camera angle to the left.', 'Slightly shift the camera angle to the right.',
            'Make a small natural change in pose or subject orientation.', 'Slightly vary the lighting direction.',
            'Slightly widen the framing.', 'Slightly tighten the framing.']),
        'medium':('中 / Medium','Create a visibly different alternative photograph. Preserve the subject identity, clothing and overall visual style, but change the composition.',[
            'Use a wider composition and place the subject off-center.',
            'Use a closer framing with a softly blurred background.',
            'Use a three-quarter view from the left and a different natural pose or subject orientation.',
            'Use a three-quarter view from the right and a different natural pose or subject orientation.',
            'Use a lower camera viewpoint with the subject on the right third.',
            'Use a higher camera viewpoint with the subject on the left third.']),
        'high':('高 / High','Create a substantially different photograph of the same subject. Preserve identity and clothing, but noticeably change the pose or orientation, camera angle, framing and background arrangement. Keep the same broad photographic style.',[
            'Create a wide environmental view in a new complementary setting with a different subject pose or orientation.',
            'Create a close side-angle composition with a contrasting lighting direction and a new background.',
            'Use a low-angle wide composition, a new natural pose or orientation and a different background layout.',
            'Use a high-angle composition with the subject placed off-center, a different pose or orientation and new scenery.',
            'Create a candid-looking composition from a markedly different camera position with a changed setting.',
            'Create a dramatic side-lit composition with a new pose or orientation and a different camera distance.'])
    }
    label,instruction,options=levels[degree]
    prompt=f'差异度 / Variation level: {label}. '+instruction+' Produce one image, not a collage. '
    changes=secrets.SystemRandom().sample(options,count)
    seeds=secrets.SystemRandom().sample(range(2147483647),count)
    with transaction.atomic():
        created=[PhotoJob.objects.create(owner=request.user,source=parent.result,model='qwen21',format=format,prompt=prompt+change,negative_prompt=parent.negative_prompt,seed=seed) for seed,change in zip(seeds,changes)]
    return JsonResponse({'jobs':[serialize(j) for j in created],'count':count,'variation_degree':degree},status=202)

@login_required
@require_POST
def cancel(request,pk):
    job=get_object_or_404(PhotoJob,pk=pk,owner=request.user)
    if request.GET.get('action')=='delete':
        if result_in_use(job):return JsonResponse({'error':'图片被其他编辑、超分或视频任务使用，请先删除关联任务 / Image is used by another task; remove dependent tasks first.'},status=409)
        PhotoJob.objects.filter(pk=pk,owner=request.user,status='queued').update(status='cancelled',cancel_requested=True,delete_requested=True,updated=timezone.now())
        PhotoJob.objects.filter(pk=pk,owner=request.user).update(cancel_requested=True,delete_requested=True)
        try:deleted=delete_finished(pk,request.user)
        except ValueError:return JsonResponse({'error':'图片被其他任务使用 / Image is used by another task.'},status=409)
        return JsonResponse({'deleted':deleted,'id':pk,'pending':not deleted})
    PhotoJob.objects.filter(pk=pk,status='queued').update(status='cancelled',stage='Cancelled',cancel_requested=True,updated=timezone.now())
    PhotoJob.objects.filter(pk=pk,status='running').update(cancel_requested=True,stage='Stopping…',updated=timezone.now())
    job.refresh_from_db()
    return JsonResponse(serialize(job))


def result_in_use(job):
    if not job.result_id:return False
    return (job.result_id==job.source_id or PhotoReference.objects.filter(document_id=job.result_id).exists() or PhotoJob.objects.filter(source_id=job.result_id).exists()
        or PhotoJob.objects.filter(result_id=job.result_id).exclude(pk=job.pk).exists()
        or ImageEditJob.objects.filter(Q(source_id=job.result_id)|Q(result_id=job.result_id)).exists()
        or VideoJob.objects.filter(source_id=job.result_id).exists())

def delete_finished(pk,owner):
    with transaction.atomic():
        job=PhotoJob.objects.select_for_update().filter(pk=pk,owner=owner).first()
        if not job:return True
        if job.status in ['queued','running']:return False
        if result_in_use(job):raise ValueError('Dependent task')
        result=job.result
        if result and result.owner_id!=owner.pk:raise ValueError('Invalid owner')
        job.delete()
        if result:
            storage,name=result.file.storage,result.file.name
            try:result.delete()
            except ProtectedError as exc:raise ValueError('Dependent task') from exc
            transaction.on_commit(lambda:storage.delete(name))
    return True
