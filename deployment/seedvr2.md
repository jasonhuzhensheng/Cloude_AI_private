# SeedVR2 result upscaling

Selected quality-first local model: `seedvr2_ema_7b_fp16.safetensors`, not the extra-sharp variant. This is a practical choice, not a universal quality ranking. SeedVR2 can invent details/oversharpen and is not a faithful recovery of missing source information.

Sources checked 2026-09-20:
- https://github.com/ByteDance-Seed/SeedVR (authors; Apache-2.0; links to community implementation)
- https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler (standalone CLI, streaming chunks, VAE tiling)
- https://github.com/OpenImagingLab/FlashVSR (speed-focused alternative, not installed)
- https://docs.topazlabs.com/topaz-video/system-requirements (Linux unsupported; Topaz not installed)

Production runtime: `/workspace/private-ai/seedvr2-runtime`, pinned Git commit `4490bd1f482e026674543386bb2a4d176da245b9`. Isolated `.venv` with system-site-packages inherits existing torch 2.8.0+cu128; dependencies installed in that venv, not the Django or H3 environment. Keep the existing checkout pinned rather than updating silently. Model repository `numz/SeedVR2_comfyUI`, verified against this commit's registry:
- 7B FP16 SHA256 `7b8241aa957606ab6cfb66edabc96d43234f9819c5392b44d2492d9f0b0bbe4a`
- VAE SHA256 `20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1`

Worker: `manage.py upscale_worker`; common media-engine and GPU locks prevent concurrent model processing. No chat restoration. CLI uses batch5, streaming chunk65, overlap4, SDPA and tiled VAE; GPU offload to CPU between phases. Targets720/1080/2160 describe short edge and preserve aspect ratio. No interpolation-only fallback. Ready marker `SEEDVR2_READY` must only be set after server inference/output verification. Remove marker to stop new admission (does not stop running tasks).

Own completed videos only; private outputs. Model edge padding is center-cropped to standard16:9 or9:16 dimensions, encoded H264 CRF18, and original audio remuxed with stream copy. Duration/frame-count/resolution/audio checked before publication. Cancel signals process group; original is untouched. No automatic retry. Generated outputs limited2GiB, working files6GiB and six hours per processing command; account10GiB total with original video outputs. Display indeterminate progress, no fabricated percent or ETA. Delete enhanced copies first before deleting their source. A model/checkpoint error does not fall back to simple resizing.

Synthetic benchmark script: `/workspace/seedvr2-benchmark.py`; results `/workspace/private-ai/seedvr2-benchmark/results.json`. Uses24frames portrait480x854 with tone; tests720,1080,2160 via actual worker. User media not automatically regenerated. See upgrade-status.md for measured completion and limitations.

Validated and enabled:720x1280,1080x1920,2160x3840;24frames and1s retained. Raw pipeline times134.63s/209.6s/592.28s before final standard-crop correction; no longvideo performance claim. Synthetic job24 and upscale1–3 remain available for review.
