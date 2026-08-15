from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from sts_bench.overnight import (
    OvernightConfig,
    completed_runs,
    expected_model_identity,
    run_overnight,
)


def _write_run(
    root: Path,
    *,
    seed: str,
    model: str = "codex-cli/model-a",
    benchmark_version: str = "v1",
) -> Path:
    run = root / f"run-{seed}"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "model": model,
                "seed": seed,
                "character": "Ironclad",
                "ascension": 0,
                "benchmark_version": benchmark_version,
            }
        ),
        encoding="utf-8",
    )
    (run / "outcome.json").write_text(json.dumps({"seed": seed}), encoding="utf-8")
    return run


def test_completed_runs_filters_the_exact_benchmark_identity(tmp_path: Path) -> None:
    matching = _write_run(tmp_path, seed="SEED1")
    _write_run(tmp_path / "other", seed="SEED2", model="another-model")

    found = completed_runs(
        tmp_path,
        model="codex-cli/model-a",
        character="IRONCLAD",
        ascension=0,
        benchmark_version="v1",
    )

    assert found == {"SEED1": matching}
    assert expected_model_identity("codex-cli", "model-a") == "codex-cli/model-a"
    assert expected_model_identity("openai", "model-a") == "model-a"


def test_dry_run_resumes_finalized_seed_without_mutating_config(tmp_path: Path) -> None:
    completed = _write_run(tmp_path / "runs", seed="SEED1")
    executable = tmp_path / "game"
    executable.touch()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    communication_config = tmp_path / "config.properties"
    original = "runAtGameStart=false\n"
    communication_config.write_text(original, encoding="utf-8")
    status_file = tmp_path / "status.json"
    config = OvernightConfig(
        seeds=("SEED1", "SEED2"),
        model="model-a",
        backend="codex-cli",
        character="Ironclad",
        ascension=0,
        runs_dir=tmp_path / "runs",
        benchmark_version="v1",
        status_file=status_file,
        max_attempts=2,
        startup_timeout=1,
        episode_timeout=1,
        restart_delay=0,
        resume=True,
        caffeinate=False,
        controller_base=("controller",),
        game_command=str(executable),
        game_cwd=cwd,
        communication_config=communication_config,
    )

    assert run_overnight(config, dry_run=True) == 0

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["completed"] == {"SEED1": str(completed)}
    assert status["pending"] == ["SEED2"]
    assert communication_config.read_text(encoding="utf-8") == original


def test_supervisor_launches_game_finalizes_seed_and_restores_config(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    controller = tmp_path / "controller.py"
    controller.write_text(
        textwrap.dedent(
            """
            import json
            import sys
            import time
            from pathlib import Path

            runs_dir = Path(sys.argv[1])
            seed = sys.argv[sys.argv.index("--seeds") + 1]
            print("Listening for a Slay the Spire worker on 127.0.0.1:17851.", flush=True)
            time.sleep(0.2)
            run = runs_dir / f"fake-{seed}"
            run.mkdir(parents=True)
            (run / "manifest.json").write_text(json.dumps({
                "model": "codex-cli/model-a",
                "seed": seed,
                "character": "Ironclad",
                "ascension": 0,
                "benchmark_version": "v1",
            }))
            (run / "outcome.json").write_text(json.dumps({"seed": seed}))
            """
        ),
        encoding="utf-8",
    )
    game = tmp_path / "game.py"
    game.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    communication_config = tmp_path / "config.properties"
    original = "runAtGameStart=false\n"
    communication_config.write_text(original, encoding="utf-8")
    status_file = tmp_path / "status.json"
    config = OvernightConfig(
        seeds=("SEED1",),
        model="model-a",
        backend="codex-cli",
        character="Ironclad",
        ascension=0,
        runs_dir=runs_dir,
        benchmark_version="v1",
        status_file=status_file,
        max_attempts=1,
        startup_timeout=2,
        episode_timeout=2,
        restart_delay=0,
        resume=True,
        caffeinate=False,
        controller_base=(sys.executable, str(controller), str(runs_dir)),
        game_command=f"{sys.executable} {game}",
        game_cwd=tmp_path,
        communication_config=communication_config,
    )

    assert run_overnight(config) == 0

    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["state"] == "completed"
    assert status["pending"] == []
    assert status["completed"]["SEED1"].endswith("fake-SEED1")
    assert status["attempts"][0]["state"] == "completed"
    assert communication_config.read_text(encoding="utf-8") == original
