from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
PRIME_COMMIT = "1c0720d9753cd9c314b882c2020c19c47b82f78c"
VERIFIERS_COMMIT = "02cc940d8f319cb518556683b31f1e0ecaed827e"


def test_training_stack_pins_are_consistent() -> None:
    stack = tomllib.loads((ROOT / "configs/train/stack.toml").read_text())
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    bootstrap = (ROOT / "scripts/bootstrap-training").read_text()

    assert stack["prime_rl"]["commit"] == PRIME_COMMIT
    assert stack["verifiers"]["commit"] == VERIFIERS_COMMIT
    assert PRIME_COMMIT in bootstrap
    assert VERIFIERS_COMMIT in bootstrap
    assert VERIFIERS_COMMIT in project["project"]["optional-dependencies"]["training"][0]


def test_one_step_config_uses_single_env_process_and_training_seeds() -> None:
    config = tomllib.loads((ROOT / "configs/train/prime-rl-one-step.toml").read_text())
    orchestrator = config["orchestrator"]
    (source,) = orchestrator["train"]["source"]

    assert config["max_steps"] == 1
    assert orchestrator["batch_size"] == orchestrator["group_size"] == 2
    assert orchestrator["algo"]["type"] == "grpo"
    assert orchestrator["train"]["filter_zero_advantages"] is False
    assert source["serve"]["pool"] == {"type": "static", "num_workers": 1}
    assert source["env"]["taskset"]["seed_set"] == "train-v1"
    assert source["env"]["agent"]["harness"]["id"] == "sts-bench"
    assert "eval" not in orchestrator
