from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sts_bench.models import Outcome


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return cleaned[:80] or "unknown"


def make_run_id(timestamp: datetime, model: str, character: str, seed: str, ascension: int) -> str:
    stamp = timestamp.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{stamp}_{_slug(model)}_{_slug(character)}_seed{_slug(str(seed))}_asc{ascension}"


@dataclass(slots=True)
class RunArtifacts:
    root: Path
    run_id: str

    @classmethod
    def create(
        cls,
        runs_dir: Path,
        *,
        model: str,
        seed: str,
        character: str,
        ascension: int,
        manifest: dict[str, Any],
    ) -> RunArtifacts:
        timestamp = datetime.now(UTC)
        run_id = make_run_id(timestamp, model, character, seed, ascension)
        root = runs_dir / run_id
        suffix = 1
        while root.exists():
            root = runs_dir / f"{run_id}-{suffix}"
            suffix += 1
        root.mkdir(parents=True)
        run_id = root.name
        payload = {
            "run_id": run_id,
            "created_at": timestamp.isoformat(),
            **manifest,
        }
        cls._write_json(root / "manifest.json", payload)
        (root / "trajectory.jsonl").touch()
        (root / "transcript.txt").write_text(
            f"sts-bench run {run_id}\nmodel={model} seed={seed}\n\n", encoding="utf-8"
        )
        return cls(root=root, run_id=run_id)

    def record(self, row: dict[str, Any], transcript: str) -> None:
        with (self.root / "trajectory.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        with (self.root / "transcript.txt").open("a", encoding="utf-8") as handle:
            handle.write(transcript.rstrip() + "\n\n")

    def finalize(self, outcome: Outcome) -> None:
        outcome.run_id = self.run_id
        self._write_json(self.root / "outcome.json", outcome.to_dict())

    def mark_interrupted(self, error: BaseException) -> None:
        self._write_json(
            self.root / "interrupted.json",
            {
                "run_id": self.run_id,
                "interrupted_at": datetime.now(UTC).isoformat(),
                "error_type": type(error).__name__,
                "message": str(error),
            },
        )

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
