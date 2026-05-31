import base64
import json

import pytest
from fastapi.testclient import TestClient

import conversational_harness.main as main
from conversational_harness.providers.factory import ProviderBundle
from conversational_harness.providers.mock import MockASRProvider, MockTTSProvider, MockVADProvider
from conversational_harness.runtime_settings import MemoryStore, RuntimeSettings


@pytest.fixture(autouse=True)
def isolated_runtime_settings(monkeypatch):
    original = main.RUNTIME_SETTINGS
    main.RUNTIME_SETTINGS = RuntimeSettings(
        profile_defaults=dict(original.profile_defaults),
        server_defaults=dict(original.server_defaults),
    )
    original_memory = main.MEMORY_STORE
    main.MEMORY_STORE = MemoryStore(original_memory.path.parent / "test-memory.md")
    main.MEMORY_STORE.clear()

    async def broadcast_noop():
        return None

    monkeypatch.setattr(main, "save_runtime_settings", lambda settings: None)
    monkeypatch.setattr(main, "broadcast_settings", broadcast_noop)
    yield
    main.MEMORY_STORE.clear()
    main.MEMORY_STORE = original_memory
    main.RUNTIME_SETTINGS = original


@pytest.fixture
def client():
    return TestClient(main.app)


def encode_json_card(card: dict) -> str:
    return base64.b64encode(json.dumps(card).encode("utf-8")).decode("ascii")


def test_direct_json_character_import_succeeds(client):
    response = client.post(
        "/api/settings/character",
        json={
            "character": {
                "name": "Safety Bot",
                "description": "Keeps the harness steady.",
                "first_mes": "Boot sequence ready.",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["character"]["name"] == "Safety Bot"
    assert response.json()["character"]["first_mes"] == "Boot sequence ready."


def test_valid_json_character_upload_succeeds(client):
    response = client.post(
        "/api/settings/character/upload",
        json={
            "filename": "safety-bot.json",
            "data": encode_json_card(
                {"name": "Safety Bot", "personality": "Careful", "first_mes": "All checks are green."}
            ),
        },
    )

    assert response.status_code == 200
    assert response.json()["character"]["name"] == "Safety Bot"
    assert response.json()["character"]["first_mes"] == "All checks are green."


def test_character_upload_rejects_invalid_base64(client):
    response = client.post(
        "/api/settings/character/upload",
        json={"filename": "bad.json", "data": "not base64!"},
    )

    assert response.status_code == 400
    assert "base64" in response.json()["detail"]


def test_character_upload_rejects_oversized_payload(client):
    oversized = base64.b64encode(b"x" * (main.MAX_CHARACTER_UPLOAD_BYTES + 1)).decode("ascii")

    response = client.post(
        "/api/settings/character/upload",
        json={"filename": "too-large.json", "data": oversized},
    )

    assert response.status_code == 400
    assert "too large" in response.json()["detail"]


def test_character_upload_rejects_malformed_json(client):
    malformed = base64.b64encode(b'{"name":').decode("ascii")

    response = client.post(
        "/api/settings/character/upload",
        json={"filename": "bad.json", "data": malformed},
    )

    assert response.status_code == 400
    assert response.json()["detail"].startswith("Invalid character card")


def test_memory_api_read_write_and_delete(client):
    response = client.get("/api/companion/memory")
    assert response.status_code == 200
    assert response.json()["text"] == ""

    response = client.put("/api/companion/memory", json={"text": "User likes quiet mornings."})
    assert response.status_code == 200
    assert "quiet mornings" in response.json()["text"]

    response = client.delete("/api/companion/memory")
    assert response.status_code == 200
    assert response.json()["text"] == ""


def test_memory_summarize_uses_companion_history(client, monkeypatch):
    class SummaryLLM:
        def set_runtime_settings(self, settings):
            self.settings = settings

        async def stream_response(self, messages):
            yield "- User likes concise memory."

    monkeypatch.setattr(
        main,
        "build_provider_bundle",
        lambda *args, **kwargs: ProviderBundle(
            MockVADProvider({}),
            MockASRProvider({}),
            SummaryLLM(),
            MockTTSProvider({}),
        ),
    )
    main.COMPANION_HISTORY_HOOKS.add(lambda: [{"role": "user", "content": "Remember concise memory."}])
    try:
        response = client.post("/api/companion/memory/summarize", json={})
    finally:
        main.COMPANION_HISTORY_HOOKS.clear()

    assert response.status_code == 200
    assert "User likes concise memory" in response.json()["text"]


def test_memory_summarize_accepts_posted_companion_transcript(client, monkeypatch):
    class SummaryLLM:
        def set_runtime_settings(self, settings):
            self.settings = settings

        async def stream_response(self, messages):
            assert {"role": "user", "content": "Visible companion text."} in messages
            yield "- Visible companion text should be remembered."

    monkeypatch.setattr(
        main,
        "build_provider_bundle",
        lambda *args, **kwargs: ProviderBundle(
            MockVADProvider({}),
            MockASRProvider({}),
            SummaryLLM(),
            MockTTSProvider({}),
        ),
    )

    response = client.post(
        "/api/companion/memory/summarize",
        json={
            "messages": [
                {"role": "system", "content": "Memory summarized"},
                {"role": "user", "content": "Visible companion text."},
            ]
        },
    )

    assert response.status_code == 200
    assert "Visible companion text should be remembered" in response.json()["text"]
