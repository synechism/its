from __future__ import annotations

import json
from importlib import resources


def load_seed_set(version: str = "v1") -> list[str]:
    if not version.replace("-", "").replace("_", "").isalnum():
        raise ValueError(f"invalid seed-set version: {version!r}")
    resource = resources.files("sts_bench").joinpath("benchmark", f"seeds-{version}.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    seeds = payload.get("seeds")
    if not isinstance(seeds, list) or not seeds or any(not isinstance(seed, str) for seed in seeds):
        raise ValueError(f"invalid seed set in {resource}")
    if len(seeds) != len(set(seeds)):
        raise ValueError(f"seed set contains duplicates: {resource}")
    return seeds
