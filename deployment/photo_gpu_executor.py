"""Dedicated GPU executor. No database, API key, public listener or web startup."""
import fcntl, json, os, signal, subprocess, time
from pathlib import Path

ROOT = Path('/workspace/private-ai')
SPOOL = ROOT / 'photo-spool'
SCRIPTS = {'qwen21': 'qwen21_infer.py', 'zimage': 'zimage_infer.py', 'upscale': 'photo_upscale_infer.py'}

def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value))
    temp.replace(path)

def heartbeat():
    state = {'time': time.time(), 'pod': os.environ.get('RUNPOD_POD_ID', '')}
    try:
        text = subprocess.check_output(['nvidia-smi','--query-gpu=name,utilization.gpu,memory.used,memory.total','--format=csv,noheader,nounits'],timeout=5,text=True).strip().splitlines()[0]
        name, utilization, used, total = [item.strip() for item in text.split(',')]
        state.update(name=name, utilization=float(utilization), memory_used_mib=float(used), memory_total_mib=float(total))
    except Exception: pass
    write(SPOOL / 'executor.json', state)

def main():
    singleton = open('/tmp/photo-gpu-executor.lock', 'w')
    fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
    SPOOL.mkdir(exist_ok=True)
    while True:
        heartbeat()
        for folder in sorted(SPOOL.glob('job-*')):
            request = folder / 'request.json'
            if not request.exists() or (folder / 'done.json').exists():
                continue
            # Old requests must never run after a coordinator restart.
            try:
                lease = json.loads((folder / 'lease.json').read_text())
                if time.time() - lease['time'] > 30:
                    continue
                data = json.loads(request.read_text())
                script = SCRIPTS[data['engine']]
                payload = data['payload']
                for key in ('output', 'progress'):
                    if Path(payload[key]).resolve().parent != folder.resolve():
                        raise ValueError('Invalid output path')
                for source in ([payload['source']] if payload.get('source') else []) + payload.get('references', []):
                    if not Path(source).resolve().is_relative_to(ROOT / 'web-data'):
                        raise ValueError('Invalid source path')
                infer = folder / 'infer.json'
                infer.write_text(json.dumps(payload))
                env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
                for key in ('CUDA_MPS_PIPE_DIRECTORY', 'CUDA_MPS_ACTIVE_THREAD_PERCENTAGE'):
                    env.pop(key, None)
                with (folder / 'engine.log').open('ab') as log:
                    proc = subprocess.Popen([str(ROOT / 'qwen21-runtime/.venv/bin/python'), str(ROOT / 'web/deployment' / script), str(infer)], stdout=log, stderr=log, env=env, start_new_session=True)
                    start = time.monotonic()
                    try:
                        while proc.poll() is None:
                            heartbeat()
                            live = json.loads((folder / 'lease.json').read_text())
                            if (folder / 'cancel').exists() or time.time() - live['time'] > 60 or time.monotonic() - start > 1800:
                                raise RuntimeError('Cancelled or lease expired')
                            time.sleep(2)
                        write(folder / 'done.json', {'ok': proc.returncode == 0, 'seconds': time.monotonic() - start})
                    finally:
                        if proc.poll() is None:
                            os.killpg(proc.pid, signal.SIGTERM)
                            try: proc.wait(timeout=15)
                            except subprocess.TimeoutExpired:
                                os.killpg(proc.pid, signal.SIGKILL)
                                proc.wait()
            except Exception as exc:
                write(folder / 'done.json', {'ok': False, 'error': type(exc).__name__})
        time.sleep(2)

if __name__ == '__main__':
    main()
