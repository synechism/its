from __future__ import annotations

import json
import shlex
import shutil
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sts_bench.game_process import GameLaunch, default_macos_game_launch


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: str
    detail: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def valid(self) -> bool:
        return all(check.status != "fail" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "valid": self.valid,
            "checks": [check.to_dict() for check in self.checks],
        }


def _check(
    name: str,
    condition: bool,
    detail: str,
    *,
    hint: str | None = None,
    required: bool = True,
) -> DoctorCheck:
    return DoctorCheck(name, "pass" if condition else "fail" if required else "warn", detail, hint)


def _command_executable(value: str) -> Path | None:
    path = Path(value).expanduser()
    if path.is_absolute() or len(path.parts) > 1:
        return path if path.is_file() else None
    found = shutil.which(value)
    return Path(found) if found else None


def _properties(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for original in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = original.strip()
        if not line or line.startswith(("#", "!")):
            continue
        separator = next((item for item in ("=", ":") if item in line), None)
        if separator is None:
            continue
        key, value = line.split(separator, 1)
        result[key.strip()] = value.strip()
    return result


def _jar_metadata(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("ModTheSpire.json"))


def _jar_check(name: str, path: Path | None, expected_modid: str) -> DoctorCheck:
    if path is None or not path.is_file():
        shown = str(path) if path is not None else "not discovered"
        return _check(
            name,
            False,
            shown,
            hint=f"install {name} or pass its explicit --*-jar path",
        )
    try:
        metadata = _jar_metadata(path)
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        return _check(name, False, f"{path}: invalid mod JAR ({error})")
    modid = str(metadata.get("modid", ""))
    version = str(metadata.get("version") or metadata.get("mts_version") or "unknown")
    if expected_modid == "modthespire" and version.startswith("999."):
        version = "installed (loader metadata does not expose its release version)"
    return _check(
        name,
        modid.casefold() == expected_modid.casefold(),
        f"{version} at {path}",
        hint=f"expected mod id {expected_modid!r}, found {modid!r}",
    )


def _find_mod(mods_dir: Path, expected_modid: str) -> Path | None:
    if not mods_dir.is_dir():
        return None
    for path in sorted(mods_dir.glob("*.jar")):
        try:
            if str(_jar_metadata(path).get("modid", "")).casefold() == expected_modid.casefold():
                return path
        except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile):
            continue
    return None


def _candidate_launch(
    game_command: str | None,
    game_cwd: Path | None,
    communication_config: Path | None,
) -> tuple[GameLaunch | None, str | None]:
    discovered: GameLaunch | None = None
    if game_command is None or game_cwd is None or communication_config is None:
        try:
            discovered = default_macos_game_launch()
        except RuntimeError as error:
            return None, str(error)
    try:
        command = tuple(shlex.split(game_command)) if game_command else discovered.command
    except ValueError as error:
        return None, f"invalid --game-command: {error}"
    return (
        GameLaunch(
            command=command,
            cwd=game_cwd or discovered.cwd,
            communication_config=communication_config or discovered.communication_config,
        ),
        None,
    )


