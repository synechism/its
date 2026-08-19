# Contributing

Code fixes, documentation improvements, installation reports, and benchmark results are welcome.
Do not commit game files, game assets, mod binaries, credentials, local run directories, or raw
screen recordings.

## Development checks

Install all test dependencies and run the same checks as CI:

```bash
uv sync --frozen --extra dev --extra training
uv run ruff check .
uv run pytest
uv build
```

CI covers every supported Python minor version from 3.11 through 3.14. Live tests are excluded
unless explicitly selected because they require a user-owned game installation.

For local game work, run the read-only preflight first:

```bash
uv run sts-bench doctor
```

If automatic discovery is unavailable, `sts-bench doctor --help` lists explicit game, config, and
mod-JAR path overrides. The command never launches the game and never changes CommunicationMod
configuration.

## Benchmark-result submissions

Use a lawful PC copy of Slay the Spire 1 and the required mods. Keep the character, Ascension,
model settings, prompt/protocol version, and seed set fixed for the entire comparison. Benchmark v1
uses the matrix in `configs/eval/benchmark-v1.toml`; its evaluation set is the untouched `v2` seed
set. Do not select seeds based on prior outcomes, silently retry completed losses, or mix tiers in an
aggregate.

Before sharing a result:

1. Confirm the run directory contains `manifest.json`, `outcome.json`, and `trajectory.jsonl`.
2. Run `uv run sts-bench submission validate /path/to/run` and resolve every error.
3. Export a safe summary with
   `uv run sts-bench submission export /path/to/run --output result.submission.tar.gz`.
4. Validate the archive itself with
   `uv run sts-bench submission validate result.submission.tar.gz`.
5. Report the exact model/backend, reasoning effort, character, Ascension, seed-set version, run
   count, wins, floors, scores, and any crashes or retries. Include the archive SHA-256 and disclose
   any deviation from the frozen config.

Summary bundles contain sanitized manifests, outcomes, source-file hashes, redaction metadata, and
the validator's result. They intentionally omit trajectories, so a reviewer can check the bundle's
integrity but cannot audit every action or state hash.

Only add `--include-trajectory` when the recipient needs action-level auditing and you are willing
to share raw model responses, prompts, and visible game text. Full export is refused when the source
contains a recognized secret-like field. Inspect every archive before publishing it; automated
redaction is a safety net, not a guarantee that arbitrary free text contains no sensitive data.

Validation establishes internal consistency: stored states match their SHA-256 hashes, transitions
link, applied commands appear in the recorded legal-action list, outcome counters reconcile, and
archive members match their digests. It does **not** prove model identity, model-provider behavior,
or provenance from an unmodified game and mod stack. Submitters must state those facts honestly.
