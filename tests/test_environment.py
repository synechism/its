from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("verifiers.v1")
environment = pytest.importorskip("sts_bench.environment")
StsBenchEnvConfig = environment.StsBenchEnvConfig
StsBenchTasksetConfig = environment.StsBenchTasksetConfig
load_environment = environment.load_environment
load_taskset = environment.load_taskset


def test_verifiers_taskset_loads_frozen_training_rows() -> None:
    config = StsBenchTasksetConfig(id="sts-bench", seed_set="train-v1", limit=2)
    tasks = list(load_taskset(config))

    assert [task.data.seed for task in tasks] == ["STSTRAIN0000", "STSTRAIN0001"]
    assert tasks[0].data.ascension == 15
    assert tasks[0].data.max_decisions == 1200


@pytest.mark.asyncio
async def test_task_scores_authoritative_outcome_from_trace() -> None:
    task = next(iter(load_taskset({"id": "sts-bench", "limit": 1})))
    trace = SimpleNamespace(
        info={
            "sts_bench": {
                "won": True,
                "floor_reached": 51,
                "bosses_killed": 3,
                "illegal_action_rate": 0.125,
            }
        }
    )

    assert await task.win(trace) == 1.0
    assert await task.floor_reached(trace) == 51.0
    assert await task.bosses_killed(trace) == 3.0
    assert await task.illegal_action_rate(trace) == 0.125


@pytest.mark.asyncio
async def test_verifiers_environment_starts_and_tears_down_pool() -> None:
    env = load_environment(
        {
            "taskset": {"id": "sts-bench", "seed_set": "train-v1", "limit": 1},
            "agent": {
                "harness": {
                    "id": "sts-bench",
                    "worker_port": 0,
                    "runs_dir": None,
                },
                "runtime": {"type": "subprocess"},
            },
        }
    )
    await env.start()
    try:
        assert env.worker_pool is not None
        host, port = env.worker_pool.bound_address
        assert host == "127.0.0.1"
        assert port > 0
        assert env.sts_harness.worker_pool is env.worker_pool
        with pytest.raises(RuntimeError, match="already running"):
            await env.start()
    finally:
        await env.stop()
    assert env.worker_pool is None
    assert env.sts_harness.worker_pool is None


@pytest.mark.asyncio
async def test_non_loopback_worker_listener_requires_token(monkeypatch) -> None:
    monkeypatch.delenv("STS_BENCH_TOKEN", raising=False)
    env = load_environment(
        {
            "taskset": {"id": "sts-bench", "limit": 1},
            "agent": {
                "harness": {
                    "id": "sts-bench",
                    "worker_host": "0.0.0.0",
                    "worker_port": 0,
                    "runs_dir": None,
                },
                "runtime": {"type": "subprocess"},
            },
        }
    )

    with pytest.raises(ValueError, match="requires a token"):
        await env.start()


def test_checked_in_environment_config_parses() -> None:
    path = Path(__file__).parents[1] / "configs" / "eval" / "sts-bench.toml"
    config = StsBenchEnvConfig.model_validate(tomllib.loads(path.read_text()))

    assert config.taskset.seed_set == "v1"
    assert config.agent.harness.require_observer is True
    assert config.agent.max_turns == 3600
