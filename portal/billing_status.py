"""Read-only Runpod credit snapshots. Never expose credentials or provider errors."""
import fcntl
import json
import math
import os
import time
from pathlib import Path

import requests
from django.conf import settings


def fetch_balance():
    path = Path(os.environ.get('RUNPOD_BILLING_TOKEN_FILE', '/workspace/private-ai/runpod-billing.key'))
    if path.stat().st_mode & 0o077:
        raise ValueError('Private token file required')
    token = path.read_text().strip()
    if not token:
        raise ValueError('Token unavailable')
    response = requests.post(
        'https://api.runpod.io/graphql',
        headers={'Authorization': 'Bearer ' + token},
        json={'query': 'query { myself { clientBalance } }'},
        timeout=(3, 5), allow_redirects=False,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get('errors'):
        raise ValueError('Provider error')
    value = payload['data']['myself']['clientBalance']
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Invalid balance')
    return round(value, 2)


def snapshot():
    root = Path(settings.DATA) / 'billing'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / 'snapshot.json'
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            state = {}
        now = time.time()
        if now - state.get('attempted_at', 0) >= 300:
            state['attempted_at'] = now
            try:
                balance = fetch_balance()
                previous = state.get('last_observed')
                published = state.get('balance_usd')
                if published is None or published - balance >= 10 or balance < 10 or (previous is not None and balance > previous):
                    state.update(balance_usd=balance, published_at=now)
                state.update(last_observed=balance, checked_at=now, stale=False)
            except Exception:
                state['stale'] = True
            temporary = root / 'snapshot.tmp'
            temporary.write_text(json.dumps(state))
            temporary.chmod(0o600)
            temporary.replace(path)
    return {key: state[key] for key in ('balance_usd', 'published_at', 'checked_at', 'stale') if key in state}
