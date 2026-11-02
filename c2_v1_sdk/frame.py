"""Transport-neutral, immutable CAN frame; compatible with CANable's frame shape."""
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class CanFrame:
    can_id: int
    data: bytes
    is_fd: bool = True
    brs: bool = True
    is_extended: bool = False
    is_error: bool = False
    is_remote: bool = False
    timestamp_ns: int | None = field(default=None, compare=False)
    sequence: int | None = field(default=None, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "data", bytes(self.data))


class CanTransport(Protocol):
    """Borrow an open, exclusively received transport. SDK does not open/close it."""

    def send(self, frame: CanFrame) -> None: ...

    def recv(self, timeout: float) -> CanFrame | None: ...


def snapshot(frame) -> CanFrame:
    """Copy payload/metadata before a transport can reuse its receive storage."""
    return CanFrame(
        frame.can_id, frame.data, frame.is_fd, frame.brs, frame.is_extended,
        frame.is_error, frame.is_remote, getattr(frame, "timestamp_ns", None),
        getattr(frame, "sequence", None),
    )
