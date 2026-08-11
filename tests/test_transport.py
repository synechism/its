from __future__ import annotations

import io
import json
import threading

from sts_bench.transport import WorkerServer, run_bridge


def test_bridge_forwards_state_and_exact_command(tmp_path) -> None:
    envelope = {
        "available_commands": ["state"],
        "ready_for_command": True,
        "in_game": False,
    }
    input_stream = io.StringIO(json.dumps(envelope) + "\n")
    output_stream = io.StringIO()

    with WorkerServer("127.0.0.1", 0, token="test-token") as server:
        host, port = server.bound_address
        thread = threading.Thread(
            target=run_bridge,
            args=(host, port),
            kwargs={
                "token": "test-token",
                "worker_id": "test",
                "input_stream": input_stream,
                "output_stream": output_stream,
                "error_log": tmp_path / "bridge.log",
            },
        )
        thread.start()
        worker = server.accept()
        assert worker.worker["id"] == "test"
        assert worker.receive_envelope() == envelope
        worker.send_command("STATE")
        thread.join(timeout=2)
        assert not thread.is_alive()

    assert output_stream.getvalue() == "ready\nSTATE\n"
