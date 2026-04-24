# Conversational AI Harness

A local, modular harness for experimenting with realtime conversational AI stacks.

The first implementation is a runnable scaffold with clear provider boundaries:

- VAD: Silero-oriented interface, mock implementation included.
- ASR: faster-whisper voice path, Kyutai-oriented interface, mock implementation included.
- LLM: llama.cpp OpenAI-compatible adapter, mock fallback included.
- TTS: Pocket TTS and Kokoro ONNX voice output, mock tone fallback included.
- UI: local browser app for text turns, status, event stream, and latency metrics.

The default start profile is `profiles/llamacpp-local.json`, which uses Silero VAD, faster-whisper ASR, llama.cpp, and Pocket TTS. A no-model smoke-test profile is still available at `profiles/mock-local.json`.

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

## Microphone Path

The web UI can capture microphone input and stream it to the backend as framed PCM:

- encoding: `pcm_s16le`
- sample rate: profile `audio.sample_rate`, default `16000`
- channels: profile `audio.channels`, default `1`
- frame size: profile `audio.frame_ms`, default `30`

The `llamacpp-local` profile runs Silero VAD on this stream. It emits:

- `vad.probability`
- `vad.speech_start`
- `vad.speech_end`

Speech start cancels active mock TTS playback so barge-in behavior can be tested before ASR is connected.

## Voice ASR

The `llamacpp-local` profile uses `faster-whisper` with `large-v3-turbo`:

```json
"asr": {
  "provider": "faster-whisper",
  "model": "large-v3-turbo",
  "device": "cpu",
  "compute_type": "int8"
}
```

The default voice profile uses CPU int8 ASR because it avoids Windows CUDA DLL failures and gives the most reliable first voice loop. The model is loaded on first spoken utterance. If it is not already cached, faster-whisper/Hugging Face may download it at that point. After Silero emits `vad.speech_end`, the harness transcribes the buffered utterance and feeds the transcript into the same llama.cpp turn pipeline used by typed messages.

During ASR the UI reports progress stages such as queued, loading, loaded, segment, and complete. If first use appears slow, check `server.err.log` for Hugging Face download/cache warnings.

CUDA ASR profile:

```powershell
$env:HARNESS_PROFILE="profiles/llamacpp-cuda-asr.json"
.\start.ps1
```

Use the CUDA profile only when CTranslate2 can find CUDA 12 runtime DLLs such as `cublas64_12.dll`. If you see `Library cublas64_12.dll is not found or cannot be loaded`, use the default CPU profile or add the correct CUDA/cuDNN directories to `PATH` before starting the harness.

## Voice TTS

The `llamacpp-local` profile uses Pocket TTS with the default voice `azelma`:

```json
"tts": {
  "provider": "pocket-tts",
  "voice": "azelma"
}
```

Pocket TTS loads the model and voice state on first use, then reuses them for later turns. The model is gated on Hugging Face, so accept the model terms for `kyutai/pocket-tts` and make sure your Hugging Face token is available if first use reports an access error.

For smoother playback, the local profiles feed Pocket TTS larger phrase-sized chunks and the browser schedules decoded WAV chunks on a shared Web Audio timeline instead of playing each chunk as a separate audio element.

### Kokoro ONNX Profile

An additional profile is included for Kokoro v1.0 ONNX on CPU:

```powershell
$env:HARNESS_PROFILE="profiles/llamacpp-kokoro-onnx.json"
.\doctor.ps1
.\start.ps1
```

Kokoro uses local ONNX files and downloads the current v1.0 assets on first use into `model-cache/kokoro`. The runtime TTS selector in the web UI can switch between Pocket TTS and Kokoro without restarting the harness, and both engines now expose curated voice choices in the UI.

Latency metrics now include ASR final, LLM first token, TTS first chunk, playback start, and turn complete where those stages are available.

## Scripts

- `install.*`: creates `.venv` and installs Python dependencies.
- `start.*`: launches the local web/backend server.
- `stop.*`: stops this harness if it is listening on port `7860`.
- `doctor.*`: checks Python, optional GPU tooling, optional llama.cpp server, and profile status.

## Profiles

Profiles live in `profiles/*.json`. The default start scripts use `llamacpp-local.json`:

```powershell
.\start.ps1
```

To run the no-model smoke profile instead:

```powershell
$env:HARNESS_PROFILE="profiles/mock-local.json"
.\start.ps1
```

## llama.cpp

The harness talks to llama.cpp through its OpenAI-compatible server API. Start `llama-server` separately, then use the `llamacpp-local` profile.

Example CUDA launch:

```powershell
llama-server -m C:\models\your-model.gguf --host 127.0.0.1 --port 8080 --alias local-gguf -ngl 999
```

Then verify:

```powershell
$env:HARNESS_PROFILE="profiles/llamacpp-local.json"
.\doctor.ps1
.\start.ps1
```

Optional live integration test:

```powershell
$env:HARNESS_TEST_LLAMA_CPP="1"
$env:HARNESS_LLAMA_CPP_MODEL="auto"
.\.venv\Scripts\python -m pytest tests\test_llamacpp_live.py
```

Notes:

- The harness uses `/v1/chat/completions` with streaming enabled.
- `doctor` checks `/health` and `/v1/models` so missing or still-loading models are visible.
- Vulkan can be useful for llama.cpp on non-CUDA systems, but it is LLM-only here and should be treated as experimental for this voice stack.
- The default profile uses `"model": "auto"` and selects the first model reported by `/v1/models`.
- If you prefer a fixed ID, set `llm.model` to the exact model ID reported by `/v1/models`, or launch `llama-server` with a matching `--alias`.

## Development

Run tests:

```powershell
.\.venv\Scripts\python -m pytest
```
