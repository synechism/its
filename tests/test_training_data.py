from __future__ import annotations

import json

import pytest

from sts_bench.training_data import export_sft_dataset


def _write_run(root, *, won: bool, rows: list[dict]) -> None:
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": root.name,
                "seed": "STSTRAIN0000",
                "character": "IRONCLAD",
                "ascension": 15,
            }
        ),
        encoding="utf-8",
    )
    (root / "outcome.json").write_text(json.dumps({"won": won}), encoding="utf-8")
    (root / "trajectory.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_sft_export_keeps_only_legal_nonautomatic_winning_decisions(tmp_path) -> None:
    legal = {
        "decision": 7,
        "state_hash": "abc",
        "prompt": "STATE\nLEGAL ACTIONS\n [3] choose",
        "raw_response": "thinking\nACTION 3",
        "action": {"index": 3},
        "automatic": False,
        "legal": True,
        "forced_default": False,
        "parse_errors": [],
    }
    automatic = {**legal, "decision": 8, "automatic": True}
    illegal = {**legal, "decision": 9, "legal": False, "forced_default": True}
    _write_run(tmp_path / "win", won=True, rows=[legal, automatic, illegal])
    _write_run(tmp_path / "loss", won=False, rows=[legal])
    output = tmp_path / "dataset.jsonl"

    stats = export_sft_dataset([tmp_path], output)
    rows = [json.loads(line) for line in output.read_text().splitlines()]

    assert stats.runs_seen == 2
    assert stats.runs_exported == 1
    assert stats.examples_exported == 1
    assert stats.automatic_skipped == 1
    assert stats.invalid_skipped == 1
    assert stats.outcome_filtered == 1
    assert stats.benchmark_filtered == 0
    assert rows[0]["messages"][-1] == {"role": "assistant", "content": "ACTION 3"}
    assert rows[0]["state_hash"] == "abc"


def test_sft_export_can_preserve_raw_reasoning(tmp_path) -> None:
    row = {
        "decision": 1,
        "state_hash": "hash",
        "prompt": "prompt",
        "raw_response": "reason\nACTION 0",
        "action": {"index": 0},
        "automatic": False,
        "legal": True,
        "forced_default": False,
        "parse_errors": [],
    }
    _write_run(tmp_path / "run", won=False, rows=[row])
    output = tmp_path / "dataset.jsonl"

    export_sft_dataset(
        [tmp_path / "run"], output, outcome="all", include_reasoning=True
    )

    exported = json.loads(output.read_text())
    assert exported["messages"][-1]["content"] == "reason\nACTION 0"


def test_sft_export_does_not_publish_empty_output(tmp_path) -> None:
    _write_run(tmp_path / "loss", won=False, rows=[])
    output = tmp_path / "dataset.jsonl"

    with pytest.raises(ValueError, match="no eligible decisions"):
        export_sft_dataset([tmp_path], output)

    assert not output.exists()


def test_sft_export_blocks_benchmark_seeds_by_default(tmp_path) -> None:
    row = {
        "decision": 1,
        "state_hash": "hash",
        "prompt": "prompt",
        "raw_response": "ACTION 0",
        "action": {"index": 0},
        "automatic": False,
        "legal": True,
        "forced_default": False,
        "parse_errors": [],
    }
    _write_run(tmp_path / "run", won=True, rows=[row])
    manifest = json.loads((tmp_path / "run/manifest.json").read_text())
    manifest["seed"] = "STSBENCHV1000"
    (tmp_path / "run/manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="no eligible decisions"):
        export_sft_dataset([tmp_path], tmp_path / "blocked.jsonl")

    stats = export_sft_dataset(
        [tmp_path], tmp_path / "allowed.jsonl", allow_benchmark_seeds=True
    )
    assert stats.examples_exported == 1
