from __future__ import annotations

import json
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from sts_bench.communication_mod import canonical_replay_state
from sts_bench.game import LiveGame
from sts_bench.models import GameState


@dataclass(frozen=True, slots=True)
class ReplayResult:
    valid: bool
    decisions_verified: int
    message: str
    normalized_comparisons: int = 0


def _first_difference(expected: object, actual: object, path: str = "state") -> str | None:
    if type(expected) is not type(actual):
        return f"{path}: expected {type(expected).__name__}, got {type(actual).__name__}"
    if isinstance(expected, dict):
        actual_dict = actual
        for key in sorted(set(expected) | set(actual_dict)):
            if key not in expected:
                return f"{path}.{key}: unexpected field"
            if key not in actual_dict:
                return f"{path}.{key}: missing field"
            difference = _first_difference(expected[key], actual_dict[key], f"{path}.{key}")
            if difference:
                return difference
        return None
    if isinstance(expected, list):
        actual_list = actual
        if len(expected) != len(actual_list):
            return f"{path}: expected {len(expected)} items, got {len(actual_list)}"
        pairs = zip(expected, actual_list, strict=True)
        for index, (expected_item, actual_item) in enumerate(pairs):
            difference = _first_difference(expected_item, actual_item, f"{path}[{index}]")
            if difference:
                return difference
        return None
    if expected != actual:
        return f"{path}: expected {expected!r}, got {actual!r}"
    return None


def _divergence_message(
    prefix: str,
    expected_hash: str,
    state: object,
    expected_state: object | None,
) -> str:
    actual_hash = state.stable_hash()
    detail = None
    if expected_state is not None:
        actual_state = canonical_replay_state(json.loads(json.dumps(state.canonical_dict())))
        detail = _first_difference(canonical_replay_state(expected_state), actual_state)
    suffix = f"; {detail}" if detail else ""
    return f"{prefix}: expected {expected_hash}, got {actual_hash}{suffix}"


def _matches_recorded_state(
    state: GameState,
    expected_hash: str,
    expected_state: dict | None,
) -> tuple[bool, bool]:
    """Return (matches, needed legacy normalization)."""
    if state.stable_hash() == expected_hash:
        return True, False
    if expected_state is None:
        return False, False
    actual_state = canonical_replay_state(json.loads(json.dumps(state.canonical_dict())))
    expected = canonical_replay_state(expected_state)
    return _first_difference(expected, actual_state) is None, True


