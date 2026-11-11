"""OpenAI-compatible chat completions: OpenAI, OpenRouter, and the local tailnet LLM server.

Tool calling modes:
- ``native`` — the server's ``tools`` / ``tool_calls`` support.
- ``json``   — for models with weak or no tool calling: tools are described in the prompt and the
  model replies with exactly one JSON object to call one. Parsed strictly, with one corrective
  retry; never spoken aloud.
- ``auto``   — native, switching to json permanently if the server rejects ``tools``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, Literal

import httpx
import openai

from .base import (
    AgentEvent,
    Completed,
    Message,
    Pricing,
    ProviderError,
    TextDelta,
    ToolCall,
    ToolCallRequested,
    ToolSpec,
)

log = logging.getLogger(__name__)
ToolMode = Literal["native", "json", "auto"]

JSON_TOOL_INSTRUCTIONS = """

## Tools
You can call tools. To call one, reply with ONLY a single JSON object and nothing else:
{{"tool": "<tool name>", "arguments": {{...}}}}
Never mix JSON with normal text. After you receive a TOOL RESULT, answer the user in plain text,
or call another tool the same way. If no tool is needed, just answer in plain text.

Available tools:
{tools}
"""


RETRY_PROMPT = (
    "That was not a valid tool call ({error}). Reply with either plain text for the user, "
    'or exactly one JSON object {{"tool": ..., "arguments": {{...}}}}.'
)


class ThinkFilter:
    """Removes <think>…</think> sections (reasoning models) from a text stream."""

    def __init__(self) -> None:
        self.inside = False
        self.buf = ""

    def push(self, text: str) -> str:
        self.buf += text
        out = []
        while self.buf:
            if self.inside:
                end = self.buf.find("</think>")
                if end < 0:
                    self.buf = self.buf[-8:]  # keep a possible partial closing tag
                    return "".join(out)
                self.buf = self.buf[end + 8 :]
                self.inside = False
            else:
                start = self.buf.find("<think>")
                if start < 0:
                    # Hold back a possible partial "<think" at the end.
                    keep = next((i for i in range(1, 7) if self.buf.endswith("<think>"[:i])), 0)
                    out.append(self.buf[: len(self.buf) - keep])
                    self.buf = self.buf[len(self.buf) - keep :]
                    return "".join(out)
                out.append(self.buf[:start])
                self.buf = self.buf[start + 7 :]
                self.inside = True
        return "".join(out)

    def flush(self) -> str:
        rest, self.buf = ("" if self.inside else self.buf), ""
        return rest


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_json_tool_call(text: str, known: set[str]) -> ToolCall | None:
    """Strictly parse a JSON-mode tool call. Returns None if ``text`` isn't one."""
    s = _FENCE.sub("", text.strip())
    if not s.startswith("{"):
        return None
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("tool call must be a JSON object")
    name = obj.get("tool") or obj.get("name")
    args = obj.get("arguments", obj.get("args", {}))
    if name not in known:
        raise ValueError(f"unknown tool {name!r}")
    if not isinstance(args, dict):
        raise ValueError("'arguments' must be an object")
    return ToolCall(id=f"call_{uuid.uuid4().hex[:10]}", name=str(name), args=args)


