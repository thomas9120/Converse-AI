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


def test_find_port_owner_parses_windows_netstat(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        doctor.subprocess,
        "check_output",
        lambda *args, **kwargs: "  TCP    127.0.0.1:7860    0.0.0.0:0    LISTENING    4321\r\n",
    )
    monkeypatch.setattr(doctor, "find_process_command", lambda pid: "python -m uvicorn app")

    owner = doctor.find_port_owner(7860)

    assert owner == {"pid": "4321", "command": "python -m uvicorn app"}


def test_check_port_available_reports_owner(monkeypatch):
    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def settimeout(self, timeout):
            return None

        def connect_ex(self, address):
            return 0

    monkeypatch.setattr(doctor.socket, "socket", lambda *args, **kwargs: FakeSocket())
    monkeypatch.setattr(doctor, "find_port_owner", lambda port: {"pid": "4321", "command": "python app"})

    check = doctor.check_port_available(7860)

    assert not check.ok
    assert "PID 4321" in check.detail
    assert "python app" in check.detail
