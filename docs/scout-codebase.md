# Binary Packaging Feasibility Analysis

## 1. Entry Points

### Primary Entry Point
**File:** `app/conversational_harness/launch.py` (lines 1-245)
- Module entry: `python -m conversational_harness.launch`
- Main function: `main()` at line 27
- Parses CLI args, loads profile, spawns uvicorn subprocess at line 174-186
- Subprocess command: `uvicorn conversational_harness.main:app --host 127.0.0.1 --port {port}`

### FastAPI Application
**File:** `app/conversational_harness/main.py` (lines 1-560)
- FastAPI app defined at line 58: `app = FastAPI(...)`
- Lifespan context manager at line 46-52 (fetches llama.cpp defaults, cleanup)
- HTTP endpoints: `/`, `/api/status`, `/api/tts/*`, `/api/settings`, `/api/companion/*`
- WebSocket endpoint: `/ws/events` at line 258

### Startup Scripts
**File:** `start.ps1` (lines 1-42)
- Activates `.venv`, sets `PYTHONPATH=app/`
- **Critical CUDA PATH hack (lines 24-27):**
  ```powershell
  $nvidiaBins = Get-ChildItem -Path ".venv\Lib\site-packages\nvidia" -Directory ... | 
                ForEach-Object { Join-Path $_.FullName "bin" }
  $env:PATH = ($nvidiaBins -join ";") + ";" + $env:PATH
  ```
- Launches: `.venv\Scripts\python -m conversational_harness.launch --port {port}`

**File:** `start.sh` (lines 1-22)
- Same logic, no CUDA PATH manipulation (Linux uses system CUDA)

## 2. Python Dependencies

**File:** `requirements.txt` (lines 1-15)

### Core Framework
- `fastapi==0.115.6` - Web framework
- `uvicorn[standard]==0.34.0` - ASGI server
- `httpx==0.28.1` - HTTP client (async)
- `websockets==14.1` - WebSocket support

### Heavy ML/AI Dependencies (Size Impact)
- `faster-whisper==1.2.1` - ASR (depends on ctranslate2, torch)
- `silero-vad==6.2.1` - Voice activity detection (depends on torch)
- `kokoro-onnx==0.5.0` - TTS (depends on onnxruntime)
- `pocket-tts==2.1.0` - TTS (pure Python, small model)
- `onnxruntime==1.23.2` - ONNX inference
- `spacy==3.8.14` - NLP (used by misaki for G2P)
- `misaki==0.7.4` - Grapheme-to-phoneme for Kokoro (depends on espeak)
- `numpy==2.2.1` - Array operations

### CUDA-Specific (Windows Only)
- `nvidia-cublas-cu12==12.9.1.4; platform_system == "Windows"` - CUDA runtime

### Transitive Heavy Dependencies (Not Listed)
- **torch** - Required by silero-vad, faster-whisper (~2-3 GB installed)
- **ctranslate2** - Required by faster-whisper (~500 MB)
- **espeak-ng** - System dependency for misaki phonemization

### Total .venv Size
**Measured:** 2.1 GB (C:/Users/pegas/Desktop/LLama/Test apps/backup/Conversational-AI-Harness/.venv)

## 3. External Non-Python Runtime Dependencies

### llama.cpp Server (EXTERNAL PROCESS - NOT BUNDLED)
**File:** `app/conversational_harness/providers/llamacpp.py` (lines 1-130)
- **Not spawned by harness** - expects external server at `http://127.0.0.1:8080`
- Connects via HTTP to `/health`, `/props`, `/v1/models`, `/v1/chat/completions`
- Default base_url: `http://127.0.0.1:8080` (line 13)
- Model resolution: auto-selects first model from `/v1/models` (line 122-128)

**File:** `app/conversational_harness/doctor.py` (lines 143-175)
- `check_llamacpp()` validates server reachability and model availability
- No subprocess calls to llama.cpp - pure HTTP health checks

**File:** `app/conversational_harness/launch.py` (lines 174-186)
- `start_server()` spawns uvicorn, NOT llama.cpp
- User must start llama-server manually before harness

### Subprocess Calls (Limited)
**File:** `app/conversational_harness/launch.py`
- Line 186: `subprocess.Popen` for uvicorn server
- Line 214-218: `wait_for_process()`, `stop_process()` - process lifecycle

**File:** `app/conversational_harness/doctor.py`
- Line 135: `subprocess.check_output(["netstat", "-ano", "-p", "tcp"])` - Windows port check
- Line 156: `subprocess.check_output(["powershell", ...])` - Windows process info

