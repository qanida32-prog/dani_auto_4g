"""Smoke tests proving the provided emulator infrastructure works after the phase-1 fixes."""

from __future__ import annotations

import time

import pytest

from Ammeters.client import (
    AmmeterConnectionError,
    AmmeterProtocolError,
    request_current_from_ammeter,
)
from Ammeters.Greenlee_Ammeter import GreenleeAmmeter
from tests.conftest import get_free_port, start_emulator


def test_valid_command_returns_positive_current(running_emulator):
    current = request_current_from_ammeter(
        running_emulator.port, running_emulator.get_current_command
    )
    assert current > 0


def test_trailing_newline_in_command_is_tolerated(running_emulator):
    current = request_current_from_ammeter(
        running_emulator.port, running_emulator.get_current_command + b"\n"
    )
    assert current > 0


def test_unknown_command_raises_protocol_error(running_emulator):
    with pytest.raises(AmmeterProtocolError, match="unknown command"):
        request_current_from_ammeter(running_emulator.port, b"MEASURE_SOMETHING_ELSE")


def test_closed_port_raises_connection_error():
    with pytest.raises(AmmeterConnectionError):
        request_current_from_ammeter(get_free_port(), b"MEASURE_GREENLEE -get_measurement")


def test_emulator_survives_a_bad_request(running_emulator):
    with pytest.raises(AmmeterProtocolError):
        request_current_from_ammeter(running_emulator.port, b"garbage")
    assert (
        request_current_from_ammeter(running_emulator.port, running_emulator.get_current_command)
        > 0
    )


def test_emulator_can_restart_on_same_port():
    port = get_free_port()
    first = start_emulator(GreenleeAmmeter, port)
    request_current_from_ammeter(port, first.get_current_command)
    first.stop()
    time.sleep(0.7)  # let the accept-poll loop observe stop() and close the socket

    second = start_emulator(GreenleeAmmeter, port)
    try:
        assert request_current_from_ammeter(port, second.get_current_command) > 0
    finally:
        second.stop()


def test_port_conflict_is_reported_not_hung():
    port = get_free_port()
    first = start_emulator(GreenleeAmmeter, port)
    try:
        clash = GreenleeAmmeter(port)
        clash.start_server()  # returns immediately on bind failure
        assert clash.wait_until_ready(1) is False
        assert isinstance(clash.startup_error, OSError)
    finally:
        first.stop()
