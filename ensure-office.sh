#!/bin/bash
set -euo pipefail
# Runpod persists /workspace; restore system packages if a new container is created.
if ! command -v libreoffice >/dev/null; then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libreoffice-writer fonts-noto-cjk fonts-liberation
fi
