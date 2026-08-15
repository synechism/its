from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType


@dataclass(frozen=True, slots=True)
class GameLaunch:
    command: tuple[str, ...]
    cwd: Path
    communication_config: Path


def default_macos_game_launch() -> GameLaunch:
    """Locate the conventional Steam Workshop installation used by sts-bench."""
    if sys.platform != "darwin":
        raise RuntimeError(
            "automatic game discovery is currently available only on macOS; "
            "pass --game-command, --game-cwd, and --communication-config"
        )
    home = Path.home()
    resources = (
        home
        / "Library/Application Support/Steam/steamapps/common/SlayTheSpire"
        / "SlayTheSpire.app/Contents/Resources"
    )
    mod_the_spire = (
        home
        / "Library/Application Support/Steam/steamapps/workshop/content/646570/1605060445"
        / "ModTheSpire.jar"
    )
    config = home / "Library/Preferences/ModTheSpire/CommunicationMod/config.properties"
    return GameLaunch(
        command=(
            str(resources / "jre/bin/java"),
            "-jar",
            str(mod_the_spire),
            "--skip-launcher",
            "--mods",
            "basemod,CommunicationMod,stsbenchobserver",
        ),
        cwd=resources,
        communication_config=config,
    )


def resolve_game_launch(
    *,
    game_command: str | None = None,
    game_cwd: Path | None = None,
    communication_config: Path | None = None,
) -> GameLaunch:
    discovered = (
        default_macos_game_launch()
        if game_command is None or game_cwd is None or communication_config is None
        else None
    )
    command = (
        tuple(shlex.split(game_command))
        if game_command
        else discovered.command if discovered is not None else ()
    )
    launch = GameLaunch(
        command=command,
        cwd=game_cwd or (discovered.cwd if discovered is not None else Path()),
        communication_config=communication_config
        or (discovered.communication_config if discovered is not None else Path()),
    )
    if not launch.command:
        raise ValueError("game command cannot be empty")
    executable = Path(launch.command[0])
    if executable.is_absolute() and not executable.is_file():
        raise FileNotFoundError(f"game executable does not exist: {executable}")
    if not launch.cwd.is_dir():
        raise FileNotFoundError(f"game working directory does not exist: {launch.cwd}")
    if not launch.communication_config.is_file():
        raise FileNotFoundError(
            f"CommunicationMod config does not exist: {launch.communication_config}"
        )
    return launch


def _set_run_at_game_start(content: bytes, enabled: bool) -> bytes:
    desired = b"runAtGameStart=" + (b"true" if enabled else b"false")
    lines = content.splitlines(keepends=True)
    for index, line in enumerate(lines):
        body = line.rstrip(b"\r\n")
        if body.startswith(b"runAtGameStart="):
            ending = line[len(body) :]
            lines[index] = desired + ending
            return b"".join(lines)
    separator = b"" if not content or content.endswith((b"\n", b"\r")) else b"\n"
    return content + separator + desired + b"\n"


def _atomic_write(path: Path, content: bytes, *, mode: int | None = None) -> None:
    temporary = path.with_name(path.name + ".sts-bench.tmp")
    temporary.write_bytes(content)
    if mode is not None:
        os.chmod(temporary, mode)
    temporary.replace(path)


class CommunicationConfigOverride:
    """Enable CommunicationMod autostart, then restore the exact original config."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._original: bytes | None = None
        self._mode: int | None = None

    def __enter__(self) -> CommunicationConfigOverride:
        self._original = self.path.read_bytes()
        self._mode = self.path.stat().st_mode
        _atomic_write(
            self.path,
            _set_run_at_game_start(self._original, True),
            mode=self._mode,
        )
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self._original is not None:
            _atomic_write(self.path, self._original, mode=self._mode)


def terminate_process(process: subprocess.Popen[object], *, timeout: float = 15.0) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                return
            process.wait(timeout=5)


class GameProcess:
    def __init__(self, launch: GameLaunch, log_path: Path) -> None:
        self.launch = launch
        self.log_path = log_path
        self.process: subprocess.Popen[bytes] | None = None
        self._log = None

    def __enter__(self) -> GameProcess:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("wb")
        try:
            self.process = subprocess.Popen(
                self.launch.command,
                cwd=self.launch.cwd,
                stdin=subprocess.DEVNULL,
                stdout=self._log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            self._log.close()
            self._log = None
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if self.process is not None:
            terminate_process(self.process)
        if self._log is not None:
            self._log.close()
