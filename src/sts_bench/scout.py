from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sts_bench.overnight import completed_runs, expected_model_identity
from sts_bench.seeds import load_seed_set

DEFAULT_CONFIG = Path("configs/eval/ascension-scout-v1.toml")


@dataclass(frozen=True, slots=True)
class ScoutConfig:
    experiment_id: str
    strategy: str
    frozen_at: str
    seed_set: str
    seed: str
    character: str
    ascensions: tuple[int, int]
    backend: str
    model: str
    reasoning_effort: str
    temperature: float
    max_tokens: int
    history_turns: int
    model_timeout: float
    max_decisions: int
    retry_budget: int
    max_attempts: int
    startup_timeout: float
    episode_timeout: float
    restart_delay: float
    require_observer: bool
    caffeinate: bool
    resume: bool
    runs_root: Path

    @property
    def a20(self) -> int:
        return self.ascensions[0]

    @property
    def a10(self) -> int:
        return self.ascensions[1]


@dataclass(frozen=True, slots=True)
class CompletedScout:
    ascension: int
    run_dir: Path
    won: bool
    floor_reached: int
    score: int | None


def _required(payload: dict[str, Any], key: str, expected: type) -> Any:
    if key not in payload:
        raise ValueError(f"scout config is missing {key!r}")
    value = payload[key]
    if expected is int and isinstance(value, bool):
        raise ValueError(f"scout config {key!r} must be an integer")
    if not isinstance(value, expected):
        raise ValueError(f"scout config {key!r} must be {expected.__name__}")
    return value


def load_scout_config(path: Path = DEFAULT_CONFIG) -> ScoutConfig:
    config_path = path.expanduser().resolve()
    with config_path.open("rb") as handle:
        payload = tomllib.load(handle)

    if _required(payload, "schema_version", int) != 1:
        raise ValueError("unsupported scout config schema_version")
    raw_ascensions = _required(payload, "ascensions", list)
    if raw_ascensions != [20, 10]:
        raise ValueError("the frozen scout must test A20, then conditionally A10")
    if _required(payload, "strategy", str) != "a20_then_a10_if_a20_loss":
        raise ValueError("unsupported ascension scout strategy")

    seed_set = _required(payload, "seed_set", str)
    seed = _required(payload, "seed", str).upper()
    if seed not in load_seed_set(seed_set):
        raise ValueError(f"seed {seed!r} is not in frozen seed set {seed_set!r}")

    runs_root_raw = Path(_required(payload, "runs_root", str))
    runs_root = runs_root_raw if runs_root_raw.is_absolute() else Path.cwd() / runs_root_raw

    return ScoutConfig(
        experiment_id=_required(payload, "experiment_id", str),
        strategy=payload["strategy"],
        frozen_at=_required(payload, "frozen_at", str),
        seed_set=seed_set,
        seed=seed,
        character=_required(payload, "character", str),
        ascensions=(20, 10),
        backend=_required(payload, "backend", str),
        model=_required(payload, "model", str),
        reasoning_effort=_required(payload, "reasoning_effort", str),
        temperature=float(_required(payload, "temperature", float)),
        max_tokens=_required(payload, "max_tokens", int),
        history_turns=_required(payload, "history_turns", int),
        model_timeout=float(_required(payload, "model_timeout", float)),
        max_decisions=_required(payload, "max_decisions", int),
        retry_budget=_required(payload, "retry_budget", int),
        max_attempts=_required(payload, "max_attempts", int),
        startup_timeout=float(_required(payload, "startup_timeout", float)),
        episode_timeout=float(_required(payload, "episode_timeout", float)),
        restart_delay=float(_required(payload, "restart_delay", float)),
        require_observer=_required(payload, "require_observer", bool),
        caffeinate=_required(payload, "caffeinate", bool),
        resume=_required(payload, "resume", bool),
        runs_root=runs_root.resolve(),
    )


