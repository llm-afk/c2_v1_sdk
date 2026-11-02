"""Optional offline integration with the real CANable codecs; never opens USB."""
from collections import deque
import struct
import threading
import unittest
from unittest.mock import patch

from c2_sdk_v1 import CanableTransport, MotorBus

try:
    import canable
    from canable.frame import CanFrame
    from canable.transport import Canable, decode_usb, encode_usb
except ImportError:
    canable = None


@unittest.skipIf(canable is None, "install CANable SDK for optional offline integration")
class CanableIntegrationTests(unittest.TestCase):
    def fake_open_transport(self):
        packets = []
        state = CanFrame(0x191, struct.pack("<fffhh", 1.5, -2, .25, 250, 300))

        class Usb:
            input, output, last_read_ns = 0x81, 1, 123456789

            def __init__(self):
                self.rx = deque([encode_usb(state, 0xFFFFFFFF)])

            def transfer(self, pipe, data=None, timeout=None):
                if pipe == self.output:
                    packets.append(data)
                else:
                    return self.rx.popleft() if self.rx else None

        transport = Canable("offline-only")  # Constructor only stores parameters.
        transport._usb = Usb()
        transport._started = transport._fd = True
        return transport, packets

    def check_roundtrip(self, bus, packets):
        motor = bus.motor(1)
        motor.enable()
        motor.send_mit(position=1, velocity=-2, torque=.5, kp=20, kd=1)
        sent = bytearray(packets[2])
        sent[:4] = bytes.fromhex("ffffffff")  # Treat captured TX bytes as a receive fixture.
        frame = decode_usb(bytes(sent))
        self.assertEqual(frame.can_id, 0x101)
        self.assertEqual(frame.data.hex(), "0000803f000000c00000003fd0076400")
        self.assertTrue(frame.is_fd and frame.brs)
        event = bus.recv()
        self.assertEqual(event.state.position, 1.5)
        self.assertEqual((event.state.timestamp_ns, event.state.sequence), (123456789, 1))
        motor.disable()
        self.assertEqual(len(packets), 4)
        controls = []
        for packet in (packets[0], packets[1], packets[3]):
            received = bytearray(packet)
            received[:4] = bytes.fromhex("ffffffff")
            controls.append(decode_usb(bytes(received)))
        self.assertEqual([(item.can_id, item.data.hex()) for item in controls],
                         [(0x601, "2f03200003000000"), (0x601, "2b02200001000000"),
                          (0x601, "2b02200002000000")])

    def test_canable_object_with_mock_usb(self):
        transport, packets = self.fake_open_transport()
        with patch.object(Canable, "_connect", side_effect=AssertionError("no real USB")):
            self.check_roundtrip(MotorBus(transport), packets)

    def test_canable_five_function_module_with_mock_usb(self):
        transport, packets = self.fake_open_transport()
        with patch.multiple(canable, _bus=transport, _owner=threading.get_ident(), _fd=True), \
                patch.object(Canable, "_connect", side_effect=AssertionError("no real USB")):
            self.check_roundtrip(MotorBus(CanableTransport(canable)), packets)


if __name__ == "__main__":
    unittest.main()
