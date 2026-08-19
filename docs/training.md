# Training setup

`sts-bench` now targets the current PrimeRL/Verifiers v1 Taskset + Harness API. The exact compatible
revisions are frozen in [`configs/train/stack.toml`](../configs/train/stack.toml); do not float either
repository independently.

## What is already wired

- `StsBenchTaskset` produces typed tasks from the private training seed set.
- `StsBenchEnv` owns one authoritative game-worker pool for the env server's lifetime.
- `StsBenchHarness` leases a game, sends every model request through Verifiers' interception
  endpoint, records the full outcome on the trace, and returns terminal games to the pool.
- `StsBenchTask` emits binary victory reward plus floor, boss, and illegal-action metrics.
- `train-v1` contains 64 training seeds and `train-eval-v1` contains 16 held-out training-eval
  seeds. Both are disjoint from benchmark v1 and the untouched benchmark v2 set.
- [`configs/train/prime-rl-one-step.toml`](../configs/train/prime-rl-one-step.toml) exercises one
  complete rollout-to-update path. It uses PrimeRL's built-in GRPO only as an executable placeholder.

The policy update remains your part. In the pinned PrimeRL checkout, the main handoff points are:

- `src/prime_rl/orchestrator/algo/grpo.py`: group reward baseline and token advantage assignment.
- `src/prime_rl/trainer/rl/loss.py`: importance ratios, policy-gradient loss, trust-region masking,
  and KL regularization.
- `src/prime_rl/orchestrator/algo/__init__.py` and
  `packages/prime-rl-configs/src/prime_rl/configs/algorithm.py`: registration/config only if you add
  a new algorithm name instead of replacing `grpo` while learning.

Keep environment tests green while changing those files. The environment contract ends at scored,
tokenized Verifiers traces; the advantage estimator and optimizer do not belong in `sts-bench`.

## Bootstrap on a Linux GPU machine

PrimeRL training requires NVIDIA GPUs and Python 3.12; the Mac continues to run the licensed Steam game worker.
From a clone of this repository on the GPU machine:

```bash
./scripts/bootstrap-training
uv run --project .training/prime-rl \
  rl @ "$PWD/configs/train/prime-rl-one-step.toml" --dry-run
```

The bootstrap script creates a detached checkout at the frozen PrimeRL commit, initializes the
pinned submodules required by PrimeRL's workspace (including matching Verifiers), installs
`sts-bench` editable into that environment, and verifies the plugin import. It refuses to touch an
existing target. Pass another empty path as its first argument if desired. Use `uv` 0.11.1 or newer,
as required by the pinned PrimeRL checkout.

The dry run validates config only. Before a live run, change the model and GPU layout in the TOML.

## Connect the Mac game to the GPU env server

The checked-in config keeps the game-worker listener on GPU-loopback. Forward the same port from
the Mac:

```bash
ssh -N -L 17851:127.0.0.1:17851 your-gpu-host
```

Keep CommunicationMod's bridge pointed at `127.0.0.1:17851` on the Mac. Start PrimeRL on the GPU
host, then launch Slay the Spire with mods on the Mac. The bridge crosses the SSH tunnel and joins
the pool. If the listener is ever bound to a non-loopback address, export the same random
`STS_BENCH_TOKEN` on both sides; the environment refuses an unauthenticated non-loopback listener.

PrimeRL's env-server pool must remain `static` with `num_workers = 1`: multiple processes cannot
own the same game-listener port. `serve.max_concurrent` controls concurrent rollout requests inside
that process, and the game pool supplies backpressure. Additional legally licensed game copies can
connect to the same listener to increase real parallelism.

## Make optional SFT warm-up data

The exporter recursively finds run directories, defaults to winning runs, removes engine-only
automatic actions and illegal/forced decisions, and normalizes the target to `ACTION <index>`:

```bash
uv run sts-bench export-sft runs/training \
  --output data/sft/sts-bench.jsonl
```

Use `--outcome all` to include clean decisions from losses, or `--include-reasoning` to retain raw
model responses. The default action-only target is safer for a format warm-up. The output is directly
loadable by [`configs/train/sft.toml`](../configs/train/sft.toml). Public v1/v2 benchmark seeds are
blocked by default to prevent leakage; the CLI has an explicit escape hatch for forensic use, not
for producing a benchmarked policy.

## Live-run cautions

- The checked-in one-step config uses group size 2 and batch size 2 only to minimize the first
  integration bill. It is not a statistically useful training setup. It also keeps zero-advantage
  groups so an all-loss first group still reaches the trainer; restore filtering for a real run.
- One game worker serializes the group. At the observed roughly 2.5 hours per complete run, a
  two-rollout step can take roughly five worker-hours.
- `max_decisions` should stay at 1200 for reusable workers. The game bridge intentionally refuses to
  abandon a non-terminal run; truncating an episode discards that connection and requires restarting
  the game.
- The reward is deliberately sparse and game-authoritative: 1 for victory, 0 otherwise. Groups whose
  outcomes are identical have zero centered advantage. Log the fraction of mixed-outcome groups.
- Do not train on `v1` or `v2`. `v2` remains the untouched benchmark holdout; use `train-v1` for
  optimization and `train-eval-v1` for development evaluation.
- Do not configure train and online-eval sources on the same worker port: PrimeRL starts a distinct
  env server per source. Run held-out evaluation separately, or give it another listener and worker
  pool.

## Verification ladder

Run these before spending a full game group:

```bash
uv sync --extra dev --extra training --python 3.13
uv run ruff check .
uv run pytest
uv run python -c \
  "import verifiers.v1 as vf; print(vf.taskset_config_type('sts-bench'))"
uv run --project .training/prime-rl \
  rl @ "$PWD/configs/train/prime-rl-one-step.toml" --dry-run
```

Then do one live group and inspect all of the following before increasing the budget: two terminal
run artifacts, two successful Verifiers traces, per-rollout `win` reward, non-empty sampled-token
masks/log-probabilities, the assigned advantage stream, finite loss/KL/gradient metrics, and a saved
step-1 checkpoint.
