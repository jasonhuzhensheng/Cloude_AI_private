"""Isolated local model process; inputs/outputs never leave the server."""
import argparse,json,time
from pathlib import Path
import torch
from PIL import Image,ImageOps
from diffusers import QwenImage21Pipeline

parser=argparse.ArgumentParser()
parser.add_argument('request')
args=parser.parse_args()
data=json.loads(Path(args.request).read_text())
progress=Path(data['progress'])
def report(stage,steps=0):
    temporary=progress.with_suffix('.tmp')
    temporary.write_text(json.dumps({'stage':stage,'steps':steps}))
    temporary.replace(progress)
report('Loading Qwen-Image-2.1')
started=time.monotonic()
pipe=QwenImage21Pipeline.from_pretrained('/workspace/private-ai/qwen21-model',torch_dtype=torch.bfloat16,local_files_only=True).to('cuda')
torch.cuda.reset_peak_memory_stats()
paths=([data['source']] if data.get('source') else [])+data.get('references',[])
if len(paths)>3:raise ValueError('At most three reference images')
images=[]
for path in paths:
    with Image.open(path) as original:
        image=ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((1536,1536))
        images.append(image)
image=images if images else None
def step(pipe,index,timestep,kwargs):
    report('Generating image · step '+str(index+1)+' / 40',index+1)
    return kwargs
report('Encoding prompt and source image')
negative=data.get('negative_prompt','').strip()
seed=int(data.get('seed',42))
print('GENERATION_SEED',seed,flush=True)
output=pipe(negative_prompt=negative or None,prompt=data['prompt'],image=image,width=data['width'],height=data['height'],
            num_inference_steps=40,true_cfg_scale=2.0 if negative else 1.0,generator=torch.Generator('cuda').manual_seed(seed),
            callback_on_step_end=step).images[0]
report('Saving PNG image',40)
output.save(data['output'])
Path(data['output']+'.metrics.json').write_text(json.dumps({'seed':seed,'seconds':time.monotonic()-started,'peak_vram_bytes':torch.cuda.max_memory_allocated()}))
print('QWEN21_METRICS',Path(data['output']+'.metrics.json').read_text(),flush=True)
