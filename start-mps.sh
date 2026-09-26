#!/bin/bash
set -euo pipefail
export CUDA_MPS_PIPE_DIRECTORY=/tmp/private-ai-mps
export CUDA_MPS_LOG_DIRECTORY=/tmp/private-ai-mps
mkdir -p "$CUDA_MPS_PIPE_DIRECTORY"
chmod 700 "$CUDA_MPS_PIPE_DIRECTORY"
exec 9>"$CUDA_MPS_PIPE_DIRECTORY/start.lock"
flock 9
if ! echo get_server_list | nvidia-cuda-mps-control >/dev/null 2>&1; then
    nvidia-cuda-mps-control -d 9>&-
fi
