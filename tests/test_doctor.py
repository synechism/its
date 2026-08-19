from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

from sts_bench.doctor import format_doctor_report, run_doctor


def _mod_jar(path: Path, modid: str, version: str = "1.0") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "ModTheSpire.json",
            json.dumps({"modid": modid, "version": version}),
        )
    return path


def _installation(tmp_path: Path, *, autostart: bool = False) -> dict[str, object]:
    resources = tmp_path / "game"
    resources.mkdir()
    (resources / "desktop-1.0.jar").touch()
    config = tmp_path / "config.properties"
    config.write_text(
        f"command={sys.executable} -m sts_bench.cli bridge\n"
        f"runAtGameStart={'true' if autostart else 'false'}\n",
        encoding="utf-8",
    )
    return {
        "game_command": sys.executable,
        "game_cwd": resources,
        "communication_config": config,
        "mod_the_spire_jar": _mod_jar(tmp_path / "ModTheSpire.jar", "modthespire"),
        "base_mod_jar": _mod_jar(tmp_path / "BaseMod.jar", "basemod"),
        "communication_mod_jar": _mod_jar(tmp_path / "CommunicationMod.jar", "CommunicationMod"),
        "observer_jar": _mod_jar(tmp_path / "StsBenchObserver.jar", "stsbenchobserver"),
    }


def test_doctor_accepts_complete_explicit_installation(tmp_path: Path) -> None:
    report = run_doctor(**_installation(tmp_path))

    assert report.valid
    assert report.to_dict()["schema_version"] == 1
    assert "READY" in format_doctor_report(report)
    assert {check.name for check in report.checks} >= {
        "game executable",
        "game JAR",
        "ModTheSpire",
        "BaseMod",
        "CommunicationMod",
        "Sts Bench Observer",
        "bridge command",
    }


def test_doctor_treats_stale_autostart_as_warning(tmp_path: Path) -> None:
    report = run_doctor(**_installation(tmp_path, autostart=True))

    idle = next(check for check in report.checks if check.name == "config idle state")
    assert idle.status == "warn"
    assert report.valid


def test_doctor_fails_when_required_mod_is_missing(tmp_path: Path) -> None:
    installation = _installation(tmp_path)
    installation["observer_jar"] = tmp_path / "missing.jar"

    report = run_doctor(**installation)

    observer = next(check for check in report.checks if check.name == "Sts Bench Observer")
    assert observer.status == "fail"
    assert not report.valid
