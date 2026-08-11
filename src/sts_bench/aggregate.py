from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def load_outcomes(runs_dir: Path) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for path in sorted(runs_dir.glob("*/outcome.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_path"] = str(path)
        outcomes.append(payload)
    return outcomes


def _mean(values: list[float | int]) -> float | None:
    return statistics.fmean(values) if values else None


def aggregate_outcomes(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for outcome in outcomes:
        grouped[str(outcome["model"])].append(outcome)

    rows = []
    for model, model_outcomes in sorted(grouped.items()):
        response_count = sum(int(item.get("response_count", 0)) for item in model_outcomes)
        illegal_count = sum(int(item.get("illegal_action_count", 0)) for item in model_outcomes)
        scores = [int(item["score"]) for item in model_outcomes if item.get("score") is not None]
        rows.append(
            {
                "model": model,
                "runs": len(model_outcomes),
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
            -(row["win_rate"] or 0),
            -(row["avg_floor"] or 0),
            row["illegal_action_rate"],
            row["model"],
        )
    )
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "models": rows,
        "per_seed": sorted(
            outcomes, key=lambda item: (str(item["model"]), str(item["seed"]), item["_path"])
        ),
    }


def write_leaderboard(runs_dir: Path, output: Path) -> dict[str, Any]:
    payload = aggregate_outcomes(load_outcomes(runs_dir))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def write_markdown(payload: dict[str, Any], output: Path) -> None:
    lines = [
        "# sts-bench leaderboard",
        "",
        "| Rank | Model | Runs | Win rate | Avg floor | Avg score | "
        "Act 1 | Act 2 | Act 3 | Illegal |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["models"]:
        act = row["act_clear_rate"]
        avg_score = "—" if row["avg_score"] is None else f"{row['avg_score']:.1f}"
        lines.append(
            f"| {row['rank']} | {row['model']} | {row['runs']} | {row['win_rate']:.1%} | "
            f"{row['avg_floor']:.1f} | {avg_score} | {act['1']:.1%} | {act['2']:.1%} | "
            f"{act['3']:.1%} | {row['illegal_action_rate']:.1%} |"
        )
    lines.extend(
        [
            "",
            "Ranking is by win rate, then average floor, then illegal-action rate. Raw per-seed "
            "outcomes remain in the JSON artifact.",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
