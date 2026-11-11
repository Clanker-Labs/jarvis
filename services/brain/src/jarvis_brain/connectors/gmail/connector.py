"""Gmail connector (REST API v1 over httpx)."""

from __future__ import annotations

import base64
import html
import re
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any

from jarvis_protocol import SideEffect
from pydantic import BaseModel, Field

from ...tools.registry import Tool, ToolContext
from ..base import ConnectorContext

API = "https://gmail.googleapis.com/gmail/v1/users/me"


class SearchArgs(BaseModel):
    query: str = Field(
        "is:unread",
        max_length=300,
        description="Gmail search, e.g. 'from:sam newer_than:2d', 'is:unread is:important'.",
    )
    max_results: int = Field(5, ge=1, le=20)


class ReadArgs(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)


class ComposeArgs(BaseModel):
    to: str = Field(min_length=3, max_length=300, description="Email address(es), comma separated.")
    subject: str = Field("", max_length=300)
    body: str = Field(min_length=1, max_length=20_000)
    reply_to_message_id: str | None = Field(
        None, description="Reply in the thread of this message."
    )


def _header(msg: dict[str, Any], name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return str(h.get("value", ""))
    return ""


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(errors="replace")


def extract_body(payload: dict[str, Any]) -> str:
    """Prefer text/plain; fall back to text/html stripped of tags."""
    plain, rich = [], []

    def walk(part: dict[str, Any]) -> None:
        mt = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mt == "text/plain":
            plain.append(_decode(data))
        elif data and mt == "text/html":
            rich.append(_decode(data))
        for p in part.get("parts", []) or []:
            walk(p)

    walk(payload)
    if plain:
        return "\n".join(plain).strip()
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", "\n".join(rich), flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", text, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


def _summary(msg: dict[str, Any]) -> dict[str, Any]:
    name, addr = parseaddr(_header(msg, "From"))
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "from": name or addr,
        "from_email": addr,
        "subject": _header(msg, "Subject"),
        "date": _header(msg, "Date"),
        "snippet": html.unescape(msg.get("snippet", "")),
        "unread": "UNREAD" in msg.get("labelIds", []),
        "important": "IMPORTANT" in msg.get("labelIds", []),
    }


class Gmail:
    def __init__(self, ctx: ConnectorContext) -> None:
        self.ctx = ctx

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        async with self.ctx.http() as c:
            r = await c.get(f"{API}{path}", params=params, headers=await self.ctx.bearer())
            r.raise_for_status()
            return r.json()

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        async with self.ctx.http() as c:
            r = await c.post(f"{API}{path}", json=body, headers=await self.ctx.bearer())
            r.raise_for_status()
            return r.json()

    async def search(self, a: SearchArgs, _c: ToolContext | None = None) -> list[dict[str, Any]]:
        listing = await self._get("/messages", q=a.query, maxResults=a.max_results)
        out = []
        for m in listing.get("messages", [])[: a.max_results]:
            msg = await self._get(
                f"/messages/{m['id']}",
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            )
            out.append(_summary(msg))
        return out

    async def read(self, a: ReadArgs, _c: ToolContext | None = None) -> dict[str, Any]:
        msg = await self._get(f"/messages/{a.message_id}", format="full")
        body = extract_body(msg.get("payload", {}))
        return {
            **_summary(msg),
            "to": _header(msg, "To"),
            "body": body[:8000],
            "truncated": len(body) > 8000,
        }

    async def _raw(self, a: ComposeArgs) -> dict[str, Any]:
        em = EmailMessage()
        em["To"] = a.to
        em["Subject"] = a.subject
        payload: dict[str, Any] = {}
        if a.reply_to_message_id:
            orig = await self._get(
                f"/messages/{a.reply_to_message_id}",
                format="metadata",
                metadataHeaders=["Subject", "Message-ID", "References"],
            )
            mid = _header(orig, "Message-ID")
            if mid:
                em["In-Reply-To"] = mid
                em["References"] = f"{_header(orig, 'References')} {mid}".strip()
            if not a.subject:
                subj = _header(orig, "Subject")
                em.replace_header(
                    "Subject", subj if subj.lower().startswith("re:") else f"Re: {subj}"
                )
            payload["threadId"] = orig.get("threadId")
        em.set_content(a.body)
        payload["raw"] = base64.urlsafe_b64encode(em.as_bytes()).decode()
        return payload

    async def draft(self, a: ComposeArgs, _c: ToolContext | None = None) -> dict[str, Any]:
        d = await self._post("/drafts", {"message": await self._raw(a)})
        return {"draft_id": d["id"], "saved": True}

    async def send(self, a: ComposeArgs, _c: ToolContext | None = None) -> dict[str, Any]:
        sent = await self._post("/messages/send", await self._raw(a))
        return {"sent": True, "id": sent.get("id")}

    async def thread_text(self, thread_id: str) -> tuple[str, str]:
        """A whole thread as plain text, for pinning into the knowledge base."""
        t = await self._get(f"/threads/{thread_id}", format="full")
        msgs = t.get("messages", [])
        title = _header(msgs[0], "Subject") if msgs else f"Thread {thread_id}"
        parts = [
            f"From: {_header(m, 'From')}\nDate: {_header(m, 'Date')}\n\n{extract_body(m.get('payload', {}))}"
            for m in msgs
        ]
        return f"Email: {title}", "\n\n---\n\n".join(parts)

    async def now(self) -> dict[str, Any]:
        listing = await self._get(
            "/messages", q="is:unread is:important newer_than:7d", maxResults=50
        )
        return {"unread_important": len(listing.get("messages", []))}

    def tools(self) -> list[Tool]:
        def describe_send(args: dict[str, Any]) -> str:
            body = re.sub(r"\s+", " ", args.get("body", "")).strip()
            body = body if len(body) < 160 else body[:157] + "…"
            subj = args.get("subject")
            what = "reply" if args.get("reply_to_message_id") else "send an email"
            return (
                f"{what} to {args.get('to')}"
                + (f', subject "{subj}"' if subj else "")
                + f", saying: {body}"
            )

        return [
            Tool(
                "gmail_search",
                "Search the user's Gmail. Returns sender, subject, date, snippet, ids.",
                self.search,
                params=SearchArgs,
                label="Mail",
                icon="mail",
            ),
            Tool(
                "gmail_read",
                "Read one email in full by message id.",
                self.read,
                params=ReadArgs,
                label="Mail",
                icon="mail",
            ),
            Tool(
                "gmail_draft",
                "Save an email draft (does not send).",
                self.draft,
                params=ComposeArgs,
                side_effect=SideEffect.REVERSIBLE,
                label="Draft",
                icon="mail",
            ),
            Tool(
                "gmail_send",
                "Send an email, or reply in a thread with reply_to_message_id.",
                self.send,
                params=ComposeArgs,
                side_effect=SideEffect.IRREVERSIBLE,
                label="Sending mail",
                icon="send",
                describe=describe_send,
            ),
        ]


def create(ctx: ConnectorContext) -> Gmail:
    return Gmail(ctx)