### espeak-ng (System Dependency)
**File:** `app/conversational_harness/providers/kokoro_onnx.py` (lines 218-223)
- Used by `misaki.espeak.EspeakFallback` for phoneme generation
- Requires system-installed espeak-ng binary
- Only needed for Kokoro TTS with English voices

## 4. Static Assets

### Web UI (Static Files)
**Directory:** `app/static/`
**File:** `app/static/index.html` (10 KB)
**File:** `app/static/styles.css` (12 KB)
**File:** `app/static/app.js` (47 KB)

**File:** `app/conversational_harness/main.py` (line 56)
- Mounted at `/static` via `StaticFiles(directory=STATIC_ROOT)`
- STATIC_ROOT = `PROJECT_ROOT / "app" / "static"` (line 41)

### Profile Configuration
**Directory:** `profiles/`

**File:** `profiles/llamacpp-cuda-asr.json` (default, lines 1-51)
- Silero VAD, faster-whisper CUDA, llama.cpp, Pocket TTS

**File:** `profiles/llamacpp-local.json` (lines 1-51)
- CPU mode: faster-whisper CPU int8

**File:** `profiles/llamacpp-kokoro-onnx.json` (lines 1-67)
- Kokoro TTS with ONNX

**File:** `profiles/mock-local.json` (lines 1-33)
- All mock providers for testing

**File:** `profiles/tts-presets.json` (lines 1-151)
- TTS preset definitions: Pocket TTS, Pocket TTS INT8, Kokoro v1.0
- Includes voice lists per preset

## 5. CUDA/GPU Dependencies

### CUDA Detection
**File:** `app/conversational_harness/doctor.py` (lines 119-122)
- Checks for `nvidia-smi` on PATH
- Optional check - harness can run in CPU mode

### CUDA Runtime Bundling (Windows)
**File:** `requirements.txt` (line 8)
- `nvidia-cublas-cu12==12.9.1.4; platform_system == "Windows"`

**File:** `start.ps1` (lines 24-27)
- Extracts CUDA DLLs from `.venv\Lib\site-packages\nvidia\*\bin\`
- Prepends to PATH before launch
- DLLs include: `cublas64_12.dll`, `cudart64_12.dll`, etc.

### CUDA Usage
**File:** `app/conversational_harness/providers/faster_whisper.py` (lines 12-16)
- Configurable: `device: "cuda" | "cpu"`, `compute_type: "float16" | "int8"`
- Default CUDA profile uses `device: "cuda"`, `compute_type: "float16"`

**File:** `app/conversational_harness/providers/silero.py` (lines 92-93)
- Silero VAD uses torch with ONNX backend (CPU by default)

### Vulkan (Optional)
**File:** `app/conversational_harness/doctor.py` (lines 124-127)
- Checks for `vulkaninfo` - optional, only for llama.cpp server

## 6. WebSocket Server Setup

### Server Configuration
**File:** `app/conversational_harness/launch.py` (lines 174-186)
- Host: `127.0.0.1` (localhost only)
- Port: Default `7860`, configurable via `--port` or `HARNESS_PORT` env
- Uvicorn with standard websocket implementation

### WebSocket Endpoint
**File:** `app/conversational_harness/main.py` (lines 258-560)
- Endpoint: `/ws/events`
- Bidirectional JSON messages
- Handles: `user.text`, `audio.frame`, `vad.speech_start`, `tts.cancel`, `conversation.clear`

### Frontend Connection
**File:** `app/static/app.js` (lines 261-267)
- Connects to `ws://{location.host}/ws/events` or `wss://` for HTTPS
- Uses native `WebSocket` API
- Event-driven architecture with message types

## 7. Writable Data Directories

### Runtime Settings
**File:** `app/conversational_harness/runtime_settings.py` (lines 1-338)
- **Path:** `PROJECT_ROOT / "user_settings.json"` (line 11)
- Persists: LLM sampler overrides, user/AI names, character cards, additional system prompt
- Loaded at startup (line 268), saved on changes (line 309)
- **Must be writable** at runtime

### Memory Store
**File:** `app/conversational_harness/runtime_settings.py` (lines 243-338)
- **Path:** `PROJECT_ROOT / "memory.md"` (line 12)
- Persistent companion memory (up to 20KB)
- **Must be writable** at runtime

### Model Cache (Kokoro)
**File:** `app/conversational_harness/providers/kokoro_onnx.py` (lines 22-25)
- **Path:** `PROJECT_ROOT / "model-cache" / "kokoro"`
- Downloads: `kokoro-v1.0.int8.onnx` (88 MB), `voices-v1.0.bin` (27 MB)
- **Current size:** 115 MB total in `model-cache/`
- **Must be writable** on first run if Kokoro TTS used

