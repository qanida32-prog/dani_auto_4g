from Ammeters.base_ammeter import AmmeterEmulatorBase
from src.utils.Utils import generate_random_float


class GreenleeAmmeter(AmmeterEmulatorBase):
    """Greenlee emulator - Ohm's law: I = V / R."""

    @property
    def get_current_command(self) -> bytes:
        return b"MEASURE_GREENLEE -get_measurement"

    def measure_current(self) -> float:
        voltage = generate_random_float(1.0, 10.0)  # 1V - 10V
        resistance = generate_random_float(0.1, 100.0)  # 0.1Ω - 100Ω
        current = voltage / resistance
        self._logger.debug(
            "Voltage: %sV, Resistance: %sΩ, Current: %sA", voltage, resistance, current
        )
        return current
