"""Optional local verifiers.v1 adapter backed by a real-game worker pool."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import verifiers.v1 as vf
from openai import AsyncOpenAI
from pydantic import Field

from sts_bench.episode import EpisodeConfig, play_episode
from sts_bench.models import ModelReply
from sts_bench.pool import WorkerPool
from sts_bench.seeds import load_seed_set
from sts_bench.text_protocol import SYSTEM_PROMPT


class StsBenchEnvConfig(vf.EnvConfig):
    seed_set: str = "v1"
    limit: int | None = Field(None, ge=1)
    character: str = "Ironclad"
    ascension: int = Field(0, ge=0, le=20)
    max_decisions: int = Field(1200, ge=1)
    retry_budget: int = Field(2, ge=0)
    runs_dir: str | None = None
    worker_host: str = "127.0.0.1"
    worker_port: int = Field(17851, ge=0, le=65535)
    worker_token: str = ""
    worker_state_timeout: float | None = 120.0
    worker_acquire_timeout: float | None = None
    history_turns: int = Field(2, ge=0)
    require_observer: bool = True


def _source(config: StsBenchEnvConfig):
    seeds = load_seed_set(config.seed_set)
    if config.limit is not None:
        seeds = seeds[: config.limit]
    for index, seed in enumerate(seeds):
        yield {
            "example_id": index,
            "seed": seed,
            "prompt": [{"role": "user", "content": f"Play seed {seed} to completion."}],
            "max_turns": config.max_decisions * (config.retry_budget + 1),
            "info": {"seed_set": config.seed_set},
        }


def _outcome(state) -> dict[str, Any]:
    value = state.get("sts_bench") or {}
    return dict(value) if isinstance(value, Mapping) else {}


@vf.reward(weight=1.0)
async def win_reward(task, state) -> float:
    _ = task
    return float(bool(_outcome(state).get("won", False)))


@vf.metric
async def floor_reached(task, state) -> float:
    _ = task
    return float(_outcome(state).get("floor_reached", 0))


@vf.metric
async def bosses_killed(task, state) -> float:
    _ = task
    return float(_outcome(state).get("bosses_killed", 0))


@vf.metric
async def illegal_action_rate(task, state) -> float:
    _ = task
    return float(_outcome(state).get("illegal_action_rate", 0.0))


def load_taskset(config: StsBenchEnvConfig | Mapping[str, object] | None = None) -> vf.Taskset:
    resolved = config if isinstance(config, StsBenchEnvConfig) else StsBenchEnvConfig(config)
    return vf.Taskset(
        source=lambda: _source(resolved),
        taskset_id="sts-bench-v1",
        rewards=[win_reward],
        metrics=[floor_reached, bosses_killed, illegal_action_rate],
        config=resolved.taskset,
    )


def _sampling_args(state) -> dict[str, Any]:
    runtime = state.get("runtime") or {}
    if not isinstance(runtime, Mapping):
        return {}
    value = runtime.get("sampling_args") or {}
    if not isinstance(value, Mapping):
        return {}
    allowed = {
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
        "reasoning_effort",
        "seed",
        "stop",
    }
    return {str(key): item for key, item in value.items() if key in allowed}


async def _program(task, state, *, pool: WorkerPool, config: StsBenchEnvConfig):
    game = await asyncio.to_thread(pool.acquire, config.worker_acquire_timeout)
    reusable = False
    try:
        if config.require_observer and not game.engine.get("observer_version"):
            raise RuntimeError(
                "worker observations do not include Sts Bench Observer card fields; "
                "install/enable the companion mod before running model evaluations"
            )
        client = cast(AsyncOpenAI, state.get_client(api="chat"))
        model = state.get_model()
        messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

        async def respond(prompt: str, _decision: int, _attempt: int) -> ModelReply:
            messages.append({"role": "user", "content": prompt})
            history_messages = config.history_turns * 2
            request_messages = [messages[0]]
            if history_messages:
                request_messages.extend(messages[1:][-history_messages - 1 :])
            else:
                request_messages.append(messages[-1])
            response = await client.chat.completions.create(
                model=model,
                messages=request_messages,  # type: ignore[arg-type]
                **_sampling_args(state),
            )
            choice = response.choices[0]
            content = choice.message.content or ""
            messages.append({"role": "assistant", "content": content})
            usage = response.usage
            return ModelReply(
                content=content,
                tokens_in=int(usage.prompt_tokens if usage else 0),
                tokens_out=int(usage.completion_tokens if usage else 0),
                terminated=choice.finish_reason == "content_filter",
            )

        outcome = await play_episode(
            EpisodeConfig(
                seed=str(task["seed"]),
                model=model,
                character=config.character,
                ascension=config.ascension,
                max_decisions=config.max_decisions,
                retry_budget=config.retry_budget,
                runs_dir=None if config.runs_dir is None else Path(config.runs_dir),
                benchmark_version=config.seed_set,
                model_config={"source": "verifiers.v1", "history_turns": config.history_turns},
                require_observer=config.require_observer,
            ),
            respond,
            game=game,
        )
        state["sts_bench"] = outcome.to_dict()
        state["answer"] = outcome.terminal_status
        if game.state.terminal:
            await asyncio.to_thread(game.return_to_menu)
            reusable = True
        return state
    finally:
        if reusable:
            pool.release(game)
        else:
            pool.discard(game)


def load_environment(config: StsBenchEnvConfig | Mapping[str, object] | None = None) -> vf.Env:
    resolved = config if isinstance(config, StsBenchEnvConfig) else StsBenchEnvConfig(config)
    pool = WorkerPool(
        resolved.worker_host,
        resolved.worker_port,
        token=resolved.worker_token,
        state_timeout=resolved.worker_state_timeout,
    ).start()

    async def program(task, state):
        return await _program(task, state, pool=pool, config=resolved)

    @vf.teardown
    async def close_pool() -> None:
        pool.close()

    lifecycle = vf.Toolset(teardowns=[close_pool])
    harness = vf.Harness(program=program, toolsets=[lifecycle], config=resolved.harness)
    harness.sts_worker_pool = pool
    return vf.Env(taskset=load_taskset(resolved), harness=harness)


load_v1_environment = load_environment

__all__ = [
    "StsBenchEnvConfig",
    "bosses_killed",
    "floor_reached",
    "illegal_action_rate",
    "load_environment",
    "load_taskset",
    "load_v1_environment",
    "win_reward",
]