### Faster-Whisper Model Cache
- **Location:** `~/.cache/huggingface/hub/` (system-wide)
- Downloads models on first use (large-v3-turbo ~3 GB)
- Not in project directory

### Profiles Directory
**Path:** `profiles/`
- Read-only at runtime (loaded at startup)
- **Must exist** with at least one `.json` profile

### .gitignore Exclusions
**File:** `.gitignore` (lines 1-36)
- Ignores: `.venv/`, `user_settings.json`, `memory.md`, `model-cache/`, `*.gguf`, etc.

## 8. Existing Packaging Configuration

**Status:** NO EXISTING PACKAGING CONFIG

### Missing Files
- No `pyproject.toml` (no build-system config)
- No `setup.py`
- No `setup.cfg`
- No `Makefile`
- No `Dockerfile`
- No `.spec` files (PyInstaller)
- No `build/` or `dist/` directories

### Installation Approach (Current)
**File:** `install.ps1` (lines 1-56)
- Creates `.venv` with Python 3.11-3.13
- `pip install -r requirements.txt`
- No wheel building, no binary distribution

**File:** `install.sh` (lines 1-31)
- Same for Linux/macOS

## 9. Model File Sizes

### model-cache/ (Project-Local)
**Measured:** 115 MB total
- `kokoro-v1.0.int8.onnx` - 88 MB (ONNX model)
- `voices-v1.0.bin` - 27 MB (voice embeddings)

### External Model Caches (System)
- **faster-whisper large-v3-turbo:** ~3 GB (Hugging Face cache)
- **Silero VAD:** ~2 MB (torch hub cache)
- **Pocket TTS:** ~50 MB (embedded in package or downloaded)

### llama.cpp GGUF Models (User-Provided)
- Not bundled - user downloads separately
- Typical size: 4-8 GB for 7B-13B models
- Stored anywhere, referenced by llama-server

## Key Packaging Challenges

### 1. External llama.cpp Server
- **Problem:** Harness does NOT spawn llama.cpp - expects it running externally
- **Impact:** Binary packaging must either bundle llama-server or document dependency
- **Recommendation:** Bundle llama-server binary, add launcher to spawn it

### 2. Heavy PyTorch Dependency
- **Size:** ~2-3 GB for torch + CUDA
- **Used by:** silero-vad, faster-whisper
- **Impact:** Dominates package size
- **Alternative:** Consider ONNX-only backends (kokoro already ONNX)

### 3. CUDA Runtime Complexity
- **Problem:** CUDA DLLs in `.venv\nvidia\*\bin\` need PATH manipulation
- **Windows-specific:** Requires start.ps1 PATH hack
- **Impact:** Binary packaging must replicate CUDA DLL resolution

### 4. Model Downloads at Runtime
- **faster-whisper:** Downloads from Hugging Face on first use (~3 GB)
- **Kokoro:** Downloads to `model-cache/` if missing (~115 MB)
- **Impact:** First-run experience requires internet or pre-cached models

### 5. System Dependencies
- **espeak-ng:** Required for Kokoro English G2P
- **nvidia-smi:** Optional, for CUDA detection
- **Impact:** Must document or bundle system deps

### 6. Writable Runtime Paths
- `user_settings.json` - user preferences
- `memory.md` - companion memory
- `model-cache/` - downloaded models
- **Impact:** Cannot package as single read-only executable

## Packaging Recommendations

### Option A: PyInstaller + Bundled llama-server
- Bundle Python app + llama-server binary
- Include CUDA DLLs in PATH
- Pre-cache Kokoro models in `model-cache/`
- User must provide GGUF models
- **Estimated size:** 4-5 GB (with torch)

### Option B: Docker Image
- Single image with Python, llama-server, CUDA, espeak-ng
- Volume mounts for `user_settings.json`, `memory.md`, `model-cache/`
- User provides GGUF via volume mount
- **Estimated size:** 6-8 GB (with CUDA)

### Option C: ONNX-Only Build (Minimal)
- Replace silero-vad with ONNX version (no torch)
- Replace faster-whisper with whisper.cpp or whisper-onnx
- Keep Kokoro ONNX, Pocket TTS
- Bundle llama-server
- **Estimated size:** 1-2 GB

### Critical Files for Packaging
1. `app/conversational_harness/launch.py` - entry point
2. `app/conversational_harness/main.py` - FastAPI app
3. `app/conversational_harness/providers/*.py` - all providers
4. `profiles/*.json` - configuration
5. `app/static/*` - web UI
6. `start.ps1` - CUDA PATH logic (Windows)
