from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class SessionState:
    messages: list[dict[str, Any]]
    summary: str | None = None
    turn_count: int = 0


def parse_session_raw(data: Any) -> SessionState:
    if isinstance(data, list):
        return SessionState(messages=[m for m in data if isinstance(m, dict)])
    if isinstance(data, dict):
        messages = data.get("messages")
        if not isinstance(messages, list):
            messages = []
        summary = data.get("summary")
        if summary is not None and not isinstance(summary, str):
            summary = None
        turn_count = data.get("turn_count", 0)
        if not isinstance(turn_count, int):
            turn_count = 0
        return SessionState(
            messages=[m for m in messages if isinstance(m, dict)],
            summary=summary,
            turn_count=turn_count,
        )
    return SessionState(messages=[])


def serialize_session(state: SessionState) -> str:
    payload = {
        "v": 1,
        "messages": state.messages,
        "summary": state.summary,
        "turn_count": state.turn_count,
    }
    return json.dumps(payload, ensure_ascii=False)


def count_user_messages(messages: list[dict[str, Any]]) -> int:
    return sum(1 for m in messages if m.get("role") == "user")


def split_turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "user" and current:
            turns.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        turns.append(current)
    return turns


def flatten_turns(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for turn in turns:
        out.extend(turn)
    return out
