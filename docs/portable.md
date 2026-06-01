# Portable Folder Distribution Plan

## Goal

Transform Converse-AI into a self-contained portable folder that users can unzip and run without installing Python, conda, or system dependencies. Models download on-demand and stay separate from the app, enabling updates without re-downloading multi-GB model files.

## Background

**Why portable folder?**
- Dominant pattern in local AI apps (Oobabooga, AllTalk, Stable Diffusion WebUI, KoboldCpp)
- Zero installer complexity, no admin rights needed
- Trivial updates: replace app folder, preserve models/ and config
- Matches current start.ps1/start.sh workflow, just enhanced
- Lowest risk path to "works out of the box" experience

**Current state:**
- Requires user to install Python 3.11-3.13
- `install.ps1`/`install.sh` create `.venv` and run `pip install`
- 2.1 GB `.venv` after install
- Models scattered: Kokoro in `model-cache/`, faster-whisper in `~/.cache/huggingface/`, GGUFs user-provided
- llama.cpp server must be started manually by user

**Target state:**
- User downloads ZIP (~2-4 GB), extracts, runs `start.bat`/`start.sh`
- Embedded Python runtime (no system Python)
- All Python deps pre-installed in bundled environment
- Models download on first use via manifest + `huggingface_hub`
- Optional: bundle llama-server binary or auto-download
- Works on fresh Windows/Linux/macOS with zero prerequisites (except GPU drivers)

## Phases

### Phase 1: Embedded Python Runtime

Replace system Python dependency with bundled `python-build-standalone`.

#### Tasks

- [ ] Research `python-build-standalone` releases for Windows/Linux/macOS
  - Check https://github.com/indygreg/python-build-standalone/releases
  - Identify latest Python 3.11/3.12/3.13 builds
  - Note: standalone builds include pip, no conda needed
  - Download size: ~50-100 MB per platform (compressed)

- [ ] Create `scripts/bootstrap-python.ps1` (Windows)
  - Download python-build-standalone ZIP for platform
  - Extract to `python/` subfolder (gitignored)
  - Verify python executable works: `python/python --version`
  - Set `PYTHONHOME` and `PYTHONPATH` env vars
  - Make idempotent (skip if already exists)

- [ ] Create `scripts/bootstrap-python.sh` (Linux/macOS)
  - Same logic, bash version
  - Detect platform: Linux x86_64, macOS x86_64, macOS aarch64
  - Download appropriate build
  - `chmod +x python/bin/python3`

- [ ] Update `start.ps1` to use embedded Python
  - Replace `.venv\Scripts\python` with `python\python.exe`
  - Remove venv activation (not needed with standalone Python)
  - Keep CUDA PATH hack (still needed for nvidia-cublas-cu12 wheels)
  - Test: `python\python.exe -m conversational_harness.launch --port 7860`

- [ ] Update `start.sh` to use embedded Python
  - Replace `.venv/bin/python` with `python/bin/python3`
  - Keep `PYTHONPATH=app/` export
  - Test on Linux/macOS

- [ ] Verify embedded Python works end-to-end
  - Fresh extract (no .venv, no system Python in PATH)
  - Run start.ps1/start.sh
  - Confirm harness launches, web UI loads, WebSocket connects
  - Test all providers: Silero VAD, faster-whisper, Pocket TTS, Kokoro

#### Acceptance Criteria
- Fresh unzip on clean Windows machine (no Python installed) → harness runs
- Fresh unzip on clean Linux machine (no Python installed) → harness runs
- `python/` folder is gitignored, bootstrapped on first run
- Bootstrap script runs in < 2 minutes on broadband connection

### Phase 2: Pre-installed Dependencies

Bundle all Python dependencies pre-installed to avoid `pip install` on first run.

#### Tasks

