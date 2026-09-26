from portal.storage_quota import ACCOUNT_BYTES, account_used
import json, hashlib, threading, requests, re
from pathlib import Path
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.http import JsonResponse, HttpResponse, FileResponse, Http404, StreamingHttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache
from django.views.csrf import csrf_failure as default_csrf_failure
from .models import Conversation, Message, Document
from .documents import extract
from .images import is_image, normalize_image, image_content
from .model_registry import choices as model_choices, resolve as resolve_model, default_model

class LoginThrottle:
    def __init__(self,get_response): self.get_response=get_response
    def __call__(self,request):
        key=None
        if request.method=='POST' and request.path_info in ('/login/','/admin/login/'):
            key='login:'+hashlib.sha256(request.POST.get('username','').casefold().encode()).hexdigest()
            cache.add(key,0,300)
            count=cache.incr(key)
            if count>10: return HttpResponse('Too many attempts. Please try again in 5 minutes.',status=429)
        response=self.get_response(request)
        if key and response.status_code==302: cache.delete(key)
        return response

@never_cache
def csrf_failure(request, reason=""):
    # Reject the stale POST; recover with a fresh GET, never replay credentials.
    if request.path_info == "/login/":
        return redirect(reverse("login") + "?expired=1")
    return default_csrf_failure(request, reason=reason)

@never_cache
def sign_in(request):
    if request.user.is_authenticated: return redirect('home')
    error='This sign-in page has expired. Please enter your username and password again.' if request.GET.get('expired') == '1' else ''
    if request.method=='POST':
        user=authenticate(request,username=request.POST.get('username',''),password=request.POST.get('password',''))
        if user: login(request,user); return redirect('home')
        error='Incorrect username or password, or your account is inactive.'
    return render(request,'login.html',{'error':error})

@require_POST
def sign_out(request):
    logout(request)
    return redirect('login')

def setup(request):
    if not settings.ENABLE_SETUP or get_user_model().objects.filter(is_superuser=True).exists(): raise Http404
    form=UserCreationForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            if get_user_model().objects.filter(is_superuser=True).exists(): raise Http404
            user=form.save(commit=False); user.is_staff=True; user.is_superuser=True; user.save()
        return render(request,'setup.html',{'complete':True})
    return render(request,'setup.html',{'form':form})

@login_required
def home(request):
    return render(request,'chat.html',{'conversations':Conversation.objects.filter(owner=request.user)[:100], 'chat_models':model_choices(), 'default_model':default_model()})

@login_required
@require_POST
def new_chat(request):
    chat=Conversation.objects.create(owner=request.user, model_id=default_model())
    return JsonResponse({'id':chat.id,'title':chat.title})

@login_required
def detail(request,pk):
    chat=get_object_or_404(Conversation,pk=pk,owner=request.user)
    from .chat_files import metadata_data
    messages=[{'role':m.role,'content':m.content,'model_label':m.metadata.get('model_label',''),**metadata_data(m)} for m in chat.messages.select_related('conversation')]
    return JsonResponse({'id':chat.id,'title':chat.title,'model_id':chat.model_id,'messages':messages,'files':[{**f,'image':is_image(f['name'])} for f in chat.documents.values('id','name','size','truncated')]})

@login_required
@require_POST
def upload(request,pk):
    chat=get_object_or_404(Conversation,pk=pk,owner=request.user)
    f=request.FILES.get('file')
    if not f or f.size>20*1024*1024: return JsonResponse({'error':'Choose a file no larger than 20 MB.'},status=400)
    total=account_used(request.user)
    if total+f.size>ACCOUNT_BYTES: return JsonResponse({'error':'Your file storage limit is 100 GB. Please contact your administrator.'},status=400)
    if chat.documents.count()>=5: return JsonResponse({'error':'You can upload up to 5 files per chat.'},status=400)
    try:
        raw=f.read(); name=Path(f.name).name[:250]
        if is_image(name):
            if sum(is_image(d.name) for d in chat.documents.all())>=2: raise ValueError('You can upload up to 2 images per chat. Start a new chat to add more.')
            raw=normalize_image(raw); name=Path(name).stem[:240]+'.jpg'; text=''; truncated=False
        else: text,truncated=extract(name,raw)
    except ValueError as e: return JsonResponse({'error':str(e)},status=400)
    except Exception: return JsonResponse({'error':'Unable to read this file. Please check its format.'},status=400)
    doc=Document(owner=request.user,conversation=chat,name=name,size=len(raw),text=text,truncated=truncated)
    doc.file.save('upload',ContentFile(raw),save=True)
    return JsonResponse({'id':doc.id,'name':doc.name,'size':doc.size,'truncated':truncated,'image':is_image(doc.name)})

