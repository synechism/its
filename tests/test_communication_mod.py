from __future__ import annotations

from copy import deepcopy

from sts_bench.communication_mod import (
    enumerate_legal_actions,
    normalize_state,
    observer_version,
)
from sts_bench.text_protocol import serialize_state

ENGINE = {
    "game": "Slay the Spire 1",
    "game_version": "2.3",
    "communication_mod_version": "1.2.1",
    "bridge_version": "0.1.0",
    "protocol": "communicationmod-json-v1",
}


def test_actions_expand_targets_and_exclude_dead_monsters(combat_envelope: dict) -> None:
    actions = enumerate_legal_actions(combat_envelope)
    commands = [action.command for action in actions]
    assert "PLAY 1 0" in commands
    assert "PLAY 1 1" not in commands
    assert "PLAY 2" in commands
    assert "POTION USE 0 0" in commands
    assert "POTION USE 0 1" not in commands
    assert "POTION DISCARD 0" in commands
    assert "END" in commands
    assert [action.index for action in actions] == list(range(len(actions)))


def test_settings_overlay_can_be_dismissed(combat_envelope: dict) -> None:
    paused = deepcopy(combat_envelope)
    paused["available_commands"] = ["key", "state", "wait"]
    paused["game_state"]["screen_name"] = "SETTINGS"
    paused["game_state"]["combat_state"]["hand"] = []
    paused["game_state"]["combat_state"]["player"]["energy"] = 0

    actions = enumerate_legal_actions(paused)

    assert [action.command for action in actions] == ["KEY CANCEL"]
    assert actions[0].kind == "dismiss_overlay"


def test_first_time_tutorial_can_be_dismissed(combat_envelope: dict) -> None:
    tutorial = deepcopy(combat_envelope)
    tutorial["available_commands"] = ["key", "state", "wait"]
    tutorial["game_state"]["screen_name"] = "FTUE"
    tutorial["game_state"]["combat_state"]["hand"] = []

    actions = enumerate_legal_actions(tutorial)

    assert [action.command for action in actions] == ["KEY CONFIRM"]
    assert actions[0].kind == "dismiss_tutorial"


def test_observer_fields_are_detected(combat_envelope: dict) -> None:
    assert observer_version(combat_envelope) is None
    card = combat_envelope["game_state"]["combat_state"]["hand"][0]
    card.update(
        {
            "raw_description": "Deal !D! damage.",
            "damage": 6,
            "block": 0,
            "magic_number": 0,
            "sts_bench_observer_version": "0.1.0",
        }
    )
    assert observer_version(combat_envelope) == "0.1.0"


def test_observer_version_is_available_at_main_menu() -> None:
    assert (
        observer_version(
            {
                "in_game": False,
                "ready_for_command": True,
                "sts_bench_observer_version": "0.1.0",
            }
        )
        == "0.1.0"
    )


def test_hash_ignores_uuids_and_hidden_draw_order(combat_envelope: dict) -> None:
    changed = deepcopy(combat_envelope)
    combat = changed["game_state"]["combat_state"]
    combat["draw_pile"].reverse()
    for area in (changed["game_state"]["deck"], combat["hand"], combat["draw_pile"]):
        for index, card in enumerate(area):
            card["uuid"] = f"different-{index}"

    first = normalize_state(
        combat_envelope,
        requested_seed="STSBENCHV1000",
        decisions=0,
        engine=ENGINE,
    )
    second = normalize_state(
        changed,
        requested_seed="STSBENCHV1000",
        decisions=0,
        engine=ENGINE,
    )
    assert first.stable_hash() == second.stable_hash()
    prompt = serialize_state(first)
    assert "uuid" not in prompt.lower()
    assert "order hidden" in prompt


def test_terminal_outcome_is_normalized(victory_envelope: dict) -> None:
    state = normalize_state(
        victory_envelope,
        requested_seed="STSBENCHV1000",
        decisions=42,
        engine=ENGINE,
        progress={"bosses_killed": 1, "elites_killed": 2, "acts_cleared": [1]},
    )
    assert state.terminal
    assert state.won
    assert state.floor_reached == 17
