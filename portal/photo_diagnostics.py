"""Read-only, owner-protected image prompt diagnostics; no raw logs exposed."""
from pathlib import Path
from django.conf import settings
from django.http import JsonResponse
from .video_diagnostics import scan
from .photo_sizes import UPSCALES

def report(job, details=False):
    events=[];available=False;partial=False
    try:
        path=Path(settings.IMAGE_EDIT_ROOT)/'logs'/f'photo-{job.pk}.log'
        stat=path.stat()
        if stat.st_size:
            events,partial=scan(str(path),stat.st_mtime_ns,stat.st_size);available=True
    except OSError:pass
    upscale=job.format in UPSCALES
    state='not_applicable' if upscale else 'review' if any(e['type']=='possible_refusal' for e in events) else 'no_explicit_signal' if available else 'unavailable'
    labels={
        'not_applicable':'此超分模型不接收文字提示词 / Text prompts are not used by this upscaler.',
        'review':'发现疑似拒绝或过滤信号，请检查日志 / Possible refusal or filtering signal; review the log.',
        'no_explicit_signal':'未发现明确拒绝信号，不代表模型执行了所有要求 / No explicit refusal signal; instruction compliance is not guaranteed.',
        'unavailable':'暂无可检查日志，拒绝状态未知 / No log available; refusal status is unknown.'}
    result=dict(state=state,message=labels[state],partial_scan=partial,events=events,
        log_url=f'/api/photos/?prompt_log={job.pk}')
    if details:
        result.update(seed=job.seed,job_id=job.pk,status=job.status,model='SeedVR2 7B' if upscale else ('Z-Image' if job.model=='zimage' else 'Qwen-Image-2.1'),
            reference_ids=list(job.references.values_list("document_id",flat=True)),saved_prompt=job.prompt,saved_negative_prompt=job.negative_prompt,prompt_used=not upscale,source_id=job.source_id,result_id=job.result_id,
            output_format=job.format,
            prompt_source='数据库保存的提示词，并非模型内部接收记录 / Saved request, not a capture of internal model input.',
            limitation='仅检测已识别的结构化日志信号，可能漏检。生成失败、黑图或内容未变化都不能单独证明模型拒绝。 / Checks recognized structured log signals only and may miss others. Failure, black images or missing edits alone do not prove refusal.')
    return result

def download(job):
    response=JsonResponse(report(job,True),json_dumps_params={'ensure_ascii':False,'indent':2})
    response['Content-Disposition']=f'attachment; filename="photo-{job.pk}-prompt-check.json"'
    response['Cache-Control']='private, no-store'
    return response
