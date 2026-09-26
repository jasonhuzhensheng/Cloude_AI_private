"""Finish this one installation after queued smoke tests; no recurring scheduler."""
import os,time,json,subprocess,sys
from pathlib import Path
from PIL import Image,ImageChops,ImageStat
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','portal.settings')
import django
django.setup()
from portal.models import PhotoJob
root=Path('/workspace/private-ai')
try:
    # Only this installation's two smoke tests can enable the feature.
    deadline=time.monotonic()+7200
    record=root/'qwen21-runtime/benchmark.json'
    while not record.exists():
        if not Path('/proc/76809').exists():
            raise RuntimeError('Benchmark ended before both modes passed')
        if time.monotonic()>deadline:
            raise RuntimeError('GPU verification is still waiting; administrator follow-up required')
        time.sleep(10)
    results=json.loads(record.read_text())
    assert len(results)==2
    first,second=[PhotoJob.objects.get(pk=item['job'],status='succeeded') for item in results]
    assert first.source_id is None and second.source_id==first.result_id and first.owner_id==second.owner_id
    with Image.open(first.result.file.path) as a,Image.open(second.result.file.path) as b:
        assert a.size==b.size==(1024,1024)
        assert sum(ImageStat.Stat(ImageChops.difference(a.convert('RGB'),b.convert('RGB'))).mean)>1
    env=os.environ.copy()
    env['PORTAL_SETUP']='0'
    with (root/'logs/photo-worker.log').open('ab') as log:
        p=subprocess.Popen([str(root/'web/.venv/bin/python'),'manage.py','photo_worker'],cwd=root/'web',env=env,stdout=log,stderr=log,start_new_session=True)
    time.sleep(2)
    assert p.poll() is None
    (root/'QWEN21_FAILED').unlink(missing_ok=True)
    (root/'QWEN21_READY').touch()
    print('QWEN21_ENABLED',p.pid,flush=True)
except BaseException:
    (root/'QWEN21_FAILED').touch()
    raise
