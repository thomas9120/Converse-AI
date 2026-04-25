import base64
import json

import pytest
from fastapi.testclient import TestClient

import conversational_harness.main as main
from conversational_harness.runtime_settings import RuntimeSettings


@pytest.fixture(autouse=True)
def isolated_runtime_settings(monkeypatch):
    original = main.RUNTIME_SETTINGS
    main.RUNTIME_SETTINGS = RuntimeSettings(
        profile_defaults=dict(original.profile_defaults),
        server_defaults=dict(original.server_defaults),
    )

    async def broadcast_noop():
        return None

    monkeypatch.setattr(main, "save_runtime_settings", lambda settings: None)
    monkeypatch.setattr(main, "broadcast_settings", broadcast_noop)
    yield
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
