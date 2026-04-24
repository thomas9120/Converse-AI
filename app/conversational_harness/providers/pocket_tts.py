from __future__ import annotations

import asyncio
import threading
import time
from typing import AsyncIterator

from conversational_harness.audio import float_audio_to_wav_bytes
from conversational_harness.providers.base import AudioChunk, ProgressCallback, ProviderCapabilities, ProviderStatus, TTSProvider


class PocketTTSProvider(TTSProvider):
    def __init__(self, config: dict):
        self.voice = str(config.get("voice", "azelma"))
        self.language = config.get("language")
        self.temp = float(config.get("temp", 0.7))
        self.max_tokens = int(config.get("max_tokens", 50))
        self.quantize = bool(config.get("quantize", False))
        self._model = config.get("_model")
        self._voice_state = config.get("_voice_state")
        self._load_error: str | None = None
        self._lock = threading.Lock()

    @property
    def status(self) -> ProviderStatus:
        if self._load_error:
            return ProviderStatus(
                name="pocket-tts",
                kind="tts",
                ready=False,
                message=f"Pocket TTS failed to load: {self._load_error}",
                capabilities=ProviderCapabilities(supports_streaming_tts=True),
            )
        if self._model is not None and self._voice_state is not None:
            message = f"Loaded Pocket TTS voice '{self.voice}'."
        else:
            message = f"Configured for Pocket TTS voice '{self.voice}'. Model and voice load on first TTS request."
        return ProviderStatus(
            name="pocket-tts",
            kind="tts",
            ready=True,
            message=message,
            capabilities=ProviderCapabilities(supports_streaming_tts=True, languages=("en",)),
        )

    async def check_status(self) -> ProviderStatus:
        try:
            import pocket_tts  # noqa: F401
        except Exception as exc:
            self._load_error = str(exc)
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
                    started = time.perf_counter()
                    self._emit_progress(loop, progress, "loading", f"Loading Pocket TTS voice '{self.voice}'.")
                    self._ensure_model()
                    self._emit_progress(
                        loop,
                        progress,
                        "loaded",
                        f"Pocket TTS ready after {round(time.perf_counter() - started, 1)}s.",
                    )
                    self._emit_progress(loop, progress, "generating", "Generating speech.")
                    chunks = self._model.generate_audio_stream(
                        self._voice_state,
                        text,
                        max_tokens=self.max_tokens,
                        copy_state=True,
                    )
                    for index, audio in enumerate(chunks):
                        wav_bytes = float_audio_to_wav_bytes(audio, self._model.sample_rate)
                        if wav_bytes:
                            self._emit_progress(loop, progress, "chunk", f"Generated audio chunk {index + 1}.")
                            asyncio.run_coroutine_threadsafe(
                                queue.put(AudioChunk(wav_bytes, "audio/wav", final=False)),
                                loop,
                            )
                    self._emit_progress(loop, progress, "complete", "TTS complete.")
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
        if self._model is not None and self._voice_state is not None:
            return
        from pocket_tts import TTSModel

        if self._model is None:
            kwargs = {"temp": self.temp, "quantize": self.quantize}
            if self.language:
                kwargs["language"] = self.language
            self._model = TTSModel.load_model(**kwargs)
        if self._voice_state is None:
            self._voice_state = self._model.get_state_for_audio_prompt(self.voice)

    def _emit_progress(self, loop: asyncio.AbstractEventLoop, progress: ProgressCallback | None, stage: str, message: str) -> None:
        if not progress:
            return
        loop.call_soon_threadsafe(asyncio.create_task, progress("tts.progress", {"stage": stage, "message": message}))
