from __future__ import annotations

import pytest

from sts_bench.models import LegalAction
from sts_bench.text_protocol import ActionParseError, _card, parse_action, safe_default


def test_only_final_action_line_is_parsed() -> None:
    actions = (
        LegalAction(0, "PLAY 1", "play_card", "play Defend"),
        LegalAction(1, "END", "end_turn", "end turn"),
    )
    assert parse_action("I should block.\nACTION 0", actions) == actions[0]
    with pytest.raises(ActionParseError):
        parse_action("ACTION 0\nextra text", actions)
    with pytest.raises(ActionParseError):
        parse_action("ACTION 9", actions)
    assert safe_default(actions) == actions[1]


def test_enriched_card_observation_serializes_rules_and_live_values() -> None:
    text = _card(
        {
            "name": "Bash",
            "id": "Bash",
            "cost": 2,
            "type": "ATTACK",
            "damage": 10,
            "block": 0,
            "magic_number": 3,
            "keywords": ["vulnerable"],
            "raw_description": "Deal !D! damage. Apply !M! Vulnerable.",
        }
    )

    assert "damage=10" in text
    assert "block=0" in text
    assert "magic=3" in text
    assert "keywords=vulnerable" in text
    assert "text=Deal !D! damage. Apply !M! Vulnerable." in text