def run_doctor(
    *,
    game_command: str | None = None,
    game_cwd: Path | None = None,
    communication_config: Path | None = None,
    mod_the_spire_jar: Path | None = None,
    base_mod_jar: Path | None = None,
    communication_mod_jar: Path | None = None,
    observer_jar: Path | None = None,
    require_video: bool = False,
) -> DoctorReport:
    """Inspect a local installation without launching the game or changing any files."""
    checks: list[DoctorCheck] = []
    supported_python = (3, 11) <= sys.version_info[:2] < (3, 15)
    checks.append(
        _check(
            "python",
            supported_python,
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            hint="sts-bench supports Python 3.11 through 3.14",
        )
    )
    uv_path = _command_executable("uv")
    checks.append(
        _check(
            "uv",
            uv_path is not None,
            str(uv_path or "not found"),
            hint="install uv to use the repository commands",
            required=False,
        )
    )
    ffmpeg_path = _command_executable("ffmpeg")
    checks.append(
        _check(
            "ffmpeg",
            ffmpeg_path is not None,
            str(ffmpeg_path or "not found; gameplay evaluation still works"),
            hint="install FFmpeg for optional replay video",
            required=require_video,
        )
    )

    launch, launch_error = _candidate_launch(game_command, game_cwd, communication_config)
    if launch is None:
        checks.append(
            _check(
                "game configuration",
                False,
                launch_error or "could not resolve game paths",
                hint="pass --game-command, --game-cwd, and --communication-config",
            )
        )
        return DoctorReport(tuple(checks))

    executable = _command_executable(launch.command[0]) if launch.command else None
    checks.append(
        _check(
            "game executable",
            executable is not None,
            str(executable or (launch.command[0] if launch.command else "empty command")),
        )
    )
    checks.append(_check("game resources", launch.cwd.is_dir(), str(launch.cwd)))
    game_jar = launch.cwd / "desktop-1.0.jar"
    checks.append(_check("game JAR", game_jar.is_file(), str(game_jar)))

    mods_dir = launch.cwd / "mods"
    command_mts: Path | None = None
    if "-jar" in launch.command:
        index = launch.command.index("-jar")
        if index + 1 < len(launch.command):
            command_mts = Path(launch.command[index + 1]).expanduser()
    if sys.platform == "darwin":
        workshop = (
            Path.home() / "Library/Application Support/Steam/steamapps/workshop/content/646570"
        )
        mod_the_spire_jar = (
            mod_the_spire_jar or command_mts or workshop / "1605060445/ModTheSpire.jar"
        )
        base_mod_jar = base_mod_jar or workshop / "1605833019/BaseMod.jar"
        communication_mod_jar = (
            communication_mod_jar or workshop / "2131373661/CommunicationMod.jar"
        )
    else:
        mod_the_spire_jar = mod_the_spire_jar or command_mts
    base_mod_jar = base_mod_jar or _find_mod(mods_dir, "basemod")
    communication_mod_jar = communication_mod_jar or _find_mod(mods_dir, "CommunicationMod")
    observer_jar = observer_jar or _find_mod(mods_dir, "stsbenchobserver")

    checks.extend(
        (
            _jar_check("ModTheSpire", mod_the_spire_jar, "modthespire"),
            _jar_check("BaseMod", base_mod_jar, "basemod"),
            _jar_check("CommunicationMod", communication_mod_jar, "CommunicationMod"),
            _jar_check("Sts Bench Observer", observer_jar, "stsbenchobserver"),
        )
    )

    config = launch.communication_config
    checks.append(_check("CommunicationMod config", config.is_file(), str(config)))
    if config.is_file():
        try:
            properties = _properties(config)
        except OSError as error:
            checks.append(_check("bridge command", False, f"could not read config: {error}"))
        else:
            configured_command = properties.get("command", "")
            try:
                bridge_parts = shlex.split(configured_command)
            except ValueError as error:
                bridge_parts = []
                bridge_detail = f"invalid command quoting: {error}"
            else:
                bridge_detail = configured_command or "command property is empty"
            bridge_executable = _command_executable(bridge_parts[0]) if bridge_parts else None
            bridge_shape = "bridge" in bridge_parts and bridge_executable is not None
            checks.append(
                _check(
                    "bridge command",
                    bridge_shape,
                    bridge_detail,
                    hint="set command to an absolute sts-bench bridge invocation",
                )
            )
            autostart = properties.get("runAtGameStart", "false").casefold() == "true"
            checks.append(
                _check(
                    "config idle state",
                    not autostart,
                    f"runAtGameStart={'true' if autostart else 'false'}",
                    hint="set false outside supervised runs; overnight toggles it temporarily",
                    required=False,
                )
            )
    if sys.platform == "darwin" and ffmpeg_path is not None:
        checks.append(
            DoctorCheck(
                "screen recording permission",
                "warn",
                "macOS permission cannot be proven without starting a capture",
                "enable Screen & System Audio Recording for the terminal/app before video capture",
            )
        )
    return DoctorReport(tuple(checks))


def format_doctor_report(report: DoctorReport) -> str:
    lines = []
    for check in report.checks:
        lines.append(f"{check.status.upper():4}  {check.name}: {check.detail}")
        if check.status != "pass" and check.hint:
            lines.append(f"      hint: {check.hint}")
    lines.append("READY" if report.valid else "NOT READY")
    return "\n".join(lines)
