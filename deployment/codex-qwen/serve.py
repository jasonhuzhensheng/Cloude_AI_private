"""Run the existing Qwen model with an inference key and exclusive media GPU lock."""
import argparse
import fcntl
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--public', action='store_true', help='Bind to network after endpoint approval')
args = parser.parse_args()
root = Path('/workspace/private-ai')
key = Path('/root/.config/private-ai/codex-qwen.key')
if not key.is_file() or not key.read_text().strip():
    raise SystemExit('Set the dedicated inference key privately before starting.')
lock = (root / 'web-data/media-engine.lock').open('a')
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('GPU is busy with a media task; retry after it finishes.')
os.set_inheritable(lock.fileno(), True)
server = str(root / 'llama.cpp/build/bin/llama-server')
cmd = [server, '-m', str(root / 'flash-model/UD-Q4_K_XL/Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf'),
       '--alias', 'Qwen3.8-Flash-Next', '--host', '0.0.0.0' if args.public else '127.0.0.1',
       '--port', '8082', '-c', '16384', '-ngl', '44', '-np', '1', '-t', '12', '-tb', '12',
       '--flash-attn', 'on', '--override-tensor', 'per_layer_token_embd.weight=CPU',
       '--chat-template-kwargs', '{"enable_thinking":false}', '--api-key-file', str(key)]
os.execv(server, cmd)
