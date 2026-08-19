"""PrimeRL/Verifiers v1 adapter backed by authoritative game workers.

The taskset is data-only and may be loaded in the PrimeRL orchestrator. The custom
environment owns the game-worker listener for the lifetime of an env server, while
the harness leases one connected game for each rollout. Model calls always go
through Verifiers' interception endpoint so PrimeRL receives token ids, sampling
log-probabilities, and the final reward in its native trace format.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import verifiers.v1 as vf
from openai import AsyncOpenAI
from pydantic import Field

from sts_bench.episode import EpisodeConfig, play_episode
from sts_bench.models import ModelReply
from sts_bench.pool import WorkerPool
from sts_bench.seeds import load_seed_set
from sts_bench.text_protocol import SYSTEM_PROMPT


def _outcome(trace: vf.Trace) -> dict[str, Any]:
    value = trace.info.get("sts_bench") or {}
    return dict(value) if isinstance(value, Mapping) else {}


class StsBenchData(vf.TaskData):
    """Everything needed to reproduce one seeded game rollout."""

    seed: str
    seed_set: str = "train-v1"
    character: str = "Ironclad"
    ascension: int = Field(15, ge=0, le=20)
    max_decisions: int = Field(1_200, ge=1)
    retry_budget: int = Field(2, ge=0)


class StsBenchTask(vf.Task[StsBenchData]):
    @property
    def key(self) -> str:
        data = self.data
        return (
            f"{data.seed}:{data.character.lower()}:{data.ascension}:"
            f"{data.max_decisions}:{data.retry_budget}"
        )

    @vf.reward(weight=1.0)
    async def win(self, trace: vf.Trace) -> float:
        return float(bool(_outcome(trace).get("won", False)))

    @vf.metric
    async def floor_reached(self, trace: vf.Trace) -> float:
        return float(_outcome(trace).get("floor_reached", 0))

    @vf.metric
    async def bosses_killed(self, trace: vf.Trace) -> float:
        return float(_outcome(trace).get("bosses_killed", 0))

    @vf.metric
    async def illegal_action_rate(self, trace: vf.Trace) -> float:
        return float(_outcome(trace).get("illegal_action_rate", 0.0))


class StsBenchTasksetConfig(vf.TasksetConfig):
    seed_set: str = "train-v1"
    limit: int | None = Field(None, ge=1)
    character: str = "Ironclad"
    ascension: int = Field(15, ge=0, le=20)
    max_decisions: int = Field(1_200, ge=1)
    retry_budget: int = Field(2, ge=0)


class StsBenchTaskset(vf.Taskset[StsBenchTask, StsBenchTasksetConfig]):
    def load(self) -> list[StsBenchTask]:
        config = self.config
        seeds = load_seed_set(config.seed_set)
        if config.limit is not None:
            seeds = seeds[: config.limit]
        return [
            StsBenchTask(
                StsBenchData(
                    idx=index,
                    name=f"{config.seed_set}:{seed}:a{config.ascension}",
                    prompt=f"Play seed {seed} to completion.",
                    system_prompt=SYSTEM_PROMPT,
                    seed=seed,
                    seed_set=config.seed_set,
                    character=config.character,
                    ascension=config.ascension,
                    max_decisions=config.max_decisions,
                    retry_budget=config.retry_budget,
                ),
                config.task,
            )
            for index, seed in enumerate(seeds)
        ]


class StsBenchHarnessConfig(vf.HarnessConfig):
    runs_dir: Path | None = Path("runs/training")
    worker_host: str = "127.0.0.1"
    worker_port: int = Field(17_851, ge=0, le=65_535)
    worker_token_env: str = "STS_BENCH_TOKEN"
    worker_state_timeout: float | None = Field(120.0, gt=0)
    worker_acquire_timeout: float | None = Field(None, gt=0)
    history_turns: int = Field(2, ge=0)
    require_observer: bool = True

    def worker_token(self) -> str:
        return os.environ.get(self.worker_token_env, "") if self.worker_token_env else ""


class StsBenchHarness(vf.Harness[StsBenchHarnessConfig]):
    """In-process game loop whose model calls are captured by Verifiers."""

    APPENDS_SYSTEM_PROMPT = True
    EXECUTES_CODE = False
    NEEDS_CONTAINER = False

    def __init__(self, config: StsBenchHarnessConfig) -> None:
        super().__init__(config)
        self.worker_pool: WorkerPool | None = None

    def attach_worker_pool(self, pool: WorkerPool) -> None:
        if self.worker_pool is not None:
            raise RuntimeError("StsBenchHarness already has a worker pool")
        self.worker_pool = pool

    def detach_worker_pool(self) -> None:
        self.worker_pool = None

    async def launch(
        self,
        ctx: vf.ModelContext,
        trace: vf.Trace,
        runtime: vf.Runtime,
        endpoint: str,
        secret: str,
        mcp_urls: dict[str, str],
        data: vf.TaskData,
    ) -> vf.ProgramResult:
        del runtime, mcp_urls
        if not isinstance(data, StsBenchData):
            raise TypeError(f"expected StsBenchData, got {type(data).__name__}")
        pool = self.worker_pool
        if pool is None:
            raise RuntimeError("game-worker pool is not running")

        game = await asyncio.to_thread(pool.acquire, self.config.worker_acquire_timeout)
        reusable = False
        client: AsyncOpenAI | None = None
        messages: list[dict[str, str]] = [
            {"role": "system", "content": data.system_prompt or SYSTEM_PROMPT}
        ]
        try:
            client = AsyncOpenAI(
                base_url=endpoint,
                api_key=secret,
                timeout=httpx.Timeout(connect=5.0, read=None, write=None, pool=None),
                max_retries=0,
            )
            if self.config.require_observer and not game.engine.get("observer_version"):
                raise RuntimeError(
                    "worker observations do not include Sts Bench Observer card fields; "
                    "install/enable the companion mod before training"
                )

            async def respond(prompt: str, _decision: int, _attempt: int) -> ModelReply:
                messages.append({"role": "user", "content": prompt})
                history_messages = self.config.history_turns * 2
                request_messages = [messages[0]]
                if history_messages:
                    request_messages.extend(messages[1:][-history_messages - 1 :])
                else:
                    request_messages.append(messages[-1])
                response = await client.chat.completions.create(
                    model=ctx.model,
                    messages=request_messages,  # type: ignore[arg-type]
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
                    seed=data.seed,
                    model=ctx.model,
                    character=data.character,
                    ascension=data.ascension,
                    max_decisions=data.max_decisions,
                    retry_budget=data.retry_budget,
                    runs_dir=self.config.runs_dir,
                    benchmark_version=data.seed_set,
                    model_config={
                        "source": "prime-rl/verifiers-v1",
                        "history_turns": self.config.history_turns,
                    },
                    require_observer=self.config.require_observer,
                ),
                respond,
                game=game,
            )
            trace.info["sts_bench"] = outcome.to_dict()
            if game.state.terminal:
                await asyncio.to_thread(game.return_to_menu)
                reusable = True
            return vf.ProgramResult(
                exit_code=0,
                stdout=outcome.terminal_status,
                stderr="",
            )
        finally:
            try:
                if client is not None:
                    await client.close()
            finally:
                if reusable:
                    pool.release(game)
                else:
                    pool.discard(game)


class StsBenchEnvConfig(vf.SingleAgentEnvConfig):
    """Single-agent env with an env-server-scoped authoritative worker pool."""


class StsBenchEnv(vf.Env[StsBenchEnvConfig]):
    def __init__(self, config: StsBenchEnvConfig) -> None:
        super().__init__(config)
        harness = self._harnesses["agent"]
        if not isinstance(harness, StsBenchHarness):
            raise TypeError(
                "sts-bench requires the bundled harness; set "
                "env.agent.harness.id = 'sts-bench'"
            )
        self.sts_harness = harness
        self.worker_pool: WorkerPool | None = None

    async def start(self) -> None:
        if self.worker_pool is not None:
            raise RuntimeError("sts-bench environment is already running")
        config = self.sts_harness.config
        token = config.worker_token()
        if config.worker_host not in {"127.0.0.1", "::1", "localhost"} and not token:
            raise ValueError(
                "a non-loopback game-worker listener requires a token; set "
                f"{config.worker_token_env or 'worker_token_env'}"
            )
        pool = WorkerPool(
            config.worker_host,
            config.worker_port,
            token=token,
            state_timeout=config.worker_state_timeout,
        ).start()
        try:
            self.sts_harness.attach_worker_pool(pool)
        except BaseException:
            await asyncio.to_thread(pool.close)
            raise
        self.worker_pool = pool

    async def stop(self) -> None:
        pool, self.worker_pool = self.worker_pool, None
        self.sts_harness.detach_worker_pool()
        if pool is not None:
            await asyncio.to_thread(pool.close)

    async def run(self, task: vf.Task, agents: vf.Agents) -> None:
        await agents.agent.run(task)


def load_taskset(
    config: StsBenchTasksetConfig | Mapping[str, object] | None = None,
) -> StsBenchTaskset:
    """Convenience constructor for local tests and scripts."""
    resolved = (
        config
        if isinstance(config, StsBenchTasksetConfig)
        else StsBenchTasksetConfig.model_validate(dict(config or {}))
    )
    return StsBenchTaskset(resolved)


def load_environment(
    config: StsBenchEnvConfig | Mapping[str, object] | None = None,
) -> StsBenchEnv:
    """Convenience constructor matching the Verifiers plugin loader."""
    if isinstance(config, StsBenchEnvConfig):
        resolved = config
    else:
        raw = dict(config or {})
        raw.setdefault("taskset", {"id": "sts-bench"})
        raw.setdefault("agent", {"harness": {"id": "sts-bench"}})
        resolved = StsBenchEnvConfig.model_validate(raw)
    return StsBenchEnv(resolved)


__all__ = [
    "StsBenchData",
    "StsBenchEnv",
    "StsBenchEnvConfig",
    "StsBenchHarness",
    "StsBenchHarnessConfig",
    "StsBenchTask",
    "StsBenchTaskset",
    "StsBenchTasksetConfig",
    "load_environment",
    "load_taskset",
]
