from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from pathlib import Path

from sts_bench.aggregate import write_leaderboard, write_markdown
from sts_bench.episode import EpisodeConfig, play_episode
from sts_bench.evaluator import FirstLegalPolicy, ModelConfig, evaluate_model
from sts_bench.game import LiveGame
from sts_bench.replay import ExternalRecorder, replay_live, verify_determinism
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
            outcomes = await evaluate_model(
                ModelConfig(
                    model=args.model,
                    base_url=args.base_url,
                    api_key=args.api_key,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                    reasoning_effort=args.reasoning_effort,
                    history_turns=args.history_turns,
                ),
                seeds,
                game=game,
                character=args.character,
                ascension=args.ascension,
                max_decisions=args.max_decisions,
                retry_budget=args.retry_budget,
                runs_dir=args.runs_dir,
                benchmark_version=args.seed_set,
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
        game = LiveGame(server.accept())
        try:
            if determinism:
                result = verify_determinism(game, args.run_dir)
            elif args.recorder_command:
                with ExternalRecorder(args.recorder_command, args.video_output):
                    result = replay_live(game, args.run_dir, step_delay=args.step_delay)
            else:
                result = replay_live(game, args.run_dir, step_delay=args.step_delay)
        finally:
            game.close()
    print(json.dumps(dataclasses.asdict(result), indent=2, sort_keys=True))
    return 0 if result.valid else 1


def _aggregate(args: argparse.Namespace) -> int:
    payload = write_leaderboard(args.runs_dir, args.output)
    write_markdown(payload, args.markdown)
    print(f"wrote {args.output} and {args.markdown}")
    return 0


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
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument("--base-url", default=None)
    evaluate.add_argument("--api-key", default=None)
    evaluate.add_argument("--temperature", type=float, default=0.0)
    evaluate.add_argument("--max-tokens", type=int, default=768)
    evaluate.add_argument("--reasoning-effort", default=None)
    evaluate.add_argument("--history-turns", type=int, default=2)
    evaluate.add_argument("--retry-budget", type=int, default=2)
    evaluate.add_argument("--seed-set", default="v1")
    evaluate.add_argument("--seeds", default=None, help="comma-separated override")
    evaluate.add_argument("--limit", type=int, default=None)

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

    deterministic = subparsers.add_parser(
        "verify-determinism", help="replay a terminal run twice and compare every state"
    )
    _add_worker_server(deterministic)
    deterministic.add_argument("run_dir", type=Path)

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
