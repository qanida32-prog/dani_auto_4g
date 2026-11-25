"""Low-level TCP client for the ammeter emulators."""

from __future__ import annotations

import math
import socket

DEFAULT_HOST = "127.0.0.1"  # not "localhost": see docs/FIXES.md #21
DEFAULT_TIMEOUT_SECONDS = 2.0
_RECV_BUFFER_SIZE = 1024
_ERROR_PREFIX = "ERROR"


class AmmeterError(Exception):
    """Base class for every ammeter communication failure."""


class AmmeterConnectionError(AmmeterError):
    """The device could not be reached or the connection broke / timed out."""


class AmmeterProtocolError(AmmeterError):
    """The device answered, but the answer is not a valid measurement."""


def request_current_from_ammeter(
    port: int,
    command: bytes,
    host: str = DEFAULT_HOST,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> float:
    """Send ``command`` to the ammeter at ``host:port`` and return the current in amperes.

    Raises:
        AmmeterConnectionError: connection refused, reset or timed out.
        AmmeterProtocolError: empty, error or non-numeric response.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(command)
            raw = _read_until_closed(sock)
    except OSError as exc:  # includes TimeoutError / ConnectionRefusedError
        raise AmmeterConnectionError(f"{host}:{port} - {exc}") from exc

    return _parse_current(raw, host, port)


def _read_until_closed(sock: socket.socket) -> bytes:
    """The emulator closes the connection after replying, so read until EOF."""
    chunks: list[bytes] = []
    while chunk := sock.recv(_RECV_BUFFER_SIZE):
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_current(raw: bytes, host: str, port: int) -> float:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        raise AmmeterProtocolError(f"{host}:{port} - empty response")
    if text.startswith(_ERROR_PREFIX):
        raise AmmeterProtocolError(f"{host}:{port} - device error: {text}")
    try:
        value = float(text)
    except ValueError as exc:
        raise AmmeterProtocolError(f"{host}:{port} - non-numeric response: {text!r}") from exc
    if not math.isfinite(value):
        raise AmmeterProtocolError(f"{host}:{port} - non-finite value: {text!r}")
    return value
