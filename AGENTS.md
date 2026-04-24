# Conversational AI Harness Agent Notes

This project is intended to become a local, modular conversational AI harness for realtime voice interaction. Keep implementation choices boring, swappable, and measurable. The main stack direction is:

- VAD: Silero VAD, preferably ONNX on CPU.
- ASR: Kyutai STT first, with Whisper/faster-whisper style fallbacks.
- LLM: llama.cpp server first, with CUDA default and Vulkan treated as experimental.
- TTS: Pocket TTS and Kokoro ONNX as the primary local CPU options.
- UI: local desktop/web app launched by plain scripts.

## Design Mistakes To Avoid

- Do not hardwire the app around one model family. Each component should sit behind a provider interface so ASR, LLM, TTS, and VAD can be swapped independently.
- Do not treat PersonaPlex as the default architecture. It is valuable research direction for full-duplex speech-to-speech, but the first useful harness should be a modular cascade that can actually be installed, debugged, and improved component by component.
- Do not assume all backends stream the same way. Some produce partials, some only finals, some stream audio chunks, and some need phrase-sized text. Normalize events at the harness boundary.
- Do not let model-specific config leak into orchestration logic. Provider settings belong in profiles/config files, not scattered through the core loop.
- Do not design around perfect turn boundaries. Real users hesitate, interrupt, mumble, cough, and speak over TTS.
- Do not make the UI a demo page. The first screen should be the working harness: mic, model status, transcript, response stream, audio state, and latency metrics.

## Latency Mistakes To Avoid

- Do not wait for a full transcript before doing useful work when partial transcripts are available.
- Do not wait for a full LLM answer before starting TTS. Chunk on stable sentence or phrase boundaries.
- Do not run VAD on the GPU by default. Keep it cheap, local, and always-on through CPU/ONNX unless a better reason appears.
- Do not let TTS keep talking through user barge-in. On speech start, pause, duck, or cancel playback according to the active profile.
- Do not optimize only tokens per second. Measure end-to-end stages: mic capture, VAD, ASR partial/final, LLM first token, TTS first chunk, and playback start.
- Do not over-trust Vulkan. It is promising for llama.cpp on diverse GPUs, but CUDA remains the default path for NVIDIA and for the broader speech stack.
- Do not assume CPU offload is free. It may make larger models fit, but it can destroy conversational feel if first-token latency gets too high.

## Installation And Runtime Mistakes To Avoid

- Do not assume the folder name identifies the active harness. Several similarly named copies may exist; verify with `git status`, `Get-Location`, `/api/status`, and the process command line for port `7860` before editing or debugging.
- Do not let script defaults disagree with code defaults. `start.*`, `doctor.*`, README instructions, and `config.DEFAULT_PROFILE` should all point at the same intended default profile.
- Do not diagnose provider behavior from the UI alone. Check `/api/status` to confirm which profile and providers the running server actually loaded.
- Do not leave a stale server running while testing frontend changes. Confirm which PID owns port `7860`, stop that process if it is this harness, then restart from the intended workspace.
- Do not require Docker for V1 unless a specific backend truly needs it. The preferred install path is plain scripts.
- Do not silently download huge gated models without clear status and failure messages.
- Do not make CUDA mandatory for the whole app. The harness should still run in a reduced mode with llama.cpp CPU/offload and Pocket TTS.
- Do not hide missing audio device, sample-rate, or driver errors. `doctor` scripts should make these obvious.
- Do not assume Windows, Linux, and macOS audio stacks behave the same. Keep audio device handling isolated.
- Do not build startup around one long opaque command. Start services explicitly and show per-service readiness.
- Do not rely on global Python packages. Use a project environment managed by the install scripts.
- Do not assume the system CUDA toolkit version matches what CTranslate2 needs. CTranslate2 ships with `cudnn64_*.dll` but requires `cublas64_12.dll` at runtime. If the system CUDA toolkit is v13+, `cublas64_12.dll` will be missing and CUDA inference will fail with `RuntimeError: Library cublas64_12.dll is not found or cannot be loaded`. The fix is `pip install nvidia-cublas-cu12` into the project venv, and the `start.ps1` script adds the NVIDIA bin directories to `PATH` before launching uvicorn. Without this, CUDA ASR silently hangs because the error is thrown inside an `asyncio.to_thread` worker.

## Modularity Guidelines

- Define provider contracts before adding model-specific glue.
- Keep experimental adapters thin. Prefer in-process or simple local runtime boundaries that keep TTS swappable without forcing the harness around one engine.
- Prefer event streams over blocking calls for realtime paths.
- Keep audio format conversions explicit: sample rate, channel count, frame size, PCM type.
- Make every provider report capabilities such as `supports_partials`, `supports_streaming_tts`, `supports_barge_in`, `requires_gpu`, and `languages`.
- Keep profiles small and readable. A user should be able to understand which ASR, LLM, TTS, VAD, model paths, and device settings are active.
- Put fallbacks in config/profile selection, not hidden catch-all exception paths.

## Testing Guidelines

- Include canned audio tests for VAD and ASR so regressions do not require live microphone input.
- Add tests around defaults and profile selection. A working provider can look broken if the app silently starts in `mock-local`.
- Include a tiny LLM profile for smoke tests.
- Include a TTS smoke test that writes playable audio and records first-audio latency.
- Include an integration test for barge-in: user speech begins while TTS is active.
- Include a degraded-mode test: no CUDA available, Pocket TTS active, llama.cpp CPU or partial offload.
- Keep latency tests approximate but visible. A slow test should explain which stage regressed.

## Implementation Bias

- Start with the simplest local cascade that can hold a real conversation.
- Measure before swapping models.
- When audio sounds bad, separate model quality from transport quality. For browser TTS playback, avoid starting one `Audio()` element per chunk; prefer scheduled Web Audio playback, larger phrase chunks, and explicit latency markers.
- For Pocket TTS specifically, the main playback regression was transport-side, not model-side. The effective fix was:
  - keep Pocket output as raw `pcm_s16le`,
  - coalesce the model's tiny streaming chunks on the backend into larger packets before sending them over WebSocket,
  - carry explicit audio metadata such as sample rate, channels, encoding, and duration on `tts.audio`,
  - build browser `AudioBuffer`s directly from PCM instead of calling `decodeAudioData()` on many tiny standalone WAV chunks.
- If Pocket TTS sounds choppy again, inspect chunk size, PCM packet coalescing, queue lead, and browser scheduling first. Text normalization quirks such as missing contractions are more likely model behavior than playback transport.
- Prefer boring protocols: HTTP for control, WebSocket for realtime events/audio, OpenAI-compatible APIs where llama.cpp already provides them.
- Keep the harness useful even when only one component is excellent and the others are merely serviceable.
- Make failure states friendly. A user should know whether they need a model file, a Hugging Face gate approval, CUDA drivers, Vulkan support, or an audio permission fix.
