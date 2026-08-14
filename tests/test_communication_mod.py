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


def test_transient_monster_intent_waits_before_model_actions(combat_envelope: dict) -> None:
    combat_envelope["game_state"]["combat_state"]["monsters"][0]["intent"] = "DEBUG"

    actions = enumerate_legal_actions(combat_envelope)

    assert [action.command for action in actions] == ["WAIT 100"]
    assert actions[0].kind == "wait"
    assert actions[0].label == "wait for monster intent initialization"


def test_observer_fields_are_detected(combat_envelope: dict) -> None:
    assert observer_version(combat_envelope) is None
    card = combat_envelope["game_state"]["combat_state"]["hand"][0]
    card.update(
        {
            "raw_description": "Deal !D! damage.",
            "damage": 6,
            "block": 0,
            "magic_number": 0,
            "sts_bench_observer_version": "0.4.0",
        }
    )
    assert observer_version(combat_envelope) == "0.4.0"


def test_observer_version_is_available_at_main_menu() -> None:
    assert (
        observer_version(
            {
                "in_game": False,
                "ready_for_command": True,
                "sts_bench_observer_version": "0.4.0",
            }
        )
        == "0.4.0"
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


def test_hash_ignores_transient_values_outside_the_hand(combat_envelope: dict) -> None:
    baseline = deepcopy(combat_envelope)
    original_combat = baseline["game_state"]["combat_state"]
    for area in (baseline["game_state"]["deck"], original_combat["draw_pile"]):
        for card in area:
            card.update(
                {
                    "base_cost": card["cost"],
                    "base_damage": 6 if card["type"] == "ATTACK" else -1,
                    "base_block": 5 if card["type"] == "SKILL" else -1,
                    "base_magic_number": -1,
                }
            )
    original_combat["discard_pile"] = [deepcopy(original_combat["draw_pile"][1])]
    changed = deepcopy(baseline)
    changed_combat = changed["game_state"]["combat_state"]
    for card in (
        changed["game_state"]["deck"][0],
        changed_combat["draw_pile"][0],
        changed_combat["discard_pile"][0],
    ):
        card.update(
            {
                "cost": 0,
                "damage": 99,
                "block": 99,
                "magic_number": 99,
                "is_cost_modified": True,
                "is_damage_modified": True,
                "is_block_modified": True,
                "is_magic_number_modified": True,
            }
        )

    first = normalize_state(
        baseline,
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
    discard_card = first.visible["combat"]["discard_pile"][0]["card"]
    assert discard_card["block"] == discard_card["base_block"]


def test_hash_keeps_live_values_for_actionable_hand_cards(combat_envelope: dict) -> None:
    changed = deepcopy(combat_envelope)
    changed["game_state"]["combat_state"]["hand"][0]["damage"] = 99

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

    assert first.stable_hash() != second.stable_hash()


def test_hash_ignores_power_cleanup_on_gone_monsters(combat_envelope: dict) -> None:
    baseline = deepcopy(combat_envelope)
    monster = baseline["game_state"]["combat_state"]["monsters"][1]
    monster.update(
        {
            "current_hp": 0,
            "is_gone": True,
            "half_dead": False,
            "powers": [{"id": "Vulnerable", "name": "Vulnerable", "amount": 1}],
        }
    )
    cleaned = deepcopy(baseline)
    cleaned["game_state"]["combat_state"]["monsters"][1]["powers"] = []

    first = normalize_state(
        baseline,
        requested_seed="STSBENCHV1000",
        decisions=0,
        engine=ENGINE,
    )
    second = normalize_state(
        cleaned,
        requested_seed="STSBENCHV1000",
        decisions=0,
        engine=ENGINE,
    )

    assert first.stable_hash() == second.stable_hash()
    assert first.visible["combat"]["monsters"][1]["powers"] == []


def test_hash_keeps_powers_on_half_dead_monsters(combat_envelope: dict) -> None:
    changed = deepcopy(combat_envelope)
    monster = changed["game_state"]["combat_state"]["monsters"][1]
    monster.update(
        {
            "current_hp": 0,
            "is_gone": True,
            "half_dead": True,
            "powers": [{"id": "Regrow", "name": "Regrow", "amount": 1}],
        }
    )

    state = normalize_state(
        changed,
        requested_seed="STSBENCHV1000",
        decisions=0,
        engine=ENGINE,
    )

    assert state.visible["combat"]["monsters"][1]["powers"] == [
        {"amount": 1, "id": "Regrow", "name": "Regrow"}
    ]


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


def test_normalized_progress_does_not_mutate_retroactively(combat_envelope: dict) -> None:
    progress = {"bosses_killed": 0, "elites_killed": 0, "acts_cleared": []}
    state = normalize_state(
        combat_envelope,
        requested_seed="STSBENCHV1000",
        decisions=0,
        engine=ENGINE,
        progress=progress,
    )

    progress["bosses_killed"] = 1
    progress["acts_cleared"].append(1)

    assert state.progress == {"bosses_killed": 0, "elites_killed": 0, "acts_cleared": []}
