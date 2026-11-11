"""Turning streamed model text into speakable sentences."""

from __future__ import annotations

import re

# Abbreviations whose trailing period does not end a sentence.
_ABBREV = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "sr",
    "jr",
    "st",
    "vs",
    "etc",
    "e.g",
    "i.e",
    "approx",
    "no",
    "fig",
    "inc",
    "ltd",
    "co",
    "mt",
    "ft",
    "km",
    "min",
    "max",
    "a.m",
    "p.m",
}
_END = re.compile(r"([.!?…]+)([\"')\]]*)(\s+)")
_MIN_FIRST = 12  # don't send a tiny first fragment ("Right.") on its own unless it's all there is


class SentenceSplitter:
    """Feed text chunks; get back complete sentences as soon as they exist.

    The first sentence is released as early as possible (it determines time-to-first-audio);
    later ones are merged up to ~200 chars so TTS gets natural prosody with fewer requests.
    """

    def __init__(self, merge_after_first: int = 200) -> None:
        self.buf = ""
        self.emitted = 0
        self.merge = merge_after_first

    def push(self, text: str) -> list[str]:
        self.buf += text
        out: list[str] = []
        while True:
            cut = self._find_boundary()
            if cut is None:
                break
            sentence, self.buf = self.buf[:cut].strip(), self.buf[cut:]
            if sentence:
                out.append(sentence)
        return self._merge(out)

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return self._merge([rest]) if rest else []

    def _merge(self, sentences: list[str]) -> list[str]:
        merged: list[str] = []
        for s in sentences:
            # Never merge into the first sentence: it alone decides time-to-first-audio.
            if merged and self.emitted > 1 and len(merged[-1]) + len(s) < self.merge:
                merged[-1] += " " + s
            else:
                merged.append(s)
                self.emitted += 1
        return merged

    def _find_boundary(self) -> int | None:
        for m in _END.finditer(self.buf):
            end = m.end()
            before = self.buf[: m.start()]
            word = re.split(r"\s", before)[-1].lower().rstrip(".") if before else ""
            if m.group(1) == "." and word in _ABBREV:
                continue
            if m.group(1) == "." and re.search(r"\d$", before) and re.match(r"\d", self.buf[end:]):
                continue  # 3. 5 — unlikely, but keep decimals/lists together
            if self.emitted == 0 and len(before.strip()) < _MIN_FIRST:
                continue
            return end
        # A paragraph break also ends a sentence.
        nl = self.buf.find("\n\n")
        if nl > 0:
            return nl + 2
        return None


_MD = [
    (re.compile(r"```.*?```", re.S), " "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"(\*\*|__)(.+?)\1"), r"\2"),
    (re.compile(r"(?<![\w*])[*_](\S.*?)[*_](?![\w*])"), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),
    (re.compile(r"^\s*[-*+•]\s+", re.M), ""),
    (re.compile(r"^\s*\d+[.)]\s+", re.M), ""),
    (re.compile(r"^\s*>\s?", re.M), ""),
    (re.compile(r"https?://\S+"), "the link"),
    (re.compile(r"\s*\n\s*"), " "),
    (re.compile(r"\s{2,}"), " "),
]


def speakable(text: str) -> str:
    """Strip markdown and other things that shouldn't be read aloud."""
    for pat, rep in _MD:
        text = pat.sub(rep, text)
    return text.strip()
