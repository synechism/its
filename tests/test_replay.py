from __future__ import annotations

from pathlib import Path

from sts_bench.replay import ReplayTimeline, _srt_timestamp


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
