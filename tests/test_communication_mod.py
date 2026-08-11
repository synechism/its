from __future__ import annotations

from copy import deepcopy

from sts_bench.communication_mod import enumerate_legal_actions, normalize_state
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
