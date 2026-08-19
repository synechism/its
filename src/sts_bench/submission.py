from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "sts_bench_token",
    "token",
}
MAX_JSON_MEMBER = 1_000_000
MAX_TRAJECTORY_MEMBER = 1_000_000_000


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    source: str
    valid: bool
    issues: tuple[ValidationIssue, ...]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": self.source,
            "valid": self.valid,
            "issues": [asdict(issue) for issue in self.issues],
            "summary": self.summary,
        }


class _Issues:
    def __init__(self) -> None:
        self.items: list[ValidationIssue] = []
        self._keys: set[tuple[str, str]] = set()

    def add(self, severity: str, code: str, message: str, *, once: bool = False) -> None:
        key = (severity, code)
        if once and key in self._keys:
            return
        self._keys.add(key)
        if len(self.items) < 100:
            self.items.append(ValidationIssue(severity, code, message))

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.items)


def _read_json(path: Path, label: str, issues: _Issues) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        issues.add("error", f"missing_{label}", f"missing {path.name}")
        return None
    except (OSError, json.JSONDecodeError) as error:
        issues.add("error", f"invalid_{label}", f"could not read {path.name}: {error}")
        return None
    if not isinstance(payload, dict):
        issues.add("error", f"invalid_{label}", f"{path.name} must contain a JSON object")
        return None
    return payload


