"""Run once on primary: provision one bounded-price photo pod, then stop it."""
import json, os, time
from pathlib import Path
import requests
ROOT=Path('/workspace/private-ai')
NAME='private-ai-photo-ondemand'
KEY=Path('/root/.config/private-ai/runpod-photo.key')
s=requests.Session();s.headers['Authorization']='Bearer '+KEY.read_text().strip()
query='query { myself { clientBalance } gpuTypes(input: {id: "NVIDIA RTX PRO 6000 Blackwell Server Edition"}) { lowestPrice(input: {gpuCount: 1, secureCloud: true, dataCenterId: "CA-MTL-3"}) { stockStatus uninterruptablePrice } } }'
r=s.post('https://api.runpod.io/graphql',json={'query':query},timeout=30);r.raise_for_status();data=r.json()['data']
price=data['gpuTypes'][0]['lowestPrice'];balance=float(data['myself']['clientBalance'])
assert price and float(price['uninterruptablePrice'])<=2.09 and price['stockStatus']!='None','No GPU within price limit'
assert balance>15,'Insufficient balance'
r=s.get('https://rest.runpod.io/v1/pods',timeout=30);r.raise_for_status()
assert not any(p.get('name')==NAME for p in r.json()),'Existing photo pod: reconcile instead of creating duplicate'
intent=ROOT/'photo-provision-intent.json'
assert not intent.exists(),'Existing provisioning attempt: reconcile first'
intent.write_text(json.dumps({'time':time.time(),'name':NAME}))
payload={'name':NAME,'cloudType':'SECURE','computeType':'GPU','gpuCount':1,'gpuTypeIds':['NVIDIA RTX PRO 6000 Blackwell Server Edition'],'dataCenterIds':['CA-MTL-3'],'imageName':'runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404','containerDiskInGb':10,'volumeInGb':0,'networkVolumeId':'lnfg49c1r0','volumeMountPath':'/workspace','ports':[], 'interruptible':False,'dockerEntrypoint':['/bin/bash','-lc'],'dockerStartCmd':['exec /usr/bin/python3 /workspace/private-ai/web/deployment/photo_gpu_executor.py'],'env':{}}
r=s.post('https://rest.runpod.io/v1/pods',json=payload,timeout=60)
print('CREATE_HTTP',r.status_code,flush=True)
if not r.ok:
 print('CREATE_ERROR',r.text[:800]);raise SystemExit(1)
pod=r.json();identity=pod['id'];print('NEW_PHOTO_POD',identity,flush=True)
config={'enabled':False,'pod_id':identity,'volume_id':'lnfg49c1r0','max_hourly':2.10,'initial_balance':balance,'remaining_budget':min(balance,130.0)}
(ROOT/'photo-gpu.json').write_text(json.dumps(config))
r=s.post('https://rest.runpod.io/v1/pods/'+identity+'/stop',timeout=30)
print('INITIAL_STOP_HTTP',r.status_code,flush=True);r.raise_for_status()