- [ ] Create `scripts/build-portable.ps1` (Windows build script)
  - Run on developer machine (not end user)
  - Bootstrap embedded Python (from Phase 1)
  - `python\python.exe -m pip install -r requirements.txt`
  - Verify all imports work: `python\python.exe -c "import fastapi, uvicorn, faster_whisper, ..."`
  - Measure total size of `python/` folder (expect ~2-3 GB)
  - Optional: strip unnecessary files (tests, docs, __pycache__)

- [ ] Create `scripts/build-portable.sh` (Linux/macOS build script)
  - Same logic for Unix platforms
  - Handle platform-specific wheels (CUDA only on Windows, onnxruntime-gpu optional)

- [ ] Optimize dependency size
  - Audit `requirements.txt` for unused heavy deps
  - Consider: `torch` CPU-only wheel if CUDA not needed for all providers
  - Exclude: `torchvision`, `torchaudio`, `matplotlib`, `pandas` if not used
  - Test: remove `spacy` if `misaki` works without it (check kokoro_onnx.py)
  - Target: reduce from 2.1 GB to < 1.5 GB if possible

- [ ] Verify CUDA runtime bundling (Windows)
  - Confirm `nvidia-cublas-cu12` wheel installs to `python\Lib\site-packages\nvidia\*\bin\`
  - Test CUDA inference: faster-whisper with `device: "cuda"`
  - Verify `start.ps1` PATH hack still works with embedded Python path

- [ ] Test pre-installed bundle
  - ZIP the entire folder (python/, app/, profiles/, start.ps1, start.sh)
  - Extract on clean machine
  - Run start.ps1/start.sh
  - Confirm no `pip install` runs, harness launches immediately
  - Measure startup time (expect < 30 seconds)

- [ ] Document build process in `docs/BUILD.md`
  - How to build portable release on Windows
  - How to build on Linux/macOS
  - How to update dependencies (re-run build script)
  - How to test before release

#### Acceptance Criteria
- `python/` folder contains all dependencies pre-installed
- No network access needed to launch harness (models still download on-demand)
- Total folder size: 2-4 GB (acceptable for local AI app)
- Build script runs unattended, produces ready-to-zip folder

### Phase 3: Model Management

Implement manifest-based model download with `huggingface_hub`.

#### Tasks

- [ ] Create `models/manifest.json`
  ```json
  {
    "silero-vad": {
      "source": "torch-hub",
      "description": "Voice activity detection model",
      "size_mb": 2,
      "required": true,
      "auto_download": true
    },
    "faster-whisper-large-v3-turbo": {
      "source": "huggingface",
      "repo": "Systran/faster-whisper-large-v3-turbo",
      "description": "Speech recognition model (3 GB)",
      "size_mb": 3000,
      "required": false,
      "auto_download": false,
      "profile": "llamacpp-cuda-asr"
    },
    "kokoro-v1.0-int8": {
      "source": "url",
      "url": "https://github.com/nazdridoy/kokoro-onnx/releases/download/v1.0.0/kokoro-v1.0.int8.onnx",
      "path": "model-cache/kokoro/kokoro-v1.0.int8.onnx",
      "description": "Text-to-speech model (88 MB)",
      "size_mb": 88,
      "required": false,
      "auto_download": true,
      "profile": "llamacpp-kokoro-onnx"
    },
    "kokoro-voices-v1.0": {
      "source": "url",
      "url": "https://github.com/nazdridoy/kokoro-onnx/releases/download/v1.0.0/voices-v1.0.bin",
      "path": "model-cache/kokoro/voices-v1.0.bin",
      "description": "Kokoro voice embeddings (27 MB)",
      "size_mb": 27,
      "required": false,
      "auto_download": true,
      "profile": "llamacpp-kokoro-onnx"
    }
  }
  ```

- [ ] Create `app/conversational_harness/model_manager.py`
  - `class ModelManager`
  - `load_manifest()` → parse models/manifest.json
  - `check_model(model_id)` → return True if file exists or HF cache has it
  - `download_model(model_id, progress_callback)` → download with progress
  - `download_all_required()` → download models where `required: true` and `auto_download: true`
  - Use `huggingface_hub.hf_hub_download()` for HF models
  - Use `httpx` for direct URL downloads (Kokoro)
  - Show progress in console and via WebSocket events

- [ ] Integrate model manager into `launch.py`
  - Before starting uvicorn, call `model_manager.download_all_required()`
  - Show progress: "Downloading faster-whisper model (3 GB)... 45%"
  - Fail gracefully if download fails (log error, continue with available models)
  - Add `--skip-model-download` flag to bypass (for testing)

- [ ] Update `main.py` lifespan to check model availability
  - On startup, check if active profile's models are present
  - If missing and `auto_download: false`, emit WebSocket event: `model.missing`
  - Frontend shows: "Model X not downloaded. Run `python -m model_manager download <id>` or switch profile."

- [ ] Add WebSocket events for model status
  - `model.downloading` → `{model_id, progress_percent, bytes_downloaded, total_bytes}`
  - `model.ready` → `{model_id}`
  - `model.missing` → `{model_id, download_command}`
  - Frontend can show download progress bar

- [ ] Create `scripts/download-models.ps1` / `.sh`
  - Standalone script to pre-download all models
  - Usage: `.\scripts\download-models.ps1` (downloads everything)
  - Usage: `.\scripts\download-models.ps1 faster-whisper-large-v3-turbo` (specific model)
  - Calls `python\python.exe -m conversational_harness.model_manager download <id>`

- [ ] Test model management
  - Fresh extract (no models, no model-cache/, no ~/.cache/huggingface/)
  - Run start.ps1 → auto-downloads required models
  - Switch profile to Kokoro → auto-downloads Kokoro models
  - Switch profile to faster-whisper → prompts user to download 3 GB model
  - Verify downloads resume after interruption (huggingface_hub handles this)

#### Acceptance Criteria
- Fresh install downloads required models automatically (< 5 MB for Silero VAD)
- Optional models download on-demand when profile activates them
- Downloads show progress in console and web UI
- Interrupted downloads resume (no re-download from zero)
- `models/manifest.json` is the single source of truth for model requirements

### Phase 4: llama.cpp Server Integration

Bundle or auto-download llama-server binary.

#### Tasks

- [ ] Research llama.cpp release binaries
  - Check https://github.com/ggerganov/llama.cpp/releases
  - Identify pre-built binaries: Windows CUDA, Windows Vulkan, Linux CUDA, Linux Vulkan, macOS Metal
  - Note: releases include `llama-server` (or `server` in older versions)
  - Size: ~50-100 MB per platform variant

- [ ] Add llama-server to `models/manifest.json`
  ```json
  "llama-server-windows-cuda": {
    "source": "github-release",
    "repo": "ggerganov/llama.cpp",
    "tag": "b4056",
    "asset": "llama-b4056-bin-win-cuda-cu12.x.zip",
    "path": "llama-server/llama-server.exe",
    "description": "LLM inference server (Windows CUDA)",
    "size_mb": 100,
    "required": false,
    "auto_download": false,
    "platform": "windows-cuda"
  }
  ```

- [ ] Create `scripts/start-llama.ps1` / `.sh`
  - Check if `llama-server/llama-server.exe` exists
  - If not, prompt: "llama-server not found. Download? (Y/n) or provide path:"
  - Launch: `llama-server/llama-server.exe --model models/your-model.gguf --host 127.0.0.1 --port 8080`
  - Wait for `/health` endpoint to return 200
  - Print: "llama.cpp server ready on http://127.0.0.1:8080"

- [ ] Update `start.ps1` to optionally launch llama-server
  - Add `--with-llama` flag
  - If flag present, spawn `scripts/start-llama.ps1` in parallel
  - Wait for llama-server health check before starting harness
  - On exit, kill llama-server process

- [ ] Document llama.cpp setup in `README.md`
  - Option 1: Bundle llama-server (adds ~100 MB to download)
  - Option 2: User downloads llama-server separately (current approach)
  - Option 3: Auto-download on first run (if user opts in)
  - Explain: "GGUF models are user-provided (4-8 GB), not bundled"

- [ ] Test llama-server integration
  - Fresh extract, no llama-server installed
  - Run `start.ps1 --with-llama` → prompts download
  - Download completes, server launches
  - Harness connects, LLM works
  - User provides GGUF model path via `--model` flag or config

#### Acceptance Criteria
- llama-server can be bundled (adds ~100 MB) or downloaded on-demand
- `start.ps1 --with-llama` launches both server and harness
- GGUF models remain user-provided (not bundled, too large)
- Clear error message if llama-server missing and user didn't opt for auto-download

### Phase 5: Packaging & Distribution

Create the final distributable ZIP.

#### Tasks

- [ ] Create `scripts/package-portable.ps1` (Windows)
  - Run after `build-portable.ps1` (Phase 2)
  - Copy required folders: `python/`, `app/`, `profiles/`, `models/manifest.json`
  - Copy scripts: `start.ps1`, `start.sh`, `scripts/download-models.ps1`, `scripts/start-llama.ps1`
  - Copy docs: `README.md`, `LICENSE`, `docs/portable.md`
  - Exclude: `.venv/`, `.git/`, `__pycache__/`, `*.pyc`, `user_settings.json`, `memory.md`, `model-cache/`
  - Create ZIP: `conversational-ai-harness-portable-win64.zip`
  - Measure final size (expect 2-4 GB)
  - Generate SHA256 checksum: `conversational-ai-harness-portable-win64.zip.sha256`

- [ ] Create `scripts/package-portable.sh` (Linux/macOS)
  - Same logic, produces `.tar.gz` or `.zip`
  - Platform-specific naming: `conversational-ai-harness-portable-linux-x64.tar.gz`

- [ ] Write `README-PORTABLE.md` (user-facing)
  - Quick start: "1. Extract ZIP, 2. Run start.bat, 3. Open http://127.0.0.1:7860"
  - First run: "Models download automatically (5 MB - 3 GB depending on profile)"
  - Prerequisites: "NVIDIA GPU drivers (optional, CPU mode works without)"
  - Updating: "Download new ZIP, extract, copy user_settings.json and memory.md from old folder"
  - Troubleshooting: "If Windows Defender warns, click 'More info' → 'Run anyway' (unsigned binary)"
  - Uninstalling: "Delete the folder. That's it."

- [ ] Create `start.bat` (Windows double-click launcher)
  ```batch
  @echo off
  echo Starting Converse-AI...
  powershell -ExecutionPolicy Bypass -File start.ps1
  pause
  ```
  - Users double-click `start.bat` instead of opening PowerShell manually

- [ ] Test full distribution flow
  - Run `scripts/package-portable.ps1` → produces ZIP
  - Extract ZIP on clean Windows VM (no Python, no prior harness install)
  - Double-click `start.bat`
  - Verify: Python bootstraps (if not bundled), deps load, models download, harness launches
  - Test all profiles: mock-local, llamacpp-cuda-asr, llamacpp-kokoro-onnx
  - Test on Linux: extract tar.gz, run `./start.sh`, verify same flow

- [ ] Set up GitHub Actions for automated builds (optional, future)
  - `.github/workflows/build-portable.yml`
  - Triggers on tag push: `v*.*.*`
  - Builds on Windows, Linux, macOS runners
  - Uploads artifacts to GitHub Releases
  - Signs binaries if code signing cert available (future)

- [ ] Document release process in `docs/RELEASE.md`
  - Bump version in `app/conversational_harness/__init__.py`
  - Run `scripts/package-portable.ps1` on each platform
  - Upload ZIPs to GitHub Releases
  - Update download links in README

#### Acceptance Criteria
- Single ZIP file, 2-4 GB, ready for distribution
- Clean extraction + double-click `start.bat` → harness runs in < 2 minutes
- No manual steps required (except GPU drivers if CUDA wanted)
- Works on Windows 10/11, Ubuntu 22.04+, macOS 12+
- Update path documented and tested (preserve user data)

### Phase 6: Update Mechanism

Enable easy updates without re-downloading models.

#### Tasks

- [ ] Add version check to `launch.py`
  - On startup, fetch `https://api.github.com/repos/<owner>/<repo>/releases/latest`
  - Compare local version (`__version__`) with latest release tag
  - If newer version available, emit WebSocket event: `update.available`
  - Frontend shows banner: "Update available: v1.2.0 → v1.3.0. Download?"

