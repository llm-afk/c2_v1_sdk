"""Pure codecs for the source-verified 0x400 MIT / 0x190 TPDO path."""
from dataclasses import dataclass, field
import math
import struct

from .errors import ErrorFlag
from .frame import CanFrame

MIT_BASE = 0x400
FEEDBACK_BASE = 0x190
SDO_REQUEST_BASE = 0x600
SDO_RESPONSE_BASE = 0x580
CONTROL_WORD_INDEX = 0x2002
_MIT = struct.Struct("<fffHH")
_STATE = struct.Struct("<fffhh")


def validate_node(node_id: int) -> int:
    if type(node_id) is not int or not 0 <= node_id <= 127:
        raise ValueError("node_id must be an integer in 0..127; 0 is not broadcast here")
    return node_id


def _finite_number(value, name):
    try:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
    except OverflowError as exc:
        raise ValueError(f"{name} is too large") from exc


@dataclass(frozen=True)
class MitCommand:
    position: float = 0.0  # rad, firmware's feedback coordinate
    velocity: float = 0.0  # rad/s
    torque: float = 0.0    # firmware feedforward torque value, not amperes
    kp: float = 0.0        # API gain; uint16 wire value = round(gain * 100)
    kd: float = 0.0

    def pack(self) -> bytes:
        for name in ("position", "velocity", "torque", "kp", "kd"):
            _finite_number(getattr(self, name), name)
        for name in ("kp", "kd"):
            if not 0 <= getattr(self, name) <= 655.35:
                raise ValueError(f"{name} must be in 0..655.35 (wire range, not a motor limit)")
        try:
            return _MIT.pack(self.position, self.velocity, self.torque,
                             round(self.kp * 100), round(self.kd * 100))
        except (OverflowError, struct.error) as exc:
            raise ValueError("MIT values must be representable as finite float32/uint16") from exc


@dataclass(frozen=True)
class MotorState:
    node_id: int
    position: float
    velocity: float
    torque: float
    motor_temperature: float
    driver_temperature: float
    error_code: int
    timestamp_ns: int | None = None
    sequence: int | None = None
    raw_data: bytes = field(default=b"", repr=False)

    @property
    def errors(self) -> ErrorFlag:
        return ErrorFlag(self.error_code)


@dataclass(frozen=True)
class ControlReply:
    """Control-word SDO response, with no transaction or power-stage proof."""

    node_id: int
    accepted: bool
    raw_data: bytes


def mit_frame(node_id: int, command: MitCommand) -> CanFrame:
    return CanFrame(MIT_BASE + validate_node(node_id), command.pack())


def disable_frame(node_id: int) -> CanFrame:
    # Keep FD+BRS as in the known transport path, although SDO payload is 8 bytes.
    return CanFrame(SDO_REQUEST_BASE + validate_node(node_id),
                    bytes.fromhex("2b02200002000000"))


def _data_frame(frame, expected_id):
    if (frame.can_id != expected_id or frame.is_extended
            or frame.is_error or frame.is_remote):
        raise ValueError("not the expected standard CAN data frame")


def parse_state(frame, node_id: int) -> MotorState:
    _data_frame(frame, FEEDBACK_BASE + validate_node(node_id))
    if not frame.is_fd or len(frame.data) not in (16, 20):
        raise ValueError("MIT feedback must be a 16- or 20-byte CAN FD frame")
    raw = bytes(frame.data)
    position, velocity, torque, mt, dt = _STATE.unpack_from(raw)
    if not all(math.isfinite(value) for value in (position, velocity, torque)):
        raise ValueError("non-finite MIT feedback")
    error = struct.unpack_from("<H", raw, 16)[0] if len(raw) == 20 else 0
    # Bytes 18..19 are not defined by these firmware paths; retain but do not decode.
    return MotorState(node_id, position, velocity, torque, mt / 10, dt / 10,
                      error, getattr(frame, "timestamp_ns", None),
                      getattr(frame, "sequence", None), raw)


def parse_control_reply(frame, node_id: int) -> ControlReply:
    _data_frame(frame, SDO_RESPONSE_BASE + validate_node(node_id))
    raw = bytes(frame.data)
    if len(raw) != 8 or raw[1:4] != bytes.fromhex("022000"):
        raise ValueError("not an 8-byte SDO reply for 0x2002:00")
    if raw[0] not in (0x60, 0x80):
        raise ValueError("unexpected SDO response command specifier")
    # Rejection payload is not assumed to contain a standard CANopen abort code.
    return ControlReply(node_id, raw[0] == 0x60, raw)
