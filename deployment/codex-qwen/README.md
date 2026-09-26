# Codex / Qwen connection

Dedicated inference endpoint: https://liokr6kbd9vlwk-8090.proxy.runpod.net/codex/v1 . The existing HTTPS website port is reused to avoid Runpod's container reset when adding ports. The native llama.cpp backend listens only on 127.0.0.1:8082.

Installed local profile: ~/.codex/runpod-qwen.config.toml. Launch with `codex --profile runpod-qwen`, or double-click Start-Qwen-Codex.command. This starts a Codex CLI session. The current desktop conversation and global model selection are not changed. Do not pass --ignore-user-config: this CLI version also skips profile settings with that flag.

The profile reads a dedicated inference-only key from ~/.codex/runpod-qwen.key using the provider auth command. Both local files are mode 0600. The server key is /root/.config/private-ai/codex-qwen.key, mode 0600, outside the shared network volume. No management API credential is used. Container reset destroys the server key; restoring it from the local private copy is required before restarting this service.

Model: Qwen3.8-Flash-Next, context 16384, auto-compaction threshold12000, 44 GPU layers, CPU embedding tensor, 12 CPU threads, one inference slot. Large plugin/MCP tool catalogs are disabled only in this profile to fit context. The gateway forwards only core coding function tools (shell execution, process input, planning and image-view requests); the model itself accepts text only. Hosted web search and freeform custom tool bridging are not implemented. Codex can use its shell tool for file work. Existing Codex sandbox and approval settings remain intact.

User approved Codex GPU priority. serve.py holds web-data/media-engine.lock for the lifetime of the model process; video and main-GPU image work wait until it exits. It is currently manually started, not automatically restarted after a Pod reset. Website chat remains disabled; dedicated photo GPU remains stopped/disabled. No hardware upgrade and no paused 8K upload changes deployed.

Verification: unauthenticated public request401; authenticated model metadata200. Native Responses and streaming function call tested. Actual Codex client selected runpod_qwen/Qwen3.8-Flash-Next, called shell to read a synthetic check.txt and returned QWEN_CONNECTION_OK. First client prompt7378 tokens processed in110.3s (~66.9 tokens/s); generated29 tokens at26.7tokens/s. Cold start and long context remain slower than hosted services. Final compatibility profile validation is recorded in deployment/upgrade-status.md.

Gateway checks: authentication rejection, path allowlist, namespaced function flattening and streaming cleanup; three tests passed. Django configuration check passed locally and on server. Gateway preserves website session/CSRF behavior elsewhere; only this inference endpoint uses bearer auth.

Runtime compute remains $2.09/hour while the Pod is running, including idle time, plus storage. Existing $200 total budget remains. No automatic budget cutoff has been enabled.

2026-09-21 desktop follow-up: User requested a Codex-style graphical agent. Installed global default model/provider and16k settings in ~/.codex/config.toml, preserving other settings. Backup: ~/.codex/config.before-qwen-desktop-20260921-163655.toml. Local desktop source reads configured model_provider and models, but UI restart/new-task selection still requires user verification. Global default CLI verification passed: provider runpod_qwen/model Qwen3.8-Flash-Next, shell read check.txt and returned QWEN_CONNECTION_OK. GUI restart/picker verification remains pending. Earlier statements that global settings are unchanged are superseded by this entry.
