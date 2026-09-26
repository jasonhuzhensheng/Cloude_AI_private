**Deployment status:** Dedicated photo routing is enabled on replacement Pod rv5jtln62s3bix. Browser Photo28 verified automatic start, Qwen generation, saved PNG download, and automatic stop. Original failed photo Pod stays stopped. Video remains independent.

## Dedicated on-demand Photos GPU

`photo-gpu.json` under IMAGE_EDIT_ROOT controls the optional dedicated Photos GPU. The main server runs `photo_worker` plus `photo_gpu_controller`; the second Pod runs only `deployment/photo_gpu_executor.py`, with no public ports or database process. Requests and outputs use a shared-volume spool. Qwen, Z-Image and Photos upscaling use this path; legacy chat image edits remain on their original worker. Original uploads are preserved and the main server alone saves private Documents. Generation models are loaded per task.

Controller can start/stop only the allowlisted dedicated Pod, with name/volume/GPU-count checks and four original Pod IDs explicitly protected. It polls every10seconds, starts only for live coordinator demand, and stops after results are committed and the queue finishes. Expired coordinator demand also triggers stop. Credentials must be at `/root/.config/private-ai/runpod-photo.key`, mode0600; never on the shared network volume (this deployment's volume ignores chmod). Main-server stop/recreation may erase this credential. The main server must remain online for automatic shutdown; a main-host outage or provider API outage can delay stop and incur charges. Missing credentials do not silently fall back to local inference while dedicated mode remains enabled.

Current configured hourly ceiling $2.10; budget ledger starts conservatively at $130 remaining and subtracts observed account balance decreases, without increasing allowance on top-up. Below $10 reserve, photo GPU is stopped/blocked. This does not enforce a spending cap on the original video server or other account resources. Shared storage continues billing while GPUs are stopped. Resume depends on provider GPU availability. A safe rollback disables the config after active Photos jobs finish, verifies the dedicated pod EXITED, then leaves normal local Photos execution enabled.

Authenticated pages show separate Video GPU and Photo GPU cards. GPU utilization and VRAM use are distinct. The photo executor samples its own device; stale or stopped metrics are displayed as unavailable, never invented zeroes. This is independent of system RAM utilization.

# 私人 AI 网页端

当前账号保存文件总额度为100 GiB（页面标为100 GB），上传、图片、视频/音频及超分结果合计统计。单文件限制不变；这是应用额度，并不增加Runpod磁盘容量。此条取代下文历史说明中的100MB/10GiB账号额度。

纯用户名登录，英文界面、多语言聊天，文件上传，Django 管理后台。普通用户不能注册，账号由管理员创建。

## 使用

- 普通用户入口：https://ng5y0mjyosl248-8090.proxy.runpod.net/
- 后台入口：https://ng5y0mjyosl248-8090.proxy.runpod.net/admin/
- 首位管理员初始化：通过已登录的 Jupyter 访问 `/proxy/8091/setup/`。只允许初始化一次，正式公开入口不提供此功能。
- 管理员在「用户 → 增加用户」创建用户名和密码；普通用户不要勾选「工作人员状态」「超级用户状态」。取消「有效」即可禁用账号，用户详情中可重置密码。
- 支持 JPG/JPEG、PNG、WebP 图片，以及 PDF（文字版）、DOCX、XLSX、CSV、TXT、MD、JSON。每份 20MB，每个对话最多 5 份，每账号总计 100MB。较长文件按提问选取文字片段，界面明确提示，扫描件需要先 OCR。
- 回答采用流式显示，生成过程中逐段呈现；等待时显示计时，超时保留输入。当前单张显卡一次处理一个问答；忙碌时提示稍后重试。支持 JPG/JPEG、PNG、WebP 图片理解与图片中文字识别；不自动识别扫描版 PDF。图片最多 2400 万像素，上传后缩放到最长边 1280 像素并转成 JPEG，每个对话最多 2 张图片，支持私有缩略图预览。

## 数据与启动

程序：`/workspace/private-ai/web`；账号、聊天、上传文件和密钥：`/workspace/private-ai/web-data`。不要公开或发送该数据目录。
`start-services.sh` 在现有模型启动后启动网页端。公开端口 8090；管理员初始化进程只监听本机 8091，沿用 Jupyter 登录保护。
保留原 Jupyter 与 llama.cpp 页面作为管理入口，不向普通用户提供 Jupyter 登录链接或令牌。
停止 GPU 后网页也会暂时不可用，磁盘仍计费。启动后自动恢复。

## 验证

`python manage.py test portal` 测试账号隔离、CSRF、文件解析、聊天失败处理、禁用账号、登录限流、初始化仅一次等。
生产中以 `requirements.lock` 安装已测试的依赖版本；单工作进程、4 个线程。

## PDF editing

Upload a PDF in chat, then select **Edit PDF** next to the attachment. Select the source page and describe the requested changes, or open **Exact edits** for text replacement, text-form filling, rotation, or page deletion. The model produces a restricted JSON operation plan; it cannot execute arbitrary code. Every successful edit creates a separate private Document in the same conversation. The original is never overwritten. Preview pages are authenticated PNGs, and the result is downloaded as an attachment.

Text replacement needs searchable, horizontal, single-line text; replacement font appearance may differ and long replacements are rejected. Digitally signed PDFs are rejected. Form editing supports existing text fields with Latin values, not signatures or checkboxes. Scanned PDFs support page operations but not text replacement. PDFs are limited to 300 pages, 20 MB per file, and 12 operations per request. Form field values are verified in both widget values and the canonical AcroForm field tree before saving.

## Local image editing

Upload an image in chat and select **Edit image**. Enter an instruction in English or Chinese, then select **Create edited image**. Recent edits show progress, a private preview, a download link, and an option to edit the result again. The source is preserved. Image recognition continues to use the chat vision model; image changes use Qwen-Image-Edit-2511 Q4_K_M through stable-diffusion.cpp, entirely on the existing Runpod server.

By default the worker pauses the chat model, performs one image edit, then restores chat. Chat and AI PDF requests report busy in that serial mode. With the concurrent GPU marker enabled (see below), chat remains resident and available during image edits. Each user may have one active edit, with up to four queued/running globally. Output is resized to a longest edge of 768 pixels (multiples of 64), uses 20 steps, and may take several minutes. Generated images count toward the existing account storage quota. AI editing can alter unintended details.

Persistent runtime and models are under `/workspace/private-ai/image-runtime` and `/workspace/private-ai/image-models`. The model manifest records pinned source revisions and verified SHA256 checksums. `/workspace/private-ai/IMAGE_EDIT_READY` enables submissions after installation. `start-services.sh` starts the singleton `manage.py image_worker`; logs are under `/workspace/private-ai/logs/image-worker.log` and `image-job-<id>.log`. A shared file lock serializes chat/document GPU jobs and, in default serial mode, image jobs. Normal worker interruption cleans up the image process and restores chat when serial mode stopped it; interrupted jobs are marked failed on worker restart.

## Local H3 video and audio

The **Video & audio** page supports requested durations of 1–1,800 seconds. It generates linked 56-frame segments at 24 fps, the selected native resolution and 20 steps, varies the seed, concatenates the segments, and trims to the requested duration. New jobs offer 480p (854×480), 720p (1280×720), or 1080p (1920×1080), with width/height swapped for portrait. Native model canvases are 864×480, 1280×736, and 1920×1088 respectively, then center-cropped without upscaling. Legacy 288p jobs retain their original canvas. Timing history is separated by resolution. The selected resolution and orientation are saved per job and reused for every segment and resume. Existing jobs default to landscape. This is a montage, not a continuous 30-minute model generation: the first segment uses the selected mode/reference; subsequent segments use FL2VA with the actual last frame of the previous saved segment. Resume also extracts that saved last frame. The prompt remains the same; cuts or changes in identity/motion/audio may still be visible. FL2VA accepts text with an optional first image; Ref2VA accepts an existing account-owned image. Upload reference images through chat first. Reference video/audio and last-frame controls are not exposed. MP4 includes H.264/AAC; WAV is also downloadable.

A 30-minute output requires 772 segments. Estimates are rough extrapolations, not measured long-run performance or a price guarantee. The confirmed acceptance test is a 6-second, three-segment job; do not claim that a 30-minute GPU run was tested. Task cards expose **Stop task (keep progress)** above the collapsed prompt, followed by **Resume saved segments** when stopped. The worker checks stop requests every two seconds and terminates the active engine/encoder process group; saved segments survive. Stopping does not stop provider billing. Estimates show a range for total generation and remaining time, excluding the queue. They use measured successful segment durations for this task, or this owner’s recent matching mode/orientation, with a fallback before measurements exist. Interrupted attempts and paused wall time are excluded from measured segment speed. No long generation is scheduled by deployment.

`manage.py video_worker` holds the shared media and chat locks, stops chat before processing, and respects `CHAT_DISABLED` on restore. Checkpoints live privately under `PORTAL_DATA/video-checkpoints/<owner>/<job>/`. Pause interrupts the current processing step and keeps completed segments. After worker restart an interrupted job becomes paused; the owner can resume it. Completed valid segments are reused. Final output is probed for audio/video and requested duration before publication; successful jobs remove their temporary checkpoints. Original uploads and final downloads remain intact. Per job: 2 GiB final output limit, 30-minute timeout per generation/merge step; account: 10 GiB generated output; minimum free disk reserve: 5 GiB. Failed/paused checkpoints remain for recovery. Logs: `logs/h3-job-<id>.log`. No external media API.

## Word, Excel and format conversion

DOCX and XLSX attachments have **Edit Word / Convert** and **Edit Excel** links. Natural-language edits are converted by the local chat model to a bounded operation plan; no generated code is executed. Exact edits support Word text replacement and typed Excel cell values/formulas. Word paragraph editing preserves the OOXML package and unaffected runs. Excel editing preserves ordinary cell styles and formulas through openpyxl; advanced workbook features may differ. Formula results recalculate when opened in Excel. Legacy DOC/XLS and macro-enabled formats are not supported.

PDF attachments also have **PDF to Word**. DOCX can convert to PDF with local LibreOffice; PDF to DOCX uses pdf2docx, up to 30 pages, with an 80-second subprocess timeout. Scanned pages remain images; there is no OCR in this conversion. Complex layouts, fonts and pagination may change. All results remain owner-protected, use existing file quotas, and are saved separately from the original. Word conversion rejects macros, external resources, embedded objects and active linked fields. Server system dependencies: `libreoffice-writer fonts-noto-cjk fonts-liberation`; Python additions are pinned in `requirements-office.lock`.

## Edit files directly in chat

Uploaded Word, Excel, PDF, images and text files can be edited by a chat instruction. Explicit file actions are routed to the same owner-checked tools as the dedicated editors. PDF/Word conversion is also available by chat. The **File to edit** selector disambiguates multiple uploads; otherwise the router chooses the latest relevant version or asks for clarification. Each request processes one file. TXT, MD, CSV and JSON support bounded text replacements (up to 30,000 source characters); JSON and CSV outputs are parsed before saving.

Result IDs and image job IDs are stored as message metadata. The chat renders authenticated download links and image progress/previews; reopening the conversation restores completed results. The model never supplies a raw download URL or executable edit code. Image jobs retain the existing queue and shared-GPU limits. The original file is preserved. Unsupported formats and unsupported edits are explicitly rejected or clarified.

## Long chat reports

Chat responses allow up to 4,096 output tokens, with a 600-second streaming deadline and 60-second upstream idle timeout. A length-limited answer is saved with an incomplete marker and a Continue response button; it is not presented as a complete report. The button fills a follow-up request for review before Send. Existing history also offers continuation. Recent history retains the end of long responses within the shared input budget. Uploaded document analysis still uses excerpts, not guaranteed full-document coverage.

## 16K chat context budget

16,384 tokens total: 4,096 output, 1,024 template overhead, 11,264 input. Text is conservatively counted as UTF-8 bytes, not an exact tokenizer count. Each normalized image reserves another 4,096 input units. The user message and base instructions are preserved; history and file excerpts share remaining space. With files, history uses at most one third of the remainder. Oversized messages return an error rather than being silently shortened. Original files and saved history are unchanged. Upload/storage quotas are separate from inference context. Revisit image reservations if the model or image normalization changes.

## Optional concurrent GPU mode

`/workspace/private-ai/CONCURRENT_GPU_READY` enables concurrent image/chat operation. `start-mps.sh` starts a private MPS daemon; image subprocesses set active thread percentage to 90. Chat uses 100 so it can use idle capacity. This is a compute provisioning cap, not a GPU utilization or VRAM percentage guarantee. The image worker no longer holds the chat GPU lock or stops the chat model in this mode. Remove the marker to return subsequent jobs to serial scheduling. The existing single image worker and serialized chat requests remain. The server start-chat script is backed up before enabling MPS.

## Multiple chat models

The active 96 GB deployment (`liokr6kbd9vlwk`) now uses 44 GPU layers for Flash Next, increased from 32. The large `per_layer_token_embd.weight` tensor remains on CPU; context remains 16K. This preserves headroom for the existing image engine. The original preset is backed up on the server as `models.before-44-<timestamp>.ini`. Use the measured deployment status below the upgrade notes for validation details; do not assume an entire 111 GB model fits in VRAM.

The Chat model selector routes each request to a server-approved model alias. A successful response saves the model choice on the conversation and the model name on the answer, so reopening history preserves both. Unknown or unavailable model IDs are rejected before inference. The existing 16K input/output budget remains unchanged. File-editing tools retain their existing dedicated models.

Qwen3.8 Flash Next becomes selectable only when `/workspace/private-ai/FLASH_NEXT_READY` exists after installation and validation. The model backend must serve both aliases (`Qwen3.8-27B-Uncensored`, `Qwen3.8-Flash-Next`). A llama.cpp router with one loaded chat model at a time is intended for the 96GB deployment; switching may require loading weights. Flash Next allows up to 600 seconds of upstream idle time during loading, within the existing 600-second stream deadline.

## Idle shutdown (prepared; disabled until wake gateway is verified)

`PORTAL_IDLE_SHUTDOWN=1` enables account-authenticated activity tracking. Real page navigation, successful requests, and a CSRF-protected heartbeat after keyboard, pointer, paste or scroll activity update the timer. Heartbeats are limited to one per minute and only run on visible pages after interaction. GPU/status polling and anonymous traffic do not keep a Pod awake. An untouched open tab alone does not count as use.

`manage.py idle_watchdog` checks every 15 seconds and requests **Stop**, never Terminate, after 20 minutes without activity. In-flight web requests, streaming responses/downloads, and queued/running image jobs prevent shutdown. Cross-process file locks prevent new work from being admitted after the stop decision. Work completion starts a fresh idle period. Failed/uncertain provider calls keep admissions closed until provider status is reconciled; do not promise a billing hard cap from this worker.

Live shutdown also requires `PORTAL_WAKE_GATEWAY_READY=1`, the current `RUNPOD_POD_ID`, and a private 0600 token file at `RUNPOD_STOP_TOKEN_FILE` (default `/workspace/private-ai/runpod-stop.key`). Never commit or send that token. `start-services.sh` only launches the watchdog with both enablement flags. `manage.py idle_watchdog --dry-run --once` reports the decision without contacting Runpod. The persistent activity state is refreshed when a fresh container boot is detected through an ephemeral `/tmp` marker; restarting only the watchdog does not extend idle time.

**Not deployed or enabled yet.** A stopped GPU Pod cannot serve its own wake-up page. A separate always-available entrance must authenticate/authorize wake requests, hold provider credentials on the server, debounce concurrent starts, enforce the existing $100 test budget, and show startup/capacity errors without losing drafts. The user has replaced the fixed 12:00–22:00 schedule with on-demand use; no fixed-hour automation was created. The earlier daily maximum and total budget must be handled by the external power controller before activation. Network storage remains billable while the GPU is stopped. Private-data migration approval and the choice of external entrance are still pending.

Completed video generations offer **Delete generation** with a confirmation. This permanently removes the owned job and its generated MP4/WAV, releasing its video quota; uploaded source documents are preserved. Queued, paused and failed jobs can also be deleted. Running jobs receive a stop-and-delete request; task-list polling cleans up after the worker stops. Keep the page open until the card disappears, or return later to complete cleanup. Original reference images remain intact.

The video studio supports direct JPG/PNG/WebP reference uploads (20 MB, 24 megapixels, static images). Uploaded originals are preserved as owner-protected Documents in a Video reference images conversation, count toward the existing 100 MB file quota, and are automatically selected with an authenticated preview. Existing references can also be previewed in the selector.

## H3 GPU text encoding

`H3_GPU_ENCODER_READY` under IMAGE_EDIT_ROOT selects `te=cuda0`; removing it returns subsequent segments to CPU text encoding. CPU weight offload remains enabled. A controlled 480p FL2VA reference-image benchmark (56 frames, 20 steps) took 146.03s on CPU encoding versus 111.52s with GPU encoding; sampled peak GPU memory rose from 13,829 to 18,233 MiB. Cross-segment persistent models/condition caching are not implemented. These short-clip measurements do not guarantee equivalent speedups for other prompts, resolutions or Ref2VA.

## Video timeline editor

Optional timeline rows store start/end seconds and action/sound text per job. Global prompt supplies shared scene/subjects. Maximum 100 non-overlapping rows, 500 characters per action, 8,000 combined; times must fit requested output duration. Worker selects overlapping actions for each 56-frame/24fps segment, clips their local times and marks cross-boundary actions as continuations. Gaps request natural idle motion. Empty timelines preserve legacy prompts exactly. Timing and motion remain model instructions, not frame-accurate guarantees. Migration 0012 is additive; deploy views/template and restart idle video worker together. Never restart an active user job.

Auto-fill timeline: enter ordered actions in Action prompt and click Auto-fill timeline. Explicit ranges such as `0–3s: wave; 3–6s: turn` are preserved. Otherwise Chinese/English sequence markers, sentence endings and newlines split actions into equal durations. This is a rule-based editable draft, not semantic AI scheduling, and does not start GPU work. Shared scene text stays in the main prompt. Existing timeline replacement asks confirmation and failed drafts keep existing rows.

## AI upscale in video results

Completed results offer SeedVR2 7B FP16 upscaling to a higher short-side resolution:720p,1080p or2160p(4K). Portrait/landscape are retained. The original is preserved; enhanced copies have private preview/download and independent delete controls. Upscaling shares existing GPU locks, is cancellable, and preserves the source audio. Progress is indeterminate, not a fabricated percentage. Buttons stay disabled until server inference verification sets SEEDVR2_READY. Model choice, dependency pin and operating limits are in deployment/seedvr2.md.

## Video generation queue

Add to video queue remains available while another video is running, until the existing server-wide limit of4queued/running video tasks is reached. Each account may now have multiple queued jobs, but at most1running job. Existing singleton video worker executes by ascending jobID (resumed old jobs retain their original ordering). Queue position counts video tasks only; image/upscale work may also delay GPU availability. Admission/control checks share a separate cross-process lock; they do not take the GPU processing lock. Paused tasks do not occupy capacity.

## Administrator credit status

Video studio shows account-wide Runpod prepaid credit only to staff; ordinary authenticated GPU status responses omit billing entirely. Read-only GraphQL query uses a private 0600 token file at /workspace/private-ai/runpod-billing.key (override RUNPOD_BILLING_TOKEN_FILE). No token enters HTML or logs. Grant only the provider access required to read account balance. Missing/invalid credentials show unavailable, never an estimated balance.

With the page open, provider sampling is throttled across web workers to five minutes using a persistent locked cache under DATA/billing. Published snapshot changes after a cumulative $10 decrease, a detected increase/top-up, or any sample below $10. Both snapshot and last successful check timestamps are shown; failures retain the last snapshot with an unavailable warning. This does not enforce a project budget or stop billing.

## Continue a completed video

Completed results expose **Continue generation** with a new prompt and additional seconds. A new queued VideoJob uses the source result’s actual last decoded frame for FL2VA, inherits its resolution/orientation, and appends the newly generated section to the entire source result. Downloads contain the combined video and combined WAV; originals remain intact. Repeated continuations use the combined parent. The 1,800-second cap applies to the combined result; generation estimates and segment counters count only new segments. Existing queue, stop/resume, storage and owner checks apply. Source deletion is blocked while dependent continuation records exist.

Migration0015 adds continuation_of (PROTECT) and prefix_seconds. H3_CONTINUE_READY enables submissions only after the updated video worker is loaded and media validation passes. Reload only an idle video worker; never interrupt active user tasks. Last-frame decoding reverses only the final second to avoid loading a long video into memory. Joining re-encodes the combined media and preserves the original file separately; motion, identity and audio continuity are not guaranteed.

## Qwen-Image-2.1 photos

The new **Photos** page supports local text-to-image generation and editing an uploaded JPG/PNG/WebP. It provides a source preview, format choice, live denoising steps, stop control, private PNG preview/download and re-edit link. The original source remains intact. New runtime and queue are separate from the older Qwen image editor, with a shared GPU lock across video/upscale/photo engines. See deployment/qwen21.md for installation pins, readiness and test status.

## Upscale estimates and task controls

Each upscale task has separate **Stop upscale** and **Delete upscale task** controls. Deleting a running task sets persistent delete_requested and cancel_requested; the existing worker stops its process, then owner task-list polling deletes only the derived result/record after terminal status. Queued tasks are cancelled atomically before deletion. Keep the page open until removal, or return later for cleanup. Original source video stays intact.

Estimates are broad initial ranges based on measured1-second720p/1080p/4K clips (134.63/209.6/592.28s), scaled by source output duration with0.75–2.5 bounds for long-clip uncertainty. On active jobs created within24hours, the first timestamp in the server log supplies processing elapsed time, excluding earlier queue time; unavailable clocks show the full remaining range after GPU availability. Exceeding the upper estimate shows remaining-time unavailable, not a zero countdown. This is not measured long-video throughput, a guarantee or a percentage-complete calculation. Migration0017 adds deletion intent and needs no active GPU worker restart.

Upscale ETA recalibrates after the first completed SeedVR2 chunk using this task’s observed processing time and overlapping frame count. It displays chunk completion and an estimated range including export margin. Queue time is excluded; stale progress hides remaining time. Future chunk sizes are conservative until observed.

Completed-video continuation supports the same optional timeline editor and auto-draft as new generation. Timeline seconds are relative to the new section, not the original video. Each form has independent rows; continuation submits validated timeline with the parent ID.

Video tasks expose Prompt check and an owner-protected JSON report. Recognized structured safety/moderation blocking messages are flagged for review; absence is not proof of instruction compliance. Reports include reconstructed segment prompts, explicitly not historical input capture. Large logs scan only the first/last MiB and disclose this. Raw logs and server paths are not exposed.

Scene direction uses eight optional guided categories in new and continuation video forms. Filled categories and additional instructions compile into one main prompt, with live preview and a shared 2,000-character limit. Timeline actions remain separate.

The website now shows Chinese / English together for business-page controls and guidance, with shared system-message translations and an admin label override. User-entered prompts, filenames, chat messages and document contents are not automatically translated. Unknown external errors retain their original wording.

Photos offer 10 native presets:512/768/1024/1536 square;768×1024 and1024×768;768×1344 and1344×768;1024×1536 and1536×1024. Results support SeedVR2 7B image upscale2×/4×, maximum4096 per edge and16MP,20MB result/100MBaccount quotas. Separate PNG, originals preserved. Uses PhotoJob format upscale2/upscale4 and existing shared media/GPU lock, queue and cancellation. PHOTO_UPSCALE_READY gates validated readiness.

## Photo prompt diagnostics

Each photo task shows a bilingual prompt check and an owner-only JSON report download. Recognized structured safety/moderation signals produce a possible-refusal warning; missing logs remain unknown. No signal does not prove instruction compliance. Reports contain saved request metadata and sanitized diagnostic events, never raw server logs. SeedVR2 upscales are marked not applicable because they do not consume text prompts. Large logs are sampled at the beginning/end with disclosure.

## Photo model selection

The Photos form offers Qwen-Image-2.1 for generation/editing and official Tongyi-MAI/Z-Image for text-to-image photographic portraits. Z-Image does not edit a source image; the API rejects that combination. Each PhotoJob stores the model (migration0018); old jobs default to Qwen. Models execute serially under the existing media/GPU locks, release GPU memory after each job, and preserve private outputs. Z-Image uses BF16, 40 steps, guidance4, offline inference with the existing isolated runtime. `ZIMAGE_READY` enables selection only after a pinned download, LFS SHA256 verification and a synthetic fashion-portrait smoke test. This is a practical model choice, not a proven best-model ranking. Installation: deployment/install_zimage.py accepts an official 40-character revision and preserves all user data.

## Native 2K photo presets

Photos now select dimensions from the chosen model's registry (17 presets per model). Both offer native2048×2048. Qwen uses its official 2K aspect sizes2400×1792,2528×1696,2752×1536 and transposes. Z-Image uses 32-aligned equivalents2368×1760,2496×1664,2720×1536 and transposes within2048² pixel budget. These are documented/recommended native presets, not an experimentally proven absolute architectural ceiling. Existing lower presets remain, model switching preserves the preset key, and the worker resolves it against the saved job model. No post-generation enlargement. Existing20MB/100MB account quotas remain. Sources: https://github.com/QwenLM/Qwen-Image-2.1 and https://huggingface.co/Tongyi-MAI/Z-Image .

Photos source and result previews open a keyboard-accessible modal on click/Enter/Space, with fit-to-window, actual-pixel scrolling, and an authenticated image link. Close with Escape, close button or backdrop. Original files are unchanged.

Image upscales have a separate queue/results panel with source links, result previews/downloads, explicit refresh and parent-image task links. Active photo tasks remain visible beyond the latest30 history items. Submission feedback appears in the panel; only accepted jobs get an ID.

Photo and image-upscale tasks expose Delete task & result with a confirmation. Completed/failed/queued tasks delete promptly; running tasks set cancel/delete intent and owner polling cleans up after worker exits. Keep the page open or return for cleanup. Original source files are preserved; references from photo/image-edit/video tasks block deletion. Generated Document and its file are removed, not the source upload.

Photo generation/editing accepts an optional negative prompt up to1000characters. It is stored per PhotoJob (migration0020), displayed in history and private diagnostics, and passed separately to the engine. Z-Image uses existing guidance4; Qwen uses trueCFG2 onlywhen nonempty (otherwise unchangedCFG1). This increases Qwen runtime/memory and cannot guarantee perfect anatomy. Upscales ignore text/negative prompts.

Photos include general-purpose iPhone wallpaper 2K (1248×2688 portrait) and MacBook wallpaper 2K (2560×1600 landscape 16:10) presets for both models. These are not exact native screen pixels for every Apple device; wallpaper cropping may be needed. The default remains 2048×2048.

Qwen photo requests support up to three total reference images (edit source first, then ordered additional references). Photos can be batch-uploaded or selected from owned uploads, previewed and removed from the draft without deleting originals. PhotoReference (migration0021) stores order and protects referenced Documents from deletion. Z-Image and upscales reject additional references. The worker checks ownership again and passes separate PIL images to QwenImage21Pipeline; this is multi-image conditioning, not a collage. Identity consistency is not guaranteed.

Completed photo results offer a quantity input (1–4) and Generate similar images. The result becomes a single Qwen image reference; each child uses a distinct persisted random seed (migration0022), generic variation instruction, inherited negative prompt, and native format (upscaled results map to a similar native aspect). Batches are admitted atomically only if all fit the four-slot photo queue and current 20MB-per-result storage check. Original results remain protected by source links. Each child has normal progress, cancellation, deletion and download controls.

Similar-image controls include low/medium/high variation, default medium. The API validates variation_degree and uses level-specific instruction sets saved in each child prompt. This changes prompt guidance, not numerical denoising strength; high variation may reduce identity consistency. Existing tasks are unchanged.

Upscale histories display newest tasks first on every refresh. Video cards are ordered by the latest creation time among the video and its upscales, so a new upscale of an older video promotes that card into the first20 results. Video child upscale nodes are reordered in place; photo upscale cards explicitly sort descending task ID. Sorting does not change GPU processing order.
