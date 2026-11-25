from Ammeters.base_ammeter import AmmeterEmulatorBase
from src.utils.Utils import generate_random_float

NUM_SAMPLES = 10


class CircutorAmmeter(AmmeterEmulatorBase):
    """CIRCUTOR emulator - Rogowski coil integration: I = ∫V dt (rectangle rule)."""

    @property
    def get_current_command(self) -> bytes:
        # Aligned with the documented protocol in README.md (was '... -current', see FIXES.md)
        return b"MEASURE_CIRCUTOR -get_measurement"

    def measure_current(self) -> float:
        time_step = generate_random_float(0.001, 0.01)  # 0.001s - 0.01s
        voltages = [generate_random_float(0.1, 1.0) for _ in range(NUM_SAMPLES)]
        current = sum(v * time_step for v in voltages)
        self._logger.debug(
            "Voltages: %s, Time Step: %ss, Current: %sA", voltages, time_step, current
        )
        return current
