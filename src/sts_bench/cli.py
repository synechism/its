from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import time
from contextlib import ExitStack
from pathlib import Path

from sts_bench.aggregate import write_leaderboard, write_markdown
from sts_bench.episode import EpisodeConfig, play_episode
from sts_bench.evaluator import FirstLegalPolicy, ModelConfig, evaluate_model
from sts_bench.game import LiveGame
from sts_bench.game_process import (
    CommunicationConfigOverride,
    GameProcess,
    resolve_game_launch,
)
from sts_bench.overnight import OvernightConfig, run_overnight
from sts_bench.replay import (
    ExternalRecorder,
    ReplayResult,
    ReplayTimeline,
    burn_action_overlay,
    load_trajectory,
    replay_live,
    verify_determinism,
)
from sts_bench.seeds import load_seed_set
from sts_bench.transport import WorkerServer, run_bridge


def _token(value: str | None) -> str:
    return value if value is not None else os.environ.get("STS_BENCH_TOKEN", "")


def _server(args: argparse.Namespace) -> WorkerServer:
    return WorkerServer(
        args.host,
        args.port,
        token=_token(args.token),
        accept_timeout=args.accept_timeout,
        state_timeout=args.state_timeout,
    )


def _announce(server: WorkerServer) -> None:
    host, port = server.bound_address
    print(f"Listening for a Slay the Spire worker on {host}:{port}.", file=sys.stderr)
    print("Now launch the game with CommunicationMod and the sts-bench bridge.", file=sys.stderr)


def _check_observer(game: LiveGame, *, required: bool) -> None:
    if game.engine.get("observer_version"):
        return
    message = (
        "worker observations do not include Sts Bench Observer card fields; "
        "install/enable the companion mod for benchmark-quality model runs"
    )
    if required:
        raise RuntimeError(message)
    print(f"WARNING: {message}", file=sys.stderr)


def _selected_seeds(args: argparse.Namespace) -> list[str]:
    if args.seeds:
        seeds = [seed.strip().upper() for seed in args.seeds.split(",") if seed.strip()]
    else:
        seeds = load_seed_set(args.seed_set)
    return seeds[: args.limit] if args.limit is not None else seeds


async def _eval(args: argparse.Namespace) -> int:
    seeds = _selected_seeds(args)
    with _server(args) as server:
        _announce(server)
        game = LiveGame(server.accept())
        try:
            _check_observer(game, required=args.require_observer)
            outcomes = await evaluate_model(
                ModelConfig(
                    model=args.model,
                    backend=args.backend,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                    reasoning_effort=args.reasoning_effort,
                    history_turns=args.history_turns,
                    codex_path=args.codex_path,
                    timeout=args.model_timeout,
                ),
                seeds,
                game=game,
                character=args.character,
                ascension=args.ascension,
                max_decisions=args.max_decisions,
                retry_budget=args.retry_budget,
                runs_dir=args.runs_dir,
                benchmark_version=args.seed_set,
                require_observer=args.require_observer,
            )
        finally:
            game.close()
    print(json.dumps([outcome.to_dict() for outcome in outcomes], indent=2, sort_keys=True))
    return 0


async def _smoke(args: argparse.Namespace) -> int:
    with _server(args) as server:
        _announce(server)
        game = LiveGame(server.accept())
        try:
            _check_observer(game, required=False)
            policy = FirstLegalPolicy()
            outcome = await play_episode(
                EpisodeConfig(
                    seed=args.seed,
                    model="first-legal-scripted-policy",
                    character=args.character,
                    ascension=args.ascension,
                    max_decisions=args.max_decisions,
                    retry_budget=0,
                    runs_dir=args.runs_dir,
                    benchmark_version="smoke",
                ),
                policy.respond,
                game=game,
            )
        finally:
            game.close()
    print(json.dumps(outcome.to_dict(), indent=2, sort_keys=True))
    return 0


def _replay(args: argparse.Namespace, *, determinism: bool = False) -> int:
    with _server(args) as server:
        _announce(server)
        with ExitStack() as stack:
            if args.launch_game:
                launch = resolve_game_launch(
                    game_command=args.game_command,
                    game_cwd=args.game_cwd,
                    communication_config=args.communication_config,
                )
                game_log = args.game_log or args.run_dir / "replay.game.log"
                stack.enter_context(CommunicationConfigOverride(launch.communication_config))
                stack.enter_context(GameProcess(launch, game_log))
            game = LiveGame(server.accept())
            try:
                if determinism:
                    result = verify_determinism(game, args.run_dir)
                else:
                    result = _run_replay_with_optional_recording(game, args)
            finally:
                game.close()
    print(json.dumps(dataclasses.asdict(result), indent=2, sort_keys=True))
    return 0 if result.valid else 1


