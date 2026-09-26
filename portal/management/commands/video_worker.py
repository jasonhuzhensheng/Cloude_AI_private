from portal.storage_quota import ACCOUNT_BYTES, account_used
"""Resumable H3 segments and private long-form media outputs."""
import fcntl
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from PIL import Image
from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Sum
from portal.gpu import GPULock
from portal.media_lock import media_slot
from portal.models import VideoJob, VideoUpscaleJob
from portal.video_timeline import segment_prompt
from portal.video_limits import FPS, SEGMENT_FRAMES, ACCOUNT_BYTES, RESULT_BYTES, DISK_RESERVE, segment_count, dimensions
from portal.management.commands.image_worker import stop_chat, restore_chat

ROOT = Path(settings.IMAGE_EDIT_ROOT)

class Paused(Exception):
    pass

def stage(job, text):
    job.stage = text
    job.save(update_fields=['stage', 'updated'])

def command(job, output, source=None, index=0, continuation=False):
    width, height = dimensions(getattr(job, 'resolution', '288p'), getattr(job, 'orientation', 'landscape'), native=True)
    models = ROOT / 'h3-models'
    mode = 'fl2va' if continuation else job.mode
    encoder_backend = 'te=cuda0' if (ROOT / 'H3_GPU_ENCODER_READY').is_file() else 'te=cpu'
    args = [str(ROOT / 'image-runtime/build/bin/sd-cli'), '-M', 'vid_gen',
            '--diffusion-model', str(models / f'minimax_h3_{mode}_pruned-Q4_K.gguf'),
            '--vae', str(models / 'minimax_h3_video_vae_fp16.safetensors'),
            '--audio-vae', str(models / 'minimax_h3_audio_vae_fp32.safetensors'),
            '--llm', str(models / 'qwen3vl_32b_minimax_h3-Q4_K_M.gguf'),
            '--backend', encoder_backend, '--cfg-scale', '1.0', '-W', str(width), '-H', str(height),
            '--diffusion-fa', '--offload-to-cpu', '--rng', 'cpu', '--fps', str(FPS),
            '--video-frames', str(SEGMENT_FRAMES), '--steps', '20', '--seed', str(42 + index), '-p', segment_prompt(job, index), '-o', str(output)]
    if source:
        args += ['--ref-image' if mode == 'ref2va' else '--init-img', str(source)]
    return args

def check_pause(job):
    if VideoJob.objects.filter(pk=job.pk, pause_requested=True).exists():
        raise Paused()

def run_process(job, args, log, timeout, env=None):
    """Cancellation also stops encoders and merge, not just the model."""
    check_pause(job)
    process = subprocess.Popen(args, stdout=log, stderr=log, env=env, start_new_session=True)
    started = time.monotonic()
    try:
        while process.poll() is None:
            check_pause(job)
            if time.monotonic() - started > timeout:
                raise RuntimeError('This processing step timed out; completed segments are saved')
            time.sleep(2)
        if process.returncode:
            raise RuntimeError('Local media processing failed; completed segments are saved')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()

def probe(path):
    data = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=30)
    info = json.loads(data.stdout)
    if not {'video', 'audio'}.issubset({s.get('codec_type') for s in info.get('streams', [])}):
        raise RuntimeError('Generated media is missing video or audio')
    return float(info['format']['duration'])

