from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from sts_bench.cli import (
    _macos_screen_recorder_command,
    _redacted_command,
    _run_replay_with_optional_recording,
    _without_detach_arguments,
)
from sts_bench.replay import ExternalRecorder, ReplayResult


def test_detached_reexecution_removes_parent_only_flags() -> None:
    arguments = [
        "overnight",
        "--model",
        "model-a",
        "--detach",
        "--detach-log",
        "launch.log",
        "--ascension",
        "15",
    ]

    assert _without_detach_arguments(arguments) == [
        "overnight",
        "--model",
        "model-a",
        "--ascension",
        "15",
    ]


def test_detached_launch_metadata_redacts_credentials() -> None:
    command = [
        "sts-bench",
        "overnight",
        "--api-key",
        "secret",
        "--token=token",
    ]

    assert _redacted_command(command) == [
        "sts-bench",
        "overnight",
        "--api-key",
        "<redacted>",
        "--token=<redacted>",
    ]


def test_macos_display_numbers_map_to_avfoundation_screen_names() -> None:
    command = _macos_screen_recorder_command(1)

    assert "-f avfoundation" in command
    assert "Capture screen 0:none" in command
    assert "h264_videotoolbox" in command
    assert "-framerate 20" in command
    assert "-f mpegts" in command


def test_recorder_liveness_check_reports_mid_replay_exit(tmp_path) -> None:
    recorder = ExternalRecorder("recorder {output}", tmp_path / "capture.ts")
    recorder.process = MagicMock()
    recorder.process.poll.return_value = 9

    with pytest.raises(RuntimeError, match="exited during replay with status 9"):
        recorder.ensure_running()


def test_failed_replay_keeps_diagnostic_capture_without_publishing_video(tmp_path) -> None:
    output = tmp_path / "replay.mp4"
    args = Namespace(
        video_speed=8.0,
        record_display=None,
        recorder_command="recorder {output}",
        video_output=output,
        overlay=True,
        run_dir=tmp_path / "run",
        step_delay=0.0,
    )
    expected = ReplayResult(False, 12, "diverged")
    recorder = MagicMock(started_at=1.0)
    recorder_context = MagicMock()
    recorder_context.__enter__.return_value = recorder

    with (
        patch("sts_bench.cli.ExternalRecorder", return_value=recorder_context),
        patch("sts_bench.cli.load_trajectory", return_value=({"model": "model-a"}, [])),
        patch("sts_bench.cli.replay_live", return_value=expected),
        patch("sts_bench.cli.burn_action_overlay") as burn,
    ):
        result = _run_replay_with_optional_recording(MagicMock(), args)

    assert result == expected
    assert json.loads(output.with_suffix(".mp4.replay.json").read_text()) == {
        "decisions_verified": 12,
        "message": "diverged",
        "normalized_comparisons": 0,
        "valid": False,
    }
    burn.assert_not_called()