def load_trajectory(run_dir: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (run_dir / "trajectory.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, rows


ReplayStep = Callable[[int, int, dict, GameState], None]


def replay_live(
    game: LiveGame,
    run_dir: Path,
    *,
    step_delay: float = 0.0,
    on_step: ReplayStep | None = None,
) -> ReplayResult:
    manifest, rows = load_trajectory(run_dir)
    normalized_comparisons = 0
    state = game.reset(
        str(manifest["seed"]), str(manifest["character"]), int(manifest["ascension"])
    )
    for index, row in enumerate(rows):
        matches, normalized = _matches_recorded_state(state, row["state_hash"], row.get("state"))
        if not matches:
            return ReplayResult(
                False,
                index,
                _divergence_message(
                    f"state hash diverged before decision {index}",
                    row["state_hash"],
                    state,
                    row.get("state"),
                ),
                normalized_comparisons,
            )
        normalized_comparisons += int(normalized)
        command = str(row["engine_command"])
        matches = [action for action in state.legal_actions if action.command == command]
        if len(matches) != 1:
            return ReplayResult(False, index, f"recorded command is not legal at decision {index}")
        if on_step is not None:
            on_step(index, len(rows), row, state)
        if step_delay:
            time.sleep(step_delay)
        state = game.step(matches[0], count_decision=not bool(row.get("automatic", False)))
        expected_state = rows[index + 1].get("state") if index + 1 < len(rows) else None
        matches, normalized = _matches_recorded_state(
            state, row["resulting_state_hash"], expected_state
        )
        if not matches:
            return ReplayResult(
                False,
                index + 1,
                _divergence_message(
                    f"state hash diverged after decision {index}",
                    row["resulting_state_hash"],
                    state,
                    expected_state,
                ),
                normalized_comparisons,
            )
        normalized_comparisons += int(normalized)
    if normalized_comparisons:
        message = (
            "trajectory reproduced semantically in the real game; "
            f"{normalized_comparisons} legacy comparisons differed only in ignored "
            "presentation-only residue"
        )
    else:
        message = "trajectory reproduced exactly in the real game"
    return ReplayResult(True, len(rows), message, normalized_comparisons)


def verify_determinism(game: LiveGame, run_dir: Path) -> ReplayResult:
    """Replay one terminal trajectory twice and compare every player-visible state hash."""
    first = replay_live(game, run_dir)
    if not first.valid:
        return first
    if not game.state.terminal:
        return ReplayResult(
            False,
            first.decisions_verified,
            "determinism verification requires a trajectory that reaches a terminal score screen",
        )
    game.return_to_menu()
    second = replay_live(game, run_dir)
    if not second.valid:
        return ReplayResult(
            False,
            second.decisions_verified,
            f"second replay diverged: {second.message}",
        )
    return ReplayResult(
        True,
        second.decisions_verified,
        "same seed and commands produced identical player-visible trajectories twice",
    )


class ExternalRecorder:
    """Manage an ffmpeg/OBS-compatible recorder without invoking a shell."""

    def __init__(self, command_template: str, output: Path) -> None:
        if "{output}" not in command_template:
            raise ValueError("recorder command must contain a {output} placeholder")
        self.command = [
            part.replace("{output}", str(output)) for part in shlex.split(command_template)
        ]
        self.output = output
        self.process: subprocess.Popen[bytes] | None = None
        self._log = None
        self.log_path = output.with_suffix(output.suffix + ".recorder.log")
        self.started_at: float | None = None

    def __enter__(self) -> ExternalRecorder:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("wb")
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=self._log,
                stderr=subprocess.STDOUT,
            )
        except BaseException:
            self._log.close()
            self._log = None
            raise
        self.started_at = time.monotonic()
        time.sleep(1.0)
        if self.process.poll() is not None:
            if self.process.stdin is not None:
                self.process.stdin.close()
            self._log.close()
            self._log = None
            raise RuntimeError(f"recorder exited early; see {self.log_path}")
        return self

    def ensure_running(self) -> None:
        if self.process is None:
            raise RuntimeError("recorder has not started")
        return_code = self.process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"recorder exited during replay with status {return_code}; see {self.log_path}"
            )

    def __exit__(self, *_: object) -> None:
        if self.process is not None and self.process.poll() is None:
            if self.process.stdin is not None:
                try:
                    self.process.stdin.write(b"q")
                    self.process.stdin.flush()
                except BrokenPipeError:
                    pass
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.process.send_signal(signal.SIGINT)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
        if self.process is not None and self.process.stdin is not None:
            self.process.stdin.close()
        if self._log is not None:
            self._log.close()


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(milliseconds, 1_000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _caption_text(index: int, total: int, row: dict, state: GameState, model: str) -> str:
    action = row.get("action") or {}
    label = str(action.get("label") or row.get("engine_command") or "unknown action")
    automatic = " (engine maintenance)" if row.get("automatic") else ""
    return "\n".join(
        [
            f"sts-bench | {model}",
            (
                f"Seed {state.requested_seed} | Act {state.act} Floor {state.floor_reached} | "
                f"HP {state.hp}/{state.max_hp}"
            ),
            f"Step {index + 1}/{total} | {label}{automatic}",
        ]
    )


class ReplayTimeline:
    """Collect real replay timings and write them as an SRT action overlay."""

    def __init__(self, *, started_at: float, model: str) -> None:
        self.started_at = started_at
        self.model = model
        self.events: list[tuple[float, str]] = []

    def on_step(self, index: int, total: int, row: dict, state: GameState) -> None:
        elapsed = max(0.0, time.monotonic() - self.started_at)
        self.events.append((elapsed, _caption_text(index, total, row, state, self.model)))

    def write_srt(self, path: Path, *, tail_seconds: float = 3.0) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        blocks: list[str] = []
        for index, (started, caption) in enumerate(self.events):
            if index + 1 < len(self.events):
                ended = max(started + 0.05, self.events[index + 1][0])
            else:
                ended = started + tail_seconds
            blocks.append(
                f"{index + 1}\n{_srt_timestamp(started)} --> {_srt_timestamp(ended)}\n{caption}"
            )
        path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")


def _parse_srt_time(value: str) -> float:
    hours, minutes, remainder = value.split(":")
    seconds, milliseconds = remainder.split(",")
    return int(hours) * 3_600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1_000


def _read_srt(path: Path) -> list[tuple[float, float, str]]:
    events: list[tuple[float, float, str]] = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) < 3 or " --> " not in lines[1]:
            continue
        started, ended = lines[1].split(" --> ", 1)
        events.append((_parse_srt_time(started), _parse_srt_time(ended), "\n".join(lines[2:])))
    return events


