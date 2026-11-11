"""Conversation traces: every turn with its events, provider, tokens, cost and stage latencies."""

from __future__ import annotations

import json
import logging
import statistics
import time
from typing import TYPE_CHECKING, Any

from jarvis_protocol import Event

from .db import Database, dumps

if TYPE_CHECKING:
    from .orchestrator import Turn

log = logging.getLogger(__name__)

# Events kept in a trace (levels and tokens would bloat it).
TRACED = frozenset(
    {
        "wake.detected",
        "listen.start",
        "listen.final",
        "agent.thinking",
        "agent.tool_call",
        "agent.tool_result",
        "agent.confirm_request",
        "agent.progress",
        "tts.start",
        "tts.sentence",
        "tts.end",
        "barge_in",
        "error",
        "state.change",
        "agent.done",
    }
)

STAGES = {
    # name: (from mark, to mark)
    "wake_to_listen": ("wake", "listen_start"),
    "stt": ("listen_end", "stt_final"),
    "llm_first_token": ("stt_final", "first_token"),
    "tts_first_audio": ("first_token", "first_audio"),
    "end_of_speech_to_audio": ("listen_end", "first_audio"),
}


def stage_latencies(timings: dict[str, float]) -> dict[str, float]:
    out = {}
    for name, (a, b) in STAGES.items():
        if a in timings and b in timings and timings[b] >= timings[a]:
            out[name] = round(timings[b] - timings[a], 3)
    return out


class TraceStore:
    def __init__(self, db: Database, keep: int = 2000) -> None:
        self.db = db
        self.keep = keep
        self._events: dict[str, list[dict[str, Any]]] = {}

    def tap(self, ev: Event) -> None:
        if ev.type not in TRACED or not ev.turn_id:
            return
        evs = self._events.setdefault(ev.turn_id, [])
        if len(evs) < 400:
            evs.append(json.loads(ev.model_dump_json(exclude={"id"})))
        if len(self._events) > 50:  # drop stale in-flight buffers
            for k in list(self._events)[:-50]:
                self._events.pop(k, None)

    def save(self, turn: Turn) -> None:
        events = self._events.pop(turn.id, [])
        started = min(turn.timings.values()) if turn.timings else time.time()
        self.db.execute(
            "INSERT OR REPLACE INTO turns(id, started, ended, user_text, reply_text, provider, model,"
            " input_tokens, output_tokens, cost_usd, latency, events, error)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                turn.id,
                started,
                turn.timings.get("done", time.time()),
                turn.user_text,
                turn.reply,
                turn.provider,
                turn.model,
                turn.input_tokens,
                turn.output_tokens,
                turn.cost_usd,
                dumps({"timings": turn.timings, "stages": stage_latencies(turn.timings)}),
                dumps({"events": events, "tool_calls": turn.tool_calls}),
                turn.error or None,
            ),
        )
        self.db.execute(
            "DELETE FROM turns WHERE id NOT IN (SELECT id FROM turns ORDER BY started DESC LIMIT ?)",
            (self.keep,),
        )

    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id, started, ended, user_text, reply_text, provider, model, input_tokens,"
            " output_tokens, cost_usd, latency, error FROM turns ORDER BY started DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        for r in rows:
            r["latency"] = json.loads(r["latency"] or "{}").get("stages", {})
        return rows

    def get(self, turn_id: str) -> dict[str, Any] | None:
        r = self.db.one("SELECT * FROM turns WHERE id=?", (turn_id,))
        if r:
            r["latency"] = json.loads(r["latency"] or "{}")
            r["events"] = json.loads(r["events"] or "{}")
        return r

    def stats(self, last: int = 50) -> dict[str, Any]:
        rows = self.db.query(
            "SELECT latency, cost_usd, provider FROM turns ORDER BY started DESC LIMIT ?", (last,)
        )
        per_stage: dict[str, list[float]] = {k: [] for k in STAGES}
        for r in rows:
            for k, v in json.loads(r["latency"] or "{}").get("stages", {}).items():
                per_stage.setdefault(k, []).append(v)

        def pct(xs: list[float], q: float) -> float | None:
            if not xs:
                return None
            xs = sorted(xs)
            return round(xs[min(len(xs) - 1, int(q * len(xs)))], 3)

        day = self.db.one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd),0) AS cost FROM turns WHERE started > ?",
            (time.time() - 86400,),
        ) or {"n": 0, "cost": 0}
        return {
            "turns": len(rows),
            "stages": {
                k: {
                    "p50": pct(v, 0.5),
                    "p90": pct(v, 0.9),
                    "mean": round(statistics.fmean(v), 3) if v else None,
                    "n": len(v),
                }
                for k, v in per_stage.items()
            },
            "last_24h": {"turns": day["n"], "cost_usd": round(day["cost"], 4)},
        }
