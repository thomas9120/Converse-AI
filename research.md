# Research: Extracting a Speech-to-Speech Framework from a Monolith

## Summary

Extract the smallest useful surface area first — Protocol classes for ASR/VAD/LLM/TTS + a thin orchestrator that emits typed events. Keep the FastAPI/WebSocket UI as one consumer, never the only one. Use `extras_require` with lazy imports for heavy backends (ONNX, CUDA, torch). Copy-then-extract beats extract-then-generalize for the first iteration. Follow Rich's pattern: a single `Console`-like hub object, a `__rich_console__`-like Protocol, and build from there. Don't extract until you have at least one second consumer (CLI or test harness).

---

## Findings

### 1. Pitfalls of Extracting Framework Code from Applications

**Over-abstraction**: The most common failure mode. Extracting before having multiple consumers leads to wrong abstractions. Will McGugan (Rich/Textual creator) explicitly warns: *"There's quite a nice division of labor between the two libraries... If I added too much to it, it runs the risk of being too big."* He split Rich and Textual only when the interactive use case was clearly separate. [Source](https://dev.to/tim_sourcery/chatting-with-will-mcgugan-from-side-project-to-startup-4ik3)

**Breaking existing users**: Rich follows strict SemVer — *"I increased the major version for any breaking change. And I documented what changed... what developers don't want is surprises."* [Source](https://dev.to/tim_sourcery/chatting-with-will-mcgugan-from-side-project-to-startup-4ik3)

**Versioning nightmare**: Instructor (Jason Liu) deliberately stayed small to avoid this: *"I want to be like requests, not Django."* No breaking changes, thin wrapping of provider SDKs. [Source](https://www.latent.space/p/instructor)

**Test migration**: When Shopify extracted store settings from their Shop monolith, they used the Strangler Fig pattern specifically because *"We knew we'd potentially make incorrect assumptions about where these settings should be moved to"* — they wanted the extraction to be reversible. [Source](https://shopify.engineering/refactoring-legacy-code-strangler-fig-pattern)

**Hidden coupling**: Martin Fowler notes that *"Legacy systems rarely exhibit clear component boundaries"* — the hardest part isn't the code extraction, it's finding seams where none exist. [Source](https://martinfowler.com/bliki/StranglerFigApplication.html)

### 2. Extraction Strategies That Work

**Strangler Fig pattern applied to libraries (not just services)**: Fowler's pattern works for library extraction too. The key insight: *"Understand the outcomes you want to achieve, decide how to break the problem up into smaller parts, successfully deliver the parts, change the organization."* [Source](https://martinfowler.com/bliki/StranglerFigApplication.html)

**Copy-then-extract**: Will McGugan's pattern for Rich: *"I was working on another project where I hacked together a class... I realized there's potential to extract that to a library."* He did NOT extract from the original project. He took the core idea (Console object + protocol for renderable objects), rewrote from scratch with a cleaner API, and iterated. *"I didn't use any code from that application. I only took the core idea."* [Source](https://dev.to/tim_sourcery/chatting-with-will-mcgugan-from-side-project-to-startup-4ik3)

**Extract-then-generalize**: Jason Liu's Instructor started as a thin wrapper patching the OpenAI SDK. It extracted one pattern (Pydantic model → structured output) and stayed narrow. Eight months later it supported 6+ providers, still under the same simple API. *"The goal is like, this is something that you can use to build your own framework. But let me just do all the boring stuff that nobody really wants to do."* [Source](https://www.latent.space/p/instructor)

**Keep monorepo with optional extras**: This is what the project effectively already does. The recommendation from current architecture is sound — the harness is a working application. Extraction should be a new thin package that the harness depends on, not a fork. The GNES project (Han Xiao) successfully used `extras_require` with an inverted index pattern to manage hundreds of optional AI dependencies. [Source](https://hanxiao.io/2019/11/07/A-Better-Practice-for-Managing-extras-require-Dependencies-in-Python/)

**Shopify's approach to safe extraction**: *"Extracting functionality from a monolith is like performing surgery on a patient who must remain conscious and active throughout the procedure."* Their pattern: (1) create logical separation in the monolith first, (2) extract data/service behind a facade, (3) migrate consumers one by one. [Source](https://shopify.engineering/refactoring-legacy-code-strangler-fig-pattern)

### 3. How Python Frameworks Handle Optional Heavy Dependencies

**PyTorch's lazy CUDA import**: `torch.cuda` is *"lazily initialized, so you can always import it, and use is_available() to determine if your system supports CUDA."* Importing `torch` never fails even when CUDA is absent. [Source](https://github.com/pytorch/pytorch/blob/v2.6.0/torch/cuda/__init__.py)

**scientific-python/lazy-loader**: Maintained by the Scientific Python ecosystem. Subpackages and functions loaded on demand. Missing imports don't fail at import time. Environment variable `EAGER_IMPORT` for development mode. [Source](https://github.com/scientific-python/lazy-loader/)

**PyTorch backend autoloading**: Extension autoloading via entry points (`pyproject.toml` `[project.entry-points."torch.backends"]`) enables out-of-tree backends without explicit import statements. *"Zero-code changes on out-of-tree devices."* [Source](https://docs.pytorch.org/tutorials/unstable/python_extension_autoload.md)

**GNES extras_require pattern**: Package-to-feature map in a plain text file, parsed into inverted index for `extras_require`. Users install `pip install package[bert]`. Tags compose (`bert`, `nlp`, `encode`). The `all` tag auto-generated. [Source](https://hanxiao.io/2019/11/07/A-Better-Practice-for-Managing-extras-require-Dependencies-in-Python/)

**graphistry/pygraphistry**: Real-world example of `base_extras_light` vs `base_extras_heavy` with CUDA-specific extras (RAPIDS stack). The `cudf-cu12` family pinned to specific CUDA versions. [Source](https://github.com/graphistry/pygraphistry/blob/master/setup.py)

**ultralytics/autoimport**: Lightweight lazy imports using `with lazy:` context manager. Modules import only when accessed. [Source](https://github.com/ultralytics/autoimport)

**Recommendation for this project**:
```
extras_require={
    "cuda": ["faster-whisper", "nvidia-cublas-cu12"],
    "onnx": ["onnxruntime", "silero-vad-onnx"],
    "tts": ["kokoro-onnx", "PocketTTS"],
    "all": [...]  # auto-generated from tag composition
}
```
NOT `install_requires`. Base package only needs `numpy`, `pydantic`, `websockets`. Everything else is optional.

### 4. Minimum Viable Surface Area for a Speech Pipeline Framework

**Pipecat's architecture (the most complete reference)**: Frame-based pipeline. Everything is a `Frame` (dataclass). `FrameProcessor` receives frames, pushes new ones. Pipeline connects processors in sequence. This is the FULL framework — 15811 files in repo. Overkill for your extraction target. [Source](https://github.com/pipecat-ai/pipecat)

**Spokestack SpeechPipeline**: Modular components with `SpeechContext` shared between stages. Much simpler than Pipecat. VAD sets `is_speech` to True/False on the context. Each stage fires events. Closer to what you already have. [Source](https://www.spokestack.io/docs/python/speech-pipeline)

**Minimum viable surface area for YOUR extraction**:

| Layer | What it is | Why |
|---|---|---|
| Protocol classes | `ASRProvider`, `TTSProvider`, `LLMProvider`, `VADProvider` | Already have these. Extract as-is. Pure ABCs. |
| Event types | `SpeechEvent`, `TranscriptEvent`, `LLMTokenEvent`, `TTSEvent` | Replace string-keyed dicts with typed dataclasses |
| Orchestrator | `SpeechPipeline` — connects providers, emits events | Extract conversation turn logic into state machine |
| Transport adapter | `Transport` protocol — WebSocket, CLI stdin/stdout, etc. | One class with `send_event()`/`receive_event()` |

**Do NOT extract**: the FastAPI app, the WebSocket handler, the Settings UI, the HTML/JS frontend, profile file management, character card parsing, or `RuntimeSettings`. All of these are application-layer concerns.

**Rich's lesson on API surface**: *"This core idea has two main parts. A Console object... And a protocol, which I can add to any object, to define how the output for this specific object looks like."* The small initial surface (Console + `__rich_console__` protocol) enabled massive composability later. [Source](https://dev.to/tim_sourcery/chatting-with-will-mcgugan-from-side-project-to-startup-4ik3)

### 5. Frontend Independence — Designing for Multiple Consumers

**The Pipecat model**: Transport is a `FrameProcessor` plugged into the pipeline. WebSocket, WebRTC, Daily.co, Twilio — all implement the same transport interface. The pipeline doesn't know or care how frames arrive or depart. [Source](https://github.com/pipecat-ai/pipecat)

**Current harness pattern**: The orchestrator already emits typed events. The WebSocket handler is in `main.py`. To make it transport-agnostic:

1. Define a `Transport` protocol with `async def send(event) -> None` and `async def receive() -> Event`
2. Implement `WebSocketTransport`, `StdioTransport`, `QueueTransport` (for testing)
3. The orchestrator takes a `Transport` at construction time
4. Each transport handles its own serialization (JSON over WebSocket, line-delimited JSON over stdio, etc.)

**Rich/Textual parallel**: Rich's `Console` object writes to any file-like object. The `file` parameter allows `sys.stdout`, `io.StringIO`, a network socket, or a file. Same pattern: the rendering engine doesn't know what's on the other end. [Source](https://github.com/Textualize/rich)

**Instructor's lesson**: *"Think of myself more like requests than an actual framework... you don't even think about installing it. And when you do install it, you don't think of it as like, oh, this is a requests app."* The library should have zero opinions about how it's consumed. [Source](https://www.latent.space/p/instructor)

### 6. Real-World Examples of Successful Core Extraction

**Rich (extracted from earlier tool)**: McGugan had a messy terminal formatting class in another project. Instead of extracting it, he rewrote the core idea from scratch as a library. Start small (colored text + word wrap), iterate with community feedback. Progress bars, tables, markdown — all added later by user demand. Now pip uses Rich internally (40K+ stars). [Source](https://dev.to/tim_sourcery/chatting-with-will-mcgugan-from-side-project-to-startup-4ik3)

**Textual (extracted from Rich)**: Built on Rich as a dependency ("Textual for the dynamic stuff, Rich for the rendering"). Started as a messy weekend prototype, got cleaned up after funding. Kept both libraries separate — Rich stayed focused on static terminal output. *"If I added too much to it, it runs the risk of being too big. People would just look at it and think: Well, it does way more than I could possibly need."* [Source](https://dev.to/tim_sourcery/chatting-with-will-mcgugan-from-side-project-to-startup-4ik3)

**Instructor (Jason Liu)**: Extracted from Stitch Fix's internal use of OpenAI function calling + Pydantic. Started as a single-file module. Stayed narrow: *"I want to be like requests, not Django."* Deliberately rejected feature creep. 6M+ monthly downloads, 11K+ stars, cited by OpenAI. [Source](https://www.latent.space/p/instructor)

**Shopify's Strangler Fig refactoring**: For extracting store settings from the Shop monolith: (1) Created a `SettingsDeprecation` concern to log when the old path was accessed, (2) Moved settings behind a facade, (3) Migrated consumers one at a time, (4) Removed old paths when telemetry showed zero traffic. *"We set up measurement to validate each piece before committing permanently."* [Source](https://shopify.engineering/refactoring-legacy-code-strangler-fig-pattern)

**GNES (Han Xiao)**: Extracted optional AI dependencies into an inverted-index system. Each package declared feature tags. Users installed `pip install gnes[bert]` or `pip install gnes[all]`. The key insight: *"A package can enable multiple features... the same feature can be enabled by different packages."* [Source](https://hanxiao.io/2019/11/07/A-Better-Practice-for-Managing-extras-require-Dependencies-in-Python/)

---

## Recommendations: Best Effort-to-Value Extraction Scope

### Phase 1: Extract Now (1-2 weeks, highest leverage)

1. **Provider Protocols** — Extract `ASRProvider`, `TTSProvider`, `LLMProvider`, `VADProvider` into a `speech_framework/providers.py`. Keep them as ABCs. All existing harness providers become concrete implementations.

2. **Event Types** — Define typed dataclasses for all events the orchestrator emits: `SpeechStarted`, `SpeechEnded`, `TranscriptPartial`, `TranscriptFinal`, `LLMToken`, `LLMComplete`, `TTSAudio`, `TTSSpeaking`, `TTSDone`, `Error`. This replaces the ad-hoc dicts and string event types.

3. **Orchestrator Core** — Extract `ConversationOrchestrator` into `SpeechPipeline` (or similar name). It takes providers via constructor, emits events via async generator or callback. The turn logic, barge-in handling, VAD noise filtering — all stay here.

4. **Transport Protocol** — Define `Transport` ABC with `send()`/`receive()`. The harness's WebSocket handler becomes `WebSocketTransport` implementing this protocol.

### Phase 2: Extract Next (2-3 weeks, enables ecosystem)

5. **Optional dependency structure** — `pyproject.toml` with `extras_require` groups: `[cuda]`, `[onnx]`, `[tts]`, `[faster-whisper]`, `[all]`. Base package installs with zero ML dependencies.

6. **Provider auto-discovery** — Lazy import pattern: `_has_cuda = False; try: import torch.cuda; _has_cuda = True; except ImportError: pass`. Provider registry via `@register_provider` decorator or entry points.

7. **CLI consumer** — First second consumer. `speech-cli --profile dev` starts a local speech pipeline over stdio. Proves the extraction works.

### Do NOT Extract (app layer, stays in harness)

- FastAPI app, WebSocket endpoints, REST API
- HTML/JS frontend, Settings UI
- Profile file management, `RuntimeSettings`
- Character card parsing, TavernAI support
- `start.ps1` / `doctor.ps1` scripts
- `config.py`, `DEFAULT_PROFILE`

### Evidence for This Scope

- **Rich**: Started with `Console` + `__rich_console__` protocol. Two concepts. Everything grew from there. [Source](https://dev.to/tim_sourcery/chatting-with-will-mcgugan-from-side-project-to-startup-4ik3)
- **Instructor**: Started with one function: `response_model` parameter on OpenAI client. 6M downloads later, still the same API shape. [Source](https://www.latent.space/p/instructor)
- **Pipecat**: Full framework with 15811 files. They started smaller than this. [Source](https://github.com/pipecat-ai/pipecat)
- **Spokestack**: `SpeechPipeline` with `SpeechContext` shared between stages. Much closer to your current architecture. [Source](https://www.spokestack.io/docs/python/speech-pipeline)

---

## Sources

- **Kept**: Will McGugan Interview (DEV.to) — Primary source on Rich's extraction from earlier project, API design philosophy, SemVer practice, Textual split from Rich
- **Kept**: Latent Space Podcast with Jason Liu — Primary source on Instructor's origin, extraction from Stitch Fix, "requests not Django" philosophy
- **Kept**: Martin Fowler — Strangler Fig Application — Foundational pattern for incremental extraction
- **Kept**: Shopify Engineering — Real Strangler Fig refactoring example with measurement
- **Kept**: Han Xiao / GNES extras_require — Production pattern for optional AI dependencies
- **Kept**: PyTorch CUDA lazy init source — Reference implementation of lazy GPU dependency loading
- **Kept**: Pipecat README and docs — Reference architecture for speech pipeline frameworks
- **Kept**: Spokestack SpeechPipeline — Lighter-weight reference architecture
- **Kept**: scientific-python/lazy-loader — Mature lazy import library from SciPy ecosystem
- **Kept**: graphistry/pygraphistry setup.py — Real CUDA extras_require in production
- **Dropped**: Several Strangler Fig blog posts — Redundant, all quote Fowler
- **Dropped**: Atom Audio Engine — Too immature, fewer lessons
- **Dropped**: VoiceCore — Similar to Spokestack but less mature
- **Dropped**: physicsnemo pyproject.toml — Interesting but not directly transferable pattern

## Gaps

1. **No existing pure Protocol-class speech framework to reference** — Pipecat is frame-based (heavier), Spokestack uses shared context. Your Protocol → Orchestrator → Event bus pattern is novel and possibly better than both. Worth prototyping before committing.

2. **Provider lazy import in practice** — PyTorch's pattern works for one framework. For 4+ providers (faster-whisper, Silero ONNX, llama.cpp, Pocket TTS, Kokoro), the import-time cost of probing each needs measurement.

3. **Event bus vs async generator** — The Pipecat frame pipeline is push-based. Your current orchestrator uses async callbacks. Both can work. Decision affects how backpressure and barge-in cancellation work.

4. **Test strategy for extraction** — Canned audio tests for VAD/ASR exist in the harness. Do they move to the framework or stay as integration tests? Need decision.

## Supervisor Coordination

No blocking decisions needed. The research is complete and actionable. Phase 1 extraction scope (Protocols + Events + Orchestrator + Transport) is the clear winner based on Rich/Instructor evidence: smallest surface, highest leverage, proven to work.
