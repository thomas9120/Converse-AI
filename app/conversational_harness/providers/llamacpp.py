from __future__ import annotations

import json
from typing import TYPE_CHECKING, AsyncIterator

import httpx

from conversational_harness.providers.base import (
    LLMProvider,
    ProviderCapabilities,
    ProviderStatus,
)

if TYPE_CHECKING:
    from conversational_harness.runtime_settings import RuntimeSettings


class LlamaCppProvider(LLMProvider):
    def __init__(self, config: dict):
        self.base_url = str(config.get("base_url", "http://127.0.0.1:8080")).rstrip("/")
        self.model = str(config.get("model", "auto"))
        self.temperature = float(config.get("temperature", 0.7))
        self.max_tokens = int(config.get("max_tokens", 256))
        self._runtime_settings: RuntimeSettings | None = None
        self._resolved_model: str | None = None

    def set_runtime_settings(self, settings: RuntimeSettings) -> None:
        self._runtime_settings = settings

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name="llama.cpp",
            kind="llm",
            ready=False,
            message=f"Configured for OpenAI-compatible llama.cpp server at {self.base_url}.",
            capabilities=ProviderCapabilities(),
        )

    async def check_status(self) -> ProviderStatus:
        timeout = httpx.Timeout(connect=1.0, read=2.0, write=1.0, pool=1.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                health = await client.get(f"{self.base_url}/health")
                health.raise_for_status()
                health_payload = health.json()
            except Exception as exc:
                return ProviderStatus(
                    name="llama.cpp",
                    kind="llm",
                    ready=False,
                    message=f"Cannot reach llama.cpp at {self.base_url}: {exc}",
                    capabilities=ProviderCapabilities(),
                )

            if health_payload.get("status") != "ok":
                message = health_payload.get("error", {}).get(
                    "message", "server did not report ready"
                )
                return ProviderStatus(
                    name="llama.cpp",
                    kind="llm",
                    ready=False,
                    message=f"llama.cpp reachable but not ready: {message}",
                    capabilities=ProviderCapabilities(),
                )

            try:
                models = await client.get(f"{self.base_url}/v1/models")
                models.raise_for_status()
                models_payload = models.json()
            except Exception as exc:
                return ProviderStatus(
                    name="llama.cpp",
                    kind="llm",
                    ready=False,
                    message=f"llama.cpp health OK, but /v1/models failed: {exc}",
                    capabilities=ProviderCapabilities(),
                )

        model_ids = [
            str(item.get("id", "unknown")) for item in models_payload.get("data", [])
        ]
        if not model_ids:
            return ProviderStatus(
                name="llama.cpp",
                kind="llm",
                ready=False,
                message="llama.cpp health OK, but no loaded model was reported by /v1/models.",
                capabilities=ProviderCapabilities(),
            )
        model_list = ", ".join(model_ids[:3])
        selected_model = self.model if self.model != "auto" else model_ids[0]
        if self.model != "auto" and self.model not in model_ids:
            return ProviderStatus(
                name="llama.cpp",
                kind="llm",
                ready=False,
                message=f"llama.cpp is ready, but configured model '{self.model}' is not in /v1/models. Loaded: {model_list}",
                capabilities=ProviderCapabilities(),
            )
        active = "auto-selected" if self.model == "auto" else "selected"
        return ProviderStatus(
            name="llama.cpp",
            kind="llm",
            ready=True,
            message=f"Ready at {self.base_url}; {active} model: {selected_model}; loaded: {model_list}",
            capabilities=ProviderCapabilities(),
        )

    async def stream_response(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        if self._resolved_model is None:
            self._resolved_model = await self._resolve_model()
        model = self._resolved_model
        sampler = self._build_sampler()
        payload: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        for key, value in sampler.items():
            payload[key] = value
        url = f"{self.base_url}/v1/chat/completions"
        timeout = httpx.Timeout(connect=3.0, read=60.0, write=10.0, pool=3.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content

    def _build_sampler(self) -> dict:
        defaults = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self._runtime_settings is not None:
            return self._runtime_settings.effective_sampler()
        return defaults

    async def _resolve_model(self) -> str:
        if self.model != "auto":
            return self.model
        timeout = httpx.Timeout(connect=1.0, read=2.0, write=1.0, pool=1.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{self.base_url}/v1/models")
            response.raise_for_status()
            payload = response.json()
        model_data = payload.get("data", [])
        if not model_data:
            raise RuntimeError(
                "llama.cpp did not report a loaded model from /v1/models"
            )
        return str(model_data[0].get("id", "unknown"))

    async def unload(self) -> ProviderStatus:
        return self.status
