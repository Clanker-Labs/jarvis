import pyotp
import pytest
from fastapi.testclient import TestClient

from jarvis_brain.admin.app import create_admin_app
from jarvis_brain.admin.auth import hash_password, verify_password
from jarvis_brain.orchestrator import Turn
from jarvis_brain.runtime import Runtime
from jarvis_brain.settings import Settings

H = {"X-Jarvis-Admin": "1"}
PW = "correct horse battery"


@pytest.fixture
def rt(tmp_path):
    s = Settings(
        JARVIS_DATA_DIR=tmp_path,
        JARVIS_ENV="test",
        JARVIS_BACKGROUND_TASKS=False,
        JARVIS_WARM_LOCAL_VOICE=False,
        ADMIN_BOOTSTRAP_PASSWORD=PW,
        JARVIS_SECRET_KEY="ZmFrZWtleWZha2VrZXlmYWtla2V5ZmFrZWtleWZha2U=",
    )
    return Runtime(s)


@pytest.fixture
def client(rt):
    return TestClient(create_admin_app(rt))


def signed_in(client: TestClient) -> pyotp.TOTP:
    """Log in with the bootstrap password and complete TOTP setup."""
    r = client.post("/api/auth/login", json={"password": PW}, headers=H)
    assert r.status_code == 200 and r.json()["needs_totp_setup"]
    secret = client.post("/api/auth/totp/setup", headers=H).json()["secret"]
    totp = pyotp.TOTP(secret)
    assert client.post("/api/auth/totp/enable", json={"code": totp.now()}, headers=H).status_code == 200
    return totp


def test_password_hashing():
    h = hash_password("hunter22hunter")
    assert verify_password("hunter22hunter", h) and not verify_password("nope", h)
    assert "hunter" not in h


def test_requires_login(client):
    assert client.get("/api/overview").status_code == 401
    assert client.get("/api/auth/state").json()["authenticated"] is False


def test_login_needs_csrf_header(client):
    assert client.post("/api/auth/login", json={"password": PW}).status_code == 403


def test_password_session_can_only_set_up_totp(client):
    client.post("/api/auth/login", json={"password": PW}, headers=H)
    r = client.get("/api/overview")
    assert r.status_code == 403 and r.json()["detail"] == "totp_setup_required"


def test_full_login_flow_with_totp(client):
    totp = signed_in(client)
    assert client.get("/api/overview").status_code == 200
    client.post("/api/auth/logout", headers=H)
    assert client.get("/api/overview").status_code == 401
    r = client.post("/api/auth/login", json={"password": PW}, headers=H)
    assert r.status_code == 401 and r.json()["detail"] == "totp_required"
    r = client.post("/api/auth/login", json={"password": PW, "code": "000000"}, headers=H)
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"password": PW, "code": totp.now()}, headers=H)
    assert r.status_code == 200
    assert client.get("/api/overview").status_code == 200


def test_mutations_need_csrf_header(client):
    signed_in(client)
    assert client.put("/api/config/voice", json={"chime": False}).status_code == 403
    assert client.put("/api/config/voice", json={"chime": False}, headers=H).status_code == 200


def test_rate_limit_wrong_password(client):
    for _ in range(5):
        client.post("/api/auth/login", json={"password": "wrong"}, headers=H)
    r = client.post("/api/auth/login", json={"password": PW}, headers=H)
    assert r.status_code == 429


def test_secrets_are_write_only(client, rt):
    signed_in(client)
    r = client.put("/api/secrets/ANTHROPIC_API_KEY", json={"value": "sk-ant-secret"}, headers=H)
    assert r.status_code == 200
    listing = client.get("/api/secrets")
    assert "sk-ant-secret" not in listing.text
    row = next(s for s in listing.json() if s["name"] == "ANTHROPIC_API_KEY")
    assert row["source"] == "db"
    assert rt.secrets.get("ANTHROPIC_API_KEY") == "sk-ant-secret"
    # Encrypted at rest.
    raw = rt.db.one("SELECT value FROM secrets WHERE name='ANTHROPIC_API_KEY'")["value"]
    assert b"sk-ant-secret" not in raw
    assert client.put("/api/secrets/bad-name", json={"value": "x"}, headers=H).status_code == 400


def test_config_validation_and_yaml_roundtrip(client):
    signed_in(client)
    client.put("/api/secrets/OPENAI_API_KEY", json={"value": "sk-never-exported"}, headers=H)
    assert client.put("/api/config/voice", json={"follow_up_s": 99}, headers=H).status_code == 422
    assert client.put("/api/config/appearance", json={"quality": "low"}, headers=H).status_code == 200
    exported = client.get("/api/config/export").text
    assert "quality: low" in exported and "sk-never-exported" not in exported
    client.put("/api/config/appearance", json={"quality": "high"}, headers=H)
    r = client.post("/api/config/import", content=exported, headers=H)
    assert r.status_code == 200 and "appearance" in r.json()["imported"]
    assert client.get("/api/config/appearance").json()["quality"] == "low"


def test_turn_traces(client, rt):
    signed_in(client)
    t = Turn(id="abc", user_text="hi", reply="hello", provider="p", model="m")
    t.timings = {"listen_end": 1.0, "stt_final": 1.3, "first_token": 1.8, "first_audio": 2.1, "done": 3.0}
    rt.traces.save(t)
    rows = client.get("/api/turns").json()
    assert rows[0]["id"] == "abc" and rows[0]["latency"]["end_of_speech_to_audio"] == 1.1
    assert client.get("/api/turns/abc").json()["reply_text"] == "hello"
    stats = client.get("/api/overview").json()["stats"]
    assert stats["stages"]["stt"]["p50"] == 0.3


def test_security_headers(client):
    r = client.get("/api/auth/state")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
