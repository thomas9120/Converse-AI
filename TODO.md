# Implementation Plan

This harness is now past the scaffold stage. The default profile is a working local cascade:

`browser mic -> Silero VAD -> faster-whisper ASR -> llama.cpp -> Pocket TTS -> browser playback`

Keep future work modular: each VAD, ASR, LLM, and TTS backend should stay behind provider contracts and profile-driven configuration.

## Completed

- Project hygiene and GitHub readiness.
  - Git repository initialized on `main`.
  - Remote pushed to `thomas9120/Conversational-AI-Harness`.
  - `.gitignore` excludes venvs, logs, caches, env files, model weights, and generated audio.
  - `.gitattributes` added for sane line endings.
- Runtime scripts.
  - `install.ps1` / `install.sh` create the project environment.
  - `start.ps1` / `start.sh` launch the harness.
  - `stop.ps1` / `stop.sh` clear harness servers on port `7860`.
  - Default profile is now `profiles/llamacpp-local.json`, not mock mode.
- Provider architecture.
  - Provider contracts exist for VAD, ASR, LLM, and TTS.
  - Mock providers remain available for smoke testing.
  - Provider status/capabilities are surfaced through `/api/status`.
- llama.cpp integration.
  - OpenAI-compatible streaming chat adapter implemented.
  - `model: "auto"` can select the loaded llama.cpp model from `/v1/models`.
  - `doctor` checks llama.cpp `/health` and `/v1/models`.
  - Optional live llama.cpp test is gated by `HARNESS_TEST_LLAMA_CPP=1`.
- Browser audio and VAD path.
  - Browser captures microphone audio and sends framed `pcm_s16le` over WebSocket.
  - Backend validates audio frame format, sample rate, channels, frame duration, and sequence.
  - Silero VAD ONNX CPU provider implemented.
  - VAD events drive speech start, speech end, turn segmentation, and barge-in cancellation.
- ASR.
  - faster-whisper provider implemented.
  - Default ASR profile uses `large-v3-turbo` on CPU with `int8`.
  - ASR progress events report queued/loading/loaded/segment/complete states.
  - CUDA ASR profile exists but is blocked by local CUDA DLL/runtime setup.
- TTS.
  - Pocket TTS provider implemented.
  - Default Pocket TTS voice is `azelma`.
  - TTS audio streams through `tts.audio` events.
  - Browser playback now uses scheduled Web Audio playback with direct PCM buffer construction.
  - TTS text chunking now uses larger phrase-sized chunks with `tts_chunk_chars` and `min_tts_chars`.
  - Pocket TTS playback regression was fixed by coalescing tiny model chunks on the backend, sending raw `pcm_s16le` with explicit metadata, and avoiding per-fragment WAV decode in the browser.
  - Kokoro ONNX now streams audio incrementally through `create_stream(...)` instead of waiting for full-utterance synthesis.
  - Kokoro English phonemization now uses Misaki before synthesis, which avoids the earlier `phonemizer` word-count mismatch warnings on English requests.
- Runtime TTS switching now targets local CPU engines.
  - Pocket TTS remains the default low-latency path.
  - Kokoro v1.0 ONNX is now the second runtime-selectable TTS option.
  - Runtime voice selection is exposed in the web UI for both Pocket and Kokoro presets.
- UI.
  - Local web UI shows profile, provider status, mic controls, input level, VAD/ASR/TTS state, transcript, response stream, events, and latency.
  - Latency metrics include ASR final, LLM first token, TTS first chunk, playback start, and turn complete.
  - System prompt editor, dark mode, conversation clear, and stop-audio controls are in place.
  - Runtime TTS preset selection plus generic load/unload controls are in place.
- Tests.
  - Current suite passes: `38 passed, 1 skipped`.
  - Tests cover audio helpers, audio frames, config/provider building, doctor checks, orchestrator events, faster-whisper provider behavior, Pocket TTS behavior, Silero VAD behavior, and optional llama.cpp live integration.
  - Runtime TTS manager and preset switching are covered by tests.

## Current Priority

1. Validate and polish Kokoro ONNX as the second CPU TTS engine.
   - Verify first-run model download behavior and error messaging.
   - Compare Kokoro and Pocket for first-audio latency, quality, and long-form chunking now that Kokoro is using streamed synthesis.
   - Expand the curated English voice list only after a listening pass.