def _render_caption(path: Path, caption: str) -> None:
    width, height = 1_500, 210
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font_paths = (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    font_path = next((path for path in font_paths if path.is_file()), None)
    font = (
        ImageFont.truetype(str(font_path), 30)
        if font_path is not None
        else ImageFont.load_default()
    )
    lines = [line[:92] for line in caption.splitlines()[:3]]
    boxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    text_width = max((box[2] - box[0] for box in boxes), default=0)
    box_width = min(width, text_width + 52)
    draw.rounded_rectangle((0, 0, box_width, height), radius=20, fill=(8, 10, 14, 205))
    for index, line in enumerate(lines):
        fill = (235, 186, 76, 255) if index == 0 else (255, 255, 255, 255)
        draw.text((26, 20 + index * 56), line, font=font, fill=fill)
    image.save(path)


def _overlay_concat(subtitles: Path, directory: Path, *, speed: float = 1.0) -> Path:
    if speed <= 0:
        raise ValueError("video speed must be positive")
    directory.mkdir(parents=True, exist_ok=True)
    events = _read_srt(subtitles)
    if not events:
        raise ValueError("action overlay timeline is empty")
    blank = directory / "blank.png"
    Image.new("RGBA", (1_500, 210), (0, 0, 0, 0)).save(blank)
    frames: list[tuple[Path, float]] = []
    if events[0][0] > 0:
        frames.append((blank, events[0][0] / speed))
    for index, (started, ended, caption) in enumerate(events):
        frame = directory / f"caption-{index:04d}.png"
        _render_caption(frame, caption)
        frames.append((frame, max(0.01, (ended - started) / speed)))
    concat = directory / "overlay.ffconcat"
    lines = ["ffconcat version 1.0"]
    for frame, duration in frames:
        lines.extend([f"file '{frame}'", f"duration {duration:.3f}"])
    lines.append(f"file '{blank}'")
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return concat


def burn_action_overlay(
    raw_video: Path,
    subtitles: Path,
    output: Path,
    *,
    speed: float = 1.0,
) -> None:
    """Burn a Pillow-rendered action timeline with FFmpeg's standard overlay filter."""
    if speed <= 0:
        raise ValueError("video speed must be positive")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sts-bench-overlay-") as temporary:
        concat = _overlay_concat(subtitles, Path(temporary), speed=speed)
        encoder = (
            ["-c:v", "h264_videotoolbox", "-b:v", "10M"]
            if sys.platform == "darwin"
            else ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]
        )
        speed_filter = ",setpts=PTS-STARTPTS" if speed == 1 else f",setpts=(PTS-STARTPTS)/{speed:g}"
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(raw_video),
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-filter_complex",
            (
                "[0:v]scale=w='min(1920,iw)':h=-2,"
                f"tpad=stop_mode=clone:stop_duration=3{speed_filter},fps=30[base];"
                "[1:v]format=rgba[caption];"
                "[base][caption]overlay=x=32:y=H-h-120:"
                "eof_action=pass:repeatlast=0:format=auto[video]"
            ),
            "-map",
            "[video]",
            *(["-map", "0:a?"] if speed == 1 else []),
            *encoder,
            "-pix_fmt",
            "yuv420p",
            *(["-c:a", "copy"] if speed == 1 else []),
            "-movflags",
            "+faststart",
            str(output),
        ]
        process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        detail = process.stderr.strip()[-3000:]
        raise RuntimeError(f"ffmpeg could not burn the action overlay: {detail}")
