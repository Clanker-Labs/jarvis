import pytest
from fastapi.testclient import TestClient
from jarvis_brain.app import create_app
from jarvis_brain.runtime import Runtime
from jarvis_brain.settings import Settings, is_safe_bind
from jarvis_protocol import Hello, State, dump_event, parse_event
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def settings(tmp_path):
    return Settings(JARVIS_DATA_DIR=tmp_path, JARVIS_NODE_TOKEN="t0k", JARVIS_ENV="test")


def test_health(settings):
    with TestClient(create_app(Runtime(settings))) as c:
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok" and body["state"] == State.IDLE


def test_ui_ws_requires_origin_and_token(settings):
    with TestClient(create_app(Runtime(settings))) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws/ui?token=t0k", headers={"origin": "http://evil"}) as ws:
                ws.receive_text()
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect(
                "/ws/ui?token=bad", headers={"origin": "http://127.0.0.1:8080"}
            ) as ws:
                ws.receive_text()
        with c.websocket_connect(
            "/ws/ui?token=t0k", headers={"origin": "http://127.0.0.1:8080"}
        ) as ws:
            first = parse_event(ws.receive_text())
            assert first.type == "ui.snapshot"


def test_node_ws_handshake(settings):
    rt = Runtime(settings)
    with TestClient(create_app(rt)) as c:
        with c.websocket_connect("/ws/node", headers={"authorization": "Bearer t0k"}) as ws:
            ws.send_text(dump_event(Hello(role="audio-node")))
            assert c.get("/health").json()["audio_node"] is True


@pytest.mark.parametrize(
    "host,ok",
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("100.118.180.66", True),
        ("::1", True),
        ("0.0.0.0", False),
        ("192.168.1.10", False),
        ("example.com", False),
    ],
)
def test_bind_guard(host, ok):
    assert is_safe_bind(host, in_container=False) is ok


def test_bind_guard_container_allows_unspecified():
    assert is_safe_bind("0.0.0.0", in_container=True)
