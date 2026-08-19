from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

from sts_bench.submission import export_submission, validate_bundle, validate_run


def _stable_hash(value: object) -> str:
    import hashlib

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run(tmp_path: Path, *, with_secret: bool = False) -> Path:
    run_dir = tmp_path / "test-run"
    run_dir.mkdir()
    engine = {
        "game": "Slay the Spire 1",
        "game_version": "test",
        "protocol": "communicationmod-json-v1",
    }
    manifest = {
        "schema_version": 1,
        "run_id": "test-run",
        "model": "test/model",
        "seed": "STSBENCHV2000",
        "actual_seed": 12345,
        "character": "Ironclad",
        "ascension": 15,
        "benchmark_version": "v2",
        "protocol_version": "1.0",
        "engine": engine,
        "model_config": {
            "backend": "test",
            "base_url": "http://private.invalid/v1",
        },
    }
    if with_secret:
        manifest["api_key"] = "do-not-export"
    outcome = {
        "run_id": "test-run",
        "model": "test/model",
        "seed": "STSBENCHV2000",
        "actual_seed": 12345,
        "character": "IRONCLAD",
        "ascension": 15,
        "won": True,
        "terminal_status": "victory",
        "termination_reason": "terminal",
        "floor_reached": 51,
        "score": 1000,
        "decisions": 1,
        "response_count": 1,
        "illegal_action_count": 0,
        "forced_default_count": 0,
        "scalar_reward": 1.0,
        "engine": engine,
        "metadata": {"worker": {"id": "local-host", "pid": 123, "game": "test"}},
    }
    action = {
        "index": 0,
        "command": "CHOOSE 0",
        "kind": "choose",
        "label": "choose test",
        "metadata": {},
    }
    state = {
        "engine": engine,
        "requested_seed": "STSBENCHV2000",
        "actual_seed": 12345,
        "character": "IRONCLAD",
        "ascension": 15,
        "status": "playing",
        "phase": "event",
        "act": 3,
        "floor_reached": 50,
        "decisions": 0,
        "hp": 50,
        "max_hp": 75,
        "block": 0,
        "energy": None,
        "gold": 100,
        "visible": {},
        "legal_actions": [action],
        "progress": {},
    }
    row = {
        "decision": 0,
        "state_hash": _stable_hash(state),
        "state": state,
        "prompt": "test prompt",
        "raw_response": "ACTION 0",
        "attempt_responses": ["ACTION 0"],
        "parse_errors": [],
        "action": action,
        "engine_command": "CHOOSE 0",
        "automatic": False,
        "legal": True,
        "retries": 0,
        "forced_default": False,
        "resulting_state_hash": "b" * 64,
        "resulting_outcome_delta": {"hp": 0, "floor": 1, "terminal_status": "victory"},
    }
    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "outcome.json", outcome)
    (run_dir / "trajectory.jsonl").write_text(
        json.dumps(row, sort_keys=True) + "\n", encoding="utf-8"
    )
    return run_dir


def _member_json(path: Path, name: str) -> dict[str, object]:
    with tarfile.open(path, "r:gz") as archive:
        extracted = archive.extractfile(name)
        assert extracted is not None
        value = json.load(extracted)
    assert isinstance(value, dict)
    return value


def test_validate_run_checks_hashes_actions_and_counts(tmp_path: Path) -> None:
    report = validate_run(_run(tmp_path))

    assert report.valid
    assert report.summary["trajectory_rows"] == 1
    assert report.summary["model_decisions"] == 1


def test_validate_run_rejects_modified_state(tmp_path: Path) -> None:
    run_dir = _run(tmp_path)
    path = run_dir / "trajectory.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row["state"]["hp"] = 49
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    report = validate_run(run_dir)

    assert not report.valid
    assert "state_hash_mismatch" in {issue.code for issue in report.issues}


def test_summary_export_scrubs_private_fields(tmp_path: Path) -> None:
    output, _ = export_submission(_run(tmp_path, with_secret=True), tmp_path / "summary.tar.gz")

    with tarfile.open(output, "r:gz") as archive:
        assert set(archive.getnames()) == {"manifest.json", "outcome.json", "submission.json"}
    manifest = _member_json(output, "manifest.json")
    outcome = _member_json(output, "outcome.json")
    submission = _member_json(output, "submission.json")
    assert "api_key" not in manifest
    assert "base_url" not in manifest["model_config"]
    assert "id" not in outcome["metadata"]["worker"]
    assert "pid" not in outcome["metadata"]["worker"]
    assert submission["redactions"] == [
        "manifest.api_key",
        "manifest.model_config.base_url",
        "outcome.metadata.worker.id",
        "outcome.metadata.worker.pid",
    ]
    assert validate_bundle(output).valid


def test_full_export_preserves_auditable_trajectory(tmp_path: Path) -> None:
    output, _ = export_submission(_run(tmp_path), tmp_path / "full.tar.gz", include_trajectory=True)

    with tarfile.open(output, "r:gz") as archive:
        assert "trajectory.jsonl" in archive.getnames()
    report = validate_bundle(output)
    assert report.valid
    assert report.summary["trajectory_rows"] == 1


def test_export_refuses_to_overwrite_existing_archive(tmp_path: Path) -> None:
    destination = tmp_path / "existing.tar.gz"
    destination.write_bytes(b"keep me")

    try:
        export_submission(_run(tmp_path), destination)
    except FileExistsError:
        pass
    else:
        raise AssertionError("export unexpectedly overwrote an existing archive")

    assert destination.read_bytes() == b"keep me"


def test_bundle_rejects_member_changed_after_export(tmp_path: Path) -> None:
    output, _ = export_submission(_run(tmp_path), tmp_path / "summary.tar.gz")
    members: dict[str, bytes] = {}
    with tarfile.open(output, "r:gz") as archive:
        for member in archive.getmembers():
            extracted = archive.extractfile(member)
            assert extracted is not None
            members[member.name] = extracted.read()
    outcome = json.loads(members["outcome.json"])
    outcome["score"] = 9999
    members["outcome.json"] = json.dumps(outcome).encode()
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

    report = validate_bundle(tampered)

    assert not report.valid
    assert "member_digest_mismatch" in {issue.code for issue in report.issues}


def test_bundle_rejects_unknown_archive_paths(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        payload = b"{}"
        info = tarfile.TarInfo("../manifest.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    report = validate_bundle(path)

    assert not report.valid
    assert "unsafe_archive_layout" in {issue.code for issue in report.issues}