- [ ] Create `scripts/update.ps1` / `.sh`
  - Download latest portable ZIP from GitHub Releases
  - Extract to temp folder
  - Backup current `user_settings.json`, `memory.md`, `model-cache/`, `profiles/` (if user modified)
  - Replace `python/`, `app/`, `scripts/` with new versions
  - Restore user data
  - Verify new version launches
  - Clean up temp folder
  - Usage: `.\scripts\update.ps1` (one-click update)

- [ ] Document manual update process in `README-PORTABLE.md`
  - Download new ZIP
  - Extract to new folder
  - Copy `user_settings.json`, `memory.md`, `model-cache/` from old folder
  - Delete old folder
  - Models don't need re-downloading (stored in `model-cache/` and `~/.cache/huggingface/`)

- [ ] Test update flow
  - Install v1.0.0 portable
  - Create user data: settings, memory, downloaded models
  - Run `scripts/update.ps1` to update to v1.1.0
  - Verify: user data preserved, models still present, new version runs
  - Verify: no re-download of models

#### Acceptance Criteria
- Update script replaces app files, preserves user data and models
- No re-download of multi-GB models during update
- Version check notifies user of available updates
- Manual update path documented and tested

## Notes

### Known Risks

1. **Antivirus false positives**
   - Unsigned binaries trigger Windows Defender SmartScreen
   - Mitigation: Document "Run anyway" steps, consider code signing ($200-500/yr EV cert)
   - Long-term: Build reputation over time, or use code signing from day one

