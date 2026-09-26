"""One isolated SeedVR2 image upscale, keeping exact target size and source alpha."""
import json,subprocess,sys,tempfile
from pathlib import Path
from PIL import Image,ImageOps
request=json.loads(Path(sys.argv[1]).read_text())
root=Path('/workspace/private-ai/seedvr2-runtime')
progress=Path(request['progress'])
def report(text):
    temp=progress.with_suffix('.tmp');temp.write_text(json.dumps({'stage':text,'steps':0}));temp.replace(progress)
with tempfile.TemporaryDirectory(dir=Path(request['output']).parent) as tmp:
    folder=Path(tmp);source=folder/'source.png';raw=folder/'enhanced.png'
    with Image.open(request['source']) as original:
        image=ImageOps.exif_transpose(original).convert('RGBA')
    alpha=image.getchannel('A')
    background=Image.new('RGBA',image.size,'white');background.alpha_composite(image);background.convert('RGB').save(source)
    report('AI 图片超分中 / AI image upscaling')
    subprocess.run([str(root/'.venv/bin/python'),str(root/'inference_cli.py'),str(source),
        '--output',str(raw),'--output_format','png','--resolution',str(min(request['width'],request['height'])),
        '--model_dir',str(root/'models/SEEDVR2'),'--dit_model','seedvr2_ema_7b_fp16.safetensors',
        '--batch_size','1','--seed','42','--dit_offload_device','cpu','--vae_offload_device','cpu',
        '--vae_encode_tiled','--vae_decode_tiled','--attention_mode','sdpa'],check=True)
    report('保存超分图片 / Saving upscaled image')
    with Image.open(raw) as result:
        result=result.convert('RGB').resize((request['width'],request['height']),Image.Resampling.LANCZOS)
        if alpha.getextrema()!=(255,255):result.putalpha(alpha.resize(result.size,Image.Resampling.LANCZOS))
        result.save(request['output'])
