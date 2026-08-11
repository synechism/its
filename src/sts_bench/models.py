from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LegalAction:
    """A model-visible choice backed by one exact CommunicationMod command."""

    index: int
    command: str
    kind: str
    label: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LegalAction:
        return cls(
            index=int(data["index"]),
            command=str(data["command"]),
            kind=str(data["kind"]),
            label=str(data["label"]),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True, slots=True)
class GameState:
    """Normalized, player-visible state from the authoritative game process."""

    engine: dict[str, Any]
    requested_seed: str
    actual_seed: int | None
    character: str
    ascension: int
    status: str
    phase: str
    act: int
    floor_reached: int
    decisions: int
    hp: int
    max_hp: int
    block: int
    energy: int | None
    gold: int
    visible: dict[str, Any]
    legal_actions: tuple[LegalAction, ...]
    progress: dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.status in {"victory", "defeat"}

    @property
    def won(self) -> bool:
        return self.status == "victory"

    def canonical_dict(self) -> dict[str, Any]:
        """Return the replay contract; all values are model-visible and stable."""
        return dataclasses.asdict(self)

    def stable_hash(self) -> str:
        payload = json.dumps(
            self.canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class ModelReply:
    content: str
    tokens_in: int = 0
    tokens_out: int = 0
    terminated: bool = False


@dataclass(slots=True)
class Outcome:
    won: bool
    terminal_status: str
    termination_reason: str
    floor_reached: int
    score: int | None
    score_source: str
    boss_killed: bool
    bosses_killed: int
    elites_killed: int
    acts_cleared: list[int]
    decisions: int
    tokens_in: int
    tokens_out: int
    illegal_action_count: int
    forced_default_count: int
    response_count: int
    seed: str
    actual_seed: int | None
    character: str
    ascension: int
    model: str
    engine: dict[str, Any]
    run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def scalar_reward(self) -> float:
        """Sparse training reward. Benchmark ranking uses the raw fields instead."""
        return float(self.won)

    @property
    def illegal_action_rate(self) -> float:
        return self.illegal_action_count / self.response_count if self.response_count else 0.0

    def to_dict(self) -> dict[str, Any]:
        result = dataclasses.asdict(self)
        result["scalar_reward"] = self.scalar_reward
        result["illegal_action_rate"] = self.illegal_action_rate
        return result
