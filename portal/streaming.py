import json, time, requests
from django.conf import settings
from django.db import transaction
from .models import Message

def event(**data):
    return json.dumps(data,ensure_ascii=False)+'\n'

def stream_answer(chat,prompt,body,has_documents,lock,model=None):
    response=None
    try:
        model=model or {'id':body['model'],'label':body['model']}
        yield event(type='start',model_label=model['label'])
        started=time.monotonic()
        response=requests.post(settings.MODEL_URL,json={**body,'stream':True},stream=True,timeout=(5,600) if model['id']=='Qwen3.8-Flash-Next' else (5,60))
        response.raise_for_status()
        answer=''; finish_reason=None
        for line in response.iter_lines(chunk_size=1):
            if time.monotonic()-started>600:
                raise TimeoutError('generation deadline')
            if not line or not line.startswith(b'data:'): continue
            payload=line[5:].strip()
            if payload==b'[DONE]':
                break
            data=json.loads(payload)
            choices=data.get('choices',[])
            if not choices: continue
            part=choices[0].get('delta',{}).get('content') or ''
            if part:
                answer+=part
                yield event(type='delta',text=part)
            if choices[0].get('finish_reason') is not None: finish_reason=choices[0]['finish_reason']
        if not answer or finish_reason not in ('stop','length'): raise ValueError('incomplete generation')
        with transaction.atomic():
            chat.model_id=model['id'];chat.save(update_fields=['model_id'])
            Message.objects.create(conversation=chat,role='user',content=prompt)
            Message.objects.create(conversation=chat,role='assistant',content=answer,metadata={'incomplete':finish_reason=='length','model_id':model['id'],'model_label':model['label']})
            if chat.title in ('New chat','新对话'):
                chat.title=prompt[:60]; chat.save(update_fields=['title'])
        yield event(type='done',title=chat.title,files_partial=has_documents,incomplete=finish_reason=='length',model_label=model['label'])
    except Exception:
        yield event(type='error',error='The response was interrupted or timed out. Your question has been preserved. Please try again.')
    finally:
        if response is not None: response.close()
        lock.release()
