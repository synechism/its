from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sts_bench.replay import (
    ReplayTimeline,
    _matches_recorded_state,
    _overlay_concat,
    _srt_timestamp,
    burn_action_overlay,
)


def test_srt_timestamp_rounds_to_milliseconds() -> None:
    assert _srt_timestamp(0) == "00:00:00,000"
    assert _srt_timestamp(3661.2346) == "01:01:01,235"


def test_replay_timeline_writes_action_intervals(tmp_path: Path) -> None:
    timeline = ReplayTimeline(started_at=0, model="model-a")
    timeline.events = [(1.25, "first action"), (2.5, "second action")]
    output = tmp_path / "timeline.srt"

    timeline.write_srt(output, tail_seconds=2)

    assert output.read_text(encoding="utf-8") == (
        "1\n00:00:01,250 --> 00:00:02,500\nfirst action\n\n"
        "2\n00:00:02,500 --> 00:00:04,500\nsecond action\n"
    )


def test_legacy_replay_ignores_only_gone_monster_animation_residue() -> None:
    expected = {
        "visible": {
            "combat": {
                "monsters": [
                    {
                        "id": "Byrd",
                        "is_gone": True,
                        "half_dead": False,
                        "move_adjusted_damage": 14,
                    }
                ]
            }
        }
    }
    state = MagicMock()
    state.stable_hash.return_value = "new-hash"
    state.canonical_dict.return_value = {
        "visible": {
            "combat": {
                "monsters": [
                    {
                        "id": "Byrd",
                        "is_gone": True,
                        "half_dead": False,
                        "move_adjusted_damage": 15,
                    }
                ]
            }
        }
    }

    assert _matches_recorded_state(state, "old-hash", expected) == (True, True)

    state.canonical_dict.return_value["visible"]["combat"]["monsters"][0]["is_gone"] = False
    assert _matches_recorded_state(state, "old-hash", expected) == (False, True)


def test_overlay_timeline_scales_with_video_speed(tmp_path: Path) -> None:
    subtitles = tmp_path / "timeline.srt"
    subtitles.write_text(
        "1\n00:00:02,000 --> 00:00:06,000\nfirst action\n",
        encoding="utf-8",
    )

    concat = _overlay_concat(subtitles, tmp_path / "frames", speed=4)

    assert "duration 0.500" in concat.read_text(encoding="utf-8")
    assert "duration 1.000" in concat.read_text(encoding="utf-8")


def test_speedup_resamples_the_presentation_to_30_fps(tmp_path: Path) -> None:
    concat = tmp_path / "overlay.ffconcat"
    with (
        patch("sts_bench.replay._overlay_concat", return_value=concat),
        patch(
            "sts_bench.replay.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stderr=""),
        ) as run,
    ):
        burn_action_overlay(
            tmp_path / "raw.mov",
            tmp_path / "timeline.srt",
            tmp_path / "output.mp4",
            speed=8,
        )

    command = run.call_args.args[0]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "setpts=(PTS-STARTPTS)/8,fps=30[base]" in filter_graph
