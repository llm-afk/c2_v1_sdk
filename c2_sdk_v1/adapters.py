"""Optional glue for the already-open CANable module's five-function API."""


class CanableTransport:
    """Borrow the canable module after canable.open(fd=True); never open/close it.

    Does not import canable or inspect its private fields. For CANable's object
    interface (canable.transport.Canable), pass that open object to MotorBus
    directly instead. One MotorBus must be the sole receiver for this channel.
    """

    def __init__(self, module):
        self._module = module

    def send(self, frame):
        if not frame.is_fd or frame.is_error or frame.is_remote:
            raise ValueError("this adapter sends CAN FD data frames only; open(fd=True) first")
        self._module.send(frame.can_id, frame.data, brs=frame.brs,
                          is_extended=frame.is_extended)

    def recv(self, timeout):
        return self._module.recv(timeout)
