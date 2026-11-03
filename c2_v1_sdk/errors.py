"""Firmware error bit masks. Unknown bits remain visible, never mapped to OK."""
from enum import IntFlag


class ErrorFlag(IntFlag):
    """Names shared by the inspected ADM and GD headers, not protection guarantees."""

    NONE = 0
    OVER_VOLTAGE = 0x0001
    UNDER_VOLTAGE = 0x0002
    FOLLOWING_ERROR = 0x0004
    OVER_TEMP_DRV = 0x0008
    OVER_TEMP_MOTOR = 0x0010
    OVER_CURRENT_SOFT = 0x0020
    OVER_LOAD = 0x0040
    HEARTBEAT_TIMEOUT = 0x0080
    NO_SN = 0x0200
    ENC_MISSING = 0x0400
    ENC_CALIB = 0x4000
    ADC_SELFTEST = 0x8000


def error_names(code: int, *, firmware: str | None = None) -> tuple[str, ...]:
    """Decode shared bits, or the GD-only 0x0100/0x0800 when firmware='gd'.

    None means unknown firmware, not automatic identification. These names follow
    firmware constants; e.g. ADM uses OVER_CURRENT_SOFT for its thermal model.
    """
    if type(code) is not int or not 0 <= code <= 0xFFFF:
        raise ValueError("error code must be an integer in 0..65535")
    if firmware not in (None, "adm", "gd"):
        raise ValueError("firmware must be None, 'adm' or 'gd'")
    names = {int(flag): flag.name for flag in ErrorFlag}
    if firmware == "gd":
        names.update({0x0100: "MULTI_CHECK_ERROR", 0x0800: "ENC_SLIP"})
    result = []
    remaining = code
    for bit, name in sorted(names.items()):
        if code & bit:
            result.append(name)
            remaining &= ~bit
    if remaining:
        result.append(f"UNKNOWN_0x{remaining:04X}")
    return tuple(result)
