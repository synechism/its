from __future__ import annotations

import re

from sts_bench.seeds import load_seed_set


def test_v1_seed_set_is_frozen_unique_and_communicationmod_safe() -> None:
    seeds = load_seed_set("v1")
    assert len(seeds) == 100
    assert len(set(seeds)) == 100
    assert all(re.fullmatch(r"[A-Z0-9]+", seed) for seed in seeds)


def test_v2_seed_set_is_reserved_unique_and_disjoint_from_v1() -> None:
    v1 = set(load_seed_set("v1"))
    v2 = load_seed_set("v2")

    assert len(v2) == 10
    assert len(set(v2)) == 10
    assert v1.isdisjoint(v2)
    assert all(re.fullmatch(r"[A-Z0-9]+", seed) for seed in v2)


def test_training_seed_sets_are_frozen_and_disjoint_from_benchmarks() -> None:
    benchmark = set(load_seed_set("v1")) | set(load_seed_set("v2"))
    train = load_seed_set("train-v1")
    held_out = load_seed_set("train-eval-v1")

    assert len(train) == 64
    assert len(held_out) == 16
    assert len(set(train)) == len(train)
    assert len(set(held_out)) == len(held_out)
    assert set(train).isdisjoint(held_out)
    assert benchmark.isdisjoint(train)
    assert benchmark.isdisjoint(held_out)
    assert all(re.fullmatch(r"[A-Z0-9]+", seed) for seed in train + held_out)
