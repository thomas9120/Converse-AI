from pathlib import Path
from types import SimpleNamespace

from conversational_harness import launch
from conversational_harness.doctor import Check


def test_list_profiles_includes_project_profiles():
    profiles = launch.list_profiles()

    assert any(profile.name == "mock-local.json" for profile in profiles)
    assert all(profile.name != "tts-presets.json" for profile in profiles)
    assert profiles == sorted(profiles)


def test_choose_profile_uses_explicit_profile():
    profile = launch.choose_profile("profiles/mock-local.json", launch.list_profiles(), interactive=True)

    assert profile.name == "mock-local.json"


def test_maybe_open_browser_skips_when_disabled(monkeypatch):
    opened = []
    monkeypatch.setattr(launch.webbrowser, "open", lambda url: opened.append(url))
    args = SimpleNamespace(no_browser=True, open_browser=False)

    launch.maybe_open_browser("http://127.0.0.1:7860", args, interactive=True)

    assert opened == []


def test_maybe_open_browser_opens_when_requested(monkeypatch):
    opened = []
    monkeypatch.setattr(launch.webbrowser, "open", lambda url: opened.append(url))
    args = SimpleNamespace(no_browser=False, open_browser=True)

    launch.maybe_open_browser("http://127.0.0.1:7860", args, interactive=False)

    assert opened == ["http://127.0.0.1:7860"]


def test_main_list_profiles_exits_without_starting(monkeypatch, capsys):
    monkeypatch.setattr(launch, "start_server", lambda port, env: (_ for _ in ()).throw(AssertionError("started")))

    exit_code = launch.main(["--list-profiles"])

    assert exit_code == 0
    assert "profiles/mock-local.json" in capsys.readouterr().out


def test_main_stops_on_fatal_preflight(monkeypatch):
    monkeypatch.setattr(
        launch,
        "run_launch_checks",
        lambda config, port, skip_port_check=False: [Check("Harness port", False, "busy")],
    )
    monkeypatch.setattr(launch, "start_server", lambda port, env: (_ for _ in ()).throw(AssertionError("started")))

    exit_code = launch.main(["--profile", "profiles/mock-local.json", "--no-prompt", "--no-browser"])

    assert exit_code == 1


def test_main_noninteractive_starts_server_with_expected_env(monkeypatch):
    calls = {}

    class FakeProcess:
        def wait(self):
            return 0

        def poll(self):
            return 0

    def fake_start_server(port, env):
        calls["port"] = port
        calls["env"] = env
        return FakeProcess()

    monkeypatch.setattr(launch, "run_launch_checks", lambda config, port, skip_port_check=False: [])
    monkeypatch.setattr(launch, "start_server", fake_start_server)
    monkeypatch.setattr(launch, "wait_for_server", lambda url: True)
    monkeypatch.setattr(launch, "maybe_open_browser", lambda url, args, interactive: calls.setdefault("browser", False))

    exit_code = launch.main(
        ["--profile", "profiles/mock-local.json", "--port", "7861", "--no-prompt", "--no-browser"]
    )

    assert exit_code == 0
    assert calls["port"] == 7861
    assert Path(calls["env"]["HARNESS_PROFILE"]).name == "mock-local.json"
    assert calls["env"]["HARNESS_PORT"] == "7861"
    assert calls["browser"] is False
