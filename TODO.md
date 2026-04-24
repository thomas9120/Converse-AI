# TODO

## Kyutai TTS

- Build or choose the local HTTP wrapper behind `kyutai-tts-server`.
  - First target: `kyutai/tts-0.75b-en-public` for a lower-VRAM quality comparison.
  - Second target: `kyutai/tts-1.6b-en_fr` through the official Rust server path.
  - Experimental target: community GGUF/moshi.cpp conversion if it exposes a stable local server path.
- Replace the temporary HTTP wrapper adapter with a native websocket adapter if the official Kyutai server protocol is stable enough.
- Record VRAM use, first-audio latency, and perceived quality for Pocket TTS, 0.75B, 1.6B, and GGUF experiments.

## Pocket TTS Playback

- Investigate further Pocket TTS playback smoothing if choppiness becomes distracting again.
  - Check whether seams come from short generated chunks, browser scheduling, WAV chunk boundaries, or model generation itself.
  - Try larger text chunks, overlap/crossfade, PCM streaming instead of per-chunk WAV decode, and optional sentence-level buffering.

## CUDA ASR Profile

- Fix the CUDA faster-whisper profile so CTranslate2 can load CUDA runtime DLLs on Windows.
- Investigate the missing `cublas64_12.dll` error:
  - Confirm installed NVIDIA driver and CUDA runtime versions.
  - Confirm whether CUDA 12 Toolkit or bundled CTranslate2 CUDA DLLs are available.
  - Add the correct CUDA/cuDNN directories to `PATH` before launching the harness.
  - Consider documenting a helper script for CUDA ASR startup.
- Validate `profiles/llamacpp-cuda-asr.json` with `device: "cuda"` and `compute_type: "float16"`.
- Compare latency and accuracy against the default CPU int8 ASR profile.
