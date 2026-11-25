from Ammeters.base_ammeter import AmmeterEmulatorBase
from src.utils.Utils import generate_random_float


class EntesAmmeter(AmmeterEmulatorBase):
    """ENTES emulator - Hall effect: I = B * K."""

    @property
    def get_current_command(self) -> bytes:
        return b"MEASURE_ENTES -get_data"

    def measure_current(self) -> float:
        magnetic_field = generate_random_float(0.01, 0.1)  # 0.01T - 0.1T
        calibration_factor = generate_random_float(500, 2000)  # 500 - 2000
        current = magnetic_field * calibration_factor
        self._logger.debug(
            "Magnetic Field: %sT, Calibration Factor: %s, Current: %sA",
            magnetic_field,
            calibration_factor,
            current,
        )
        return current
