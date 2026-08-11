from __future__ import annotations

import hmac
import json
import os
import socket
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from sts_bench import __version__

MAX_WIRE_LINE = 64 * 1024 * 1024


class WireError(RuntimeError):
    pass


class JsonlSocket:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.reader = sock.makefile("r", encoding="utf-8", newline="\n")
        self.writer = sock.makefile("w", encoding="utf-8", newline="\n")

    def send(self, payload: dict[str, Any]) -> None:
        self.writer.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n")
        self.writer.flush()

    def receive(self) -> dict[str, Any]:
        line = self.reader.readline(MAX_WIRE_LINE + 1)
        if not line:
            raise WireError("worker connection closed")
        if len(line) > MAX_WIRE_LINE:
            raise WireError("worker message exceeded the 64 MiB safety limit")
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise WireError(f"worker sent invalid JSON: {error}") from error
        if not isinstance(payload, dict):
            raise WireError("worker message must be a JSON object")
        return payload

    def close(self) -> None:
        for stream in (self.reader, self.writer):
            with suppress(OSError):
                stream.close()
        with suppress(OSError):
            self.sock.close()


@dataclass(slots=True)
class WorkerConnection:
    wire: JsonlSocket
    worker: dict[str, Any]

    def receive_envelope(self) -> dict[str, Any]:
        message = self.wire.receive()
        if message.get("type") != "state" or not isinstance(message.get("envelope"), dict):
            raise WireError(f"expected worker state, received {message.get('type')!r}")
        return dict(message["envelope"])

    def send_command(self, command: str) -> None:
        if not command or "\n" in command or "\r" in command:
            raise ValueError("CommunicationMod command must be one non-empty line")
        self.wire.send({"type": "command", "command": command})

    def close(self) -> None:
        with suppress(OSError, WireError):
            self.wire.send({"type": "stop"})
        self.wire.close()


class WorkerServer:
    """Accept one game-owned bridge process over an authenticated JSONL socket."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 17851,
        *,
        token: str = "",
        accept_timeout: float | None = None,
        state_timeout: float | None = 120.0,
    ) -> None:
        if host not in {"127.0.0.1", "::1", "localhost"} and not token:
            raise ValueError("a bridge token is required when listening beyond localhost")
        self.host = host
        self.port = port
        self.token = token
        self.accept_timeout = accept_timeout
        self.state_timeout = state_timeout
        self._server: socket.socket | None = None

    def __enter__(self) -> WorkerServer:
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        server = socket.socket(family, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(1)
        server.settimeout(self.accept_timeout)
        self._server = server
        return self

    @property
    def bound_address(self) -> tuple[str, int]:
        if self._server is None:
            raise RuntimeError("server has not been started")
        address = self._server.getsockname()
        return str(address[0]), int(address[1])

    def accept(self) -> WorkerConnection:
        if self._server is None:
            raise RuntimeError("server has not been started")
        sock, _address = self._server.accept()
        sock.settimeout(10.0)
        wire = JsonlSocket(sock)
        try:
            hello = wire.receive()
            if hello.get("type") != "hello":
                raise WireError("first worker message was not a hello")
            supplied = str(hello.get("token", ""))
            if not hmac.compare_digest(supplied, self.token):
                wire.send({"type": "rejected", "reason": "invalid bridge token"})
                raise WireError("worker supplied an invalid bridge token")
            worker = dict(hello.get("worker") or {})
            worker["bridge_version"] = str(hello.get("bridge_version", "unknown"))
            wire.send({"type": "accepted", "protocol": 1})
            sock.settimeout(self.state_timeout)
            return WorkerConnection(wire=wire, worker=worker)
        except Exception:
            wire.close()
            raise

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None

    def __exit__(self, *_: object) -> None:
        self.close()


def run_bridge(
    host: str,
    port: int,
    *,
    token: str = "",
    worker_id: str | None = None,
    game_version: str = "unknown",
    mod_the_spire_version: str = "unknown",
    base_mod_version: str = "unknown",
    communication_mod_version: str = "unknown",
    connect_timeout: float = 8.0,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
    error_log: Path | None = None,
) -> None:
    """Run inside CommunicationMod. stdout is reserved exclusively for game commands."""

    def log(message: str) -> None:
        line = f"sts-bench bridge: {message}\n"
        if error_log is None:
            sys.stderr.write(line)
            sys.stderr.flush()
        else:
            error_log.parent.mkdir(parents=True, exist_ok=True)
            with error_log.open("a", encoding="utf-8") as handle:
                handle.write(line)

    sock = socket.create_connection((host, port), timeout=connect_timeout)
    sock.settimeout(connect_timeout)
    wire = JsonlSocket(sock)
    wire.send(
        {
            "type": "hello",
            "token": token,
            "bridge_version": __version__,
            "worker": {
                "id": worker_id or socket.gethostname(),
                "pid": os.getpid(),
                "game": "Slay the Spire 1",
                "game_version": game_version,
                "mod_the_spire_version": mod_the_spire_version,
                "base_mod_version": base_mod_version,
                "communication_mod_version": communication_mod_version,
            },
        }
    )
    response = wire.receive()
    if response.get("type") != "accepted":
        raise WireError(f"controller rejected bridge: {response.get('reason', 'unknown reason')}")
    sock.settimeout(None)

    # CommunicationMod will kill the process unless this exact signal arrives quickly.
    output_stream.write("ready\n")
    output_stream.flush()
    log(f"connected to {host}:{port}")

    try:
        for raw_line in input_stream:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                envelope = json.loads(raw_line)
            except json.JSONDecodeError as error:
                raise WireError(f"CommunicationMod emitted invalid JSON: {error}") from error
            wire.send({"type": "state", "envelope": envelope})
            message = wire.receive()
            if message.get("type") == "stop":
                return
            if message.get("type") != "command":
                raise WireError(f"unexpected controller message: {message.get('type')!r}")
            command = str(message.get("command", ""))
            if not command or "\n" in command or "\r" in command:
                raise WireError("controller supplied an invalid command line")
            output_stream.write(command + "\n")
            output_stream.flush()
    finally:
        wire.close()
