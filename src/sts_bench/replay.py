from __future__ import annotations

import json
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from sts_bench.game import LiveGame


@dataclass(frozen=True, slots=True)
class ReplayResult:
    valid: bool
    decisions_verified: int
    message: str


def load_trajectory(run_dir: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (run_dir / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, rows


def replay_live(game: LiveGame, run_dir: Path, *, step_delay: float = 0.0) -> ReplayResult:
    manifest, rows = load_trajectory(run_dir)
    state = game.reset(
        str(manifest["seed"]), str(manifest["character"]), int(manifest["ascension"])
    )
    for index, row in enumerate(rows):
        if state.stable_hash() != row["state_hash"]:
            return ReplayResult(False, index, f"state hash diverged before decision {index}")
        command = str(row["engine_command"])
        matches = [action for action in state.legal_actions if action.command == command]
        if len(matches) != 1:
            return ReplayResult(False, index, f"recorded command is not legal at decision {index}")
        if step_delay:
            time.sleep(step_delay)
        state = game.step(matches[0])
        if state.stable_hash() != row["resulting_state_hash"]:
            return ReplayResult(False, index + 1, f"state hash diverged after decision {index}")
    return ReplayResult(True, len(rows), "trajectory reproduced exactly in the real game")


def verify_determinism(game: LiveGame, run_dir: Path) -> ReplayResult:
    """Replay one terminal trajectory twice and compare every player-visible state hash."""
    first = replay_live(game, run_dir)
    if not first.valid:
        return first
    if not game.state.terminal:
        return ReplayResult(
            False,
            first.decisions_verified,
            "determinism verification requires a trajectory that reaches a terminal score screen",
        )
    game.return_to_menu()
    second = replay_live(game, run_dir)
    if not second.valid:
        return ReplayResult(
            False,
            second.decisions_verified,
            f"second replay diverged: {second.message}",
        )
    return ReplayResult(
        True,
        second.decisions_verified,
        "same seed and commands produced identical player-visible trajectories twice",
    )


class ExternalRecorder:
    """Manage an ffmpeg/OBS-compatible recorder without invoking a shell."""

    def __init__(self, command_template: str, output: Path) -> None:
        if "{output}" not in command_template:
            raise ValueError("recorder command must contain a {output} placeholder")
        self.command = [
            part.replace("{output}", str(output)) for part in shlex.split(command_template)
        ]
        self.output = output
        self.process: subprocess.Popen[bytes] | None = None
        self._log = None

    def __enter__(self) -> ExternalRecorder:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        log_path = self.output.with_suffix(self.output.suffix + ".recorder.log")
        self._log = log_path.open("wb")
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        time.sleep(1.0)
        if self.process.poll() is not None:
            self._log.close()
            self._log = None
            raise RuntimeError(f"recorder exited early; see {log_path}")
        return self

    def __exit__(self, *_: object) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
        if self._log is not None:
            self._log.close()
