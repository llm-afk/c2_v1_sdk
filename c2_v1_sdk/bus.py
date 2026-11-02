"""One synchronous receive owner per borrowed CAN transport, no hidden traffic."""
from dataclasses import dataclass
import math
import threading
import time

from .frame import CanFrame, CanTransport, snapshot
from .protocol import (ControlReply, FEEDBACK_BASE, SDO_RESPONSE_BASE, MitCommand,
                       MotorState, disable_frame, mit_frame, parse_control_reply,
                       parse_state, validate_node)


@dataclass(frozen=True)
class ReceivedFrame:
    """Every received frame is returned, including unrelated/malformed/error frames.

    receive_index is SDK delivery order. received_at_ns is the time recv observed
    the frame; frame.timestamp_ns/sequence preserve transport metadata as given.
    """

    frame: CanFrame
    receive_index: int
    received_at_ns: int
    state: MotorState | None = None
    control_reply: ControlReply | None = None
    decode_error: str | None = None


class MotorBus:
    """Borrow an open send(frame)/recv(timeout) transport, without closing it.

    Create all desired nodes with motor(id), then call recv from the same thread.
    There is no background task, internal receive loop, bounded queue or retry.
    The caller receives and can record each raw frame, even when decoding fails.
    """

    def __init__(self, transport: CanTransport):
        self._transport = transport
        self._owner = threading.get_ident()
        self._motors = {}
        self._feedback = {}
        self._sdo = {}
        self._receive_index = 0

    def _check(self):
        if threading.get_ident() != self._owner:
            raise RuntimeError("MotorBus I/O and node registration require its creating thread")

    def motor(self, node_id: int):
        self._check()
        validate_node(node_id)
        if node_id not in self._motors:
            motor = Motor(self, node_id)
            self._motors[node_id] = motor
            self._feedback[FEEDBACK_BASE + node_id] = motor
            self._sdo[SDO_RESPONSE_BASE + node_id] = motor
        return self._motors[node_id]

    def _send(self, frame):
        self._check()
        self._transport.send(frame)

    def recv(self, timeout: float = 0.1) -> ReceivedFrame | None:
        self._check()
        try:
            valid = type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("timeout must be finite and positive")
        raw = self._transport.recv(timeout)
        if raw is None:
            return None
        at = time.perf_counter_ns()
        frame = snapshot(raw)
        self._receive_index += 1
        state = reply = problem = None
        if not (frame.is_error or frame.is_remote or frame.is_extended):
            try:
                if motor := self._feedback.get(frame.can_id):
                    state = parse_state(frame, motor.node_id)
                    motor._last_state = state
                elif motor := self._sdo.get(frame.can_id):
                    # Other SDO objects stay raw; this SDK only handles disable.
                    if len(frame.data) >= 4 and frame.data[1:4] == bytes.fromhex("022000"):
                        reply = parse_control_reply(frame, motor.node_id)
            except ValueError as exc:
                problem = str(exc)
        return ReceivedFrame(frame, self._receive_index, at, state, reply, problem)


class Motor:
    """Node handle. Creating it does not enable or contact the motor."""

    def __init__(self, bus: MotorBus, node_id: int):
        self._bus = bus
        self._node_id = validate_node(node_id)
        self._last_state = None

    @property
    def node_id(self) -> int:
        return self._node_id

    @property
    def last_state(self) -> MotorState | None:
        """Convenience cache, potentially stale; use the recv stream for recording."""
        return self._last_state

    def send_mit(self, *, position: float, velocity: float, torque: float,
                 kp: float, kd: float) -> None:
        """Send exactly one command; firmware attempts auto-enable on this path.

        Does not wait for feedback or retry. Transport completion is not proof of
        a CAN ACK, driver acceptance, power-stage enable or physical movement.
        """
        frame = mit_frame(self.node_id, MitCommand(position, velocity, torque, kp, kd))
        self._bus._send(frame)

    def disable(self) -> None:
        """Send only SDO 0x2002=2; recv exposes any reply without correlating it.

        No acknowledgement is waited for and no disabled state is inferred. A
        delayed 0x2002 reply has no transaction number or echoed written value.
        """
        self._bus._send(disable_frame(self.node_id))
