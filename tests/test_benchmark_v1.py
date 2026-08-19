from __future__ import annotations

import json
import tomllib
from pathlib import Path


def test_benchmark_v1_freezes_frontier_tiers_and_reserved_seed_set() -> None:
    with Path("configs/eval/benchmark-v1.toml").open("rb") as handle:
        config = tomllib.load(handle)
    seed_payload = json.loads(
        Path("src/sts_bench/benchmark/seeds-v2.json").read_text(encoding="utf-8")
    )

    assert config["calibration_seed"] == "STSBENCHV1005"
    assert config["evaluation_seed_set"] == "v2"
    assert config["tiers"]["standard"]["ascension"] == 15
    assert config["tiers"]["challenge"]["ascension"] == 16
    assert config["tiers"]["maximum"]["ascension"] == 20
    assert len(config["calibration_runs"]) == 6
    assert seed_payload["status"] == "reserved-unobserved"
