from __future__ import annotations

import json
from pathlib import Path

from sts_bench.aggregate import aggregate_outcomes, filter_outcomes, load_outcomes


def _write_outcome(root: Path, *, model: str, ascension: int, won: bool) -> None:
    run = root / "nested" / f"{model}-{ascension}"
    run.mkdir(parents=True)
    outcome = {
        "model": model,
        "character": "IRONCLAD",
        "ascension": ascension,
        "seed": "SEED1",
        "won": won,
        "floor_reached": 51 if won else 16,
        "score": 900 if won else 200,
        "acts_cleared": [1, 2, 3] if won else [],
        "response_count": 10,
        "illegal_action_count": 0,
        "forced_default_count": 0,
        "decisions": 10,
        "tokens_in": 100,
        "tokens_out": 10,
        "metadata": {"worker": {"id": "local-host", "pid": 123, "game": "STS"}},
    }
    (run / "outcome.json").write_text(json.dumps(outcome), encoding="utf-8")
    (run / "manifest.json").write_text(
        json.dumps({"model": model, "ascension": ascension}), encoding="utf-8"
    )


def test_recursive_loader_and_filters_select_an_exact_slice(tmp_path: Path) -> None:
    _write_outcome(tmp_path, model="model-a", ascension=15, won=True)
    _write_outcome(tmp_path, model="model-a", ascension=16, won=False)

    outcomes = load_outcomes(tmp_path)
    selected = filter_outcomes(
        outcomes,
        models=["model-a"],
        seeds=["seed1"],
        ascensions=[16],
        character="ironclad",
    )

    assert len(outcomes) == 2
    assert len(selected) == 1
    assert selected[0]["ascension"] == 16
    assert selected[0]["_manifest"] == {"model": "model-a", "ascension": 16}
    assert selected[0]["_path"] == "nested/model-a-16/outcome.json"


def test_aggregation_never_combines_different_ascensions(tmp_path: Path) -> None:
    _write_outcome(tmp_path, model="model-a", ascension=15, won=True)
    _write_outcome(tmp_path, model="model-a", ascension=16, won=False)

    payload = aggregate_outcomes(load_outcomes(tmp_path))

    assert payload["schema_version"] == 2
    assert {(row["ascension"], row["wins"], row["runs"]) for row in payload["models"]} == {
        (15, 1, 1),
        (16, 0, 1),
    }
    assert payload["per_seed"][0]["metadata"]["worker"] == {"game": "STS"}
