"""deploy/ui-server.py: brain URL follows the page's host; unknown hosts are refused."""

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def ui(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("ui_server", ROOT / "deploy/ui-server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setenv("TAILNET_HOSTS", "100.101.102.103,jarvis-iot")
    monkeypatch.setenv("JARVIS_NODE_TOKEN", "t0k")
    monkeypatch.delenv("BRAIN_WS_URL", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(mod.Handler, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield mod, server.server_address[1]
    server.shutdown()


def get(port: int, path: str, host: str):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": host})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def test_brain_url_follows_host(ui):
    _, port = ui
    for host, expected in [
        ("127.0.0.1:8080", "ws://127.0.0.1:8000/ws/ui"),
        ("jarvis-iot:8080", "ws://jarvis-iot:8000/ws/ui"),
        ("100.101.102.103:8080", "ws://100.101.102.103:8000/ws/ui"),
    ]:
        status, body = get(port, "/config.json", host)
        assert status == 200
        assert json.loads(body) == {"brainUrl": expected, "token": "t0k"}


def test_unknown_host_refused(ui):
    _, port = ui
    assert get(port, "/config.json", "evil.example:8080")[0] == 421
    assert get(port, "/", "evil.example")[0] == 421
    assert get(port, "/", "localhost:8080")[0] == 200


def test_explicit_brain_url_wins(ui, monkeypatch):
    _, port = ui
    monkeypatch.setenv("BRAIN_WS_URL", "ws://100.1.2.3:8000/ws/ui")
    assert json.loads(get(port, "/config.json", "jarvis-iot")[1])["brainUrl"] == (
        "ws://100.1.2.3:8000/ws/ui"
    )