def _run_replay_with_optional_recording(
    game: LiveGame, args: argparse.Namespace
) -> ReplayResult:
    if args.record_display is not None and args.recorder_command:
        raise ValueError("use either --record-display or --recorder-command, not both")
    command = args.recorder_command
    raw_output = args.video_output
    if args.record_display is not None:
        if args.overlay:
            raw_output = args.video_output.with_name(args.video_output.stem + ".raw.mov")
        command = f"/usr/sbin/screencapture -v -x -D{args.record_display} {{output}}"
    elif command and args.overlay:
        raw_output = args.video_output.with_name(
            args.video_output.stem + ".raw" + args.video_output.suffix
        )
    if not command:
        return replay_live(game, args.run_dir, step_delay=args.step_delay)

    manifest, _ = load_trajectory(args.run_dir)
    with ExternalRecorder(command, raw_output) as recorder:
        started_at = recorder.started_at or time.monotonic()
        timeline = ReplayTimeline(started_at=started_at, model=str(manifest.get("model", "model")))
        result = replay_live(
            game,
            args.run_dir,
            step_delay=args.step_delay,
            on_step=timeline.on_step if args.overlay else None,
        )
    replay_result = args.video_output.with_suffix(args.video_output.suffix + ".replay.json")
    replay_result.parent.mkdir(parents=True, exist_ok=True)
    temporary = replay_result.with_suffix(replay_result.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dataclasses.asdict(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(replay_result)
    if args.overlay:
        subtitles = args.video_output.with_suffix(".srt")
        timeline.write_srt(subtitles)
        burn_action_overlay(raw_output, subtitles, args.video_output)
    return result


def _aggregate(args: argparse.Namespace) -> int:
    payload = write_leaderboard(args.runs_dir, args.output)
    write_markdown(payload, args.markdown)
    print(f"wrote {args.output} and {args.markdown}")
    return 0


def _overnight(args: argparse.Namespace) -> int:
    seeds = tuple(_selected_seeds(args))
    if not seeds:
        raise ValueError("no seeds selected")
    command = [
        sys.executable,
        "-m",
        "sts_bench.cli",
        "eval",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--state-timeout",
        str(args.state_timeout),
        "--character",
        args.character,
        "--ascension",
        str(args.ascension),
        "--max-decisions",
        str(args.max_decisions),
        "--runs-dir",
        str(args.runs_dir),
        "--model",
        args.model,
        "--backend",
        args.backend,
        "--temperature",
        str(args.temperature),
        "--max-tokens",
        str(args.max_tokens),
        "--history-turns",
        str(args.history_turns),
        "--codex-path",
        args.codex_path,
        "--model-timeout",
        str(args.model_timeout),
        "--retry-budget",
        str(args.retry_budget),
        "--seed-set",
        args.seed_set,
        "--require-observer" if args.require_observer else "--no-require-observer",
    ]
    optional = (
        ("--token", args.token),
        ("--accept-timeout", args.accept_timeout),
        ("--base-url", args.base_url),
        ("--api-key", args.api_key),
        ("--reasoning-effort", args.reasoning_effort),
    )
    for flag, value in optional:
        if value is not None:
            command.extend([flag, str(value)])
    status_file = args.status_file or args.runs_dir / "overnight-status.json"
    return run_overnight(
        OvernightConfig(
            seeds=seeds,
            model=args.model,
            backend=args.backend,
            character=args.character,
            ascension=args.ascension,
            runs_dir=args.runs_dir,
            benchmark_version=args.seed_set,
            status_file=status_file,
            max_attempts=args.max_attempts,
            startup_timeout=args.startup_timeout,
            episode_timeout=args.episode_timeout,
            restart_delay=args.restart_delay,
            resume=args.resume,
            caffeinate=args.caffeinate,
            controller_base=tuple(command),
            game_command=args.game_command,
            game_cwd=args.game_cwd,
            communication_config=args.communication_config,
        ),
        dry_run=args.dry_run,
    )


def _add_worker_server(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17851)
    parser.add_argument("--token", default=None, help="defaults to STS_BENCH_TOKEN")
    parser.add_argument("--accept-timeout", type=float, default=None)
    parser.add_argument("--state-timeout", type=float, default=120.0)


def _add_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--character", default="Ironclad")
    parser.add_argument("--ascension", type=int, default=0)
    parser.add_argument("--max-decisions", type=int, default=1200)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))


def _add_model_evaluation(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--backend",
        choices=("openai", "codex-cli"),
        default="openai",
        help="direct OpenAI-compatible API or authenticated local Codex CLI",
    )
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=768)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--history-turns", type=int, default=2)
    parser.add_argument("--codex-path", default="codex")
    parser.add_argument("--model-timeout", type=float, default=300.0)
    parser.add_argument("--retry-budget", type=int, default=2)
    parser.add_argument("--seed-set", default="v1")
    parser.add_argument("--seeds", default=None, help="comma-separated override")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--require-observer",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="reject workers missing the Sts Bench Observer card fields (default: true)",
    )


