from __future__ import annotations

from pathlib import Path

import pytest

from sts_bench.game_process import (
    CommunicationConfigOverride,
    _set_run_at_game_start,
    resolve_game_launch,
)


def test_run_at_game_start_property_is_replaced_or_added() -> None:
    assert _set_run_at_game_start(b"verbose=false\nrunAtGameStart=false\n", True) == (
        b"verbose=false\nrunAtGameStart=true\n"
    )
    assert _set_run_at_game_start(b"verbose=false", True) == (
        b"verbose=false\nrunAtGameStart=true\n"
    )


def test_communication_config_is_restored_after_error(tmp_path: Path) -> None:
    config = tmp_path / "config.properties"
    original = b"# generated\r\nrunAtGameStart=false\r\nverbose=false\r\n"
    config.write_bytes(original)

    with pytest.raises(RuntimeError, match="probe"), CommunicationConfigOverride(config):
        assert b"runAtGameStart=true\r\n" in config.read_bytes()
        raise RuntimeError("probe")

    assert config.read_bytes() == original


def test_custom_launch_does_not_require_macos_discovery(tmp_path: Path) -> None:
    executable = tmp_path / "game"
    executable.touch()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    config = tmp_path / "config.properties"
    config.write_text("runAtGameStart=false\n", encoding="utf-8")

    launch = resolve_game_launch(
        game_command=f"{executable} --flag value",
        game_cwd=cwd,
        communication_config=config,
    )

    assert launch.command == (str(executable), "--flag", "value")
    assert launch.cwd == cwd
    assert launch.communication_config == config
