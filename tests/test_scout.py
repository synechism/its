from __future__ import annotations

import json
import sys
from dataclasses import replace

from sts_bench.scout import (
    CompletedScout,
    a10_gate,
    completed_scout,
    load_scout_config,
    overnight_command,
)


def test_frozen_scout_command_is_exact_and_dry_run_is_opt_in() -> None:
    config = load_scout_config()
    command = overnight_command(config, 20, dry_run=False)

    assert command[:4] == [sys.executable, "-m", "sts_bench.cli", "overnight"]
    assert command[command.index("--seeds") + 1] == "STSBENCHV1005"
    assert command[command.index("--ascension") + 1] == "20"
    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert command[command.index("--reasoning-effort") + 1] == "high"
    assert command[command.index("--runs-dir") + 1].endswith(
        "runs/ascension-scout-v1/a20"
    )
    assert "--require-observer" in command
    assert "--resume" in command
    assert "--dry-run" not in command
    assert overnight_command(config, 20, dry_run=True)[-1] == "--dry-run"


def test_a10_gate_opens_only_after_a20_defeat(tmp_path) -> None:
    assert a10_gate(None)[0] is False
    victory = CompletedScout(20, tmp_path, True, 51, 900)
    defeat = CompletedScout(20, tmp_path, False, 43, 500)
    assert a10_gate(victory)[0] is False
    assert a10_gate(defeat)[0] is True


def test_completed_scout_ignores_wrong_ascension(tmp_path) -> None:
    config = replace(load_scout_config(), runs_root=tmp_path)
    run_dir = tmp_path / "a20" / "candidate"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "model": "codex-cli/gpt-5.6-sol",
                "seed": config.seed,
                "character": config.character,
                "ascension": 10,
                "benchmark_version": config.seed_set,
            }
        )
    )
    (run_dir / "outcome.json").write_text(
        json.dumps({"won": False, "floor_reached": 12, "score": 100})
    )

    assert completed_scout(config, 20) is None