2. **CUDA driver compatibility**
   - Bundled `nvidia-cublas-cu12` requires CUDA 12.x drivers
   - Users with older drivers (CUDA 11.x) need to update or use CPU mode
   - Mitigation: Detect driver version, show clear error message if incompatible

3. **espeak-ng system dependency**
   - Kokoro TTS with English voices needs espeak-ng
   - Not bundled (system package)
   - Mitigation: Detect missing espeak-ng, show install instructions, or bundle binary (adds ~10 MB)

4. **Large initial download**
   - 2-4 GB ZIP is big for users with slow internet
   - Mitigation: Offer "minimal" build (CPU-only, no CUDA, ~1 GB) and "full" build (CUDA, ~3 GB)

5. **Platform-specific builds**
   - Need separate ZIPs for Windows, Linux, macOS
   - Mitigation: Automate with GitHub Actions, test on each platform

6. **Model download failures**
   - HuggingFace Hub or GitHub releases may be unreachable (firewalls, offline)
   - Mitigation: Allow offline model placement (download manually, copy to expected path)
   - Document: "If auto-download fails, download model from <url> and place in <path>"

### Design Decisions

1. **Why `python-build-standalone` instead of conda/miniconda?**
   - Smaller footprint (~50-100 MB vs ~500 MB for conda)
   - No conda command complexity for end users
   - Includes pip, works like regular Python
   - Maintained by Gregory Szorc (indygreg), used by PyOxidizer

