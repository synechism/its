from __future__ import annotations

import json
from collections import deque

import pytest

from sts_bench.episode import EpisodeConfig, play_episode
from sts_bench.evaluator import _codex_failure_detail, _codex_usage
from sts_bench.game import LiveGame
from sts_bench.models import ModelReply
from sts_bench.transport import WireError


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


def test_codex_cli_usage_parser() -> None:
    events = "\n".join(
        [
            '{"type":"thread.started","thread_id":"test"}',
            '{"type":"turn.completed","usage":{"input_tokens":120,'
            '"output_tokens":7,"reasoning_output_tokens":11}}',
        ]
    )
    assert _codex_usage(events) == (120, 18)


def test_codex_cli_failure_prefers_structured_stdout_error() -> None:
    stdout = b"\n".join(
        [
            b'{"type":"thread.started","thread_id":"test"}',
            b'{"type":"error","message":"You have hit your usage limit."}',
            b'{"type":"turn.failed","error":{"message":"You have hit your usage limit."}}',
        ]
    )
    stderr = b"WARN state db discrepancy"

    assert _codex_failure_detail(stdout, stderr) == "You have hit your usage limit."


def test_codex_cli_failure_falls_back_to_stderr() -> None:
    assert _codex_failure_detail(b"not json", b"useful stderr") == "useful stderr"


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


@pytest.mark.asyncio
async def test_episode_applies_single_automatic_action_without_calling_model(
    tmp_path, menu_envelope: dict, combat_envelope: dict, victory_envelope: dict
) -> None:
    tutorial = json.loads(json.dumps(combat_envelope))
    tutorial["available_commands"] = ["key", "state", "wait"]
    tutorial["game_state"]["screen_name"] = "FTUE"
    tutorial["game_state"]["combat_state"]["hand"] = []
    connection = FakeConnection([menu_envelope, tutorial, victory_envelope])
    game = LiveGame(connection)  # type: ignore[arg-type]

    async def model_must_not_run(*_args) -> ModelReply:
        raise AssertionError("automatic engine transitions must not call the model")

    outcome = await play_episode(
        EpisodeConfig(
            seed="STSBENCHV1000",
            model="test-model",
            runs_dir=tmp_path,
            max_decisions=1,
        ),
        model_must_not_run,
        game=game,
    )

    assert connection.commands == ["START IRONCLAD 0 STSBENCHV1000", "KEY CONFIRM"]
    assert outcome.won
    assert outcome.decisions == 0
    assert outcome.response_count == 0
    assert outcome.forced_default_count == 0
    row = json.loads((next(tmp_path.iterdir()) / "trajectory.jsonl").read_text().strip())
    assert row["automatic"] is True
    assert row["forced_default"] is False
    assert row["raw_response"] == ""


def test_card_reward_selection_records_a_stable_deck_settlement_wait(
    menu_envelope: dict, combat_envelope: dict
) -> None:
    reward = json.loads(json.dumps(combat_envelope))
    reward["available_commands"] = ["choose", "state", "wait"]
    reward["game_state"]["room_phase"] = "COMPLETE"
    reward["game_state"]["screen_type"] = "CARD_REWARD"
    reward["game_state"]["screen_name"] = "CARD_REWARD"
    reward["game_state"]["choice_list"] = ["battle trance"]
    reward["game_state"].pop("combat_state")

    selected = json.loads(json.dumps(reward))
    selected["available_commands"] = ["proceed", "state", "wait"]
    selected["game_state"]["screen_type"] = "COMBAT_REWARD"
    selected["game_state"]["screen_name"] = "COMBAT_REWARD"
    selected["game_state"]["choice_list"] = []
    selected["game_state"]["deck"].append(
        {
            "name": "Battle Trance",
            "id": "Battle Trance",
            "cost": 0,
            "upgrades": 0,
            "type": "SKILL",
            "rarity": "UNCOMMON",
            "has_target": False,
            "exhausts": False,
            "ethereal": False,
            "description": "Draw 3 cards.",
        }
    )
    settled = json.loads(json.dumps(selected))

    connection = FakeConnection([menu_envelope, reward, selected, settled])
    game = LiveGame(connection)  # type: ignore[arg-type]
    state = game.reset("STSBENCHV1000", "Ironclad")
    deck_before = state.visible["deck"]

    state = game.step(state.legal_actions[0])

    assert [action.command for action in state.legal_actions] == ["WAIT 100"]
    assert state.visible["deck"] == deck_before

    state = game.step(state.legal_actions[0], count_decision=False)

    assert [action.command for action in state.legal_actions] == ["PROCEED"]
    assert state.visible["deck"] != deck_before
    assert connection.commands == ["START IRONCLAD 0 STSBENCHV1000", "CHOOSE 0", "WAIT 100"]


