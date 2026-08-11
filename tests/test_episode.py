from __future__ import annotations

import json
from collections import deque

import pytest

from sts_bench.episode import EpisodeConfig, play_episode
from sts_bench.game import LiveGame
from sts_bench.models import ModelReply


class FakeConnection:
    def __init__(self, envelopes: list[dict]) -> None:
        self.envelopes = deque(envelopes)
        self.commands: list[str] = []
        self.worker = {
            "id": "test-worker",
            "game": "Slay the Spire 1",
            "game_version": "test",
            "communication_mod_version": "test",
            "bridge_version": "test",
        }

    def receive_envelope(self) -> dict:
        return self.envelopes.popleft()

    def send_command(self, command: str) -> None:
        self.commands.append(command)

    def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_episode_uses_real_commands_and_writes_replay_artifacts(
    tmp_path, menu_envelope: dict, combat_envelope: dict, victory_envelope: dict
) -> None:
    connection = FakeConnection([menu_envelope, combat_envelope, victory_envelope])
    game = LiveGame(connection)  # type: ignore[arg-type]

    async def choose_end(prompt: str, _decision: int, _attempt: int) -> ModelReply:
        for line in prompt.splitlines():
            if "(end_turn)" in line:
                return ModelReply(f"ACTION {line.strip().split(']')[0][1:]}", 10, 2)
        raise AssertionError("END action not found")

    outcome = await play_episode(
        EpisodeConfig(
            seed="STSBENCHV1000",
            model="test-model",
            runs_dir=tmp_path,
            max_decisions=3,
        ),
        choose_end,
        game=game,
    )
    assert connection.commands == ["START IRONCLAD 0 STSBENCHV1000", "END"]
    assert outcome.won
    assert outcome.score == 777
    assert outcome.bosses_killed == 1
    assert outcome.acts_cleared == [1]
    assert outcome.tokens_in == 10

    run_dir = next(tmp_path.iterdir())
    row = json.loads((run_dir / "trajectory.jsonl").read_text().strip())
    assert row["engine_command"] == "END"
    assert row["state_hash"]
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "outcome.json").exists()
    assert "ENGINE COMMAND END" in (run_dir / "transcript.txt").read_text()
