"""Official Z-Image text-to-image, local weights and isolated GPU lifetime."""
import argparse,json,time
from pathlib import Path
import torch
from diffusers import ZImagePipeline
parser=argparse.ArgumentParser();parser.add_argument('request');args=parser.parse_args()
data=json.loads(Path(args.request).read_text())
if data.get('source'):raise ValueError('Z-Image supports text-to-image only')
progress=Path(data['progress'])
def report(stage,steps=0):
    temporary=progress.with_suffix('.tmp');temporary.write_text(json.dumps({'stage':stage,'steps':steps}));temporary.replace(progress)
report('正在加载 Z-Image / Loading Z-Image')
started=time.monotonic()
pipe=ZImagePipeline.from_pretrained('/workspace/private-ai/zimage-model',torch_dtype=torch.bfloat16,local_files_only=True).to('cuda')
torch.cuda.reset_peak_memory_stats()
def step(pipe,index,timestep,kwargs):
    report('生成图片 / Generating image · '+str(index+1)+' / 40',index+1)
    return kwargs
output=pipe(prompt=data['prompt'],negative_prompt=data.get('negative_prompt',''),width=data['width'],height=data['height'],num_inference_steps=40,guidance_scale=4.0,cfg_normalization=False,generator=torch.Generator('cuda').manual_seed(int(data.get('seed',42))),callback_on_step_end=step).images[0]
report('保存图片 / Saving PNG',40);output.save(data['output'])
metrics={'seconds':round(time.monotonic()-started,2),'peak_vram_bytes':torch.cuda.max_memory_allocated()}
Path(data['output']+'.metrics.json').write_text(json.dumps(metrics));print('ZIMAGE_METRICS',json.dumps(metrics),flush=True)
