"""Curate recorded authoritative trajectories into PrimeRL-compatible SFT rows."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from sts_bench.seeds import load_seed_set
from sts_bench.text_protocol import SYSTEM_PROMPT


@dataclass(frozen=True, slots=True)
class SftExportStats:
    runs_seen: int = 0
    runs_exported: int = 0
    examples_exported: int = 0
    automatic_skipped: int = 0
    invalid_skipped: int = 0
    outcome_filtered: int = 0
    benchmark_filtered: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _discover_runs(paths: list[Path]) -> list[Path]:
    found: set[Path] = set()
    for raw in paths:
        path = raw.expanduser().resolve()
        if (path / "manifest.json").is_file() and (path / "trajectory.jsonl").is_file():
            found.add(path)
            continue
        if path.is_dir():
            found.update(
                manifest.parent
                for manifest in path.rglob("manifest.json")
                if (manifest.parent / "trajectory.jsonl").is_file()
            )
            continue
        raise FileNotFoundError(f"no run directory found at {raw}")
    return sorted(found)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def _matches_outcome(run_dir: Path, outcome: Literal["all", "wins", "losses"]) -> bool:
    if outcome == "all":
        return True
    path = run_dir / "outcome.json"
    if not path.is_file():
        return False
    won = bool(_load_json(path).get("won", False))
    return won if outcome == "wins" else not won


def _example(
    manifest: dict[str, Any],
    row: dict[str, Any],
    *,
    include_reasoning: bool,
) -> dict[str, Any] | None:
    if row.get("automatic"):
        return None
    if not row.get("legal") or row.get("forced_default") or row.get("parse_errors"):
        return None
    prompt = row.get("prompt")
    action = row.get("action")
    if not isinstance(prompt, str) or not isinstance(action, dict):
        return None
    try:
        action_index = int(action["index"])
        decision = int(row["decision"])
    except (KeyError, TypeError, ValueError):
        return None
    assistant = row.get("raw_response") if include_reasoning else f"ACTION {action_index}"
    if not isinstance(assistant, str) or not assistant.strip():
        return None
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": assistant.strip()},
        ],
        "run_id": str(manifest.get("run_id", "")),
        "seed": str(manifest.get("seed", "")),
        "character": str(manifest.get("character", "")),
        "ascension": int(manifest.get("ascension", 0)),
        "decision": decision,
        "state_hash": str(row.get("state_hash", "")),
        "action_index": action_index,
    }


def export_sft_dataset(
    inputs: list[Path],
    output: Path,
    *,
    outcome: Literal["all", "wins", "losses"] = "wins",
    include_reasoning: bool = False,
    allow_benchmark_seeds: bool = False,
) -> SftExportStats:
    """Write legal model decisions as JSONL chat examples, atomically."""
    run_dirs = _discover_runs(inputs)
    runs_exported = examples = automatic = invalid = filtered = benchmark_filtered = 0
    benchmark_seeds = set(load_seed_set("v1")) | set(load_seed_set("v2"))
    output = output.expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for run_dir in run_dirs:
                if not _matches_outcome(run_dir, outcome):
                    filtered += 1
                    continue
                manifest = _load_json(run_dir / "manifest.json")
                manifest_seed = str(manifest.get("seed", "")).upper()
                if not allow_benchmark_seeds and manifest_seed in benchmark_seeds:
                    benchmark_filtered += 1
                    continue
                before = examples
                with (run_dir / "trajectory.jsonl").open(encoding="utf-8") as trajectory:
                    for line_number, line in enumerate(trajectory, start=1):
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError as error:
                            raise ValueError(
                                f"invalid JSON in {run_dir / 'trajectory.jsonl'}:"
                                f"{line_number}: {error}"
                            ) from error
                        if not isinstance(row, dict):
                            invalid += 1
                            continue
                        if row.get("automatic"):
                            automatic += 1
                            continue
                        item = _example(manifest, row, include_reasoning=include_reasoning)
                        if item is None:
                            invalid += 1
                            continue
                        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
                        examples += 1
                if examples > before:
                    runs_exported += 1
        if not examples:
            raise ValueError("no eligible decisions found; output was not written")
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return SftExportStats(
        runs_seen=len(run_dirs),
        runs_exported=runs_exported,
        examples_exported=examples,
        automatic_skipped=automatic,
        invalid_skipped=invalid,
        outcome_filtered=filtered,
        benchmark_filtered=benchmark_filtered,
    )


__all__ = ["SftExportStats", "export_sft_dataset"]