def test_shop_purchase_records_a_stable_deck_settlement_wait(
    menu_envelope: dict, combat_envelope: dict
) -> None:
    shop = json.loads(json.dumps(combat_envelope))
    shop["available_commands"] = ["choose", "return", "state", "wait"]
    shop["game_state"]["room_phase"] = "COMPLETE"
    shop["game_state"]["screen_type"] = "SHOP_SCREEN"
    shop["game_state"]["screen_name"] = "SHOP"
    shop["game_state"]["choice_list"] = ["uppercut"]
    shop["game_state"].pop("combat_state")

    purchased = json.loads(json.dumps(shop))
    purchased["game_state"]["choice_list"] = []
    purchased["game_state"]["deck"].append(
        {
            "name": "Uppercut",
            "id": "Uppercut",
            "cost": 2,
            "upgrades": 0,
            "type": "ATTACK",
            "rarity": "UNCOMMON",
            "has_target": True,
            "exhausts": False,
            "ethereal": False,
            "description": "Deal 13 damage.",
        }
    )
    settled = json.loads(json.dumps(purchased))

    connection = FakeConnection([menu_envelope, shop, purchased, settled])
    game = LiveGame(connection)  # type: ignore[arg-type]
    state = game.reset("STSBENCHV1000", "Ironclad")
    deck_before = state.visible["deck"]

    state = game.step(state.legal_actions[0])

    assert [action.command for action in state.legal_actions] == ["WAIT 100"]
    assert state.visible["deck"] == deck_before

    state = game.step(state.legal_actions[0], count_decision=False)

    assert [action.command for action in state.legal_actions] == ["RETURN"]
    assert state.visible["deck"] != deck_before


@pytest.mark.asyncio
async def test_observer_requirement_fails_before_first_model_call(
    menu_envelope: dict, combat_envelope: dict
) -> None:
    connection = FakeConnection([menu_envelope, combat_envelope])
    game = LiveGame(connection)  # type: ignore[arg-type]

    async def model_must_not_run(*_args) -> ModelReply:
        raise AssertionError("observer preflight must happen before the model call")

    with pytest.raises(RuntimeError, match="Sts Bench Observer"):
        await play_episode(
            EpisodeConfig(
                seed="STSBENCHV1000",
                model="test-model",
                max_decisions=1,
                require_observer=True,
            ),
            model_must_not_run,
            game=game,
        )

    assert connection.commands == []


@pytest.mark.asyncio
async def test_interrupted_episode_writes_diagnostic_artifact(
    tmp_path, menu_envelope: dict, combat_envelope: dict
) -> None:
    class DroppingConnection(FakeConnection):
        def receive_envelope(self) -> dict:
            if not self.envelopes:
                raise WireError("worker connection closed")
            return super().receive_envelope()

    connection = DroppingConnection([menu_envelope, combat_envelope])
    game = LiveGame(connection)  # type: ignore[arg-type]

    async def choose_first(*_args) -> ModelReply:
        return ModelReply("ACTION 0")

    with pytest.raises(WireError, match="worker connection closed"):
        await play_episode(
            EpisodeConfig(
                seed="STSBENCHV1000",
                model="test-model",
                runs_dir=tmp_path,
                max_decisions=1,
            ),
            choose_first,
            game=game,
        )

    interrupted = json.loads(
        (next(tmp_path.iterdir()) / "interrupted.json").read_text(encoding="utf-8")
    )
    assert interrupted["error_type"] == "WireError"
    assert interrupted["message"] == "worker connection closed"
