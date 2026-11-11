import base64
import json
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from jarvis_brain.config import ConfigStore
from jarvis_brain.connectors.base import ConnectorRegistry, ManifestMismatch, check_against_manifest
from jarvis_brain.connectors.gmail.connector import extract_body
from jarvis_brain.connectors.oauth import OAuthError, OAuthManager
from jarvis_brain.db import Database
from jarvis_brain.manifest import Manifest
from jarvis_brain.providers.base import ToolCall
from jarvis_brain.secrets import SecretStore
from jarvis_brain.tools.registry import Tool, ToolContext, ToolRegistry
from jarvis_protocol import SideEffect

from .test_router_agent import FakeProvider, collect, make_agent, text_step


class FakeGoogle:
    """Mock for Google's token, Gmail and Calendar endpoints."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.created_events: list[dict] = []
        self.sent: list[dict] = []
        self.token_calls = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        url = str(req.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            self.token_calls += 1
            form = parse_qs(req.content.decode())
            if form.get("grant_type") == ["refresh_token"] and form["refresh_token"] == ["revoked"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": f"at{self.token_calls}",
                    "expires_in": 3600,
                    "refresh_token": "rt1",
                },
            )
        assert req.headers["authorization"].startswith("Bearer at")
        if "/gmail/v1/users/me/messages/send" in url:
            self.sent.append(json.loads(req.content))
            return httpx.Response(200, json={"id": "sent1"})
        if "/gmail/v1/users/me/messages/m1" in url:
            body = base64.urlsafe_b64encode(b"<p>Running <b>late</b>, sorry!</p>").decode()
            return httpx.Response(
                200,
                json={
                    "id": "m1",
                    "threadId": "t1",
                    "labelIds": ["UNREAD", "IMPORTANT"],
                    "snippet": "Running late",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Sam Jones <sam@example.com>"},
                            {"name": "Subject", "value": "Tonight"},
                            {"name": "Message-ID", "value": "<abc@mail>"},
                        ],
                        "mimeType": "multipart/alternative",
                        "parts": [{"mimeType": "text/html", "body": {"data": body}}],
                    },
                },
            )
        if "/gmail/v1/users/me/messages" in url:
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if "/calendar/v3/calendars/primary/events" in url:
            if req.method == "POST":
                ev = json.loads(req.content)
                self.created_events.append(ev)
                return httpx.Response(200, json={"id": "e9", **ev})
            tomorrow = (datetime.now().astimezone() + timedelta(days=1)).replace(
                hour=10, minute=0, second=0, microsecond=0
            )
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "e1",
                            "summary": "Dentist",
                            "start": {"dateTime": tomorrow.isoformat()},
                            "end": {"dateTime": (tomorrow + timedelta(hours=1)).isoformat()},
                            "location": "High St",
                        },
                        {
                            "id": "e2",
                            "summary": "Dinner with Sam",
                            "start": {"dateTime": tomorrow.replace(hour=20).isoformat()},
                            "end": {"dateTime": tomorrow.replace(hour=22).isoformat()},
                        },
                    ]
                },
            )
        return httpx.Response(404)


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def env(google, tmp_path):
    db = Database(":memory:")
    secrets = SecretStore(db, "")
    cfg = ConfigStore(db)
    http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(google.handler))  # noqa: E731
    oauth = OAuthManager(secrets, http=http())
    reg = ToolRegistry()
    connectors = ConnectorRegistry(cfg, secrets, oauth, reg, http=http)
    connectors.discover()
    return {
        "db": db,
        "secrets": secrets,
        "cfg": cfg,
        "oauth": oauth,
        "reg": reg,
        "connectors": connectors,
    }


async def connect(env, cid="gmail"):
    env["secrets"].set("GOOGLE_CLIENT_ID", "cid")
    env["secrets"].set("GOOGLE_CLIENT_SECRET", "csecret")
    url = env["oauth"].begin(cid, "google", ["scope"], "https://jarvis.example.ts.net/cb")
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
    await env["oauth"].complete(q["state"][0], "the-code")
    env["connectors"].sync()


# --------------------------------------------------------------------------- OAuth


async def test_oauth_requires_client_secrets(env):
    with pytest.raises(OAuthError, match="GOOGLE_CLIENT_ID"):
        env["oauth"].begin("gmail", "google", ["s"], "https://x/cb")


async def test_oauth_state_is_single_use_and_tokens_encrypted(env, google):
    await connect(env)
    assert env["oauth"].connected("gmail")
    raw = env["db"].one("SELECT value FROM secrets WHERE name='OAUTH_TOKEN_GMAIL'")["value"]
    assert b"rt1" not in raw  # encrypted at rest
    exchange = next(r for r in google.requests if "oauth2" in str(r.url))
    assert "code_verifier" in exchange.content.decode()
    with pytest.raises(OAuthError, match="expired or was already used"):
        await env["oauth"].complete("forged-state", "code")


async def test_oauth_refreshes_expired_tokens(env, google):
    await connect(env)
    tok = env["oauth"].load("gmail")
    tok["expires_at"] = time.time() - 1
    env["oauth"].save("gmail", tok)
    assert await env["oauth"].access_token("gmail") == "at2"
    tok = env["oauth"].load("gmail")
    tok.update(expires_at=0, refresh_token="revoked")
    env["oauth"].save("gmail", tok)
    with pytest.raises(OAuthError, match="revoked"):
        await env["oauth"].access_token("gmail")


async def test_oauth_paste_fallback(env):
    env["secrets"].set("GOOGLE_CLIENT_ID", "cid")
    env["secrets"].set("GOOGLE_CLIENT_SECRET", "cs")
    url = env["oauth"].begin("gmail", "google", ["s"], "http://127.0.0.1:9000/cb")
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(OAuthError):
        await env["oauth"].complete_from_url("http://127.0.0.1:9000/cb?error=access_denied")
    assert (
        await env["oauth"].complete_from_url(f"http://127.0.0.1:9000/cb?state={state}&code=c")
        == "gmail"
    )


# --------------------------------------------------------------------------- framework


def test_connectors_gated_on_auth(env):
    c = env["connectors"]
    assert {"gmail", "google-calendar"} <= set(c.connectors)
    assert all(not lc.error for lc in c.connectors.values())
    c.sync()
    assert env["reg"].get("gmail_search") is None  # not connected yet
    d = {x["id"]: x for x in c.describe()}
    assert d["gmail"]["missing_secrets"] == ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]


def test_manifest_mismatch_is_rejected():
    async def h(a, c):
        return None

    m = Manifest(
        id="x", name="X", type="connector", tools=[{"name": "send", "side_effect": "irreversible"}]
    )
    with pytest.raises(ManifestMismatch):
        check_against_manifest(m, [Tool("send", "s", h, side_effect=SideEffect.NONE)])
    with pytest.raises(ManifestMismatch):
        check_against_manifest(m, [Tool("other", "o", h)])


# --------------------------------------------------------------------------- Gmail


def test_extract_body_prefers_plain_then_strips_html():
    enc = lambda s: base64.urlsafe_b64encode(s.encode()).decode()  # noqa: E731
    assert extract_body({"mimeType": "text/plain", "body": {"data": enc("hi there")}}) == "hi there"
    html_only = {
        "parts": [
            {"mimeType": "text/html", "body": {"data": enc("<style>x{}</style><p>A &amp; B</p>")}}
        ]
    }
    assert extract_body(html_only) == "A & B"


async def test_gmail_search_read_and_send(env, google):
    await connect(env)
    reg = env["reg"]
    res = await reg.execute(ToolCall("1", "gmail_search", {"query": "is:unread"}), ToolContext())
    assert res.ok and "Sam Jones" in res.content and '"important": true' in res.content
    res = await reg.execute(ToolCall("2", "gmail_read", {"message_id": "m1"}), ToolContext())
    assert (
        res.ok and "Running" in res.content and "late" in res.content and "<b>" not in res.content
    )
    send = reg.get("gmail_send")
    assert send.side_effect == SideEffect.IRREVERSIBLE
    args = {"to": "sam@example.com", "body": "On my way.", "reply_to_message_id": "m1"}
    assert send.readback(args) == "reply to sam@example.com, saying: On my way."
    res = await reg.execute(ToolCall("3", "gmail_send", args), ToolContext())
    assert res.ok and google.sent[0]["threadId"] == "t1"
    raw = base64.urlsafe_b64decode(google.sent[0]["raw"]).decode()
    assert "In-Reply-To: <abc@mail>" in raw and "Subject: Re: Tonight" in raw


# --------------------------------------------------------------------------- Calendar


async def test_calendar_events_create_and_now(env, google):
    await connect(env, "google-calendar")
    reg = env["reg"]
    tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
    res = await reg.execute(
        ToolCall("1", "calendar_events", {"start_date": tomorrow}), ToolContext()
    )
    assert "Dentist" in res.content and "Dinner with Sam" in res.content
    create = reg.get("calendar_create_event")
    args = {"title": "Haircut", "start": f"{tomorrow}T10:00"}
    assert create.readback(args) == 'add "Haircut" to your calendar tomorrow at 10:00 for an hour'
    res = await reg.execute(ToolCall("2", "calendar_create_event", args), ToolContext())
    assert res.ok and google.created_events[0]["summary"] == "Haircut"
    card = await env["connectors"].refresh_now()
    assert card.next_event_title == "Dentist"


async def test_whats_on_my_calendar_tomorrow_end_to_end(env, google):
    """The spec's acceptance sentence, through the agent loop with a scripted model."""
    await connect(env, "google-calendar")
    tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
    call = ToolCall("c1", "calendar_events", {"start_date": tomorrow, "days": 1})
    p = FakeProvider(
        "p",
        [
            text_step("", [call]),
            text_step("Two things tomorrow: the dentist at ten, and dinner with Sam at eight."),
        ],
    )
    agent, _ = make_agent({"p": p})
    agent.tools = env["reg"]
    text, turn, ctx = await collect(agent, "What's on my calendar tomorrow?")
    assert text.startswith("Two things tomorrow")
    tool_msg = p.calls[1][-1]
    assert tool_msg.role == "tool" and "Dentist" in tool_msg.content
    assert any(e.type == "agent.tool_call" and e.icon == "calendar" for e in ctx.events)


def test_template_connector_matches_its_manifest(env):
    import importlib
    from pathlib import Path

    from jarvis_brain.connectors.base import ConnectorContext

    root = Path(importlib.import_module("jarvis_brain.connectors").__file__).parent / "_template"
    manifest = Manifest.load(root / "manifest.yaml")
    module = importlib.import_module("jarvis_brain.connectors._template.connector")
    impl = module.create(
        ConnectorContext(manifest.id, manifest, env["secrets"], env["oauth"], env["cfg"])
    )
    check_against_manifest(manifest, impl.tools())
    assert "_template" not in env["connectors"].connectors  # not loaded in production
