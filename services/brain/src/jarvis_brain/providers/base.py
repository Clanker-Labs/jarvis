"""Provider abstraction: one interface for API models, local models and agent CLIs.

Messages use a small neutral format; each provider translates it. Within one turn an assistant
message may carry ``raw`` provider-native content (e.g. Anthropic thinking blocks) so the same
provider can replay it verbatim — required for append-only tool loops.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None  # role == "tool"
    tool_name: str | None = None
    is_error: bool = False
    raw: Any = None  # provider-native content, valid only for ``raw_provider``
    raw_provider: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object)


# ---- events a provider yields while running


@dataclass
class TextDelta:
    text: str
    type: Literal["text"] = "text"


@dataclass
class ToolCallRequested:
    call: ToolCall
    type: Literal["tool_call"] = "tool_call"


@dataclass
class Progress:
    """Long-running providers (agent CLIs) reporting what they're doing."""

    message: str
    type: Literal["progress"] = "progress"


@dataclass
class Completed:
    message: Message  # the full assistant message (text + tool calls + raw)
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    type: Literal["completed"] = "completed"


AgentEvent = TextDelta | ToolCallRequested | Progress | Completed


class ProviderError(RuntimeError):
    """Raised for failures where falling back to another provider makes sense."""


@runtime_checkable
class Provider(Protocol):
    id: str
    capabilities: set[str]  # {"tools", "streaming", "vision", "long_context", "task"}

    def run(
        self, messages: list[Message], tools: list[ToolSpec], *, stream: bool = True
    ) -> AsyncIterator[AgentEvent]: ...

    async def health(self) -> bool: ...


@dataclass
class Pricing:
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    def cost(self, inp: int, out: int) -> float:
        return (inp * self.input_per_mtok + out * self.output_per_mtok) / 1_000_000
