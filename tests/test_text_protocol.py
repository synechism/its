from __future__ import annotations

import pytest

from sts_bench.models import LegalAction
from sts_bench.text_protocol import ActionParseError, parse_action, safe_default


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