2. Improve runtime/doctor diagnostics.
   - Report which process owns port `7860`, not just whether the port is occupied.
   - Add doctor checks for microphone/browser constraints where possible.
   - Add clearer Pocket TTS Hugging Face gate/token messages.
   - Add a profile summary showing active model names, devices, compute types, endpoints, and selected TTS runtime preset.

3. Expand TTS runtime polish.
   - Save transcript to local text/JSON.
   - Surface provider-specific runtime notes without breaking the generic UI contract.
   - Investigate Pocket TTS text normalization quirks such as missing contractions, treating them as model behavior unless transport evidence appears.
   - Tune Pocket TTS's existing streaming path: chunk coalescing window, playback queue lead, and phrase chunk sizing.

## Next Implementation Tracks

### TTS Engines

- Keep Pocket TTS and Kokoro ONNX as the active local TTS choices.
- Track side-by-side notes for:
  - first-audio latency,
  - playback smoothness,
  - voice quality,
  - interruption behavior,
  - long response chunking.
- Pocket TTS already supports streaming in this codebase through `TTSModel.generate_audio_stream(...)`; future work should focus on buffering and latency tuning rather than adding streaming from scratch.
- Keep the runtime TTS preset switcher generic so future engines can still slot in through a provider adapter plus preset metadata.

### Kyutai STT

- Research the cleanest provider boundary for Kyutai STT.
  - PyTorch provider for experimentation.
  - Rust/moshi-server provider for production-style streaming.
  - MLX provider only for Apple Silicon profiles.
- Keep faster-whisper as the default working ASR until Kyutai STT is locally proven.
- Add profiles for Kyutai STT without making it mandatory for startup.
- Compare Kyutai STT against faster-whisper large-v3-turbo on:
  - first partial/final latency,
  - accuracy on short conversational utterances,
  - punctuation,
  - word timestamps,
  - GPU/CPU memory pressure.

### CUDA ASR

- Fix the CUDA faster-whisper profile so CTranslate2 can load CUDA runtime DLLs on Windows.
- Investigate the missing `cublas64_12.dll` error:
  - Confirm installed NVIDIA driver and CUDA runtime versions.
  - Confirm whether CUDA 12 Toolkit or bundled CTranslate2 CUDA DLLs are available.
  - Add the correct CUDA/cuDNN directories to `PATH` before launching the harness.
  - Consider documenting a helper script for CUDA ASR startup.
- Validate `profiles/llamacpp-cuda-asr.json` with `device: "cuda"` and `compute_type: "float16"`.
- Compare latency and accuracy against the default CPU int8 ASR profile.

### Audio And Turn Taking

- Add canned WAV tests for VAD state transitions.
- Add tests for utterance buffering:
  - pre-speech buffer inclusion,
  - max utterance cutoff,
  - speech start while TTS is active,
  - speech end with empty/too-short audio.
- Add WebSocket tests for audio frame messages and barge-in cancellation.
- Add configurable end-of-turn behavior:
  - VAD-only,
  - semantic ASR-based endpointing when available,
  - manual push-to-talk/debug mode.

### Latency And Evaluation

- Persist per-turn timing records for later comparison.
- Add a small benchmark command or script that reports:
  - VAD speech start/end timing,
  - ASR final latency,
  - LLM first-token latency,
  - TTS first-audio latency,
  - browser playback start,
  - total turn time.
- Add approximate regression thresholds that warn without making normal local variance fail the test suite.
- Add a TTS smoke test that writes a playable output file only when explicitly requested, keeping generated audio ignored by git.

### Packaging And Usability

- Add a one-command launcher that runs `doctor` first and opens the browser only when core dependencies are ready.
- Keep the runtime TTS preset switcher generic so future engines only need a provider adapter plus preset entries.
- Add a profile selector or explicit startup prompt so users do not accidentally run the wrong stack.
- Keep Windows first-class, but maintain simple shell equivalents for Linux/macOS.
- Document model downloads, Hugging Face gates, CUDA caveats, and expected first-run delays in a troubleshooting section.

## Working Assumptions

- The target architecture remains a local modular cascade, not PersonaPlex-style full-duplex speech-to-speech.
- Text input stays as a debug fallback even when voice mode is working.
- CUDA is the preferred NVIDIA performance path; Vulkan remains experimental and llama.cpp-only for now.
- Docker is avoided for V1 unless a specific backend truly needs it.
- Profiles should remain small, readable, and explicit about provider choices.