2. **Why not PyInstaller (yet)?**
   - Portable folder is lower risk, faster to implement
   - PyInstaller has known issues with uvicorn + multiprocessing
   - Can upgrade to PyInstaller later if needed (portable folder is stepping stone)

3. **Why separate models from app?**
   - Models are 3-8 GB, app is 2-4 GB
   - App updates frequently, models rarely change
   - Users may have models from other tools (Ollama, LM Studio) they want to reuse
   - Matches precedent: Ollama, LM Studio, SillyTavern all separate models

4. **Why manifest-based model download?**
   - Single source of truth for what's needed
   - Can show progress, resume downloads, verify checksums
   - Profile-driven: only download models for active profile
   - Extensible: add new models by editing manifest.json

5. **Why not bundle llama-server by default?**
   - Adds 100 MB to download
   - Users may have their own llama.cpp build (custom CUDA, Vulkan, etc.)
   - GGUF models are user-provided anyway (4-8 GB)
   - Optional bundle or auto-download is more flexible

### Future Enhancements

1. **Code signing** (if budget allows)
   - Windows EV cert: $200-500/yr
   - Apple Developer ID: $99/yr
   - Eliminates SmartScreen/Gatekeeper warnings

2. **Auto-updater daemon** (if user demand)
   - Background process checks for updates daily
   - Downloads in background, prompts "Restart to update"
   - Similar to Ollama, VS Code, Discord

