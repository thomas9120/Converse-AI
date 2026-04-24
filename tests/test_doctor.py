from conversational_harness import doctor


def test_llamacpp_not_selected():
    check = doctor.check_llamacpp({"provider": "mock"})

    assert check.ok
    assert "not selected" in check.detail


def test_llamacpp_unreachable(monkeypatch):
    monkeypatch.setattr(doctor, "fetch_json", lambda url, timeout: None)

    check = doctor.check_llamacpp({"provider": "llamacpp", "base_url": "http://127.0.0.1:9999"})

    assert not check.ok
    assert "not reachable" in check.detail


def test_llamacpp_loading(monkeypatch):
    monkeypatch.setattr(doctor, "fetch_json", lambda url, timeout: {"error": {"message": "Loading model"}})

    check = doctor.check_llamacpp({"provider": "llamacpp", "base_url": "http://127.0.0.1:8080"})

    assert not check.ok
    assert "not ready" in check.detail
    assert "Loading model" in check.detail


def test_llamacpp_health_ok_but_models_missing(monkeypatch):
    def fake_fetch(url, timeout):
        if url.endswith("/health"):
            return {"status": "ok"}
        return {"data": []}

    monkeypatch.setattr(doctor, "fetch_json", fake_fetch)

    check = doctor.check_llamacpp({"provider": "llamacpp", "base_url": "http://127.0.0.1:8080"})

    assert not check.ok
    assert "no model" in check.detail


def test_llamacpp_ready(monkeypatch):
    def fake_fetch(url, timeout):
        if url.endswith("/health"):
            return {"status": "ok"}
        return {"data": [{"id": "local-gguf"}]}

    monkeypatch.setattr(doctor, "fetch_json", fake_fetch)

    check = doctor.check_llamacpp({"provider": "llamacpp", "base_url": "http://127.0.0.1:8080"})

    assert check.ok
    assert "local-gguf" in check.detail


def test_llamacpp_configured_model_mismatch(monkeypatch):
    def fake_fetch(url, timeout):
        if url.endswith("/health"):
            return {"status": "ok"}
        return {"data": [{"id": "actual-model"}]}

    monkeypatch.setattr(doctor, "fetch_json", fake_fetch)

    check = doctor.check_llamacpp(
        {"provider": "llamacpp", "base_url": "http://127.0.0.1:8080", "model": "missing-model"}
    )

    assert not check.ok
    assert "not loaded" in check.detail
