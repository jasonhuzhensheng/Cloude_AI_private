#!/bin/bash
set -euo pipefail
cd /workspace/private-ai/web
export PORTAL_DATA=/workspace/private-ai/web-data
export ALLOWED_HOSTS="127.0.0.1,localhost,${RUNPOD_POD_ID:?Missing Runpod Pod ID}-8090.proxy.runpod.net,${RUNPOD_POD_ID}-8888.proxy.runpod.net"
export MODEL_URL=http://127.0.0.1:8080/proxy/absolute/8080/v1/chat/completions
umask 077
mkdir -p "$PORTAL_DATA" /workspace/private-ai/logs
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput > /workspace/private-ai/logs/web-static.log
PORTAL_SETUP=0 .venv/bin/gunicorn portal.wsgi:application --bind 0.0.0.0:8090 --workers 1 --threads 4 --timeout 130 --daemon --pid /tmp/private-ai-web.pid --error-logfile /workspace/private-ai/logs/web.log
PORTAL_SETUP=1 PORTAL_PREFIX=/proxy/8091 .venv/bin/gunicorn portal.wsgi:application --bind 127.0.0.1:8091 --workers 1 --threads 4 --timeout 130 --daemon --pid /tmp/private-ai-setup.pid --error-logfile /workspace/private-ai/logs/web-setup.log

PORTAL_SETUP=0 nohup .venv/bin/python manage.py image_worker >> /workspace/private-ai/logs/image-worker.log 2>&1 &
PORTAL_SETUP=0 nohup .venv/bin/python manage.py video_worker >> /workspace/private-ai/logs/video-worker.log 2>&1 &
PORTAL_SETUP=0 nohup .venv/bin/python manage.py upscale_worker >> /workspace/private-ai/logs/upscale-worker.log 2>&1 &
nohup bash /workspace/private-ai/web/ensure-office.sh >> /workspace/private-ai/logs/office-install.log 2>&1 &
if [ "${PORTAL_IDLE_SHUTDOWN:-0}" = 1 ] && [ "${PORTAL_WAKE_GATEWAY_READY:-0}" = 1 ]; then
    PORTAL_SETUP=0 nohup .venv/bin/python manage.py idle_watchdog >> /workspace/private-ai/logs/idle-watchdog.log 2>&1 &
fi
PORTAL_SETUP=0 nohup .venv/bin/python manage.py photo_worker >> /workspace/private-ai/logs/photo-worker.log 2>&1 &

if [ -f /workspace/private-ai/photo-gpu.json ] && [ -f /root/.config/private-ai/runpod-photo.key ]; then
    PORTAL_SETUP=0 nohup .venv/bin/python manage.py photo_gpu_controller >> /workspace/private-ai/logs/photo-gpu-controller.log 2>&1 &
fi
