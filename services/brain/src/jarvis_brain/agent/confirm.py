"""Spoken confirmation for irreversible tools ("send email", "delete", "buy", "post", "run task").

No exceptions in v1: an irreversible call is never executed in the turn that requested it. Jarvis
reads back what it is about to do and waits for an explicit yes/no in the next utterance.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Literal

from ..providers.base import Message, ToolCall

YES = re.compile(
    r"^(?:(?:yes|yeah|yep|yup|sure|ok|okay|confirm(?:ed)?|correct|affirmative|absolutely|"
    r"definitely|please|do it|go ahead|go for it|send it|post it|delete it|book it|buy it|"
    r"that's right|that is right|right|please do|make it so)[\s,.!]*)+"
    r"(?:(?:jarvis|please|thanks|thank you|now)[\s,.!]*)*$"
)
NO = re.compile(
    r"^(?:(?:no|nope|nah|cancel|stop|don't|do not|never mind|nevermind|abort|wait|hold on|"
    r"not now|leave it|forget it|no thanks|no thank you|negative)[\s,.!]*)+"
    r"(?:(?:jarvis|please|thanks|thank you|then|actually|it)[\s,.!]*)*$"
)

Answer = Literal["yes", "no", "other"]


def classify_answer(text: str) -> Answer:
    t = re.sub(r"\s+", " ", text.lower().strip())
    t = t.replace("’", "'")
    if YES.match(t):
        return "yes"
    if NO.match(t):
        return "no"
    return "other"


@dataclass
class PendingConfirmation:
    call: ToolCall
    readback: str
    messages: list[Message]  # conversation up to and including the assistant tool-call turn
    remaining: list[ToolCall]  # calls from the same step not yet executed
    provider_id: str
    route: str
    reply_so_far: str
    created: float = field(default_factory=time.time)
    ttl_s: float = 60.0

    @property
    def expired(self) -> bool:
        return time.time() - self.created > self.ttl_s


def readback_question(action: str) -> str:
    action = action.strip().rstrip(".")
    return f"Just to confirm, I'm about to {action}. Shall I go ahead?"


DECLINED = "Okay, I won't."
EXPIRED_NOTE = "That earlier request timed out, so I didn't do it."
