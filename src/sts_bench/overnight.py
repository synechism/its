from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sts_bench.game_process import (
    CommunicationConfigOverride,
    GameProcess,
    resolve_game_launch,
    terminate_process,
)


class BackendUnavailableError(RuntimeError):
    """A backend-wide failure that retries or later seeds cannot resolve."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def expected_model_identity(backend: str, model: str) -> str:
    return f"codex-cli/{model}" if backend == "codex-cli" else model


def completed_runs(
    runs_dir: Path,
    *,
    model: str,
    character: str,
    ascension: int,
    benchmark_version: str,
) -> dict[str, Path]:
    """Return finalized matching runs, preferring the newest duplicate per seed."""
    found: dict[str, Path] = {}
    mtimes: dict[str, float] = {}
    if not runs_dir.exists():
        return found
    for outcome_path in runs_dir.rglob("outcome.json"):
        manifest_path = outcome_path.with_name("manifest.json")
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            json.loads(outcome_path.read_text(encoding="utf-8"))
            manifest_ascension = int(manifest.get("ascension", -1))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            str(manifest.get("model")) != model
            or str(manifest.get("character", "")).lower() != character.lower()
            or manifest_ascension != ascension
            or str(manifest.get("benchmark_version")) != benchmark_version
        ):
            continue
        seed = str(manifest.get("seed", "")).upper()
        modified = outcome_path.stat().st_mtime
        if seed and modified >= mtimes.get(seed, -1.0):
            found[seed] = outcome_path.parent
            mtimes[seed] = modified
    return found


@dataclass(frozen=True, slots=True)
class OvernightConfig:
    seeds: tuple[str, ...]
    model: str
    backend: str
    character: str
    ascension: int
    runs_dir: Path
    benchmark_version: str
    status_file: Path
    max_attempts: int
    startup_timeout: float
    episode_timeout: float
    restart_delay: float
    resume: bool
    caffeinate: bool
    controller_base: tuple[str, ...]
    game_command: str | None
    game_cwd: Path | None
    communication_config: Path | None


def _wait_for_ready(
    process: subprocess.Popen[str],
    log_handle: Any,
    timeout: float,
) -> threading.Thread:
    ready = threading.Event()

    def copy_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            log_handle.write(line)
            log_handle.flush()
            if "Listening for a Slay the Spire worker" in line:
                ready.set()

    reader = threading.Thread(target=copy_output, name="sts-bench-controller-log", daemon=True)
    reader.start()
    if not ready.wait(timeout):
        code = process.poll()
        detail = f" (controller exited with {code})" if code is not None else ""
        raise RuntimeError(f"controller did not become ready within {timeout:g}s{detail}")
    return reader


def _redacted(command: list[str]) -> list[str]:
    result = list(command)
    if "--api-key" in result:
        index = result.index("--api-key")
        if index + 1 < len(result):
            result[index + 1] = "<redacted>"
    if "--token" in result:
        index = result.index("--token")
        if index + 1 < len(result):
            result[index + 1] = "<redacted>"
    return result


def _controller_exit_error(return_code: int, log_path: Path) -> RuntimeError:
    try:
        log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-12_000:]
    except OSError:
        log_tail = ""
    non_retryable_markers = (
        "you've hit your usage limit",
        "insufficient_quota",
        "billing_hard_limit_reached",
    )
    for line in reversed(log_tail.splitlines()):
        if any(marker in line.lower() for marker in non_retryable_markers):
            detail = line.strip()
            prefix = "RuntimeError: "
            if detail.startswith(prefix):
                detail = detail[len(prefix) :]
            return BackendUnavailableError(detail)
    return RuntimeError(f"controller exited with {return_code}")


def run_overnight(config: OvernightConfig, *, dry_run: bool = False) -> int:
    if config.max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    launch = resolve_game_launch(
        game_command=config.game_command,
        game_cwd=config.game_cwd,
        communication_config=config.communication_config,
    )
    identity = expected_model_identity(config.backend, config.model)
    existing = (
        completed_runs(
            config.runs_dir,
            model=identity,
            character=config.character,
            ascension=config.ascension,
            benchmark_version=config.benchmark_version,
        )
        if config.resume
        else {}
    )
    pending = [seed for seed in config.seeds if seed.upper() not in existing]
    session = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    log_dir = config.runs_dir / "_overnight" / session
    status: dict[str, Any] = {
        "schema_version": 1,
        "created_at": _now(),
        "updated_at": _now(),
        "state": "dry_run" if dry_run else "starting",
        "model": identity,
        "backend": config.backend,
        "character": config.character,
        "ascension": config.ascension,
        "benchmark_version": config.benchmark_version,
        "seeds": list(config.seeds),
        "completed": {seed: str(existing[seed]) for seed in config.seeds if seed in existing},
        "pending": pending,
        "failed": {},
        "attempts": [],
        "log_dir": str(log_dir),
        "game_command": list(launch.command),
    }
    _atomic_json(config.status_file, status)
    if dry_run:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0

    log_dir.mkdir(parents=True, exist_ok=True)
    caffeinate: subprocess.Popen[bytes] | None = None
    if config.caffeinate and sys.platform == "darwin":
        caffeinate = subprocess.Popen(
            ["/usr/bin/caffeinate", "-dimsu", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    failures = 0
    try:
        with CommunicationConfigOverride(launch.communication_config):
            for seed in pending:
                succeeded = False
                for attempt in range(1, config.max_attempts + 1):
                    attempt_name = f"{seed}_attempt{attempt}"
                    controller_log = log_dir / f"{attempt_name}.controller.log"
                    game_log = log_dir / f"{attempt_name}.game.log"
                    command = [*config.controller_base, "--seeds", seed, "--limit", "1"]
                    record: dict[str, Any] = {
                        "seed": seed,
                        "attempt": attempt,
                        "started_at": _now(),
                        "controller_command": _redacted(command),
                        "controller_log": str(controller_log),
                        "game_log": str(game_log),
                        "state": "starting",
                    }
                    status["attempts"].append(record)
                    status["state"] = "running"
                    status["current"] = {"seed": seed, "attempt": attempt}
                    status["updated_at"] = _now()
                    _atomic_json(config.status_file, status)
                    print(f"[{seed}] attempt {attempt}/{config.max_attempts}: starting controller")

                    controller: subprocess.Popen[str] | None = None
                    try:
                        with controller_log.open("w", encoding="utf-8") as controller_handle:
                            controller = subprocess.Popen(
                                command,
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True,
                                bufsize=1,
                                start_new_session=True,
                            )
                            reader = _wait_for_ready(
                                controller, controller_handle, config.startup_timeout
                            )
                            record["state"] = "game_running"
                            status["updated_at"] = _now()
                            _atomic_json(config.status_file, status)
                            with GameProcess(launch, game_log):
                                try:
                                    return_code = controller.wait(timeout=config.episode_timeout)
                                except subprocess.TimeoutExpired:
                                    raise RuntimeError(
                                        f"episode exceeded {config.episode_timeout:g}s timeout"
                                    ) from None
                            reader.join(timeout=5)
                            if return_code != 0:
                                raise _controller_exit_error(return_code, controller_log)

                        matches = completed_runs(
                            config.runs_dir,
                            model=identity,
                            character=config.character,
                            ascension=config.ascension,
                            benchmark_version=config.benchmark_version,
                        )
                        if seed not in matches:
                            raise RuntimeError(
                                "controller succeeded without a finalized run artifact"
                            )
                        status["completed"][seed] = str(matches[seed])
                        status["pending"] = [item for item in status["pending"] if item != seed]
                        record.update(
                            {
                                "state": "completed",
                                "finished_at": _now(),
                                "run_dir": str(matches[seed]),
                            }
                        )
                        succeeded = True
                        print(f"[{seed}] completed: {matches[seed]}")
                        break
                    except Exception as error:
                        record.update(
                            {
                                "state": "failed",
                                "finished_at": _now(),
                                "error": f"{type(error).__name__}: {error}",
                            }
                        )
                        print(f"[{seed}] attempt {attempt} failed: {error}", file=sys.stderr)
                        if isinstance(error, BackendUnavailableError):
                            raise
                    finally:
                        if controller is not None:
                            terminate_process(controller)
                        status["updated_at"] = _now()
                        _atomic_json(config.status_file, status)
                    if attempt < config.max_attempts:
                        time.sleep(config.restart_delay)

                if not succeeded:
                    failures += 1
                    status["failed"][seed] = f"exhausted {config.max_attempts} attempts"
                    status["pending"] = [item for item in status["pending"] if item != seed]
                    status["updated_at"] = _now()
                    _atomic_json(config.status_file, status)
    except BaseException as error:
        status["state"] = "interrupted"
        status["updated_at"] = _now()
        status["error"] = f"{type(error).__name__}: {error}"
        _atomic_json(config.status_file, status)
        raise
    finally:
        if caffeinate is not None:
            terminate_process(caffeinate)

    status.pop("current", None)
    status["state"] = "completed" if not failures else "completed_with_failures"
    status["finished_at"] = _now()
    status["updated_at"] = _now()
    _atomic_json(config.status_file, status)
    print(json.dumps(status, indent=2, sort_keys=True))
    return 1 if failures else 0