def _stable_hash(state: object) -> str:
    encoded = json.dumps(
        state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _secret_paths(value: object, path: str = "state") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            normalized = str(key).casefold().replace("-", "_")
            if normalized in SECRET_KEYS or normalized.endswith(("_api_key", "_secret")):
                found.append(child)
            found.extend(_secret_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_secret_paths(item, f"{path}[{index}]"))
    return found


def _require_fields(
    payload: dict[str, Any], fields: Iterable[str], label: str, issues: _Issues
) -> None:
    for field in fields:
        if field not in payload:
            issues.add("error", f"{label}_missing_field", f"{label} is missing {field!r}")


def _same_text(left: object, right: object) -> bool:
    return str(left).casefold() == str(right).casefold()


def _validate_identity(manifest: dict[str, Any], outcome: dict[str, Any], issues: _Issues) -> None:
    _require_fields(
        manifest,
        (
            "schema_version",
            "run_id",
            "model",
            "seed",
            "character",
            "ascension",
            "benchmark_version",
            "protocol_version",
            "engine",
            "model_config",
        ),
        "manifest",
        issues,
    )
    _require_fields(
        outcome,
        (
            "run_id",
            "model",
            "seed",
            "character",
            "ascension",
            "won",
            "terminal_status",
            "termination_reason",
            "floor_reached",
            "decisions",
            "response_count",
            "illegal_action_count",
            "forced_default_count",
            "engine",
        ),
        "outcome",
        issues,
    )
    for field in ("run_id", "model", "seed", "ascension"):
        if field in manifest and field in outcome and manifest[field] != outcome[field]:
            issues.add(
                "error",
                "identity_mismatch",
                f"manifest/outcome {field} mismatch: {manifest[field]!r} != {outcome[field]!r}",
            )
    if (
        "character" in manifest
        and "character" in outcome
        and not _same_text(manifest["character"], outcome["character"])
    ):
        issues.add("error", "identity_mismatch", "manifest/outcome character mismatch")
    if manifest.get("engine") != outcome.get("engine"):
        issues.add("error", "engine_mismatch", "manifest/outcome engine metadata differs")
    ascension = outcome.get("ascension")
    if not isinstance(ascension, int) or isinstance(ascension, bool) or not 0 <= ascension <= 20:
        issues.add("error", "invalid_ascension", f"invalid Ascension value: {ascension!r}")
    won = outcome.get("won")
    terminal_status = outcome.get("terminal_status")
    if isinstance(won, bool):
        expected_status = "victory" if won else "defeat"
        if outcome.get("termination_reason") == "terminal" and terminal_status != expected_status:
            issues.add(
                "error",
                "terminal_mismatch",
                f"won={won} conflicts with terminal_status={terminal_status!r}",
            )
        scalar_reward = outcome.get("scalar_reward")
        if scalar_reward is not None:
            try:
                reward_matches = float(scalar_reward) == float(won)
            except (TypeError, ValueError):
                reward_matches = False
            if not reward_matches:
                issues.add("error", "reward_mismatch", "scalar_reward does not match won")
    else:
        issues.add("error", "invalid_won", "outcome won must be boolean")

    if not isinstance(manifest.get("model_config"), dict):
        issues.add("error", "invalid_model_config", "manifest model_config must be an object")
    if not isinstance(outcome.get("metadata", {}), dict):
        issues.add("error", "invalid_metadata", "outcome metadata must be an object")


def _validate_trajectory(
    lines: Iterable[str],
    manifest: dict[str, Any],
    outcome: dict[str, Any],
    issues: _Issues,
) -> dict[str, Any]:
    row_count = model_decisions = response_count = illegal_count = forced_count = 0
    previous_result_hash: str | None = None
    last_terminal_status: str | None = None
    first_state: dict[str, Any] | None = None
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            issues.add(
                "error",
                "invalid_trajectory_json",
                f"trajectory line {line_number}: {error}",
                once=True,
            )
            continue
        if not isinstance(row, dict):
            issues.add(
                "error",
                "invalid_trajectory_row",
                f"trajectory line {line_number} is not an object",
                once=True,
            )
            continue
        row_count += 1
        _require_fields(
            row,
            (
                "state_hash",
                "state",
                "action",
                "engine_command",
                "automatic",
                "forced_default",
                "attempt_responses",
                "parse_errors",
                "resulting_state_hash",
            ),
            f"trajectory line {line_number}",
            issues,
        )
        state = row.get("state")
        if not isinstance(state, dict):
            issues.add(
                "error",
                "invalid_state",
                f"trajectory line {line_number} state is not an object",
                once=True,
            )
            continue
        if first_state is None:
            first_state = state
        state_hash = row.get("state_hash")
        if not isinstance(state_hash, str) or HEX_DIGEST.fullmatch(state_hash) is None:
            issues.add(
                "error",
                "invalid_state_hash",
                f"trajectory line {line_number} has an invalid state hash",
                once=True,
            )
        elif _stable_hash(state) != state_hash:
            issues.add(
                "error",
                "state_hash_mismatch",
                f"trajectory line {line_number} state does not match state_hash",
                once=True,
            )
        if previous_result_hash is not None and previous_result_hash != state_hash:
            issues.add(
                "error",
                "transition_chain_broken",
                f"trajectory line {line_number} does not follow the prior resulting hash",
                once=True,
            )
        resulting_hash = row.get("resulting_state_hash")
        if not isinstance(resulting_hash, str) or HEX_DIGEST.fullmatch(resulting_hash) is None:
            issues.add(
                "error",
                "invalid_resulting_hash",
                f"trajectory line {line_number} has an invalid resulting hash",
                once=True,
            )
            previous_result_hash = None
        else:
            previous_result_hash = resulting_hash

        action = row.get("action")
        action_dict = action if isinstance(action, dict) else {}
        command = row.get("engine_command")
        if not isinstance(action, dict) or action_dict.get("command") != command:
            issues.add(
                "error",
                "action_command_mismatch",
                f"trajectory line {line_number} action does not match engine_command",
                once=True,
            )
        legal_actions = state.get("legal_actions") or []
        if not isinstance(legal_actions, list):
            legal_actions = []
            issues.add(
                "error",
                "invalid_legal_actions",
                f"trajectory line {line_number} legal_actions is not a list",
                once=True,
            )
        if not any(
            isinstance(candidate, dict)
            and candidate.get("index") == action_dict.get("index")
            and candidate.get("command") == command
            for candidate in legal_actions
        ):
            issues.add(
                "error",
                "action_not_enumerated",
                f"trajectory line {line_number} action is absent from the recorded legal list",
                once=True,
            )
        automatic_value = row.get("automatic")
        if not isinstance(automatic_value, bool):
            issues.add(
                "error",
                "invalid_automatic_flag",
                f"trajectory line {line_number} automatic must be boolean",
                once=True,
            )
        automatic = automatic_value if isinstance(automatic_value, bool) else bool(automatic_value)
        if not automatic:
            model_decisions += 1
        attempts = row.get("attempt_responses")
        errors = row.get("parse_errors")
        if isinstance(attempts, list):
            response_count += len(attempts)
        else:
            issues.add(
                "error",
                "invalid_attempt_responses",
                f"trajectory line {line_number} attempt_responses is not a list",
                once=True,
            )
        if isinstance(errors, list):
            illegal_count += sum(error != "model interaction terminated" for error in errors)
        else:
            issues.add(
                "error",
                "invalid_parse_errors",
                f"trajectory line {line_number} parse_errors is not a list",
                once=True,
            )
        forced_value = row.get("forced_default")
        if not isinstance(forced_value, bool):
            issues.add(
                "error",
                "invalid_forced_default_flag",
                f"trajectory line {line_number} forced_default must be boolean",
                once=True,
            )
        forced_count += int(forced_value) if isinstance(forced_value, bool) else 0
        delta_value = row.get("resulting_outcome_delta") or {}
        delta = delta_value if isinstance(delta_value, dict) else {}
        if delta_value and not isinstance(delta_value, dict):
            issues.add(
                "error",
                "invalid_outcome_delta",
                f"trajectory line {line_number} resulting_outcome_delta is not an object",
                once=True,
            )
        if delta.get("terminal_status") is not None:
            last_terminal_status = str(delta["terminal_status"])
        for secret_path in _secret_paths(row, f"trajectory[{line_number}]"):
            issues.add(
                "warning",
                "secret_like_field",
                f"secret-like field will not be exported: {secret_path}",
                once=True,
            )

    if row_count == 0:
        issues.add("error", "empty_trajectory", "trajectory contains no rows")
    expected_counts = {
        "decisions": model_decisions,
        "response_count": response_count,
        "illegal_action_count": illegal_count,
        "forced_default_count": forced_count,
    }
    for field, observed in expected_counts.items():
        if field in outcome and outcome[field] != observed:
            issues.add(
                "error",
                "outcome_count_mismatch",
                f"outcome {field}={outcome[field]!r}, trajectory implies {observed}",
            )
    if outcome.get("termination_reason") == "terminal" and (
        last_terminal_status != outcome.get("terminal_status")
    ):
        issues.add(
            "error",
            "terminal_mismatch",
            "final trajectory transition does not match outcome terminal_status",
        )
    if first_state is not None:
        comparisons = {
            "requested_seed": manifest.get("seed"),
            "ascension": manifest.get("ascension"),
            "actual_seed": manifest.get("actual_seed"),
        }
        for field, expected in comparisons.items():
            if expected is not None and first_state.get(field) != expected:
                issues.add(
                    "error",
                    "initial_state_mismatch",
                    f"initial state {field} does not match manifest",
                )
        if not _same_text(first_state.get("character"), manifest.get("character")):
            issues.add(
                "error",
                "initial_state_mismatch",
                "initial state character does not match manifest",
            )
    return {
        "trajectory_rows": row_count,
        "model_decisions": model_decisions,
        "response_count": response_count,
        "illegal_action_count": illegal_count,
        "forced_default_count": forced_count,
    }


def _validate_payloads(
    manifest: dict[str, Any],
    outcome: dict[str, Any],
    trajectory: Iterable[str] | None,
    *,
    source: str,
    issues: _Issues | None = None,
) -> ValidationReport:
    collected = issues or _Issues()
    _validate_identity(manifest, outcome, collected)
    for label, payload in (("manifest", manifest), ("outcome", outcome)):
        for secret_path in _secret_paths(payload, label):
            collected.add(
                "warning",
                "secret_like_field",
                f"secret-like field will not be exported: {secret_path}",
            )
    metadata = outcome.get("metadata")
    worker_value = metadata.get("worker") if isinstance(metadata, dict) else {}
    worker = worker_value if isinstance(worker_value, dict) else {}
    if any(field in worker for field in ("id", "pid")):
        collected.add(
            "warning",
            "local_worker_identity",
            "worker id/PID will be removed from public exports",
        )
    model_config = manifest.get("model_config")
    if isinstance(model_config, dict) and model_config.get("base_url"):
        collected.add(
            "warning",
            "private_endpoint",
            "model_config.base_url will be removed from public exports",
        )
    summary: dict[str, Any] = {
        "run_id": manifest.get("run_id"),
        "model": manifest.get("model"),
        "seed": manifest.get("seed"),
        "character": manifest.get("character"),
        "ascension": manifest.get("ascension"),
        "won": outcome.get("won"),
        "floor_reached": outcome.get("floor_reached"),
        "score": outcome.get("score"),
    }
    if trajectory is None:
        collected.add(
            "warning",
            "trajectory_not_included",
            "summary bundle has no trajectory; hashes and action sequence cannot be audited",
        )
    else:
        summary.update(_validate_trajectory(trajectory, manifest, outcome, collected))
    return ValidationReport(source, collected.valid, tuple(collected.items), summary)


def validate_run(run_dir: Path) -> ValidationReport:
    issues = _Issues()
    manifest = _read_json(run_dir / "manifest.json", "manifest", issues)
    outcome = _read_json(run_dir / "outcome.json", "outcome", issues)
    trajectory_path = run_dir / "trajectory.jsonl"
    if not trajectory_path.is_file():
        issues.add("error", "missing_trajectory", "missing trajectory.jsonl")
    if manifest is None or outcome is None or not trajectory_path.is_file():
        return ValidationReport(str(run_dir), False, tuple(issues.items), {})
    try:
        with trajectory_path.open(encoding="utf-8") as trajectory:
            return _validate_payloads(
                manifest,
                outcome,
                trajectory,
                source=str(run_dir),
                issues=issues,
            )
    except OSError as error:
        issues.add("error", "invalid_trajectory", f"could not read trajectory.jsonl: {error}")
        return ValidationReport(str(run_dir), False, tuple(issues.items), {})


def _sanitize(value: object, redactions: list[str], path: str) -> object:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            child = f"{path}.{key}"
            normalized = str(key).casefold().replace("-", "_")
            if normalized in SECRET_KEYS or normalized.endswith(("_api_key", "_secret")):
                redactions.append(child)
                continue
            result[key] = _sanitize(item, redactions, child)
        return result
    if isinstance(value, list):
        return [_sanitize(item, redactions, f"{path}[{index}]") for index, item in enumerate(value)]
    return value


def _public_payloads(
    manifest: dict[str, Any], outcome: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    redactions: list[str] = []
    clean_manifest = _sanitize(manifest, redactions, "manifest")
    clean_outcome = _sanitize(outcome, redactions, "outcome")
    assert isinstance(clean_manifest, dict) and isinstance(clean_outcome, dict)
    model_config_value = clean_manifest.get("model_config")
    model_config = model_config_value if isinstance(model_config_value, dict) else {}
    if "base_url" in model_config:
        model_config.pop("base_url")
        redactions.append("manifest.model_config.base_url")
    metadata = clean_outcome.get("metadata")
    worker_value = metadata.get("worker") if isinstance(metadata, dict) else {}
    worker = worker_value if isinstance(worker_value, dict) else {}
    for field in ("id", "pid"):
        if field in worker:
            worker.pop(field)
            redactions.append(f"outcome.metadata.worker.{field}")
    return clean_manifest, clean_outcome, sorted(set(redactions))


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _tar_member(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info, io.BytesIO(payload))


def export_submission(
    run_dir: Path,
    output: Path | None = None,
    *,
    include_trajectory: bool = False,
) -> tuple[Path, ValidationReport]:
    report = validate_run(run_dir)
    if not report.valid:
        raise ValueError("run failed validation; refusing to export")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    outcome = json.loads((run_dir / "outcome.json").read_text(encoding="utf-8"))
    clean_manifest, clean_outcome, redactions = _public_payloads(manifest, outcome)
    members = {
        "manifest.json": _json_bytes(clean_manifest),
        "outcome.json": _json_bytes(clean_outcome),
    }
    if include_trajectory:
        secret_warning = any(issue.code == "secret_like_field" for issue in report.issues)
        if secret_warning:
            raise ValueError(
                "source run contains a secret-like field; export without --include-trajectory"
            )
        members["trajectory.jsonl"] = (run_dir / "trajectory.jsonl").read_bytes()
    source_files = {
        name: _sha256(run_dir / name)
        for name in ("manifest.json", "outcome.json", "trajectory.jsonl")
    }
    submission = {
        "schema_version": 1,
        "format": "sts-bench-submission.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": manifest.get("run_id"),
        "includes_trajectory": include_trajectory,
        "redactions": redactions,
        "source_sha256": source_files,
        "members_sha256": {
            name: hashlib.sha256(payload).hexdigest() for name, payload in members.items()
        },
        "validation": {"valid": True, "summary": report.summary},
        "limitations": (
            "Structural validation detects corruption and internal inconsistency; it does not "
            "prove model identity or that a run came from an unmodified game."
        ),
    }
    members["submission.json"] = _json_bytes(submission)
    destination = output or Path(f"{manifest.get('run_id', run_dir.name)}.submission.tar.gz")
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    with (
        temporary.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for name, payload in sorted(members.items()):
            _tar_member(archive, name, payload)
    temporary.replace(destination)
    return destination, report


def _archive_json(archive: tarfile.TarFile, name: str, issues: _Issues) -> dict[str, Any] | None:
    member = archive.getmember(name)
    extracted = archive.extractfile(member)
    if extracted is None:
        issues.add("error", "invalid_archive_member", f"could not read {name}")
        return None
    try:
        payload = json.load(extracted)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        issues.add("error", "invalid_archive_member", f"invalid {name}: {error}")
        return None
    if not isinstance(payload, dict):
        issues.add("error", "invalid_archive_member", f"{name} must contain a JSON object")
        return None
    return payload


def _archive_digest(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    extracted = archive.extractfile(member)
    if extracted is None:
        raise OSError(f"could not read {member.name}")
    digest = hashlib.sha256()
    for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _validate_open_bundle(
    path: Path, archive: tarfile.TarFile, issues: _Issues
) -> ValidationReport:
    members = archive.getmembers()
    names = [member.name for member in members]
    allowed = {"manifest.json", "outcome.json", "submission.json", "trajectory.jsonl"}
    if len(names) != len(set(names)) or any(name not in allowed for name in names):
        issues.add("error", "unsafe_archive_layout", "archive has duplicate or unknown members")
    if any(not member.isfile() for member in members):
        issues.add("error", "unsafe_archive_layout", "archive members must be regular files")
    by_name = {member.name: member for member in members}
    for required in ("manifest.json", "outcome.json", "submission.json"):
        if required not in by_name:
            issues.add("error", "missing_archive_member", f"archive is missing {required}")
    for name, member in by_name.items():
        limit = MAX_TRAJECTORY_MEMBER if name == "trajectory.jsonl" else MAX_JSON_MEMBER
        if member.size > limit:
            issues.add("error", "archive_member_too_large", f"{name} exceeds size limit")
    if not issues.valid:
        return ValidationReport(str(path), False, tuple(issues.items), {})
    manifest = _archive_json(archive, "manifest.json", issues)
    outcome = _archive_json(archive, "outcome.json", issues)
    submission = _archive_json(archive, "submission.json", issues)
    if manifest is None or outcome is None or submission is None:
        return ValidationReport(str(path), False, tuple(issues.items), {})
    if submission.get("format") != "sts-bench-submission.v1":
        issues.add("error", "invalid_submission_format", "unknown submission format")
    if submission.get("schema_version") != 1:
        issues.add("error", "invalid_submission_schema", "unknown submission schema version")
    if submission.get("run_id") != manifest.get("run_id"):
        issues.add("error", "submission_identity_mismatch", "submission run_id mismatch")
    expected_value = submission.get("members_sha256")
    expected_digests = expected_value if isinstance(expected_value, dict) else {}
    if not isinstance(expected_value, dict):
        issues.add("error", "invalid_member_digests", "members_sha256 must be an object")
    for name in ("manifest.json", "outcome.json", "trajectory.jsonl"):
        if name not in by_name:
            continue
        try:
            actual = _archive_digest(archive, by_name[name])
        except OSError as error:
            issues.add("error", "invalid_archive_member", str(error))
            continue
        if expected_digests.get(name) != actual:
            issues.add("error", "member_digest_mismatch", f"{name} digest mismatch")
    trajectory_member = by_name.get("trajectory.jsonl")
    includes_trajectory = submission.get("includes_trajectory")
    if not isinstance(includes_trajectory, bool):
        issues.add("error", "invalid_trajectory_flag", "includes_trajectory must be boolean")
    elif bool(trajectory_member) != includes_trajectory:
        issues.add(
            "error",
            "trajectory_flag_mismatch",
            "includes_trajectory does not match archive contents",
        )
    if trajectory_member is None:
        return _validate_payloads(manifest, outcome, None, source=str(path), issues=issues)
    extracted = archive.extractfile(trajectory_member)
    if extracted is None:
        issues.add("error", "invalid_archive_member", "could not read trajectory.jsonl")
        return ValidationReport(str(path), False, tuple(issues.items), {})
    with io.TextIOWrapper(extracted, encoding="utf-8") as trajectory:
        return _validate_payloads(
            manifest,
            outcome,
            trajectory,
            source=str(path),
            issues=issues,
        )


def validate_bundle(path: Path) -> ValidationReport:
    issues = _Issues()
    try:
        with tarfile.open(path, mode="r:*") as archive:
            return _validate_open_bundle(path, archive, issues)
    except (OSError, tarfile.TarError, UnicodeError) as error:
        issues.add("error", "invalid_archive", str(error))
        return ValidationReport(str(path), False, tuple(issues.items), {})


def validate_submission_path(path: Path) -> ValidationReport:
    return validate_run(path) if path.is_dir() else validate_bundle(path)


def format_validation_report(report: ValidationReport) -> str:
    result = "VALID" if report.valid else "INVALID"
    lines = [f"{result}: {report.source}"]
    summary = report.summary
    if summary:
        lines.append(
            f"  {summary.get('model')} | {summary.get('character')} A{summary.get('ascension')} | "
            f"seed {summary.get('seed')} | {'win' if summary.get('won') else 'loss'} | "
            f"floor {summary.get('floor_reached')}"
        )
        if "trajectory_rows" in summary:
            lines.append(
                f"  {summary['trajectory_rows']} transitions, "
                f"{summary['model_decisions']} model decisions"
            )
    for issue in report.issues:
        lines.append(f"  {issue.severity.upper()}: [{issue.code}] {issue.message}")
    return "\n".join(lines)
