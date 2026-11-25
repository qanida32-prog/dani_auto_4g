"""Shared pytest fixtures."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator

import pytest

from Ammeters.base_ammeter import AmmeterEmulatorBase
from Ammeters.Circutor_Ammeter import CircutorAmmeter
from Ammeters.Entes_Ammeter import EntesAmmeter
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter

EMULATORS = {
    "greenlee": GreenleeAmmeter,
    "entes": EntesAmmeter,
    "circutor": CircutorAmmeter,
}


def get_free_port() -> int:
    """Ask the OS for an unused TCP port so tests never collide with running emulators."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_emulator(cls: type[AmmeterEmulatorBase], port: int) -> AmmeterEmulatorBase:
    emulator = cls(port)
    threading.Thread(target=emulator.start_server, daemon=True).start()
    assert emulator.wait_until_ready(5), f"{cls.__name__} failed to start: {emulator.startup_error}"
    return emulator


@pytest.fixture(params=sorted(EMULATORS), ids=str)
def running_emulator(request) -> Iterator[AmmeterEmulatorBase]:
    """Each supported emulator, running on a free port, stopped after the test."""
    emulator = start_emulator(EMULATORS[request.param], get_free_port())
    yield emulator
    emulator.stop()
