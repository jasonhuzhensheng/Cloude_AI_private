"""Conservative evidence, not a claim that generation followed every instruction."""
import re
from functools import lru_cache
from pathlib import Path
from django.conf import settings
from django.http import JsonResponse
from .video_timeline import segment_prompt
from .video_limits import segment_count

@lru_cache(maxsize=32)
def scan(path, mtime, size):
    with Path(path).open('rb') as stream:
        data=stream.read(1024*1024)
        partial=size>len(data)
        if partial:
            stream.seek(max(len(data),size-1024*1024))
            data+=b'\n'+stream.read(1024*1024)
    events=[]
    for line in data.decode('utf-8',errors='replace').splitlines():
        line=re.sub(r'\x1b\[[0-9;]*m','',line).strip()
        # Only structured engine messages; never match a quoted prompt or scene text.
        clean=re.sub(r'^\[[0-9:. -]+\]\s*','',line)
        if re.match(r'^(?:\[?(?:ERROR|WARN(?:ING)?)\]?[: ]+)?(?:safety(?: checker| filter)?|content filter|moderation)\s*[:=-]',clean,re.I):
            if re.search(r'\b(blocked|rejected|refused|filtered)\b',clean,re.I) and not re.search(r'\b(not|no|disabled|false)\b',clean,re.I):
                events.append({'type':'possible_refusal','message':'Engine safety/moderation log contains a blocking signal.'})
        if re.match(r'^(?:\[?(?:WARN(?:ING)?|ERROR)\]?[: ]+)?(?:prompt|input tokens?|token sequence)\s+(?:was |is |has been )?truncated\b',clean,re.I):
            events.append({'type':'truncation','message':'Engine log reports truncated prompt/input tokens.'})
    return events[-30:],partial

def report(job, details=False):
    path=Path(settings.IMAGE_EDIT_ROOT)/'logs'/f'h3-job-{job.pk}.log'
    events=[];available=False;partial=False
    try:
        stat=path.stat();events,partial=scan(str(path),stat.st_mtime_ns,stat.st_size);available=True
    except OSError:
        pass
    refusal=any(e['type']=='possible_refusal' for e in events)
    state='review' if refusal else 'no_explicit_signal' if available else 'unavailable'
    message=('Possible refusal/filter signal found in engine logs; review required.' if refusal else
             'No explicit refusal signal found. This does not confirm every instruction was followed.' if available else
             'No engine log available yet; refusal status is unknown.')
    if any(e['type']=='truncation' for e in events):message+=' Prompt truncation reported.'
    result=dict(state=state,message=message,partial_scan=partial,events=events,
                log_url=f'/api/video/{job.pk}/prompt-log/?download=1')
    if details:
        result.update(job_id=job.pk,status=job.status,original_prompt=job.prompt,timeline=job.timeline,
            prompt_source='Reconstructed from saved settings using the current compiler; not a historical capture of model input.',
            limitation='The local engine does not expose a reliable per-instruction refusal flag. Missing motion is not evidence of refusal. Only recognized structured log signals are checked; unknown formats can be missed. Raw server logs are not exposed.',
            segments=[dict(segment=i+1,prompt=segment_prompt(job,i)) for i in range(segment_count(job.duration_seconds))])
    return result

def download(job):
    response=JsonResponse(report(job,True),json_dumps_params={'ensure_ascii':False,'indent':2})
    response['Content-Disposition']=f'attachment; filename="h3-{job.pk}-prompt-check.json"'
    response['Cache-Control']='private, no-store'
    return response
