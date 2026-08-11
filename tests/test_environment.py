from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("verifiers.v1")
environment = pytest.importorskip("sts_bench.environment")
StsBenchEnvConfig = environment.StsBenchEnvConfig
load_environment = environment.load_environment
load_taskset = environment.load_taskset


def test_verifiers_taskset_loads_frozen_seed_rows() -> None:
    config = StsBenchEnvConfig({"limit": 2, "worker_port": 0})
    taskset = load_taskset(config)
    rows = taskset.rows()
    assert [row["seed"] for row in rows] == ["STSBENCHV1000", "STSBENCHV1001"]
    assert rows[0]["max_turns"] == 3600


@pytest.mark.asyncio
async def test_verifiers_environment_starts_and_tears_down_pool() -> None:
    env = load_environment({"limit": 1, "worker_port": 0})
    host, port = env.harness.sts_worker_pool.bound_address
    assert host == "127.0.0.1"
    assert port > 0
    await env.harness.teardown()


def test_checked_in_environment_config_parses() -> None:
    path = Path(__file__).parents[1] / "configs" / "eval" / "sts-bench.toml"
    config = StsBenchEnvConfig.from_toml(path)
    assert config.seed_set == "v1"
    assert config.harness["max_turns"] == 3600
