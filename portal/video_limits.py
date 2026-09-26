"""Output duration is independent of the engine's bounded segment length."""
import math

FPS = 24
SEGMENT_FRAMES = 56
MAX_DURATION = 1800
from .storage_quota import ACCOUNT_BYTES
RESULT_BYTES = 2 * 1024**3
DISK_RESERVE = 5 * 1024**3

def segment_count(seconds):
    return math.ceil(seconds * FPS / SEGMENT_FRAMES)

# Native engine dimensions use a 32-pixel grid; export only crops, never upscales.
RESOLUTIONS = {
    '288p': ((512, 288), (512, 288)),  # Existing jobs keep their original dimensions.
    '480p': ((864, 480), (854, 480)),
    '720p': ((1280, 736), (1280, 720)),
    '1080p': ((1920, 1088), (1920, 1080)),
}

def dimensions(resolution, orientation, native=False):
    width, height = RESOLUTIONS[resolution][0 if native else 1]
    return (height, width) if orientation == 'portrait' else (width, height)