def valid_segment(path):
    if not path.exists():
        return False
    try:
        return abs(probe(path) - SEGMENT_FRAMES / FPS) < 0.12
    except (ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        return False

def merge_command(manifest, output, seconds):
    return ['ffmpeg', '-y', '-v', 'error', '-f', 'concat', '-safe', '1', '-i', str(manifest),
            '-t', str(seconds), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-r', str(FPS),
            '-c:a', 'aac', '-af', 'aresample=async=1:first_pts=0', '-movflags', '+faststart', str(output)]

def run_job(job):
    directory = settings.DATA / 'video-checkpoints' / str(job.owner_id) / str(job.pk)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    total = segment_count(job.duration_seconds)
    parent = job.continuation_of
    if parent and (parent.owner_id != job.owner_id or parent.status != 'succeeded' or not parent.video):
        raise RuntimeError('Continuation source is unavailable')
    final_seconds = job.duration_seconds + job.prefix_seconds
    source = None
    if job.source_id:
        source = directory / 'reference.png'
        with job.source.file.open('rb') as handle, Image.open(handle) as im:
            im = im.convert('RGB')
            im.thumbnail((768, 768))
            im.save(source)
    if job.mode == 'ref2va' and source is None:
        raise RuntimeError('Reference image is no longer available')
    if shutil.disk_usage(directory).free < DISK_RESERVE:
        raise RuntimeError('Not enough free disk space; ask the administrator to free space')
    env = os.environ.copy()
    env.update(CUDA_MPS_PIPE_DIRECTORY='/tmp/private-ai-mps', CUDA_MPS_ACTIVE_THREAD_PERCENTAGE='90')
    lock = GPULock()
    with media_slot():
        stage(job, 'Waiting for the GPU')
        lock.acquire()
        try:
            check_pause(job)
            stop_chat()
            # A terminated CUDA client can leave the MPS server unusable.
            # Media lock excludes other media clients; never reset resident chat.
            if (ROOT / 'CHAT_DISABLED').exists():
                subprocess.run(['nvidia-cuda-mps-control'], input='quit\n', text=True,
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            subprocess.run(['bash', str(ROOT / 'start-mps.sh')], check=True, timeout=15)
            log_path = ROOT / 'logs' / f'h3-job-{job.pk}.log'
            with log_path.open('ab') as log:
                if parent:
                    stage(job, 'Preparing the last frame of the source video')
                    source = directory / 'reference.png'
                    # Decode only the final second to bound reverse-filter memory.
                    run_process(job, ['ffmpeg','-y','-v','error','-sseof','-1','-i',parent.video.path,
                                     '-vf','reverse','-frames:v','1',str(source)],log,120)
                    with Image.open(source) as frame:
                        frame.verify()
                for index in range(total):
                    check_pause(job)
                    segment = directory / f'segment-{index:04d}.mp4'
                    if not valid_segment(segment):
                        if shutil.disk_usage(directory).free < DISK_RESERVE:
                            raise RuntimeError('Not enough free disk space; completed segments are saved')
                        segment_started = time.monotonic()
                        stage(job, f'Generating segment {index + 1} of {total}; {index} completed')
                        raw, pending = directory / 'segment.avi', directory / 'pending.mp4'
                        raw.unlink(missing_ok=True)
                        segment_source = source
                        if index:
                            stage(job, f'Preparing continuation for segment {index + 1} of {total}')
                            previous = directory / f'segment-{index - 1:04d}.mp4'
                            segment_source = directory / 'continuation.png'
                            segment_source.unlink(missing_ok=True)
                            # Decode the actual last displayed frame, including on resume.
                            run_process(job, ['ffmpeg', '-y', '-v', 'error', '-i', str(previous),
                                             '-vf', 'reverse', '-frames:v', '1', str(segment_source)], log, 60)
                            with Image.open(segment_source) as frame:
                                frame.verify()
                        stage(job, f'Generating segment {index + 1} of {total}; {index} completed')
                        run_process(job, command(job, raw, segment_source, index, continuation=bool(index)), log, 1800, env)
                        stage(job, f'Saving segment {index + 1} of {total}')
                        run_process(job, ['ffmpeg', '-y', '-v', 'error', '-i', str(raw), '-vf', 'crop=%s:%s' % dimensions(job.resolution, job.orientation), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(pending)], log, 120)
                        if not valid_segment(pending):
                            raise RuntimeError('Segment verification failed; completed segments are saved')
                        pending.replace(segment)
                        job.segment_seconds += time.monotonic() - segment_started
                        job.timed_segments += 1
                        job.save(update_fields=['segment_seconds', 'timed_segments', 'updated'])
                        raw.unlink(missing_ok=True)
                    job.completed_segments = index + 1
                    job.save(update_fields=['completed_segments', 'updated'])
                    if sum(p.stat().st_size for p in directory.glob('segment-*.mp4')) > RESULT_BYTES:
                        raise RuntimeError('Segments exceed the 2 GiB job storage limit')
                check_pause(job)
                stage(job, f'Merging {total} segments into {job.duration_seconds} seconds')
                manifest = directory / 'segments.txt'
                manifest.write_text(''.join(f"file 'segment-{i:04d}.mp4'\n" for i in range(total)))
                output, audio = directory / 'output.mp4', directory / 'audio.wav'
                run_process(job, merge_command(manifest, output, job.duration_seconds), log, 1800)
                if abs(probe(output) - job.duration_seconds) > 0.15:
                    raise RuntimeError('Final duration verification failed; completed segments are saved')
                if parent:
                    stage(job, 'Combining the original video and new continuation')
                    extension = directory / 'extension.mp4'
                    output.replace(extension)
                    prefix = directory / 'prefix.mp4'
                    # Same filesystem hard link preserves the original without copying large files.
                    if not prefix.exists():
                        os.link(parent.video.path, prefix)
                    combined = directory / 'combined.txt'
                    combined.write_text("file 'prefix.mp4'\nfile 'extension.mp4'\n")
                    run_process(job, merge_command(combined, output, final_seconds), log, 1800)
                    if abs(probe(output) - final_seconds) > 0.15:
                        raise RuntimeError('Combined duration verification failed; segments are saved')
                stage(job, 'Saving video and audio downloads')
                run_process(job, ['ffmpeg', '-y', '-v', 'error', '-i', str(output), '-vn', '-c:a', 'pcm_s16le', str(audio)], log, 300)
                size = output.stat().st_size + audio.stat().st_size
                used =account_used(job.owner, exclude_video=job.pk)

                if size > RESULT_BYTES or used + size > ACCOUNT_BYTES:
                    raise RuntimeError('Generated media exceeds storage quota; completed segments are saved')
                check_pause(job)
                with output.open('rb') as handle:
                    job.video.save('video.mp4', File(handle), save=False)
                with audio.open('rb') as handle:
                    job.audio.save('audio.wav', File(handle), save=False)
                job.size = size
                job.status, job.stage, job.error = 'succeeded', 'Your video and audio are ready', ''
                job.save(update_fields=['video', 'audio', 'size', 'status', 'stage', 'error', 'updated'])
                # Only our derived checkpoints; originals and final downloads stay intact.
                shutil.rmtree(directory)
        finally:
            try:
                restore_chat()
            finally:
                lock.release()

class Command(BaseCommand):
    help = 'Generate resumable H3 segments and merge private video/audio outputs.'

    def handle(self, *args, **options):
        settings.DATA.mkdir(parents=True, exist_ok=True)
        with (settings.DATA / 'video-worker.lock').open('a') as singleton:
            try:
                fcntl.flock(singleton, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            def stop(*_):
                raise KeyboardInterrupt()
            signal.signal(signal.SIGTERM, stop)
            VideoJob.objects.filter(status='running').update(status='paused', stage='Interrupted; resume to continue saved segments')
            while True:
                close_old_connections()
                job = VideoJob.objects.filter(status='queued').order_by('id').first()
                if job is None:
                    time.sleep(2)
                    continue
                if not VideoJob.objects.filter(pk=job.pk, status='queued').update(status='running'):
                    continue
                job.refresh_from_db()
                try:
                    run_job(job)
                except BaseException as exc:
                    self.stderr.write(f'H3 job {job.pk}: {type(exc).__name__}: {exc}')
                    # A completed result remains successful even if chat restoration fails.
                    if VideoJob.objects.filter(pk=job.pk, status='succeeded').exists():
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            raise
                        continue
                    paused = isinstance(exc, (Paused, KeyboardInterrupt, SystemExit))
                    job.status = 'paused' if paused else 'failed'
                    job.stage = 'Paused; completed segments are saved' if paused else 'Generation did not complete'
                    job.error = '' if paused else 'Processing failed. Completed segments are saved. Resume to retry, or contact your administrator.'
                    job.save(update_fields=['status', 'stage', 'error', 'updated'])
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