def overnight_command(config: ScoutConfig, ascension: int, *, dry_run: bool) -> list[str]:
    if ascension not in config.ascensions:
        raise ValueError(f"ascension {ascension} is not in the frozen scout")
    command = [
        sys.executable,
        "-m",
        "sts_bench.cli",
        "overnight",
        "--backend",
        config.backend,
        "--model",
        config.model,
        "--reasoning-effort",
        config.reasoning_effort,
        "--seed-set",
        config.seed_set,
        "--seeds",
        config.seed,
        "--character",
        config.character,
        "--ascension",
        str(ascension),
        "--runs-dir",
        str(config.runs_root / f"a{ascension}"),
        "--max-decisions",
        str(config.max_decisions),
        "--retry-budget",
        str(config.retry_budget),
        "--temperature",
        str(config.temperature),
        "--max-tokens",
        str(config.max_tokens),
        "--history-turns",
        str(config.history_turns),
        "--model-timeout",
        str(config.model_timeout),
        "--max-attempts",
        str(config.max_attempts),
        "--startup-timeout",
        str(config.startup_timeout),
        "--episode-timeout",
        str(config.episode_timeout),
        "--restart-delay",
        str(config.restart_delay),
        "--require-observer" if config.require_observer else "--no-require-observer",
        "--caffeinate" if config.caffeinate else "--no-caffeinate",
        "--resume" if config.resume else "--no-resume",
    ]
    if dry_run:
        command.append("--dry-run")
    return command


def completed_scout(config: ScoutConfig, ascension: int) -> CompletedScout | None:
    runs_dir = config.runs_root / f"a{ascension}"
    matches = completed_runs(
        runs_dir,
        model=expected_model_identity(config.backend, config.model),
        character=config.character,
        ascension=ascension,
        benchmark_version=config.seed_set,
    )
    run_dir = matches.get(config.seed)
    if run_dir is None:
        return None
    outcome = json.loads((run_dir / "outcome.json").read_text(encoding="utf-8"))
    return CompletedScout(
        ascension=ascension,
        run_dir=run_dir,
        won=bool(outcome["won"]),
        floor_reached=int(outcome["floor_reached"]),
        score=None if outcome.get("score") is None else int(outcome["score"]),
    )


def a10_gate(a20: CompletedScout | None) -> tuple[bool, str]:
    if a20 is None:
        return False, "closed: A20 has no completed outcome"
    if a20.won:
        return False, "closed: A20 was won, so the A10 probe is redundant"
    return True, "open: A20 was a defeat"


def _result_label(result: CompletedScout | None) -> str:
    if result is None:
        return "pending"
    status = "victory" if result.won else "defeat"
    score = "unavailable" if result.score is None else str(result.score)
    return f"{status}; floor={result.floor_reached}; score={score}; run={result.run_dir}"


def print_status(config: ScoutConfig) -> None:
    a20 = completed_scout(config, config.a20)
    a10 = completed_scout(config, config.a10)
    _, gate = a10_gate(a20)
    print(f"experiment: {config.experiment_id} (frozen {config.frozen_at})")
    print(f"matrix: {config.model} {config.reasoning_effort}, {config.character}, {config.seed}")
    print(f"A20: {_result_label(a20)}")
    print(f"A10: {_result_label(a10)}")
    print(f"A10 gate: {gate}")


def _run(command: list[str]) -> int:
    print(f"$ {shlex.join(command)}", flush=True)
    return subprocess.run(command, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sts-ascension-scout",
        description="Run the frozen A20-first sts-bench capability scout.",
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("action", choices=("check", "status", "a20", "a10"))
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow A10 despite the frozen conditional gate",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_scout_config(args.config)
    if args.force and args.action != "a10":
        raise ValueError("--force is only valid with the a10 action")

    if args.action == "status":
        print_status(config)
        return 0
    if args.action == "check":
        print_status(config)
        for ascension in config.ascensions:
            return_code = _run(overnight_command(config, ascension, dry_run=True))
            if return_code:
                return return_code
        print("preflight complete: no game was launched and no model was called")
        return 0
    if args.action == "a20":
        return _run(overnight_command(config, config.a20, dry_run=False))

    a20 = completed_scout(config, config.a20)
    gate_open, reason = a10_gate(a20)
    if not gate_open and not args.force:
        print(f"refusing to start A10; {reason}", file=sys.stderr)
        return 2
    if not gate_open:
        print(f"WARNING: overriding A10 gate ({reason})", file=sys.stderr)
    return _run(overnight_command(config, config.a10, dry_run=False))


if __name__ == "__main__":
    raise SystemExit(main())
