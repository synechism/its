from __future__ import annotations

import queue
import threading
from contextlib import suppress

from sts_bench.game import LiveGame
from sts_bench.transport import WireError, WorkerServer


class WorkerPool:
    """Lease reusable, user-owned game processes to concurrent rollouts."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 17851,
        *,
        token: str = "",
        state_timeout: float | None = 120.0,
    ) -> None:
        self.server = WorkerServer(
            host,
            port,
            token=token,
            accept_timeout=0.5,
            state_timeout=state_timeout,
        )
        self._games: queue.Queue[LiveGame] = queue.Queue()
        self._all_games: set[LiveGame] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def bound_address(self) -> tuple[str, int]:
        return self.server.bound_address

    def start(self) -> WorkerPool:
        if self._thread is not None:
            return self
        self.server.__enter__()
        self._thread = threading.Thread(
            target=self._accept_loop, name="sts-worker-pool", daemon=True
        )
        self._thread.start()
        return self

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                connection = self.server.accept()
                game = LiveGame(connection)
            except TimeoutError:
                continue
            except (OSError, WireError):
                if not self._stop.is_set():
                    continue
                return
            self._all_games.add(game)
            self._games.put(game)

    def acquire(self, timeout: float | None = None) -> LiveGame:
        if self._thread is None:
            raise RuntimeError("worker pool has not been started")
        try:
            return self._games.get(timeout=timeout)
        except queue.Empty as error:
            raise TimeoutError("timed out waiting for a Slay the Spire worker") from error

    def release(self, game: LiveGame) -> None:
        if game not in self._all_games:
            raise ValueError("cannot release a game owned by another pool")
        if self._stop.is_set():
            game.close()
            return
        self._games.put(game)

    def discard(self, game: LiveGame) -> None:
        self._all_games.discard(game)
        with suppress(Exception):
            game.close()

    def close(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self.server.close()
        for game in list(self._all_games):
            self.discard(game)
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def __enter__(self) -> WorkerPool:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close()