class OpenAICompatProvider:
    def __init__(
        self,
        id: str,
        base_url: Callable[[], str],
        model: Callable[[], str],
        api_key: Callable[[], str],
        *,
        tool_mode: ToolMode = "native",
        local: bool = False,
        pricing: Pricing | None = None,
        max_tokens: int = 2048,
    ) -> None:
        self.id = id
        self._base_url = base_url
        self._model = model
        self._api_key = api_key
        self.tool_mode: ToolMode = tool_mode
        self._effective_mode: Literal["native", "json"] = (
            "json" if tool_mode == "json" else "native"
        )
        self.local = local
        self.pricing = pricing or Pricing()
        self.max_tokens = max_tokens
        self.capabilities = {"tools", "streaming"}
        self._client: openai.AsyncOpenAI | None = None
        self._client_sig: tuple[str, str] = ("", "")
        self._stream_usage = True

    @property
    def model(self) -> str:
        return self._model()

    def configured(self) -> bool:
        return bool(self._base_url() and self._model() and (self.local or self._api_key()))

    def _get_client(self) -> openai.AsyncOpenAI:
        if not self.configured():
            raise ProviderError(f"{self.id}: not configured")
        sig = (self._base_url(), self._api_key())
        if self._client is None or sig != self._client_sig:
            self._client = openai.AsyncOpenAI(
                base_url=sig[0], api_key=sig[1] or "not-needed", max_retries=1, timeout=60
            )
            self._client_sig = sig
        return self._client

    # ------------------------------------------------------------------ mapping

    def _convert(self, messages: list[Message], tools: list[ToolSpec], mode: str) -> list[dict]:
        out: list[dict[str, Any]] = []
        for i, m in enumerate(messages):
            if m.role == "system":
                content = m.content
                if i == 0 and mode == "json" and tools:
                    content += JSON_TOOL_INSTRUCTIONS.format(tools=self._describe(tools))
                out.append({"role": "system", "content": content})
            elif m.role == "user":
                out.append({"role": "user", "content": m.content})
            elif m.role == "assistant":
                if mode == "json":
                    if m.tool_calls:
                        c = m.tool_calls[0]
                        out.append(
                            {
                                "role": "assistant",
                                "content": json.dumps({"tool": c.name, "arguments": c.args}),
                            }
                        )
                    else:
                        out.append({"role": "assistant", "content": m.content})
                else:
                    msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
                    if m.tool_calls:
                        msg["tool_calls"] = [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {"name": c.name, "arguments": json.dumps(c.args)},
                            }
                            for c in m.tool_calls
                        ]
                    out.append(msg)
            elif m.role == "tool":
                if mode == "json":
                    status = "ERROR" if m.is_error else "OK"
                    out.append(
                        {
                            "role": "user",
                            "content": f"TOOL RESULT ({m.tool_name}, {status}):\n{m.content}",
                        }
                    )
                else:
                    out.append(
                        {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
                    )
        return out

    @staticmethod
    def _describe(tools: list[ToolSpec]) -> str:
        return "\n".join(
            f"- {t.name}: {t.description}\n  arguments schema: {json.dumps(t.parameters)}"
            for t in sorted(tools, key=lambda t: t.name)
        )

    # ------------------------------------------------------------------ run

    async def run(
        self, messages: list[Message], tools: list[ToolSpec], *, stream: bool = True
    ) -> AsyncIterator[AgentEvent]:
        if tools and self._effective_mode == "native":
            try:
                async for ev in self._run_native(messages, tools):
                    yield ev
                return
            except openai.BadRequestError as e:
                if self.tool_mode != "auto":
                    raise ProviderError(f"{self.id}: {e.message}") from e
                log.warning(
                    "%s rejected native tools (%s); switching to JSON tool mode", self.id, e.message
                )
                self._effective_mode = "json"
        async for ev in self._run_json(messages, tools):
            yield ev

    async def _stream(self, payload: dict[str, Any]):  # type: ignore[no-untyped-def]
        client = self._get_client()
        try:
            if self._stream_usage:
                try:
                    return await client.chat.completions.create(
                        **payload, stream=True, stream_options={"include_usage": True}
                    )
                except openai.BadRequestError as e:
                    if "stream_options" not in str(e):
                        raise
                    self._stream_usage = False
            return await client.chat.completions.create(**payload, stream=True)
        except openai.BadRequestError:
            raise
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise ProviderError(f"{self.id}: connection error: {e}") from e
        except openai.RateLimitError as e:
            raise ProviderError(f"{self.id}: rate limited") from e
        except openai.APIStatusError as e:
            raise ProviderError(f"{self.id}: HTTP {e.status_code}") from e

    def _completed(self, msg: Message, stop: str, usage: Any) -> Completed:
        inp = getattr(usage, "prompt_tokens", 0) or 0
        out = getattr(usage, "completion_tokens", 0) or 0
        return Completed(msg, stop, inp, out, self.pricing.cost(inp, out), self.model)

    async def _run_native(
        self, messages: list[Message], tools: list[ToolSpec]
    ) -> AsyncIterator[AgentEvent]:
        payload = {
            "model": self.model,
            "messages": self._convert(messages, tools, "native"),
            "max_tokens": self.max_tokens,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ],
        }
        resp = await self._stream(payload)
        think = ThinkFilter()
        text: list[str] = []
        partial: dict[int, dict[str, str]] = {}
        stop, usage = "end_turn", None
        try:
            async for chunk in resp:
                if chunk.usage:
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta and delta.content and (t := think.push(delta.content)):
                    text.append(t)
                    yield TextDelta(t)
                for tc in (delta.tool_calls or []) if delta else []:
                    p = partial.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    p["id"] = tc.id or p["id"]
                    if tc.function:
                        p["name"] += tc.function.name or ""
                        p["args"] += tc.function.arguments or ""
                if choice.finish_reason:
                    stop = choice.finish_reason
        except (openai.APIError, httpx.HTTPError) as e:
            raise ProviderError(f"{self.id}: stream failed: {e}") from e
        if t := think.flush():
            text.append(t)
            yield TextDelta(t)
        calls = []
        for p in partial.values():
            try:
                args = json.loads(p["args"] or "{}")
            except json.JSONDecodeError:
                args = {"__invalid_json__": p["args"]}
            calls.append(ToolCall(p["id"] or f"call_{uuid.uuid4().hex[:10]}", p["name"], args))
        if stop == "length" and calls:
            calls = []
        for c in calls:
            yield ToolCallRequested(c)
        msg = Message("assistant", "".join(text).strip(), tool_calls=calls)
        yield self._completed(msg, "tool_use" if calls else stop, usage)

    async def _run_json(
        self, messages: list[Message], tools: list[ToolSpec]
    ) -> AsyncIterator[AgentEvent]:
        known = {t.name for t in tools}
        convo = self._convert(messages, tools, "json")
        for attempt in range(2):
            resp = await self._stream(
                {"model": self.model, "messages": convo, "max_tokens": self.max_tokens}
            )
            think = ThinkFilter()
            buf: list[str] = []
            streaming_text = False
            decided = False
            usage = None
            try:
                async for chunk in resp:
                    if chunk.usage:
                        usage = chunk.usage
                    if not chunk.choices or not chunk.choices[0].delta.content:
                        continue
                    t = think.push(chunk.choices[0].delta.content)
                    if not t:
                        continue
                    buf.append(t)
                    if not decided:
                        head = "".join(buf).lstrip()
                        if not head:
                            continue
                        decided = True
                        # Anything that starts like JSON/code is held back until parsed.
                        streaming_text = not (bool(tools) and head[0] in "{`")
                        if streaming_text:
                            yield TextDelta("".join(buf))
                    elif streaming_text:
                        yield TextDelta(t)
            except (openai.APIError, httpx.HTTPError) as e:
                raise ProviderError(f"{self.id}: stream failed: {e}") from e
            tail = think.flush()
            if tail:
                buf.append(tail)
                if streaming_text:
                    yield TextDelta(tail)
            full = "".join(buf).strip()
            if streaming_text or not tools:
                yield self._completed(Message("assistant", full), "end_turn", usage)
                return
            try:
                call = parse_json_tool_call(full, known)
            except ValueError as e:
                if attempt == 0:
                    log.info("%s: bad JSON tool call (%s); retrying once", self.id, e)
                    convo = [
                        *convo,
                        {"role": "assistant", "content": full},
                        {
                            "role": "user",
                            "content": RETRY_PROMPT.format(error=e),
                        },
                    ]
                    continue
                raise ProviderError(f"{self.id}: invalid tool call after retry: {e}") from e
            if call is None:  # held back but turned out to be text
                yield TextDelta(full)
                yield self._completed(Message("assistant", full), "end_turn", usage)
                return
            yield ToolCallRequested(call)
            yield self._completed(Message("assistant", "", tool_calls=[call]), "tool_use", usage)
            return

    async def health(self) -> bool:
        if not self.configured():
            return False
        headers = {"Authorization": f"Bearer {self._api_key()}"} if self._api_key() else {}
        try:
            async with httpx.AsyncClient(timeout=4) as c:
                r = await c.get(self._base_url().rstrip("/") + "/models", headers=headers)
            return r.status_code < 500 and r.status_code != 401
        except httpx.HTTPError as e:
            log.info("%s unhealthy: %s", self.id, e)
            return False
