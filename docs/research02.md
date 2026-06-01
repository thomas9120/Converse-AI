# Research: Non-CUDA ASR Options for Realtime Converse-AI

## Summary

**whisper.cpp in persistent server mode is the strongest non-CUDA ASR path for a realtime harness.** It delivers the lowest latency (0.024 P95 RTF vs 0.044 for faster-whisper), runs on Vulkan/Metal/CPU without NVIDIA dependency, has the most active maintenance (50K★, 789 contributors, 32 releases), and supports all Whisper model sizes. The cost is managing a persistent subprocess. For simpler Python-native integration where latency tail isn't critical, faster-whisper on CPU (INT8) is a solid fallback but still needs CUDA cuBLAS DLLs on Windows. ONNX Whisper (sherpa-onnx) shows significant accuracy regressions (CER 0.81 vs 0.25) and should not be used. mlx-whisper is Apple Silicon–only and has known stall/OOM bugs.

---

## Findings

### 1. Accuracy: WER differences are small between backends that use the same weights — except ONNX

All backends that load the same OpenAI Whisper model weights produce **roughly equivalent accuracy** when decoding settings are matched (beam size, temperature). The WER gap between faster-whisper (INT8) and whisper.cpp (q5_1) on LibriSpeech test-clean is only **0.3%** (4.42% vs 4.71%). [Source](https://tildalice.io/whisper-cpp-vs-faster-whisper-wer-librispeech-benchmark/)

Key accuracy findings:
- **faster-whisper INT8** → 4.42% WER on LibriSpeech test-clean (small.en). Uses CTranslate2 dynamic per-tensor quantization, which adapts better to activation distributions than static quantization. [Source](https://tildalice.io/whisper-cpp-vs-faster-whisper-wer-librispeech-benchmark/)
- **whisper.cpp q5_1** → 4.71% WER on same set. The gap is quantization, not architecture — with f16 weights, whisper.cpp scores 4.48%, nearly matching faster-whisper. [Source](https://tildalice.io/whisper-cpp-vs-faster-whisper-wer-librispeech-benchmark/)
- **whisper.cpp server (multilingual small)** → actually slightly beat faster-whisper on real speech in one benchmark (0.074 WER vs 0.076). [Source](https://allenkuo.medium.com/choosing-a-real-time-whisper-engine-c4eeb5885e22)
- **ONNX Whisper via sherpa-onnx** → **Severe accuracy regression**. On Chinese FLEURS with Whisper Tiny, CER was 0.81 vs 0.25 for faster-whisper on the same model and audio. This is caused by ONNX export differences, missing token suppression heuristics, and BPE handling mismatches. [Source](https://github.com/k2-fsa/sherpa-onnx/issues/2900)
- **mlx-whisper** — no published WER comparison vs faster-whisper on identical hardware. In practice, accuracy is comparable since same weights, but mlx-whisper only supports **greedy decoding** (no beam search), which can increase WER on challenging audio. [Source](https://github.com/naveedn/whisper-bench)
- On GPU, the gap narrows further — both faster-whisper and whisper.cpp use f16, and measured WER on LibriSpeech is 4.41% vs 4.49%. [Source](https://tildalice.io/whisper-cpp-vs-faster-whisper-wer-librispeech-benchmark/)

**Verdict**: Accuracy is not the differentiating factor between faster-whisper and whisper.cpp. Avoid ONNX Whisper (sherpa-onnx).

---

### 2. Latency: whisper.cpp server mode is fastest for realtime use

A detailed 2026 benchmark of realtime Whisper engines measured:

| Engine | Mean Time | Mean RTF | P95 RTF |
|--------|-----------|----------|---------|
| faster-whisper (small, CUDA) | 153ms | 0.033 | 0.044 |
| whisper.cpp small multilingual (server) | **106ms** | **0.024** | **0.024** |
| whisper.cpp small.en (server) | **101ms** | **0.024** | **0.024** |
| TheWhisper turbo (PyTorch) | 143ms | 0.034 | — |
| whisper.cpp CLI (model-load each call) | 746ms | 0.199 | 0.575 |

[Source](https://allenkuo.medium.com/choosing-a-real-time-whisper-engine-c4eeb5885e22)

**Critical insight**: whisper.cpp via CLI is a **trap** — model-load per invocation dominates latency. In persistent server mode, whisper.cpp is ~30% faster than faster-whisper with half the P95 tail. [Source](https://allenkuo.medium.com/choosing-a-real-time-whisper-engine-c4eeb5885e22)

**On Apple Silicon**, mlx-whisper benchmarks show:
- **base model**: 29.7× realtime (18.7s on podcast audio) vs faster-whisper 17.3× (32.1s). [Source](https://github.com/naveedn/whisper-bench)
- **flash attention + batched decoding**: up to **44.8× RT** on whisper-small (5 min Russian audio, M2 8GB). [Source](https://github.com/ml-explore/mlx-examples/issues/1412)
- However, large-v3 on mlx-whisper can **stall >200s** on ~10s audio clips. [Source](https://github.com/ml-explore/mlx-examples/issues/1373)

**whisper.cpp Vulkan performance**: v1.8.3 claims 12× performance boost on integrated AMD/Intel GPUs. On N100 (Intel), small model went from ~7s CPU to competitive with dedicated GPU. large-v3 on discrete GPU: ~8-9s. [Source](https://www.phoronix.com/news/Whisper-cpp-1.8.3-12x-Perf), [Source](https://github.com/ggml-org/whisper.cpp/discussions/2662)

---

### 3. Integration Complexity

| Aspect | faster-whisper | whisper.cpp | mlx-whisper | ONNX (sherpa-onnx) |
|--------|---------------|-------------|-------------|-------------------|
| Python bindings? | ✅ Native Python | ✅ pywhispercpp, whisper-cpp-python, pybind11 bindings | ✅ Native Python | ✅ sherpa-onnx Python API |
| Subprocess needed? | ❌ No | ⚠️ Recommended (server mode) | ❌ No | ❌ No |
| pip install? | ✅ Yes | ✅ Yes (pywhispercpp/ whisper-cpp-python) | ✅ Yes | ✅ Yes |
| GPU deps | cuBLAS+cuDNN (NVIDIA only) | Vulkan SDK (any GPU), Metal (Apple), none for CPU | Apple Silicon only | onnxruntime (CPU/CUDA) |
| Windows CUDA DLL hell | ⚠️ Needs nvidia-cublas-cu12 pip + PATH fix | ❌ No cuBLAS dependency | ❌ Not applicable | ❌ onnxruntime handles it |

**faster-whisper** is the simplest Python-native integration: `pip install`, `WhisperModel("small"`, `model.transcribe(audio)`. But on Windows, it silently fails without `nvidia-cublas-cu12` in PATH — exactly the bug this project already fixed. No subprocess management needed. Native VAD (Silero VAD v6 bundled). Supports streaming via generator. [Source](https://github.com/SYSTRAN/faster-whisper)

**whisper.cpp** requires either:
- **Server mode** (recommended): compile `whisper-server`, manage subprocess lifecycle, POST WAV segments. Handles its own lifecycle, restart on crash logic needed.
- **Python bindings**: pywhispercpp [Source](https://github.com/absadiki/pywhispercpp) or whisper-cpp-python [Source](https://pypi.org/project/whisper-cpp-python/) provide `model.transcribe()`. Good for bypassing CLI overhead, but server mode gives best latency.
- **Memory**: ~200MB for small.en, no Python/PyTorch overhead.

- The project has bindings in Go, Java, Ruby, JavaScript, Python, and C API. [Source](https://github.com/ggml-org/whisper.cpp)

**mlx-whisper**: Simplest integration on Apple Silicon: `pip install mlx-whisper`, `model.transcribe()`. But Apple-only. Has known stall and OOM bugs on longer audio. [Source](https://github.com/ml-explore/mlx-examples/issues/1366)

**sherpa-onnx**: Python API with microphone examples and WebSocket server. But the known accuracy regression makes it unsuitable for production Whisper usage. [Source](https://k2-fsa.github.io/sherpa/onnx/python/streaming-websocket-server.html), [Source](https://github.com/k2-fsa/sherpa-onnx/issues/2900)

---

### 4. Model Support

| Model size | faster-whisper (CT2) | whisper.cpp (GGML) | mlx-whisper | ONNX (sherpa-onnx) |
|------------|---------------------|-------------------|-------------|-------------------|
| tiny/tiny.en | ✅ | ✅ | ✅ | ✅ |
| base/base.en | ✅ | ✅ | ✅ | ✅ |
| small/small.en | ✅ | ✅ | ✅ | ✅ |
| medium/medium.en | ✅ | ✅ | ✅ | ✅ |
| large-v1/v2/v3 | ✅ | ✅ | ✅ | ✅ |
| large-v3-turbo | ✅ | ✅ | ✅ (weight issue) | ✅ |
| distil-* models | ✅ | ✅ | ✅ | ✅ |
| HuggingFace models directly? | ✅ (HF hub auto-download) | ❌ (needs conversion to GGML) | ✅ (HF hub) | ❌ (needs ONNX export) |

**Key difference**: faster-whisper loads models directly from HuggingFace hub by model ID. whisper.cpp needs pre-converted GGML format models (provided by the project's download scripts) or a conversion step. mlx-whisper also loads from HF hub.

whisper.cpp quantization support: Q2 through Q8, plus f16. Quantized models use less RAM (5-bit quantization reduces model size ~45%) with minimal WER impact on larger models. [Source](https://subsmith.app/blog/whisper-variants-explained)

---

### 5. Maintenance Risk

| Project | Stars | Contributors | Releases | Last Release | Last Commit | Verdict |
|---------|-------|-------------|----------|-------------|-------------|---------|
| **whisper.cpp** (ggml-org) | **50K** | **789** | **32** | **v1.8.4 (Mar 2026)** | **Active (days ago)** | ✅ **Excellent** |
| faster-whisper (SYSTRAN) | 23K | 50 | 21 | v1.2.1 (Oct 2025) | Nov 2025 (6mo gap) | ⚠️ **Slowing** |
| mlx-whisper (Apple) | Part of mlx-examples (9K) | ~20-30 | 7 | v0.4.3 (Aug 2025) | Aug 2025 | ⚠️ **Low activity** |
| sherpa-onnx (k2-fsa) | ~3K | ~60 | Many | Active 2026 | Active | ✅ **Active but accuracy issue** |

**whisper.cpp**: Georgi Gerganov (same author as llama.cpp). 4208 commits, very active CI, regular releases (~monthly). 32 releases tracked. Owned by ggml-org organization (same as llama.cpp). [Source](https://github.com/ggml-org/whisper.cpp), [Source](https://www.openhub.net/p/whisper-cpp)

**faster-whisper**: 6 months without a commit (last Nov 2025). The original author (guillaumekln) appears to have stepped back. Main activity is from MahmoudAshraf97 and community PRs. The CTranslate2 upstream dependency is also slowing. Risk of bitrot is real. [Source](https://github.com/SYSTRAN/faster-whisper)

**mlx-whisper**: Last PyPI release Aug 2025. The project is part of ml-explore/mlx-examples (a collection of examples, not a dedicated product). Open bugs (stall, OOM) remain unresolved for months. [Source](https://pypi.org/project/mlx-whisper/), [Source](https://github.com/ml-explore/mlx-examples/issues/1373)

**sherpa-onnx**: Actively maintained (2026 commits), large team, many supported models. However, the Whisper ONNX accuracy gap appears architectural (native vs ONNX export differences) — fixing it would require deep ONNX runtime work. [Source](https://k2-fsa.github.io/sherpa/onnx/)

---

### 6. Edge Cases

| Concern | faster-whisper | whisper.cpp | mlx-whisper | ONNX (sherpa-onnx) |
|---------|---------------|-------------|-------------|-------------------|
| Barge-in support | ✅ Via VAD + streaming generator | ✅ Via server mode + VAD | ❌ Not designed for realtime | ⚠️ WebSocket server exists but accuracy bad |
| VAD quality | ✅ Bundled Silero VAD v6 (best-in-class) | ⚠️ Energy-based (basic) | ❌ None built-in | ⚠️ External VAD needed |
| Silence hallucination | ✅ Mitigation via VAD/energy gates | ✅ VAD can filter but less sophisticated | ❌ Hallucinates on silence (no VAD) | ❌ Accuracy too degraded to matter |
| Partial transcripts | ✅ Generator yields segments as computed | ✅ Server can stream partial results | ❌ No streaming support | ⚠️ Streaming server exists |
| Language support | ✅ 100+ languages | ✅ 100+ languages | ✅ 100+ languages | ✅ 100+ languages |
| Long audio stability | ✅ Batched inference pipeline | ✅ Good (many tools/tests) | ❌ OOM >1GB, stalls on large-v3 | ⚠️ Unknown |
| Hotword support | ✅ Built-in hotword list | ⚠️ Via grammar/tokens | ❌ None | ❌ None |
| GPU memory (small) | ~150MB (INT8) | ~200MB (q5_1) | ~200MB | Varies |

**Whisper.cpp lacks bundled VAD** — you'd need Silero VAD as a separate provider (exactly what the project already has). The project's existing `SileroVADProvider` + the whisper.cpp server backend would be a natural pairing.

**faster-whisper's bundled Silero VAD v6** is convenient but also means the ASR provider bundle pulls in the ONNX runtime for VAD — redundant with your existing VAD provider.

**Partial/streaming**: faster-whisper's generator naturally yields partial segments as they're decoded. whisper.cpp server supports streaming by POSTing short audio chunks. mlx-whisper has no streaming support — it transcribes the full audio buffer.

---

## Recommendation

### Primary path: **whisper.cpp server mode**

**Why it wins for this harness:**
1. **No NVIDIA dependency** — runs on Vulkan (AMD/Intel GPUs), Metal (Apple), or pure CPU. Future-proof across GPU vendors.
2. **Fastest latency** — P95 RTF 0.024, ~30% faster than faster-whisper, half the tail. Critical for conversational feel.
3. **Best maintained** — 50K★, 789 contributors, monthly releases, from the llama.cpp creator.
4. **Full model support** — tiny through large-v3-turbo, quantization options from q2 to f16.
5. **Mature server mode** — proper HTTP API, persistent process, no model-load overhead per segment.
6. **Existing project alignment** — project already manages subprocesses (llama.cpp server), adding whisper-server follows same pattern.

**Cost**: Must build `whisper-server` from source (pre-built binaries available for some platforms). Must manage subprocess lifecycle (start, crash restart, health check). Need separate VAD provider (already exists in project).

### Fallback: **faster-whisper CPU INT8**

- Simpler Python integration, no subprocess.
- But: 6-month maintenance gap, CUDA DLL dependency on Windows even for CPU mode (CTranslate2 links cuBLAS), and higher latency.
- Use only if whisper.cpp integration proves too complex for current sprint.

### Avoid:
- **ONNX Whisper** (sherpa-onnx) — unacceptable accuracy regression.
- **mlx-whisper** — Apple-only, stalled maintenance, known stall/OOM bugs.
- **whisper.cpp CLI mode** — model-load overhead makes it 7× slower than server mode.

---

## Sources

### Kept:
- **TildAlice: Whisper.cpp vs Faster-Whisper WER** — Direct LibriSpeech benchmark, crucial for accuracy comparison. [Source](https://tildalice.io/whisper-cpp-vs-faster-whisper-wer-librispeech-benchmark/)
- **Allen Kuo: Choosing a Real-Time Whisper Engine (May 2026)** — Most relevant realtime benchmarking, reveals whisper.cpp server advantage. [Source](https://allenkuo.medium.com/choosing-a-real-time-whisper-engine-c4eeb5885e22)
- **whisper.cpp GitHub** — 50K★, 4208 commits, active maintenance. [Source](https://github.com/ggml-org/whisper.cpp)
- **faster-whisper GitHub** — 23K★, but 6mo since last commit. [Source](https://github.com/SYSTRAN/faster-whisper)
- **sherpa-onnx #2900** — Documents ONNX accuracy regression (CER 0.81 vs 0.25). [Source](https://github.com/k2-fsa/sherpa-onnx/issues/2900)
- **mlx-whisper issues #1373, #1366** — Stall and OOM bugs, unresolved. [Source](https://github.com/ml-explore/mlx-examples/issues/1373), [Source](https://github.com/ml-explore/mlx-examples/issues/1366)
- **SubSmith: Whisper Variants Explained** — Good overview of tradeoffs. [Source](https://subsmith.app/blog/whisper-variants-explained)
- **naveedn/whisper-bench** — Apple Silicon benchmark comparison. [Source](https://github.com/naveedn/whisper-bench)
- **Phoronix: whisper.cpp 1.8.3 Vulkan** — iGPU performance claims. [Source](https://www.phoronix.com/news/Whisper-cpp-1.8.3-12x-Perf)

### Dropped:
- **MACGPU Blog MLX vs whisper.cpp** — Qualitative/opinion, low concrete numbers.
- **OpenHUB whisper.cpp stats** — Redundant with GitHub data above.
- **wyoming-mlx-whisper** — Niche Home Assistant integration, not relevant.
- **Various generic whisper-streaming repos** — Outdated or superseded by SimulStreaming, not applicable.

---

## Gaps

1. **No direct whisper.cpp (Vulkan) vs faster-whisper (CUDA) RTF comparison on same AMD GPU hardware.** All available data compares CUDA vs CUDA or CPU vs CPU. For the non-CUDA use case specifically on AMD/NVIDIA-integrated, measured numbers would be valuable but can be estimated from the Phoronix 12× iGPU claim.

2. **No quantified WER comparison of mlx-whisper vs faster-whisper on identical Apple Silicon hardware.** The naveedn/whisper-bench repo exists but benchmarks speed, not accuracy.

3. **No long-running stability test of whisper.cpp server mode** (hours of continuous conversation). Would be valuable before committing to the subprocess approach.

4. **ONNX Whisper accuracy regression root cause** — the sherpa-onnx issue documents the gap but doesn't identify the fix. Could be worth deeper investigation if ONNX path is essential.

### Suggested next steps:
- Build whisper.cpp with Vulkan support, run `whisper-server` with small model, measure end-to-end latency in the existing harness pipeline.
- Compare against faster-whisper CUDA on the same machine for apples-to-apples.
- Profile memory/CPU usage of whisper-server vs faster-whisper CPU INT8 under continuous 8+ hour load.
