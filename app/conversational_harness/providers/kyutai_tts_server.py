from __future__ import annotations

import base64
from typing import AsyncIterator

import httpx

from conversational_harness.providers.base import AudioChunk, ProgressCallback, ProviderCapabilities, ProviderStatus, TTSProvider


class KyutaiTTSServerProvider(TTSProvider):
    """HTTP adapter for a local Kyutai TTS wrapper service.

    The official Kyutai Rust server uses a websocket protocol. This provider is
    intentionally a thin boundary for local wrappers that expose a simple HTTP
    synthesis endpoint while we keep the harness orchestration provider-neutral.
    """

    def __init__(self, config: dict):
        self.base_url = str(config.get("base_url", "http://127.0.0.1:8998")).rstrip("/")
        self.health_path = str(config.get("health_path", "/health"))
        self.synthesize_path = str(config.get("synthesize_path", "/tts"))
        self.model = str(config.get("model", "kyutai/tts-1.6b-en_fr"))
        self.voice = str(config.get("voice", "default"))
        self.timeout_s = float(config.get("timeout_s", 120))
        self._last_error: str | None = None

    @property
    def status(self) -> ProviderStatus:
        ready = self._last_error is None
        message = (
            f"Configured for Kyutai TTS HTTP server at {self.base_url} using {self.model}."
            if ready
            else f"Kyutai TTS HTTP server is not ready: {self._last_error}"
        )
        return ProviderStatus(
            name="kyutai-tts-server",
            kind="tts",
            ready=ready,
            message=message,
            capabilities=ProviderCapabilities(
                supports_streaming_tts=False,
                supports_barge_in=True,
                requires_gpu=self.model != "kyutai/tts-0.75b-en-public",
                languages=("en", "fr"),
            ),
        )

    async def check_status(self) -> ProviderStatus:
        url = f"{self.base_url}{self.health_path}"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(url)
                response.raise_for_status()
            self._last_error = None
        except Exception as exc:
            detail = str(exc) or exc.__class__.__name__
            self._last_error = f"{url} failed: {detail}"
        return self.status

    async def stream_audio(self, text: str) -> AsyncIterator[AudioChunk]:
        async for chunk in self.stream_audio_with_progress(text):
            yield chunk

    async def stream_audio_with_progress(
        self, text: str, progress: ProgressCallback | None = None
    ) -> AsyncIterator[AudioChunk]:
        if progress:
            await progress("tts.progress", {"stage": "requesting", "message": "Requesting Kyutai TTS audio."})

        payload = {"text": text, "model": self.model, "voice": self.voice}
        url = f"{self.base_url}{self.synthesize_path}"
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

        if progress:
            await progress("tts.progress", {"stage": "received", "message": "Received Kyutai TTS audio."})

        content_type = response.headers.get("content-type", "audio/wav").split(";")[0].strip()
        if content_type.startswith("audio/"):
            yield AudioChunk(response.content, content_type, final=True)
            return

        data = response.json()
        mime_type = str(data.get("mime_type", "audio/wav"))
        encoded = data.get("audio_base64") or data.get("data")
        if not isinstance(encoded, str):
            raise RuntimeError("Kyutai TTS HTTP response must be audio/* bytes or JSON with audio_base64.")
        yield AudioChunk(base64.b64decode(encoded), mime_type, final=True)
