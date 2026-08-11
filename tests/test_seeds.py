from __future__ import annotations

import re

from sts_bench.seeds import load_seed_set


def test_v1_seed_set_is_frozen_unique_and_communicationmod_safe() -> None:
    seeds = load_seed_set("v1")
    assert len(seeds) == 100
    assert len(set(seeds)) == 100
    assert all(re.fullmatch(r"[A-Z0-9]+", seed) for seed in seeds)
