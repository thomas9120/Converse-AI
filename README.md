# Conversational AI Harness

A local, modular harness for experimenting with realtime conversational AI stacks.

The implementation provides a runnable scaffold with clear provider boundaries:

- **VAD**: Silero VAD (ONNX on CPU) with configurable speech threshold, hysteresis, hangover, and minimum speech duration gating.
- **ASR**: faster-whisper with CUDA or CPU support, mock fallback included.
- **LLM**: llama.cpp OpenAI-compatible adapter with runtime-adjustable sampler settings.
- **TTS**: Pocket TTS and Kokoro ONNX voice output, mock tone fallback included.
- **UI**: local browser app with Chat, Companion, and Settings tabs, text/voice turns, event stream, and latency metrics.
- **Companion mode**: separate prompt, names, sampler overrides, transcript, and optional Markdown memory backed by `memory.md`.

The default start profile is `profiles/llamacpp-cuda-asr.json`, which uses Silero VAD, CUDA faster-whisper ASR, llama.cpp, and Pocket TTS. A CPU ASR fallback is available at `profiles/llamacpp-local.json`. A no-model smoke-test profile is at `profiles/mock-local.json`.

## Quick Start

Windows:

```powershell
.\install.ps1
.\start.ps1
```

Linux/macOS:

```bash
./install.sh
./start.sh
```

Then open `http://127.0.0.1:7860`.

`start.*` now runs a guided launcher. It checks the selected profile, required Python packages, the harness port, provider status, and llama.cpp readiness before starting uvicorn. In an interactive terminal it can prompt for the profile and ask before opening the browser. For non-interactive use, pass `--no-prompt --no-browser`.

On Windows, you can also start a Cloudflare quick tunnel from the launcher:

```powershell
.\start.ps1 -CloudflareTunnel
```

The tunnel prints a public Cloudflare URL in the terminal, and the launcher reprints it as `Cloudflare tunnel URL: ...` when detected. Use this only when you intentionally want remote access to your local harness.

If the local readiness check times out but the server process is still running, the launcher still starts the Cloudflare tunnel and warns that the public URL may show a temporary error until the harness finishes warming up.

## Prerequisites