@login_required
def download(request,pk):
    doc=get_object_or_404(Document,pk=pk,owner=request.user)
    if request.GET.get('preview')=='1' and is_image(doc.name):
        response=FileResponse(doc.file.open('rb'),content_type='image/png' if Path(doc.name).suffix.lower()=='.png' else 'image/jpeg')
        response['Cache-Control']='private, no-store'
        return response
    return FileResponse(doc.file.open('rb'),as_attachment=True,filename=doc.name,content_type='application/octet-stream')

from .gpu import GPULock
_generation_lock=GPULock()
@login_required
@require_POST
def send(request,pk):
    chat=get_object_or_404(Conversation,pk=pk,owner=request.user)
    try:
        payload=json.loads(request.body);prompt=payload.get('message','')
        if not isinstance(prompt,str):raise ValueError()
        prompt=prompt.strip()
    except (ValueError,AttributeError,TypeError): return JsonResponse({'error':'Invalid message format.'},status=400)
    if not isinstance(prompt,str) or not prompt or len(prompt)>4000: return JsonResponse({'error':'Messages must contain 1–4,000 characters.'},status=400)
    try:model=resolve_model(payload.get('model_id',chat.model_id))
    except ValueError as exc:return JsonResponse({'error':str(exc)},status=400)
    from .chat_files import maybe_file_action
    action=maybe_file_action(request,chat,prompt,payload)
    if action is not None:return action
    if not _generation_lock.acquire(blocking=False): return JsonResponse({'error':'The GPU is busy with another chat or image edit. Please try again shortly.'},status=429)
    streaming=False
    try:
        attachments=list(chat.documents.order_by('-id')[:10])
        images=[d for d in attachments if is_image(d.name)][:2]
        docs=[d for d in attachments if not is_image(d.name)]
        document_budget=6000
        excerpts=[]
        for doc in docs:
            limit=document_budget//max(len(docs),1)
            pieces=[doc.text[i:i+1000] for i in range(0,len(doc.text),900)]
            words=re.findall(r'[A-Za-z0-9_]{2,}',prompt.lower())
            chinese=''.join(re.findall(r'[\u4e00-\u9fff]',prompt))
            words+=list({chinese[i:i+2] for i in range(max(0,len(chinese)-1))})
            ranked=sorted(enumerate(pieces),key=lambda p:sum(p[1].lower().count(w) for w in words),reverse=True)
            chosen=sorted(ranked[:max(1,limit//1000)],key=lambda p:p[0])
            excerpt='\n[…]\n'.join(p for _,p in chosen)[:limit]
            excerpts.append(f'文件：{doc.name}\n{excerpt}')
        system='你是用户的私人 AI 助手，默认用简体中文回答。不要声称已经执行现实世界操作。'
        from .context_budget import build_messages, OUTPUT_TOKENS
        history=[{'role':m.role,'content':m.content} for m in reversed(list(chat.messages.order_by('-id')[:12]))]
        try:
            messages=build_messages(system,prompt,excerpts,history,len(images))
        except ValueError as exc:
            return JsonResponse({'error':str(exc)},status=400)
        body={'model':model['id'],'messages':messages,'max_tokens':OUTPUT_TOKENS,'temperature':0.7,'chat_template_kwargs':{'enable_thinking':False}}
        if images:
            body['messages'][-1]['content']=[{'type':'text','text':prompt}]+[image_content(d) for d in images]
        if request.headers.get('Accept') == 'application/x-ndjson':
            from .streaming import stream_answer
            response=StreamingHttpResponse(stream_answer(chat,prompt,body,bool(docs),_generation_lock,model),content_type='application/x-ndjson')
            response['Cache-Control']='no-cache, no-store'
            response['X-Accel-Buffering']='no'
            streaming=True
            return response
        response=requests.post(settings.MODEL_URL,json=body,timeout=(5,600))
        response.raise_for_status()
        choice=response.json()['choices'][0]
        answer=choice['message']['content']
        finish_reason=choice.get('finish_reason','stop')
        if finish_reason not in ('stop','length'): raise ValueError('incomplete generation')
        if not answer: raise ValueError('empty')
        with transaction.atomic():
            chat.model_id=model['id']; chat.save(update_fields=['model_id'])
            Message.objects.create(conversation=chat,role='user',content=prompt)
            Message.objects.create(conversation=chat,role='assistant',content=answer,metadata={'incomplete':finish_reason=='length','model_id':model['id'],'model_label':model['label']})
            if chat.title in ('New chat','新对话'): chat.title=prompt[:60]; chat.save(update_fields=['title'])
        return JsonResponse({'answer':answer,'title':chat.title,'files_partial':bool(docs),'incomplete':finish_reason=='length','model_label':model['label']})
    except Exception:
        return JsonResponse({'error':'The model is unavailable or the request timed out. Your input has been preserved. Please try again.'},status=503)
    finally:
        if not streaming: _generation_lock.release()
