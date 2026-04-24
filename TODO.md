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
  - Browser playback uses scheduled Web Audio decoding instead of one `Audio()` element per chunk.
  - TTS text chunking now uses larger phrase-sized chunks with `tts_chunk_chars` and `min_tts_chars`.
- Kyutai TTS scaffolding.
  - `kyutai-tts-server` provider added as a local HTTP wrapper boundary.
  - Profiles added for `kyutai/tts-0.75b-en-public` and `kyutai/tts-1.6b-en_fr`.
  - Kyutai TTS is not required for app startup.
- UI.
  - Local web UI shows profile, provider status, mic controls, input level, VAD/ASR/TTS state, transcript, response stream, events, and latency.
  - Latency metrics include ASR final, LLM first token, TTS first chunk, playback start, and turn complete.
- Tests.
  - Current suite passes: `24 passed, 1 skipped`.
  - Tests cover audio helpers, audio frames, config/provider building, doctor checks, orchestrator events, faster-whisper provider behavior, Pocket TTS behavior, Silero VAD behavior, and optional llama.cpp live integration.

## Current Priority

1. Stabilize Pocket TTS playback quality.
   - Test whether the Web Audio scheduling and larger phrase chunks reduce choppiness enough for regular use.
   - If choppiness remains, identify whether seams come from Pocket generation chunks, WAV chunk boundaries, browser scheduling, or overly small LLM-to-TTS text chunks.
   - Try PCM streaming instead of per-chunk WAV decode if browser scheduling is not enough.
   - Consider short overlap/crossfade only if seams are playback-bound rather than generation-bound.

2. Add conversation controls to the UI.
   - Clear/reset current conversation.
   - Save transcript to local text/JSON.
   - Show active profile name and provider details more explicitly.
   - Add a visible system prompt editor if not already present in the active UI copy.

3. Improve runtime/doctor diagnostics.
   - Report which process owns port `7860`, not just whether the port is occupied.
   - Add doctor checks for microphone/browser constraints where possible.
   - Add clearer Pocket TTS Hugging Face gate/token messages.
   - Add a profile summary showing active model names, devices, compute types, and endpoints.

## Next Implementation Tracks

### Kyutai TTS

- Build or choose the local HTTP wrapper behind `kyutai-tts-server`.
  - First target: `kyutai/tts-0.75b-en-public` for a lower-VRAM quality comparison.
  - Second target: `kyutai/tts-1.6b-en_fr` through the official Rust server path.
  - Experimental target: community GGUF/moshi.cpp conversion if it exposes a stable local server path.
- Replace the temporary HTTP wrapper adapter with a native websocket adapter if the official Kyutai server protocol is stable enough.
- Record VRAM use, first-audio latency, playback latency, and perceived quality for Pocket TTS, 0.75B, 1.6B, and GGUF experiments.
- Keep Pocket TTS as the CPU fallback even if Kyutai TTS becomes the preferred quality path.

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
- Add a profile selector or explicit startup prompt so users do not accidentally run the wrong stack.
- Keep Windows first-class, but maintain simple shell equivalents for Linux/macOS.
- Document model downloads, Hugging Face gates, CUDA caveats, and expected first-run delays in a troubleshooting section.

## Working Assumptions

- The target architecture remains a local modular cascade, not PersonaPlex-style full-duplex speech-to-speech.
- Text input stays as a debug fallback even when voice mode is working.
- CUDA is the preferred NVIDIA performance path; Vulkan remains experimental and llama.cpp-only for now.
- Docker is avoided for V1 unless a specific backend truly needs it.
- Profiles should remain small, readable, and explicit about provider choices.