- **Python 3.11, 3.12, or 3.13** (Python 3.14 is not supported by `kokoro-onnx==0.5.0`; the project venv uses whatever Python created it)
- **llama.cpp server** running separately for LLM inference (see [llama.cpp](#llamacpp) section)
- **CUDA 12+** (optional, for GPU ASR — the harness falls back to CPU if unavailable)

### CUDA ASR Setup

The CUDA ASR profile requires CTranslate2 to find CUDA 12 runtime DLLs. If your system CUDA toolkit is v13+, `cublas64_12.dll` will be missing. The fix:

```powershell
.\.venv\Scripts\pip install nvidia-cublas-cu12
```

The `start.ps1` script automatically adds the NVIDIA bin directories from `.venv` to `PATH` before launching. If you see `Library cublas64_12.dll is not found or cannot be loaded`, verify this package is installed.

## Microphone Path

The web UI captures microphone input and streams it to the backend as framed PCM:

- encoding: `pcm_s16le`
- sample rate: profile `audio.sample_rate`, default `16000`
- channels: profile `audio.channels`, default `1`
- frame size: profile `audio.frame_ms`, default `30`

Silero VAD runs on this stream and emits `vad.probability`, `vad.speech_start`, and `vad.speech_end`. Speech start cancels active TTS playback for barge-in support. A minimum speech duration gate (`min_speech_duration_ms`) filters out short noise bursts like keystrokes before they reach ASR.

## Voice ASR

The default CUDA profile uses `faster-whisper` with `large-v3-turbo` on GPU:

```json
"asr": {
  "provider": "faster-whisper",
  "model": "large-v3-turbo",
  "device": "cuda",
  "compute_type": "float16"
}
```

A CPU fallback profile is available:

```powershell
$env:HARNESS_PROFILE="profiles/llamacpp-local.json"
.\start.ps1
```

The model starts loading when you click Connect, so the first spoken utterance does not pay the model-load cost. If it is not already cached, faster-whisper/Hugging Face may download it at that point. After Silero emits `vad.speech_end`, the harness trims quiet leading/trailing audio, applies low-energy rejection guards, transcribes the buffered utterance, and feeds the transcript into the active mode's llama.cpp turn pipeline.

During ASR the UI reports progress stages such as queued, loading, loaded, segment, trim, and complete. If first use appears slow, check `server.err.log` for Hugging Face download/cache warnings.

The included faster-whisper profiles also use conservative anti-hallucination settings for `large-v3-turbo`, including `condition_on_previous_text: false`, `temperature: 0`, `no_speech_threshold: 0.2`, tighter Silero VAD thresholds, short/quiet utterance rejection, and silence trimming before ASR.

## Voice TTS

The default profile uses Pocket TTS with the voice `azelma`:

```json
"tts": {
  "provider": "pocket-tts",
  "voice": "azelma"
}
```

Pocket TTS loads the model and voice state on first use, then reuses them for later turns. The model is gated on Hugging Face, so accept the model terms for `kyutai/pocket-tts` and make sure your Hugging Face token is available if first use reports an access error.

Pocket TTS 2.1.0 supports int8 quantization through the `quantize` profile option. The default Pocket preset keeps full precision, and the runtime selector also includes `Pocket TTS INT8` for lower memory use and faster CPU inference:

```json
"quantize": true
```

For smoother playback, the local profiles feed Pocket TTS larger phrase-sized chunks and the browser schedules decoded PCM chunks on a shared Web Audio timeline instead of playing each chunk as a separate audio element.

### Kokoro ONNX Profile

An additional profile is included for Kokoro v1.0 ONNX on CPU:

```powershell
$env:HARNESS_PROFILE="profiles/llamacpp-kokoro-onnx.json"
.\doctor.ps1
.\start.ps1
```

Kokoro uses local ONNX files and downloads the current v1.0 assets on first use into `model-cache/kokoro`. The runtime TTS selector in the web UI can switch between Pocket TTS and Kokoro without restarting the harness.

Kokoro is tuned separately from Pocket TTS because its CPU ONNX path benefits from shorter phrase chunks. The Kokoro profile and preset use lower `tts_chunk_chars` / `min_tts_chars` values, prewarm English G2P on model load, and expose optional CPU threading controls:

```json
"onnx_intra_op_num_threads": 4,
"onnx_inter_op_num_threads": 1,
"preload_g2p": true
```

## Settings Tab

The UI has Chat, Companion, and Settings tabs in the topbar. Chat is the standard harness conversation, Companion is a separate persistent companion workspace, and Settings controls the standard chat defaults and character-card workflow.

## Companion Tab

Companion mode is separate from standard Chat mode:

- It has its own transcript, composer, user name, AI name, system prompt, and sampler overrides.
- Text and voice turns are routed to the active tab, so Companion speech and Chat speech keep separate histories.
- Character-card first-message seeding applies only to standard Chat mode.
- Companion prompt assembly uses the Companion names, Companion system prompt, optional memory, and Companion sampler overrides.

### Companion Memory

Companion memory is stored in project-root `memory.md`, which is ignored by git. The file is created on first memory write, not on startup.

Memory is manual-save in this version:

- **Save Memory** writes the current memory editor text to `memory.md`.
- **Summarize Chat** sends the visible Companion transcript to the active LLM and appends a dated Markdown summary to `memory.md`.
- **Clear Memory** removes the saved memory file.
- The Memory checkbox controls whether saved memory is injected into Companion LLM requests.

Saved memory is injected only in Companion mode when memory is enabled. It is not injected into standard Chat or character-card conversations.

## Settings Tab

The Settings tab has three sections:

### Names

Set a display name for yourself and the AI. Names are used:
- In chat bubble labels (replacing "You" and "Assistant")
- In the system prompt sent to the LLM

### Sampler Settings

Adjust llama.cpp sampler parameters at runtime without restarting:

| Parameter | Description |
|---|---|
| Temperature | Sampling randomness (0.0 - 2.0) |
| Top K | Limit to top K tokens |
| Top P | Nucleus sampling threshold |
| Min P | Minimum probability threshold |
| Repeat Penalty | Discourages token repetition |
| Frequency Penalty | Penalizes frequent tokens |
| Presence Penalty | Penalizes present tokens |
| Max Tokens | Max tokens per turn (-1 = unlimited) |

Settings are pre-filled from the llama.cpp server's current defaults on startup. Edits are saved to `user_settings.json` and persist across restarts. The "Reset to Defaults" button clears all overrides.

### Character Cards

Import TavernAI V2 character cards (`.png` with embedded data or standalone `.json`). The card's name, description, personality, scenario, and example dialogue are assembled into the system prompt, replacing any automatic name-based prompt. Template variables `{{user}}` and `{{char}}` are resolved to the configured names.

You can add extra instructions in the "Additional instructions" textarea below the card, which are appended after the character data. The manual system prompt textarea on the Chat tab can also supplement the card.

## Continue Button

The Chat tab has a "Continue" button next to Send. When the LLM response is cut off (by hitting the context limit or max tokens), clicking Continue asks the model to pick up where it left off. The last assistant message stays in the conversation history as a prefix, and new tokens are appended to the existing bubble.

## API Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Serves the web UI |
| `/api/status` | GET | Profile, providers, TTS runtime, settings |
| `/api/settings` | GET | Current runtime settings |
| `/api/settings` | PATCH | Update sampler/names/additional prompt |
| `/api/settings/character` | POST | Import character card (JSON body) |
| `/api/settings/character/upload` | POST | Upload character card file (PNG or JSON) |
| `/api/settings/character` | DELETE | Clear active character card |
| `/api/companion/memory` | GET | Read Companion memory text and metadata |
| `/api/companion/memory` | PUT | Save edited Companion memory text |
| `/api/companion/memory/summarize` | POST | Summarize Companion transcript into `memory.md` |
| `/api/companion/memory` | DELETE | Clear Companion memory |
| `/api/tts/select` | POST | Select TTS preset |
| `/api/tts/load` | POST | Load TTS model |
| `/api/tts/unload` | POST | Unload TTS model |
| `/api/tts/voice` | POST | Select TTS voice |
| `/ws/events` | WebSocket | Bidirectional event stream |

## Scripts

- `install.*`: verifies Python 3.11+, creates or reuses `.venv`, installs Python dependencies, and prints next steps.
- `start.*`: launches the guided startup flow and then runs the local web/backend server in the foreground. It honors `HARNESS_PROFILE` and `HARNESS_PORT`, refuses to start if the target port is already occupied, checks provider/backend readiness, and prints the local URL. Windows also adds NVIDIA package bin directories from `.venv` to `PATH` for CUDA ASR.
- `stop.*`: stops this harness if it is listening on `HARNESS_PORT` (default `7860`). It leaves unrelated processes alone and force-stops only if graceful shutdown times out.
- `doctor.*`: checks Python, optional GPU tooling, optional llama.cpp server, and profile status.
- `update.*`: updates this checkout from `origin/main` only. It refuses to run with uncommitted changes, uses fast-forward-only merge behavior, and reinstalls requirements when `requirements.txt` changed.

Examples:

```powershell
.\start.ps1 -Port 7861 -Profile profiles/mock-local.json
.\start.ps1 -ListProfiles
.\start.ps1 -NoPrompt -NoBrowser
.\start.ps1 -CloudflareTunnel
$env:PYTHONPATH = "$(Get-Location)\app"; .\.venv\Scripts\python -m conversational_harness.launch --cloudflare-tunnel
.\update.ps1
```

```bash
HARNESS_PORT=7861 HARNESS_PROFILE=profiles/mock-local.json ./start.sh
./start.sh --list-profiles
./start.sh --no-prompt --no-browser
./update.sh
```

The update scripts intentionally do not pull from the current branch if it is `Beta` or another working branch; `origin/main` is the only update source.

## Profiles

Profiles live in `profiles/*.json`. Available profiles:

| Profile | ASR | TTS | Notes |
|---|---|---|---|
| `llamacpp-cuda-asr.json` | CUDA float16 | Pocket TTS | **Default**. Requires CUDA 12 runtime. |
| `llamacpp-local.json` | CPU int8 | Pocket TTS | CPU fallback. No CUDA needed. |
| `llamacpp-kokoro-onnx.json` | CPU int8 | Kokoro ONNX | Kokoro TTS on CPU. |
| `mock-local.json` | Mock | Mock | No-model smoke test for UI and orchestration. |

To use a non-default profile:

```powershell
$env:HARNESS_PROFILE="profiles/mock-local.json"
.\start.ps1
```

## llama.cpp

The harness talks to llama.cpp through its OpenAI-compatible server API. Start `llama-server` separately, then use any `llamacpp-*` profile.

Example CUDA launch:

```powershell
llama-server -m C:\models\your-model.gguf --host 127.0.0.1 --port 8080 --alias local-gguf -ngl 999
```

Then verify:

```powershell
.\doctor.ps1
.\start.ps1
```

Notes:

- The harness uses `/v1/chat/completions` with streaming enabled.
- `doctor` checks `/health` and `/v1/models` so missing or still-loading models are visible.
- On startup, the harness fetches `/props` from llama.cpp to learn the server's current sampler defaults and pre-fills the Settings UI.
- Sampler resolution is server defaults, then profile defaults, then the active mode's runtime overrides. Companion mode has its own override set.
- Vulkan can be useful for llama.cpp on non-CUDA systems, but it is LLM-only here and should be treated as experimental for this voice stack.
- The default profile uses `"model": "auto"` and selects the first model reported by `/v1/models`.
- If you prefer a fixed ID, set `llm.model` to the exact model ID reported by `/v1/models`, or launch `llama-server` with a matching `--alias`.

## Graceful Shutdown

The server runs a lifespan handler that cleans up on Ctrl+C or SIGTERM:

- Cancels all active TTS playback
- Calls `unload()` on all providers (releases CUDA GPU memory from ASR, drops VAD model)
- Discards all active WebSocket queues

`stop.ps1` tries graceful shutdown first with a 5-second timeout, then force-kills if needed.

## Development

Run tests:

```powershell
.\.venv\Scripts\python -m pytest
```

Optional live llama.cpp integration test:

```powershell
$env:HARNESS_TEST_LLAMA_CPP="1"
$env:HARNESS_LLAMA_CPP_MODEL="auto"
.\.venv\Scripts\python -m pytest tests\test_llamacpp_live.py
```
