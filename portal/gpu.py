"""Serialize GPU use across web threads and the persistent image worker."""
import fcntl,threading
from django.conf import settings
class GPULock:
    def __init__(self): self.thread=threading.Lock(); self.file=None
    def acquire(self,blocking=True):
        if not self.thread.acquire(blocking=blocking): return False
        try:
            self.file=(settings.DATA/'gpu.lock').open('a')
            fcntl.flock(self.file,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            return True
        except BlockingIOError:
            self.file.close();self.file=None;self.thread.release();return False
        except BaseException:
            if self.file:self.file.close();self.file=None
            self.thread.release();raise
    def release(self):
        if self.file: fcntl.flock(self.file,fcntl.LOCK_UN);self.file.close();self.file=None
        self.thread.release()
    def locked(self): return self.thread.locked()
