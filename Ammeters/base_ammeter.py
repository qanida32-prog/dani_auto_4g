"""Base class shared by all ammeter emulators.

Each emulator is a tiny TCP server: a client connects, sends a command, and - if the
command matches the device protocol - receives the measured current as a UTF-8 float
string. The connection is closed after every request.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from abc import ABC, abstractmethod

NOT_IMPLEMENTED_MSG = "Subclasses must implement this property."
UNKNOWN_COMMAND_RESPONSE = b"ERROR: unknown command"
_ACCEPT_POLL_SECONDS = 0.5
_RECV_BUFFER_SIZE = 1024


class AmmeterEmulatorBase(ABC):
    """Common TCP server logic; subclasses only define the protocol command and physics."""

    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self.port = port
        self.host = host
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self.startup_error: OSError | None = None
        self._logger = logging.getLogger(f"emulator.{self.__class__.__name__}")

    # ------------------------------------------------------------------ lifecycle
    def start_server(self) -> None:
        """Run the server loop (blocking) until :meth:`stop` is called.

        Requests are handled one at a time, as on a real serial-style instrument.
        """
        try:
            server = self._create_listening_socket()
        except OSError as exc:
            self.startup_error = exc
            self._logger.error("Cannot bind %s:%s - %s", self.host, self.port, exc)
            self._ready.set()  # unblock waiters; they must check startup_error
            return

        with server:
            self._logger.info("%s is running on port %s", self.__class__.__name__, self.port)
            self._ready.set()
            while not self._stop_requested.is_set():
                try:
                    conn, addr = server.accept()
                except TimeoutError:
                    continue  # periodic wake-up to honour stop()
                except OSError as exc:
                    self._logger.error("accept() failed: %s", exc)
                    continue
                self._handle_client(conn, addr)

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Block until the server is listening. Returns False on timeout or bind error."""
        return self._ready.wait(timeout) and self.startup_error is None

    def stop(self) -> None:
        """Ask the server loop to exit (takes effect within ~0.5 s)."""
        self._stop_requested.set()

    # ------------------------------------------------------------------ internals
    def _create_listening_socket(self) -> socket.socket:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # On POSIX, allow an immediate restart while the old socket is in TIME_WAIT.
            # On Windows SO_REUSEADDR lets *another* process steal the port, and the
            # TIME_WAIT problem does not exist there, so it is deliberately skipped.
            if os.name != "nt":
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen()
            server.settimeout(_ACCEPT_POLL_SECONDS)
        except OSError:
            server.close()
            raise
        return server

    def _handle_client(self, conn: socket.socket, addr: tuple) -> None:
        with conn:
            try:
                self._logger.debug("Connected by %s", addr)
                data = conn.recv(_RECV_BUFFER_SIZE).strip()
                if data == self.get_current_command:
                    current = self.measure_current()
                    conn.sendall(str(current).encode("utf-8"))
                else:
                    self._logger.warning("Unknown command %r from %s", data, addr)
                    conn.sendall(UNKNOWN_COMMAND_RESPONSE)
            except OSError as exc:
                # A single misbehaving client must never kill the emulator thread.
                self._logger.warning("Connection error with %s: %s", addr, exc)

    # ------------------------------------------------------------------ device contract
    @property
    @abstractmethod
    def get_current_command(self) -> bytes:
        """The exact command (bytes) that triggers a current measurement."""
        raise NotImplementedError(NOT_IMPLEMENTED_MSG)

    @abstractmethod
    def measure_current(self) -> float:
        """Device-specific current measurement logic, in amperes."""
        raise NotImplementedError(NOT_IMPLEMENTED_MSG)
