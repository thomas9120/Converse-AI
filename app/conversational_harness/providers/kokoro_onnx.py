from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import AsyncIterator

import httpx

from conversational_harness.audio import float_audio_to_pcm_s16le_bytes
from conversational_harness.config import PROJECT_ROOT
from conversational_harness.providers.base import AudioChunk, ProgressCallback, ProviderCapabilities, ProviderStatus, TTSProvider


DEFAULT_KOKORO_MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
    "kokoro-v1.0.int8.onnx"
)
DEFAULT_KOKORO_VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
    "voices-v1.0.bin"
)


class KokoroOnnxProvider(TTSProvider):
    def __init__(self, config: dict):
        self.voice = str(config.get("voice", "af_heart"))
        self.lang = str(config.get("lang", "en-us"))
        self.speed = float(config.get("speed", 1.0))
        self.trim = bool(config.get("trim", True))
        self.timeout_s = float(config.get("timeout_s", 300))
        self.cache_dir = Path(str(config.get("cache_dir", PROJECT_ROOT / "model-cache" / "kokoro")))
        self.model_filename = str(config.get("model_filename", "kokoro-v1.0.int8.onnx"))
        self.voices_filename = str(config.get("voices_filename", "voices-v1.0.bin"))
        self.model_url = str(config.get("model_url", DEFAULT_KOKORO_MODEL_URL))
        self.voices_url = str(config.get("voices_url", DEFAULT_KOKORO_VOICES_URL))
        self._model = config.get("_model")
        self._load_error: str | None = None
        self._lock = threading.Lock()

    @property
    def status(self) -> ProviderStatus:
        if self._load_error:
            return ProviderStatus(
                name="kokoro-onnx",
                kind="tts",
                ready=False,
                message=f"Kokoro ONNX failed to load: {self._load_error}",
                capabilities=ProviderCapabilities(supports_streaming_tts=False, languages=("en",)),
                provider_id="kokoro-onnx",
                loaded=False,
                supports_model_management=True,
                supports_voice_selection=True,
            )

        message = (
            f"Loaded Kokoro v1.0 ONNX voice '{self.voice}' ({self.lang})."
            if self._model is not None
            else f"Configured for Kokoro v1.0 ONNX voice '{self.voice}' ({self.lang}). Model loads on first TTS request."
        )
        return ProviderStatus(
            name="kokoro-onnx",
            kind="tts",
            ready=True,
            message=message,
            capabilities=ProviderCapabilities(supports_streaming_tts=False, languages=("en",)),
            provider_id="kokoro-onnx",
            loaded=self._model is not None,
            supports_model_management=True,
            supports_voice_selection=True,
        )

    async def check_status(self) -> ProviderStatus:
        try:
            import kokoro_onnx  # noqa: F401
        except Exception as exc:
            self._load_error = str(exc)
        return self.status

    async def load(self) -> ProviderStatus:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._ensure_model)
        return self.status

    async def unload(self) -> ProviderStatus:
        def release() -> None:
            with self._lock:
                self._model = None
                self._load_error = None

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, release)
        return self.status

    async def stream_audio(self, text: str) -> AsyncIterator[AudioChunk]:
        async for chunk in self.stream_audio_with_progress(text):
            yield chunk

    async def stream_audio_with_progress(
        self, text: str, progress: ProgressCallback | None = None
    ) -> AsyncIterator[AudioChunk]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[AudioChunk | Exception | None] = asyncio.Queue()

        def worker() -> None:
            try:
                with self._lock:
                    self._emit_progress(loop, progress, "loading", f"Loading Kokoro voice '{self.voice}'.")
                    self._ensure_model()
                    self._emit_progress(loop, progress, "loaded", "Kokoro ready.")
                    self._emit_progress(loop, progress, "generating", "Generating speech.")
                    audio, sample_rate = self._model.create(
                        text,
                        voice=self.voice,
                        speed=self.speed,
                        lang=self.lang,
                        trim=self.trim,
                    )
                    pcm_bytes = float_audio_to_pcm_s16le_bytes(audio)
                    self._emit_progress(loop, progress, "complete", "TTS complete.")
                    asyncio.run_coroutine_threadsafe(
                        queue.put(
                            AudioChunk(
                                pcm_bytes,
                                sample_rate=sample_rate,
                                channels=1,
                                encoding="pcm_s16le",
                                duration_ms=int((len(pcm_bytes) // 2) * 1000 / sample_rate) if sample_rate else None,
                                final=True,
                            )
                        ),
                        loop,
                    )
                    asyncio.run_coroutine_threadsafe(queue.put(None), loop)
            except Exception as exc:
                self._load_error = str(exc)
                asyncio.run_coroutine_threadsafe(queue.put(exc), loop)

        threading.Thread(target=worker, daemon=True).start()

        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        model_path = self._download_asset(self.model_url, self.model_filename)
        voices_path = self._download_asset(self.voices_url, self.voices_filename)
        from kokoro_onnx import Kokoro

        self._model = Kokoro(str(model_path), str(voices_path))
        self._load_error = None

    def _download_asset(self, url: str, filename: str) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / filename
        if target.exists():
            return target
        temp = target.with_suffix(target.suffix + ".part")
        with httpx.Client(follow_redirects=True, timeout=self.timeout_s) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with temp.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        if chunk:
                            handle.write(chunk)
        temp.replace(target)
        return target

    def _emit_progress(
        self,
        loop: asyncio.AbstractEventLoop,
        progress: ProgressCallback | None,
        stage: str,
        message: str,
    ) -> None:
        if not progress:
            return
        loop.call_soon_threadsafe(asyncio.create_task, progress("tts.progress", {"stage": stage, "message": message}))
