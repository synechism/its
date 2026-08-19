from __future__ import annotations

import copy
import json
from collections import Counter
from typing import Any

from sts_bench.models import GameState, LegalAction

PROTOCOL_NAME = "communicationmod-json-v1"
EMPTY_POTION_IDS = {"Potion Slot", "Potion Slot "}
OBSERVER_REQUIRED_CARD_FIELDS = {"raw_description", "damage", "block", "magic_number"}
_BASE_CARD_VALUES = {
    "cost": "base_cost",
    "damage": "base_damage",
    "block": "base_block",
    "magic_number": "base_magic_number",
}
_CARD_MODIFICATION_FLAGS = {
    "is_cost_modified",
    "is_damage_modified",
    "is_block_modified",
    "is_magic_number_modified",
}
_GONE_MONSTER_TRANSIENT_FIELDS = {
    "block",
    "intent",
    "last_move_id",
    "move_adjusted_damage",
    "move_base_damage",
    "move_hits",
    "move_id",
}


class CommunicationError(RuntimeError):
    pass


def _scrub(value: Any) -> Any:
    """Remove process-random identities without changing player-visible ordering."""
    if isinstance(value, dict):
        return {
            key: _scrub(item)
            for key, item in sorted(value.items())
            if key not in {"uuid", "instance_id"}
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _canonical_card(card: dict[str, Any]) -> dict[str, Any]:
    """Return stable rules data for a card that is not currently actionable."""
    result = dict(_scrub(card))
    result.pop("is_playable", None)
    for current, base in _BASE_CARD_VALUES.items():
        if base in result:
            result[current] = result[base]
    for flag in _CARD_MODIFICATION_FLAGS:
        result.pop(flag, None)
    return result


def _stable_non_actionable_cards(value: Any) -> Any:
    """Canonicalize card objects nested in rewards and other non-combat screens."""
    if isinstance(value, dict):
        if "id" in value and "base_cost" in value and "type" in value:
            return _canonical_card(value)
        return {
            key: _stable_non_actionable_cards(item)
            for key, item in sorted(value.items())
            if key not in {"uuid", "instance_id"}
        }
    if isinstance(value, list):
        return [_stable_non_actionable_cards(item) for item in value]
    return value


def _canonical_screen(screen: dict[str, Any]) -> dict[str, Any]:
    """Remove known presentation-only event flavor while retaining actionable text."""
    result = _stable_non_actionable_cards(screen)
    options = result.get("options") or []
    sole_action = (
        str(options[0].get("label", options[0].get("text", ""))).strip("[]").lower()
        if len(options) == 1
        else None
    )
    library_epilogue = result.get("event_id") == "The Library" and sole_action == "leave"
    heart_statistics = result.get("event_id") == "Spire Heart" and sole_action == "sleep"
    if library_epilogue or heart_statistics:
        result.pop("body_text", None)
    return result


def _canonical_monster(monster: dict[str, Any]) -> dict[str, Any]:
    """Remove post-death animation residue from an already-gone monster."""
    result = dict(_scrub(monster))
    if result.get("is_gone", False) and not result.get("half_dead", False):
        result["powers"] = []
        for field in _GONE_MONSTER_TRANSIENT_FIELDS:
            result.pop(field, None)
    return result


def canonical_replay_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply current semantic normalization to a canonical state from any protocol version."""
    result = copy.deepcopy(payload)
    combat = (result.get("visible") or {}).get("combat") or {}
    if "monsters" in combat:
        combat["monsters"] = [
            _canonical_monster(monster) for monster in combat.get("monsters") or []
        ]
    visible = result.get("visible") or {}
    if "screen" in visible:
        visible["screen"] = _canonical_screen(visible.get("screen") or {})
    return result


def _card_key(card: dict[str, Any]) -> str:
    return json.dumps(_canonical_card(card), sort_keys=True, separators=(",", ":"))


def _unordered_cards(cards: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Represent a viewable pile as a multiset, never as its hidden internal order."""
    counts = Counter(_card_key(card) for card in cards or [])
    return [
        {"count": count, "card": json.loads(card_json)}
        for card_json, count in sorted(counts.items())
    ]


def _map_state(nodes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    cleaned = []
    for original in nodes or []:
        node = dict(_scrub(original))
        for edge_name in ("parents", "children"):
            if edge_name in node:
                node[edge_name] = sorted(
                    node[edge_name],
                    key=lambda edge: (int(edge.get("y", -1)), int(edge.get("x", -1))),
                )
        cleaned.append(node)
    return sorted(cleaned, key=lambda node: (int(node.get("y", -1)), int(node.get("x", -1))))


def _alive_monsters(game: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    combat = game.get("combat_state") or {}
    result = []
    for index, monster in enumerate(combat.get("monsters") or []):
        if (
            not monster.get("is_gone", False)
            and not monster.get("half_dead", False)
            and int(monster.get("current_hp", 0)) > 0
        ):
            result.append((index, monster))
    return result


def _card_label(card: dict[str, Any]) -> str:
    upgraded = f"+{int(card.get('upgrades', 0))}" if card.get("upgrades") else ""
    cost = card.get("cost", "?")
    return f"play {card.get('name', card.get('id', 'card'))}{upgraded} (cost {cost})"


def observer_version(envelope: dict[str, Any]) -> str | None:
    """Return the companion observer version from an enriched visible card."""
    advertised = envelope.get("sts_bench_observer_version")
    if advertised:
        return str(advertised)
    game = envelope.get("game_state") or {}
    cards: list[dict[str, Any]] = list(game.get("deck") or [])
    combat = game.get("combat_state") or {}
    for pile in ("hand", "draw_pile", "discard_pile", "exhaust_pile", "limbo"):
        cards.extend(combat.get(pile) or [])
    screen = game.get("screen_state") or {}
    for value in screen.values():
        if isinstance(value, list):
            cards.extend(item for item in value if isinstance(item, dict) and "id" in item)
    for card in cards:
        version = card.get("sts_bench_observer_version")
        if version and OBSERVER_REQUIRED_CARD_FIELDS.issubset(card):
            return str(version)
    return None


def enumerate_legal_actions(envelope: dict[str, Any]) -> tuple[LegalAction, ...]:
    """Expand CommunicationMod's command capabilities into exact, indexed choices."""
    if not envelope.get("in_game"):
        return ()
    game = envelope.get("game_state") or {}
    commands = {str(command).lower() for command in envelope.get("available_commands") or []}
    candidates: list[tuple[str, str, str, dict[str, Any]]] = []
    monsters = _alive_monsters(game)

    # Slay the Spire opens its Settings screen when a windowed game loses
    # focus. That can happen while a local orchestrator starts a controller or
    # recorder. CommunicationMod still reports a stable state, but none of the
    # semantic combat commands can advance until the overlay is dismissed.
    screen_name = str(game.get("screen_name", "")).upper()
    if screen_name == "SETTINGS" and "key" in commands:
        candidates.append(("KEY CANCEL", "dismiss_overlay", "close settings overlay", {}))
    elif screen_name == "FTUE" and "key" in commands:
        candidates.append(("KEY CONFIRM", "dismiss_tutorial", "dismiss tutorial overlay", {}))

    # CommunicationMod can publish the opening combat frame while a monster's
    # move is still initialized to the game's DEBUG sentinel. A slow model call
    # hides this race, while a fast replay can execute an intent-sensitive card
    # (for example Spot Weakness) before the real intent appears. Make that
    # frame an explicit, replayed maintenance transition.
    transient_intent = any(
        str(monster.get("intent", "")).upper() == "DEBUG" for _, monster in monsters
    )
    if not candidates and transient_intent and "wait" in commands:
        candidates.append(
            (
                "WAIT 100",
                "wait",
                "wait for monster intent initialization",
                {"frames": 100},
            )
        )
        return tuple(
            LegalAction(index=index, command=command, kind=kind, label=label, metadata=metadata)
            for index, (command, kind, label, metadata) in enumerate(candidates)
        )

    if "play" in commands:
        hand = (game.get("combat_state") or {}).get("hand") or []
        for hand_index, card in enumerate(hand, start=1):
            if not card.get("is_playable", False):
                continue
            if card.get("has_target", False):
                for monster_index, monster in monsters:
                    candidates.append(
                        (
                            f"PLAY {hand_index} {monster_index}",
                            "play_card",
                            f"{_card_label(card)} -> [{monster_index}] "
                            f"{monster.get('name', monster.get('id'))}",
                            {"hand_index": hand_index, "target_index": monster_index},
                        )
                    )
            else:
                candidates.append(
                    (
                        f"PLAY {hand_index}",
                        "play_card",
                        _card_label(card),
                        {"hand_index": hand_index},
                    )
                )

    if "potion" in commands:
        for slot, potion in enumerate(game.get("potions") or []):
            if str(potion.get("id")) in EMPTY_POTION_IDS:
                continue
            name = potion.get("name", potion.get("id", "potion"))
            if potion.get("can_use", False):
                if potion.get("requires_target", False):
                    for monster_index, monster in monsters:
                        candidates.append(
                            (
                                f"POTION USE {slot} {monster_index}",
                                "use_potion",
                                f"use {name} -> [{monster_index}] "
                                f"{monster.get('name', monster.get('id'))}",
                                {"slot": slot, "target_index": monster_index},
                            )
                        )
                else:
                    candidates.append(
                        (f"POTION USE {slot}", "use_potion", f"use {name}", {"slot": slot})
                    )
            if potion.get("can_discard", False):
                candidates.append(
                    (
                        f"POTION DISCARD {slot}",
                        "discard_potion",
                        f"discard {name}",
                        {"slot": slot},
                    )
                )

    if "end" in commands:
        candidates.append(("END", "end_turn", "end turn", {}))

    if "choose" in commands:
        for choice_index, choice in enumerate(game.get("choice_list") or []):
            candidates.append(
                (
                    f"CHOOSE {choice_index}",
                    "choose",
                    f"choose {choice}",
                    {"choice_index": choice_index, "choice": choice},
                )
            )

    if commands.intersection({"proceed", "confirm"}):
        candidates.append(("PROCEED", "proceed", "proceed / confirm", {}))
    if commands.intersection({"return", "cancel", "leave", "skip"}):
        candidates.append(("RETURN", "return", "return / skip / leave", {}))

    # A rare stable animation state can expose no semantic choice. WAIT asks the
    # game to advance frames and is only available as a last resort.
    if not candidates and "wait" in commands:
        candidates.append(("WAIT 100", "wait", "wait for game animation", {"frames": 100}))

    return tuple(
        LegalAction(index=index, command=command, kind=kind, label=label, metadata=metadata)
        for index, (command, kind, label, metadata) in enumerate(candidates)
    )


def _visible_state(
    game: dict[str, Any], *, deck_override: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    visible = {
        "room_type": game.get("room_type"),
        "room_phase": game.get("room_phase"),
        "screen_type": game.get("screen_type"),
        "screen_name": game.get("screen_name"),
        "act_boss": game.get("act_boss"),
        "keys": _scrub(game.get("keys") or {}),
        "relics": sorted(
            _scrub(game.get("relics") or []), key=lambda item: str(item.get("id", ""))
        ),
        "potions": _scrub(game.get("potions") or []),
        "deck": _unordered_cards(game.get("deck") or []),
        "map": _map_state(game.get("map") or []),
        "screen": _canonical_screen(game.get("screen_state") or {}),
    }
    if deck_override is not None:
        visible["deck"] = copy.deepcopy(deck_override)
    combat = game.get("combat_state")
    if combat is not None:
        combat_copy = copy.deepcopy(combat)
        combat_copy["hand"] = [_scrub(card) for card in combat.get("hand") or []]
        combat_copy["draw_pile"] = _unordered_cards(combat.get("draw_pile") or [])
        combat_copy["discard_pile"] = _unordered_cards(combat.get("discard_pile") or [])
        combat_copy["exhaust_pile"] = _unordered_cards(combat.get("exhaust_pile") or [])
        combat_copy["limbo"] = [_canonical_card(card) for card in combat.get("limbo") or []]
        combat_copy["monsters"] = [
            _canonical_monster(monster) for monster in combat.get("monsters") or []
        ]
        combat_copy["player"] = _scrub(combat.get("player") or {})
        if "card_in_play" in combat_copy:
            combat_copy["card_in_play"] = _canonical_card(combat_copy["card_in_play"])
        visible["combat"] = combat_copy
    return visible


def normalize_state(
    envelope: dict[str, Any],
    *,
    requested_seed: str,
    decisions: int,
    engine: dict[str, Any],
    progress: dict[str, Any] | None = None,
    force_wait_reason: str | None = None,
    deck_override: list[dict[str, Any]] | None = None,
) -> GameState:
    if envelope.get("error"):
        raise CommunicationError(str(envelope["error"]))
    if not envelope.get("ready_for_command", False):
        raise CommunicationError("game sent a state that is not ready for a command")
    if not envelope.get("in_game", False):
        raise CommunicationError("expected an active run, but the game is at its main menu")

    game = envelope.get("game_state")
    if not isinstance(game, dict):
        raise CommunicationError("active-run envelope has no game_state object")
    screen_type = str(game.get("screen_type", "NONE")).upper()
    screen = game.get("screen_state") or {}
    if screen_type == "GAME_OVER":
        status = "victory" if bool(screen.get("victory")) else "defeat"
    else:
        status = "running"
    combat_player = (game.get("combat_state") or {}).get("player") or {}
    phase = "combat" if game.get("room_phase") == "COMBAT" else screen_type.lower()
    if phase == "none":
        phase = str(game.get("room_phase", "unknown")).lower()

    legal_actions = enumerate_legal_actions(envelope)
    if force_wait_reason is not None:
        commands = {str(command).lower() for command in envelope.get("available_commands") or []}
        if "wait" not in commands:
            raise CommunicationError("game cannot settle a transient state without WAIT")
        legal_actions = (
            LegalAction(
                index=0,
                command="WAIT 100",
                kind="wait",
                label=force_wait_reason,
                metadata={"frames": 100},
            ),
        )

    return GameState(
        engine=dict(engine),
        requested_seed=requested_seed,
        actual_seed=None if game.get("seed") is None else int(game["seed"]),
        character=str(game.get("class", "unknown")),
        ascension=int(game.get("ascension_level", 0)),
        status=status,
        phase=phase,
        act=int(game.get("act", 0)),
        floor_reached=int(game.get("floor", 0)),
        decisions=decisions,
        hp=int(game.get("current_hp", 0)),
        max_hp=int(game.get("max_hp", 0)),
        block=int(combat_player.get("block", 0)),
        energy=(None if "energy" not in combat_player else int(combat_player.get("energy", 0))),
        gold=int(game.get("gold", 0)),
        visible=_visible_state(game, deck_override=deck_override),
        legal_actions=legal_actions,
        progress=copy.deepcopy(progress or {}),
    )


def state_score(envelope: dict[str, Any]) -> int | None:
    if not envelope.get("in_game"):
        return None
    game = envelope.get("game_state") or {}
    if str(game.get("screen_type", "")).upper() != "GAME_OVER":
        return None
    score = (game.get("screen_state") or {}).get("score")
    return None if score is None else int(score)
