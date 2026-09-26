"""Pinned official model installation and non-user smoke test; no worker restarts."""
import os,sys,json,time,subprocess,hashlib,fcntl
from pathlib import Path
ROOT=Path('/workspace/private-ai')
# The resolved official revision is provided by the deployment command.
revision=sys.argv[1]
runtime=ROOT/'qwen21-runtime/.venv/bin/python'
if '--download' in sys.argv:
    from huggingface_hub import HfApi,snapshot_download
    info=HfApi().model_info('Tongyi-MAI/Z-Image',revision=revision,files_metadata=True)
    assert info.sha==revision
    folder=Path(snapshot_download('Tongyi-MAI/Z-Image',revision=revision,local_dir=ROOT/'zimage-model',max_workers=4))
    checked=[]
    for item in info.siblings:
        if item.lfs:
            path=folder/item.rfilename
            with path.open('rb') as handle:digest=hashlib.file_digest(handle,'sha256').hexdigest()
            assert digest==item.lfs.sha256,item.rfilename
            checked.append(item.rfilename)
    (ROOT/'zimage-manifest.json').write_text(json.dumps({'repo':'Tongyi-MAI/Z-Image','revision':revision,'sha256_verified':checked},indent=2))
    print('ZIMAGE_WEIGHTS_VERIFIED',len(checked),flush=True);sys.exit()
subprocess.run([str(runtime),__file__,revision,'--download'],check=True,timeout=3600)
os.environ.setdefault('DJANGO_SETTINGS_MODULE','portal.settings')
sys.path.insert(0,str(ROOT/'web'))
# manage.py uses this settings module; supplied explicitly by deployment.
import django
django.setup()
from django.conf import settings
from portal.gpu import GPULock
from PIL import Image
work=ROOT/'zimage-verification';work.mkdir(exist_ok=True)
payload={'prompt':'Editorial full-length photograph of an adult woman aged 30 wearing an elegant navy tailored trouser suit and white shirt, standing naturally in a bright architectural studio, realistic hands, natural skin texture, soft window lighting, professional fashion photography.', 'width':768,'height':1024,'output':str(work/'portrait.png'),'progress':str(work/'progress.json')}
(work/'request.json').write_text(json.dumps(payload))
print('WAITING_FOR_GPU',flush=True)
start=time.monotonic()
with (settings.DATA/'media-engine.lock').open('a') as media:
    while True:
        try:fcntl.flock(media,fcntl.LOCK_EX|fcntl.LOCK_NB);break
        except BlockingIOError:
            if time.monotonic()-start>1800:raise TimeoutError('Busy GPU; no tasks interrupted')
            time.sleep(3)
    lock=GPULock()
    while not lock.acquire(blocking=False):
        if time.monotonic()-start>1800:raise TimeoutError('Busy GPU')
        time.sleep(3)
    try:
        subprocess.run([str(runtime),str(ROOT/'web/deployment/zimage_infer.py'),str(work/'request.json')],env=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1'),check=True,timeout=900)
        with Image.open(work/'portrait.png') as image:
            image.verify();assert image.size==(768,1024)
        (ROOT/'ZIMAGE_READY').touch()
        print('ZIMAGE_READY', (work/'portrait.png.metrics.json').read_text(),flush=True)
    finally:lock.release()
