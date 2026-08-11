from __future__ import annotations

from copy import deepcopy

import pytest


@pytest.fixture
def menu_envelope() -> dict:
    return {
        "available_commands": ["start", "state"],
        "ready_for_command": True,
        "in_game": False,
    }


@pytest.fixture
def combat_envelope() -> dict:
    strike = {
        "name": "Strike",
        "id": "Strike_R",
        "uuid": "random-strike-uuid",
        "is_playable": True,
        "cost": 1,
        "upgrades": 0,
        "type": "ATTACK",
        "rarity": "BASIC",
        "has_target": True,
        "exhausts": False,
        "ethereal": False,
        "description": "Deal 6 damage.",
    }
    defend = {
        "name": "Defend",
        "id": "Defend_R",
        "uuid": "random-defend-uuid",
        "is_playable": True,
        "cost": 1,
        "upgrades": 0,
        "type": "SKILL",
        "rarity": "BASIC",
        "has_target": False,
        "exhausts": False,
        "ethereal": False,
        "description": "Gain 5 Block.",
    }
    return {
        "available_commands": ["play", "end", "potion", "state", "wait"],
        "ready_for_command": True,
        "in_game": True,
        "game_state": {
            "screen_type": "NONE",
            "screen_name": "NONE",
            "screen_state": {},
            "room_phase": "COMBAT",
            "room_type": "MonsterRoomBoss",
            "action_phase": "WAITING_ON_USER",
            "current_hp": 72,
            "max_hp": 80,
            "floor": 16,
            "act": 1,
            "act_boss": "The Guardian",
            "gold": 120,
            "seed": 123456789,
            "class": "IRONCLAD",
            "ascension_level": 0,
            "relics": [{"id": "Burning Blood", "name": "Burning Blood", "counter": -1}],
            "deck": [deepcopy(strike), deepcopy(defend)],
            "potions": [
                {
                    "id": "Fire Potion",
                    "name": "Fire Potion",
                    "can_use": True,
                    "can_discard": True,
                    "requires_target": True,
                },
                {
                    "id": "Potion Slot",
                    "name": "Potion Slot",
                    "can_use": False,
                    "can_discard": False,
                    "requires_target": False,
                },
            ],
            "keys": {"ruby": False, "emerald": False, "sapphire": False},
            "map": [{"x": 1, "y": 1, "symbol": "M", "parents": [], "children": []}],
            "combat_state": {
                "turn": 1,
                "cards_discarded_this_turn": 0,
                "times_damaged": 0,
                "hand": [deepcopy(strike), deepcopy(defend)],
                "draw_pile": [deepcopy(strike), deepcopy(defend), deepcopy(strike)],
                "discard_pile": [],
                "exhaust_pile": [],
                "limbo": [],
                "player": {
                    "current_hp": 72,
                    "max_hp": 80,
                    "block": 0,
                    "energy": 3,
                    "powers": [],
                    "orbs": [],
                },
                "monsters": [
                    {
                        "id": "TheGuardian",
                        "name": "The Guardian",
                        "current_hp": 250,
                        "max_hp": 250,
                        "block": 0,
                        "intent": "ATTACK",
                        "move_adjusted_damage": 9,
                        "move_hits": 1,
                        "half_dead": False,
                        "is_gone": False,
                        "powers": [],
                    },
                    {
                        "id": "DeadMinion",
                        "name": "Dead Minion",
                        "current_hp": 0,
                        "max_hp": 10,
                        "block": 0,
                        "intent": "NONE",
                        "half_dead": False,
                        "is_gone": True,
                        "powers": [],
                    },
                ],
            },
        },
    }


@pytest.fixture
def victory_envelope(combat_envelope: dict) -> dict:
    game = deepcopy(combat_envelope["game_state"])
    game.update(
        {
            "screen_type": "GAME_OVER",
            "screen_name": "VICTORY",
            "screen_state": {"score": 777, "victory": True},
            "room_phase": "COMPLETE",
            "current_hp": 61,
            "floor": 17,
        }
    )
    game.pop("combat_state")
    return {
        "available_commands": ["proceed", "state"],
        "ready_for_command": True,
        "in_game": True,
        "game_state": game,
    }
