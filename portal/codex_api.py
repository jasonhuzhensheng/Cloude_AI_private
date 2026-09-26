"""Dedicated, bearer-authenticated inference gateway; never uses website sessions."""
import hmac
import json
import queue
import threading
from pathlib import Path
import requests
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt

KEY_FILE = Path('/root/.config/private-ai/codex-qwen.key')

@csrf_exempt
def gateway(request, endpoint):
    try:
        key = KEY_FILE.read_text().strip()
    except OSError:
        return JsonResponse({'error': 'Codex inference is unavailable'}, status=503)
    if not key or not hmac.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + key):
        return JsonResponse({'error': 'Invalid inference key'}, status=401)
    allowed = {'responses': 'POST', 'responses/input_tokens': 'POST', 'models': 'GET'}
    if endpoint not in allowed or request.method != allowed[endpoint]:
        return JsonResponse({'error': 'Unsupported inference endpoint'}, status=404)
    if endpoint == 'models':
        metadata = Path(__file__).resolve().parent.parent / 'deployment/codex-qwen/model-metadata.json'
        return JsonResponse(json.loads(metadata.read_text()))
    try:
        if int(request.headers.get('Content-Length', '0')) > 2 * 1024 * 1024:
            return JsonResponse({'error': 'Request exceeds 2 MiB'}, status=413)
        body = request.body
        if len(body) > 2 * 1024 * 1024:
            return JsonResponse({'error': 'Request exceeds 2 MiB'}, status=413)
        payload = json.loads(body) if body else {}
        tools = []
        coding_tools = {'exec_command', 'write_stdin', 'shell', 'shell_command', 'update_plan', 'view_image'}
        for tool in payload.get('tools', []):
            if tool.get('type') == 'namespace':
                for child in tool.get('tools', []):
                    if child.get('type') == 'function' and child.get('name') in coding_tools:
                        tools.append(dict(child, name=tool['name'] + '.' + child['name']))
            elif tool.get('type') == 'function' and tool.get('name') in coding_tools:
                tools.append(tool)
        if 'tools' in payload:
            payload['tools'] = tools
        body = json.dumps(payload).encode()
        upstream = requests.request(request.method, 'http://127.0.0.1:8082/v1/' + endpoint,
            data=body, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
            stream=True, timeout=(10, 900), allow_redirects=False)
    except (requests.RequestException, ValueError):
        return JsonResponse({'error': 'Inference backend is not ready'}, status=503)
    def content():
        chunks = queue.Queue(maxsize=16)
        stopped = threading.Event()
        def put(value):
            while not stopped.is_set():
                try:
                    chunks.put(value, timeout=1)
                    return
                except queue.Full:
                    pass
        def read():
            try:
                for chunk in upstream.iter_content(chunk_size=1024):
                    if stopped.is_set():
                        break
                    put(chunk)
            except requests.RequestException:
                pass
            finally:
                put(None)
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        streaming = 'text/event-stream' in upstream.headers.get('Content-Type', '')
        try:
            if streaming:
                yield b': connected\n\n'
            while True:
                try:
                    chunk = chunks.get(timeout=15)
                except queue.Empty:
                    if streaming:
                        yield b': waiting\n\n'
                    continue
                if chunk is None:
                    break
                yield chunk
        finally:
            stopped.set()
            upstream.close()
    response = StreamingHttpResponse(content(), status=upstream.status_code,
        content_type=upstream.headers.get('Content-Type', 'application/json'))
    response['Cache-Control'] = 'no-store'
    response['X-Accel-Buffering'] = 'no'
    return response
