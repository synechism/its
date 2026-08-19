from __future__ import annotations

import copy
import json
import statistics
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def load_outcomes(runs_dir: Path) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for path in sorted(runs_dir.rglob("outcome.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest_path = path.with_name("manifest.json")
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["_manifest"] = manifest
        payload["_path"] = str(path.relative_to(runs_dir))
        outcomes.append(payload)
    return outcomes


def filter_outcomes(
    outcomes: Iterable[dict[str, Any]],
    *,
    models: Iterable[str] = (),
    seeds: Iterable[str] = (),
    ascensions: Iterable[int] = (),
    character: str | None = None,
) -> list[dict[str, Any]]:
    """Select an exact benchmark slice without conflating models or difficulty tiers."""
    model_set = set(models)
    seed_set = {seed.upper() for seed in seeds}
    ascension_set = set(ascensions)
    character_key = character.lower() if character else None
    return [
        outcome
        for outcome in outcomes
        if (not model_set or str(outcome.get("model")) in model_set)
        and (not seed_set or str(outcome.get("seed", "")).upper() in seed_set)
        and (not ascension_set or int(outcome.get("ascension", -1)) in ascension_set)
        and (character_key is None or str(outcome.get("character", "")).lower() == character_key)
    ]


def _mean(values: list[float | int]) -> float | None:
    return statistics.fmean(values) if values else None


def _report_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    """Keep benchmark metadata while dropping local process identity from public reports."""
    result = copy.deepcopy(outcome)
    worker = (result.get("metadata") or {}).get("worker") or {}
    worker.pop("id", None)
    worker.pop("pid", None)
    return result


def aggregate_outcomes(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for outcome in outcomes:
        key = (
            str(outcome["model"]),
            str(outcome.get("character", "unknown")),
            int(outcome.get("ascension", 0)),
        )
        grouped[key].append(outcome)

    rows = []
    for (model, character, ascension), model_outcomes in sorted(grouped.items()):
        response_count = sum(int(item.get("response_count", 0)) for item in model_outcomes)
        illegal_count = sum(int(item.get("illegal_action_count", 0)) for item in model_outcomes)
        scores = [int(item["score"]) for item in model_outcomes if item.get("score") is not None]
        rows.append(
            {
                "model": model,
                "character": character,
                "ascension": ascension,
                "runs": len(model_outcomes),
                "wins": sum(bool(item["won"]) for item in model_outcomes),
                "win_rate": _mean([float(bool(item["won"])) for item in model_outcomes]),
                "avg_floor": _mean([int(item["floor_reached"]) for item in model_outcomes]),
                "avg_score": _mean(scores),
                "score_status": "available"
                if len(scores) == len(model_outcomes)
                else "unavailable",
                "act_clear_rate": {
                    str(act): _mean(
                        [float(act in item.get("acts_cleared", ())) for item in model_outcomes]
                    )
                    for act in (1, 2, 3)
                },
                "illegal_action_rate": illegal_count / response_count if response_count else 0.0,
                "forced_default_rate": _mean(
                    [
                        int(item.get("forced_default_count", 0))
                        / max(1, int(item.get("decisions", 0)))
                        for item in model_outcomes
                    ]
                ),
                "avg_decisions": _mean([int(item.get("decisions", 0)) for item in model_outcomes]),
                "tokens_in": sum(int(item.get("tokens_in", 0)) for item in model_outcomes),
                "tokens_out": sum(int(item.get("tokens_out", 0)) for item in model_outcomes),
            }
        )
    rows.sort(
        key=lambda row: (
            row["character"].lower(),
            row["ascension"],
            -(row["win_rate"] or 0),
            -(row["avg_floor"] or 0),
            row["illegal_action_rate"],
            row["model"],
        )
    )
    previous_tier: tuple[str, int] | None = None
    rank = 0
    for row in rows:
        tier = (row["character"].lower(), row["ascension"])
        rank = rank + 1 if tier == previous_tier else 1
        row["rank"] = rank
        previous_tier = tier

    return {
        "schema_version": 2,
        "generated_at": datetime.now(UTC).isoformat(),
        "models": rows,
        "per_seed": sorted(
            [_report_outcome(outcome) for outcome in outcomes],
            key=lambda item: (str(item["model"]), str(item["seed"]), item["_path"]),
        ),
    }


def write_leaderboard(
    runs_dir: Path,
    output: Path,
    *,
    models: Iterable[str] = (),
    seeds: Iterable[str] = (),
    ascensions: Iterable[int] = (),
    character: str | None = None,
) -> dict[str, Any]:
    outcomes = filter_outcomes(
        load_outcomes(runs_dir),
        models=models,
        seeds=seeds,
        ascensions=ascensions,
        character=character,
    )
    payload = aggregate_outcomes(outcomes)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def write_markdown(payload: dict[str, Any], output: Path) -> None:
    lines = [
        "# sts-bench leaderboard",
        "",
        "| Tier rank | Model | Character | Ascension | Record | Avg floor | Avg score | "
        "Act 1 | Act 2 | Act 3 | Illegal |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["models"]:
        act = row["act_clear_rate"]
        avg_score = "—" if row["avg_score"] is None else f"{row['avg_score']:.1f}"
        lines.append(
            f"| {row['rank']} | {row['model']} | {row['character']} | A{row['ascension']} | "
            f"{row['wins']}/{row['runs']} | {row['avg_floor']:.1f} | {avg_score} | "
            f"{act['1']:.1%} | {act['2']:.1%} | {act['3']:.1%} | "
            f"{row['illegal_action_rate']:.1%} |"
        )
    lines.extend(
        [
            "",
            "Ranks reset for every character and Ascension tier; tiers are never ranked against "
            "each other. Within a tier, ranking is by win rate, then average floor, then "
            "illegal-action rate. Raw per-run outcomes remain in the JSON artifact; small pilot "
            "samples should be reported as counts, not population win-rate estimates.",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