3. **Minimal ONNX-only build** (strategic)
   - Replace faster-whisper with whisper.cpp or whisper-ONNX
   - Drop PyTorch dependency (~2 GB savings)
   - Bundle size: ~1 GB instead of ~3 GB
   - Trade-off: loses faster-whisper's CUDA acceleration

4. **Tauri native wrapper** (if polished desktop app needed)
   - Thin Rust shell (~5-15 MB) + Python sidecar
   - Native window, system tray, auto-update
   - Proven pattern: Whisper4Windows
   - Effort: 2-4 weeks, adds Rust toolchain

5. **Docker image** (for server deployment)
   - `docker pull conversational-ai-harness:latest`
   - Includes Python, deps, llama-server
   - Volume mounts for models, config, user data
   - Effort: 1-2 days, tested pattern

## References

- **Portable folder precedent:**
  - Oobabooga: https://github.com/oobabooga/text-generation-webui/
  - AllTalk: https://github.com/erew123/alltalk_tts/
  - KoboldCpp: https://github.com/LostRuins/koboldcpp/

- **python-build-standalone:**
  - https://github.com/indygreg/python-build-standalone
  - Pre-built Python 3.8-3.14 for Windows/Linux/macOS

- **Model management:**
  - HuggingFace Hub: https://huggingface.co/docs/huggingface_hub/
  - Ollama model layers: https://deepwiki.com/ollama/ollama/4.2-model-registry-and-layers
  - LM Studio model management: https://deepwiki.com/lmstudio-ai/docs/2.1-model-management-and-lifecycle

- **llama.cpp releases:**
  - https://github.com/ggerganov/llama.cpp/releases

- **Code signing (future):**
  - Windows EV certs: https://docs.microsoft.com/en-us/windows/win32/appxpkg/how-to-create-a-package-signing-certificate
  - Apple notarization: https://developer.apple.com/documentation/security/notarizing_macos_software_before_distribution

## Timeline

- **Phase 1 (Embedded Python):** 1 day
- **Phase 2 (Pre-installed deps):** 1 day
- **Phase 3 (Model management):** 1-2 days
- **Phase 4 (llama.cpp integration):** 0.5 day
- **Phase 5 (Packaging):** 0.5 day
- **Phase 6 (Update mechanism):** 1 day

**Total:** 4-6 days for complete portable distribution

**MVP (Phases 1-3 + 5):** 3-4 days for basic portable folder

## Success Metrics

- Fresh install to first conversation: < 5 minutes (with broadband)
- Total download size: < 4 GB (acceptable for local AI app)
- Zero manual steps required (except GPU drivers if CUDA wanted)
- Works on clean Windows/Linux/macOS without Python installed
- Update preserves user data and models (no re-download)
- Zero `pip install` or `conda` commands for end user
