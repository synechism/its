from __future__ import annotations

import json
import re
from typing import Any

from sts_bench.models import GameState, LegalAction

PROTOCOL_VERSION = "1.1"
SYSTEM_PROMPT = """You are playing a seeded run of the real Slay the Spire 1 game.
The state contains the information available to a human player and an authoritative legal-action
list. You may reason before acting, but your FINAL non-empty line must be exactly:

ACTION <integer>

Use an integer in LEGAL ACTIONS. Never invent an action, state, or hidden draw-pile order. Your
objective is to win the run. Plan across combats, pathing, deck construction, relics, events,
shops, potions, and bosses."""

_ACTION_RE = re.compile(r"^ACTION\s+(\d+)$", re.IGNORECASE)


class ActionParseError(ValueError):
    pass


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _powers(items: list[dict[str, Any]] | None) -> str:
    if not items:
        return "none"
    return ", ".join(
        f"{item.get('name', item.get('id', '?'))}={item.get('amount', '?')}" for item in items
    )


def _card(card: dict[str, Any]) -> str:
    upgrade = f"+{int(card.get('upgrades', 0))}" if card.get("upgrades") else ""
    description = card.get("description") or card.get("raw_description")
    fields = [
        f"{card.get('name', card.get('id', '?'))}{upgrade}",
        f"id={card.get('id', '?')}",
        f"cost={card.get('cost', '?')}",
        f"type={card.get('type', '?')}",
    ]
    for label, key in (
        ("damage", "damage"),
        ("block", "block"),
        ("magic", "magic_number"),
    ):
        value = card.get(key)
        if isinstance(value, int) and value >= 0:
            fields.append(f"{label}={value}")
    if card.get("exhausts"):
        fields.append("exhaust")
    if card.get("ethereal"):
        fields.append("ethereal")
    if card.get("keywords"):
        fields.append("keywords=" + ",".join(str(item) for item in card["keywords"]))
    if description:
        fields.append(f"text={description}")
    return " ".join(fields)


def _pile(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "empty"
    return "; ".join(f"{entry['count']}x {_card(entry['card'])}" for entry in entries)


def serialize_state(state: GameState) -> str:
    """Serialize a stable, compact, player-visible observation."""
    visible = state.visible
    lines = [
        f"STS-BENCH STATE v{PROTOCOL_VERSION} hash={state.stable_hash()}",
        (
            f"RUN seed={state.requested_seed} actual_seed={state.actual_seed} "
            f"character={state.character} ascension={state.ascension} status={state.status} "
            f"phase={state.phase} act={state.act} floor={state.floor_reached} "
            f"decision={state.decisions}"
        ),
        (
            f"PLAYER hp={state.hp}/{state.max_hp} block={state.block} energy={state.energy} "
            f"gold={state.gold}"
        ),
        "RELICS " + (_compact(visible.get("relics")) if visible.get("relics") else "none"),
        "POTIONS " + (_compact(visible.get("potions")) if visible.get("potions") else "none"),
        "KEYS " + _compact(visible.get("keys") or {}),
        "DECK " + _pile(visible.get("deck") or []),
    ]

    combat = visible.get("combat")
    if combat:
        player = combat.get("player") or {}
        lines.extend(
            [
                (
                    f"COMBAT turn={combat.get('turn', '?')} "
                    f"discarded_this_turn={combat.get('cards_discarded_this_turn', 0)} "
                    f"times_damaged={combat.get('times_damaged', 0)} "
                    f"powers=[{_powers(player.get('powers'))}] "
                    f"orbs={_compact(player.get('orbs') or [])}"
                ),
                "HAND",
            ]
        )
        for index, card in enumerate(combat.get("hand") or [], start=1):
            playable = str(bool(card.get("is_playable", False))).lower()
            lines.append(f"  [{index}] {_card(card)} playable={playable}")
        lines.append("ENEMIES")
        for index, monster in enumerate(combat.get("monsters") or []):
            damage = monster.get("move_adjusted_damage")
            hits = monster.get("move_hits")
            attack = ""
            if damage is not None:
                attack = f" shown_damage={damage} hits={hits}"
            lines.append(
                f"  [{index}] {monster.get('name', monster.get('id', '?'))} "
                f"hp={monster.get('current_hp')}/{monster.get('max_hp')} "
                f"block={monster.get('block', 0)} intent={monster.get('intent', 'UNKNOWN')}"
                f"{attack} gone={str(bool(monster.get('is_gone'))).lower()} "
                f"powers=[{_powers(monster.get('powers'))}]"
            )
        lines.extend(
            [
                "DRAW PILE (contents only; order hidden) " + _pile(combat.get("draw_pile") or []),
                "DISCARD PILE " + _pile(combat.get("discard_pile") or []),
                "EXHAUST PILE " + _pile(combat.get("exhaust_pile") or []),
            ]
        )

    lines.append(
        f"ROOM type={visible.get('room_type')} phase={visible.get('room_phase')} "
        f"screen={visible.get('screen_type')} boss={visible.get('act_boss')}"
    )
    screen = visible.get("screen") or {}
    if screen:
        lines.append("SCREEN " + _compact(screen))

    nodes_by_row: dict[int, list[str]] = {}
    for node in visible.get("map") or []:
        row = int(node.get("y", -1))
        children = ",".join(
            f"({child.get('x')},{child.get('y')})" for child in node.get("children") or []
        )
        nodes_by_row.setdefault(row, []).append(
            f"({node.get('x')},{node.get('y')})={node.get('symbol', '?')} -> [{children}]"
        )
    if nodes_by_row:
        lines.append("MAP")
        for row in sorted(nodes_by_row):
            lines.append(f"  row {row}: " + " | ".join(nodes_by_row[row]))

    lines.append("PROGRESS " + _compact(state.progress))
    lines.append("LEGAL ACTIONS")
    for action in state.legal_actions:
        lines.append(f"  [{action.index}] {action.label} ({action.kind})")
    lines.append("Finish with exactly: ACTION <integer>")
    return "\n".join(lines)


def parse_action(response: str, legal_actions: tuple[LegalAction, ...]) -> LegalAction:
    nonempty = [line.strip() for line in response.splitlines() if line.strip()]
    if not nonempty:
        raise ActionParseError("empty response; final line must be ACTION <integer>")
    match = _ACTION_RE.fullmatch(nonempty[-1])
    if not match:
        raise ActionParseError("final non-empty line must be exactly ACTION <integer>")
    index = int(match.group(1))
    for action in legal_actions:
        if action.index == index:
            return action
    valid = ", ".join(str(action.index) for action in legal_actions) or "none"
    raise ActionParseError(f"action {index} is not legal; valid indices: {valid}")


def safe_default(legal_actions: tuple[LegalAction, ...]) -> LegalAction:
    if not legal_actions:
        raise ActionParseError("the game supplied no legal action")
    for preferred in (
        "dismiss_overlay",
        "dismiss_tutorial",
        "end_turn",
        "proceed",
        "wait",
        "return",
    ):
        for action in legal_actions:
            if action.kind == preferred:
                return action
    return legal_actions[0]


def retry_prompt(error: str, state: GameState) -> str:
    actions = "\n".join(f"[{action.index}] {action.label}" for action in state.legal_actions)
    return (
        f"ILLEGAL RESPONSE: {error}\n"
        "The game did not advance. Choose one current legal action:\n"
        f"{actions}\nFinal non-empty line must be exactly ACTION <integer>."
    )
