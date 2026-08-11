from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Protocol

from sts_bench.communication_mod import (
    PROTOCOL_NAME,
    CommunicationError,
    normalize_state,
    state_score,
)
from sts_bench.models import GameState, LegalAction, Outcome
from sts_bench.transport import WorkerConnection

_SEED_RE = re.compile(r"^[A-Za-z0-9]+$")
_CHARACTERS = {
    "ironclad": "IRONCLAD",
    "silent": "SILENT",
    "the_silent": "SILENT",
    "defect": "DEFECT",
    "watcher": "WATCHER",
}


class GameBackend(Protocol):
    def reset(self, seed: str, character: str, ascension: int = 0) -> GameState: ...

    def step(self, action: LegalAction) -> GameState: ...

    def outcome(self, state: GameState, *, model: str) -> Outcome: ...


class LiveGame:
    """A synchronous environment client for one visible Slay the Spire process."""

    def __init__(self, connection: WorkerConnection) -> None:
        self.connection = connection
        worker = connection.worker
        self.worker = dict(worker)
        self.engine = {
            "game": worker.get("game", "Slay the Spire 1"),
            "game_version": worker.get("game_version", "unknown"),
            "mod_the_spire_version": worker.get("mod_the_spire_version", "unknown"),
            "base_mod_version": worker.get("base_mod_version", "unknown"),
            "communication_mod_version": worker.get("communication_mod_version", "unknown"),
            "bridge_version": worker.get("bridge_version", "unknown"),
            "protocol": PROTOCOL_NAME,
        }
        self._envelope = connection.receive_envelope()
        self._requested_seed = ""
        self._decisions = 0
        self._progress = self._new_progress()
        self._state: GameState | None = None

    @staticmethod
    def _new_progress() -> dict[str, Any]:
        return {"elites_killed": 0, "bosses_killed": 0, "acts_cleared": []}

    @property
    def state(self) -> GameState:
        if self._state is None:
            raise CommunicationError("no run is active")
        return self._state

    def reset(self, seed: str, character: str, ascension: int = 0) -> GameState:
        seed = str(seed).upper()
        if not seed or not _SEED_RE.fullmatch(seed):
            raise ValueError("seed must contain only ASCII letters and digits")
        character_key = character.strip().lower().replace(" ", "_")
        if character_key not in _CHARACTERS:
            raise ValueError(f"unsupported character: {character!r}")
        if not 0 <= ascension <= 20:
            raise ValueError("ascension must be in 0..20")
        if self._envelope.get("in_game"):
            raise CommunicationError(
                "the worker already has an active run; finish it or return to the main menu first"
            )
        available = {str(item).lower() for item in self._envelope.get("available_commands") or []}
        if "start" not in available:
            raise CommunicationError("CommunicationMod is not ready to start a run from this menu")

        self._requested_seed = seed
        self._decisions = 0
        self._progress = self._new_progress()
        self.connection.send_command(f"START {_CHARACTERS[character_key]} {ascension} {seed}")
        self._envelope = self.connection.receive_envelope()
        self._state = self._normalize()
        return self._state

    def step(self, action: LegalAction) -> GameState:
        before = self.state
        matching = [
            candidate
            for candidate in before.legal_actions
            if candidate.index == action.index and candidate.command == action.command
        ]
        if len(matching) != 1:
            raise CommunicationError("action is not in the current game-owned legal action list")

        before_envelope = self._envelope
        self.connection.send_command(matching[0].command)
        after_envelope = self.connection.receive_envelope()
        if after_envelope.get("error"):
            raise CommunicationError(
                f"CommunicationMod rejected an enumerated legal action {action.command!r}: "
                f"{after_envelope['error']}"
            )
        self._decisions += 1
        self._update_progress(before_envelope, after_envelope)
        self._envelope = after_envelope
        self._state = self._normalize()
        return self._state

    def _normalize(self) -> GameState:
        return normalize_state(
            self._envelope,
            requested_seed=self._requested_seed,
            decisions=self._decisions,
            engine=self.engine,
            progress=self._progress,
        )

    def _update_progress(
        self, before_envelope: Mapping[str, Any], after_envelope: Mapping[str, Any]
    ) -> None:
        before = before_envelope.get("game_state") if before_envelope.get("in_game") else None
        after = after_envelope.get("game_state") if after_envelope.get("in_game") else None
        if not isinstance(before, dict) or not isinstance(after, dict):
            return
        combat_ended = before.get("room_phase") == "COMBAT" and after.get("room_phase") != "COMBAT"
        survived = int(after.get("current_hp", 0)) > 0
        if not combat_ended or not survived:
            return
        room_type = str(before.get("room_type", "")).lower()
        if "elite" in room_type:
            self._progress["elites_killed"] += 1
        if "boss" in room_type:
            self._progress["bosses_killed"] += 1
            act = int(before.get("act", 0))
            if act and act not in self._progress["acts_cleared"]:
                self._progress["acts_cleared"].append(act)

    def outcome(self, state: GameState, *, model: str) -> Outcome:
        if state.stable_hash() != self.state.stable_hash():
            raise CommunicationError("outcome requested for a stale state")
        progress = state.progress
        score = state_score(self._envelope)
        bosses = int(progress.get("bosses_killed", 0))
        return Outcome(
            won=state.won,
            terminal_status=state.status,
            termination_reason="terminal" if state.terminal else "truncated",
            floor_reached=state.floor_reached,
            score=score,
            score_source="communicationmod_game_over" if score is not None else "unavailable",
            boss_killed=bosses > 0,
            bosses_killed=bosses,
            elites_killed=int(progress.get("elites_killed", 0)),
            acts_cleared=[int(act) for act in progress.get("acts_cleared", [])],
            decisions=state.decisions,
            tokens_in=0,
            tokens_out=0,
            illegal_action_count=0,
            forced_default_count=0,
            response_count=0,
            seed=state.requested_seed,
            actual_seed=state.actual_seed,
            character=state.character,
            ascension=state.ascension,
            model=model,
            engine=dict(state.engine),
            metadata={"worker": self.worker},
        )

    def return_to_menu(self, *, max_commands: int = 12) -> None:
        """Leave a finished score screen so this worker can start another seed."""
        if not self._envelope.get("in_game"):
            self._state = None
            return
        if self._state is None or not self._state.terminal:
            raise CommunicationError("refusing to abandon a non-terminal run")
        for _ in range(max_commands):
            if not self._envelope.get("in_game"):
                self._state = None
                return
            available = {
                str(item).lower() for item in self._envelope.get("available_commands") or []
            }
            if not available.intersection({"proceed", "confirm"}):
                raise CommunicationError("finished run cannot currently proceed to the main menu")
            self.connection.send_command("PROCEED")
            self._envelope = self.connection.receive_envelope()
            if self._envelope.get("error"):
                raise CommunicationError(str(self._envelope["error"]))
            if self._envelope.get("in_game"):
                self._state = self._normalize()
        raise CommunicationError(
            "game did not return to its main menu after repeated PROCEED commands"
        )

    def close(self) -> None:
        self.connection.close()
