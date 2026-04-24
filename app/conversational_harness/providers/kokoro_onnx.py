from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
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
        self._g2p = config.get("_g2p")
        self._load_error: str | None = None
        self._g2p_error: str | None = None
        self._lock = threading.Lock()
        self._generation_lock = asyncio.Lock()

    @property
    def status(self) -> ProviderStatus:
        if self._load_error:
            return ProviderStatus(
                name="kokoro-onnx",
                kind="tts",
                ready=False,
                message=f"Kokoro ONNX failed to load: {self._load_error}",
                capabilities=ProviderCapabilities(supports_streaming_tts=True, languages=("en",)),
                provider_id="kokoro-onnx",
                loaded=False,
                supports_model_management=True,
                supports_voice_selection=True,
            )
        if self._g2p_error:
            return ProviderStatus(
                name="kokoro-onnx",
                kind="tts",
                ready=False,
                message=f"Kokoro English G2P failed: {self._g2p_error}",
                capabilities=ProviderCapabilities(supports_streaming_tts=True, languages=("en",)),
                provider_id="kokoro-onnx",
                loaded=self._model is not None,
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
            capabilities=ProviderCapabilities(supports_streaming_tts=True, languages=("en",)),
            provider_id="kokoro-onnx",
            loaded=self._model is not None,
            supports_model_management=True,
            supports_voice_selection=True,
        )

    async def check_status(self) -> ProviderStatus:
        try:
            import kokoro_onnx  # noqa: F401
            if self._should_use_misaki():
                from misaki import en as _en  # noqa: F401
                from misaki import espeak as _espeak  # noqa: F401
        except Exception as exc:
            if self._should_use_misaki():
                self._g2p_error = str(exc)
            else:
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
                self._g2p = None
                self._g2p_error = None

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
        self._emit_progress(loop, progress, "loading", f"Loading Kokoro voice '{self.voice}'.")
        await loop.run_in_executor(None, self._ensure_model)
        self._emit_progress(loop, progress, "loaded", "Kokoro ready.")

        async with self._generation_lock:
            stream_text = text
            is_phonemes = False
            if self._should_use_misaki():
                self._emit_progress(loop, progress, "phonemizing", "Preparing English phonemes with Misaki.")
                stream_text = await loop.run_in_executor(None, self._phonemize_english, text)
                is_phonemes = True

            self._emit_progress(loop, progress, "generating", "Generating speech.")
            index = 0
            previous_chunk: AudioChunk | None = None
            async for audio, sample_rate in self._model.create_stream(
                stream_text,
                voice=self.voice,
                speed=self.speed,
                lang=self.lang,
                is_phonemes=is_phonemes,
                trim=self.trim,
            ):
                pcm_bytes = float_audio_to_pcm_s16le_bytes(audio)
                if not pcm_bytes:
                    continue
                index += 1
                current_chunk = AudioChunk(
                    pcm_bytes,
                    sample_rate=sample_rate,
                    channels=1,
                    encoding="pcm_s16le",
                    duration_ms=int((len(pcm_bytes) // 2) * 1000 / sample_rate) if sample_rate else None,
                    final=False,
                )
                self._emit_progress(loop, progress, "chunk", f"Generated audio chunk {index}.")
                if previous_chunk is not None:
                    yield previous_chunk
                previous_chunk = current_chunk

            if previous_chunk is not None:
                yield replace(previous_chunk, final=True)
            self._emit_progress(loop, progress, "complete", "TTS complete.")

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        model_path = self._download_asset(self.model_url, self.model_filename)
        voices_path = self._download_asset(self.voices_url, self.voices_filename)
        from kokoro_onnx import Kokoro

        self._model = Kokoro(str(model_path), str(voices_path))
        self._load_error = None
        self._g2p_error = None

    def _ensure_g2p(self):
        if self._g2p is not None:
            return self._g2p
        british = self._use_british_english()
        from misaki import en, espeak

        self._g2p = en.G2P(
            trf=False,
            british=british,
            fallback=espeak.EspeakFallback(british=british),
        )
        self._g2p_error = None
        return self._g2p

    def _phonemize_english(self, text: str) -> str:
        try:
            g2p = self._ensure_g2p()
            phonemes, _tokens = g2p(text)
            return str(phonemes).strip()
        except Exception as exc:
            self._g2p_error = str(exc)
            raise RuntimeError(f"Misaki English phonemization failed: {exc}") from exc

    def _should_use_misaki(self) -> bool:
        return self.lang.lower().startswith("en")

    def _use_british_english(self) -> bool:
        lang = self.lang.lower()
        return lang.startswith("en-gb") or self.voice.lower().startswith("b")

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
