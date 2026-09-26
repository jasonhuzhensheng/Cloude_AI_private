"""One image or video engine at a time; independent of the chat lock."""
import fcntl
from contextlib import contextmanager
from django.conf import settings

@contextmanager
def media_slot():
    with (settings.DATA / 'media-engine.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