def _add_game_launch(parser: argparse.ArgumentParser, *, opt_in: bool) -> None:
    if opt_in:
        parser.add_argument(
            "--launch-game",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="launch the local modded game and restore CommunicationMod config afterward",
        )
    parser.add_argument(
        "--game-command", default=None, help="override auto-discovered launch command"
    )
    parser.add_argument("--game-cwd", type=Path, default=None)
    parser.add_argument("--communication-config", type=Path, default=None)
    if opt_in:
        parser.add_argument("--game-log", type=Path, default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sts-bench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bridge = subparsers.add_parser("bridge", help="stdio/TCP bridge launched by CommunicationMod")
    bridge.add_argument("--host", default="127.0.0.1")
    bridge.add_argument("--port", type=int, default=17851)
    bridge.add_argument("--token", default=None, help="defaults to STS_BENCH_TOKEN")
    bridge.add_argument("--worker-id", default=None)
    bridge.add_argument("--game-version", default="unknown")
    bridge.add_argument("--mod-the-spire-version", default="unknown")
    bridge.add_argument("--base-mod-version", default="unknown")
    bridge.add_argument("--communication-mod-version", default="unknown")
    bridge.add_argument("--connect-timeout", type=float, default=8.0)
    bridge.add_argument("--error-log", type=Path, default=None)

    evaluate = subparsers.add_parser("eval", help="run an OpenAI-compatible model")
    _add_worker_server(evaluate)
    _add_run(evaluate)
    _add_model_evaluation(evaluate)

    overnight = subparsers.add_parser(
        "overnight", help="resume a seed set with automatic game relaunch and crash retries"
    )
    _add_worker_server(overnight)
    _add_run(overnight)
    _add_model_evaluation(overnight)
    _add_game_launch(overnight, opt_in=False)
    overnight.add_argument("--status-file", type=Path, default=None)
    overnight.add_argument("--max-attempts", type=int, default=3)
    overnight.add_argument("--startup-timeout", type=float, default=90.0)
    overnight.add_argument("--episode-timeout", type=float, default=14_400.0)
    overnight.add_argument("--restart-delay", type=float, default=5.0)
    overnight.add_argument(
        "--resume", action=argparse.BooleanOptionalAction, default=True
    )
    overnight.add_argument(
        "--caffeinate", action=argparse.BooleanOptionalAction, default=True
    )
    overnight.add_argument("--dry-run", action="store_true")

    smoke = subparsers.add_parser("smoke", help="play one live run with a scripted policy")
    _add_worker_server(smoke)
    _add_run(smoke)
    smoke.add_argument("--seed", default="STSBENCHSMOKE1")

    replay = subparsers.add_parser("replay", help="replay a recorded run in the visible game")
    _add_worker_server(replay)
    replay.add_argument("run_dir", type=Path)
    replay.add_argument("--step-delay", type=float, default=0.0)
    replay.add_argument("--recorder-command", default=None)
    replay.add_argument("--video-output", type=Path, default=Path("replay.mp4"))
    replay.add_argument(
        "--record-display",
        type=int,
        default=None,
        metavar="NUMBER",
        help="record a macOS display with screencapture (1 is the main display)",
    )
    replay.add_argument(
        "--overlay",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="burn model, progress, HP, and selected actions into the video",
    )
    _add_game_launch(replay, opt_in=True)

    deterministic = subparsers.add_parser(
        "verify-determinism", help="replay a terminal run twice and compare every state"
    )
    _add_worker_server(deterministic)
    deterministic.add_argument("run_dir", type=Path)
    _add_game_launch(deterministic, opt_in=True)

    aggregate = subparsers.add_parser("aggregate", help="build leaderboard artifacts")
    aggregate.add_argument("--runs-dir", type=Path, default=Path("runs"))
    aggregate.add_argument("--output", type=Path, default=Path("leaderboard.json"))
    aggregate.add_argument("--markdown", type=Path, default=Path("leaderboard.md"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "bridge":
        run_bridge(
            args.host,
            args.port,
            token=_token(args.token),
            worker_id=args.worker_id,
            game_version=args.game_version,
            mod_the_spire_version=args.mod_the_spire_version,
            base_mod_version=args.base_mod_version,
            communication_mod_version=args.communication_mod_version,
            connect_timeout=args.connect_timeout,
            error_log=args.error_log,
        )
        return 0
    if args.command == "eval":
        return asyncio.run(_eval(args))
    if args.command == "overnight":
        return _overnight(args)
    if args.command == "smoke":
        return asyncio.run(_smoke(args))
    if args.command == "replay":
        return _replay(args)
    if args.command == "verify-determinism":
        return _replay(args, determinism=True)
    if args.command == "aggregate":
        return _aggregate(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
