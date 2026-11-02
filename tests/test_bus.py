from collections import deque
from dataclasses import replace
import math
import struct
import threading
from types import SimpleNamespace
import unittest

from c2_v1_sdk import CanableTransport, CanFrame, MotorBus


def feedback(node, count=0):
    return CanFrame(0x190 + node, struct.pack("<fffhh", count, -count, .5, 100, 250),
                    timestamp_ns=1000 + count, sequence=count + 1)


class FakeTransport:
    def __init__(self, frames=()):
        self.frames = deque(frames)
        self.sent = []
        self.receive_calls = []

    def send(self, frame):
        self.sent.append(frame)

    def recv(self, timeout):
        self.receive_calls.append(timeout)
        return self.frames.popleft() if self.frames else None


class BusTests(unittest.TestCase):
    def test_registration_and_timeout_have_no_hidden_traffic(self):
        transport = FakeTransport()
        bus = MotorBus(transport)
        motor = bus.motor(0)
        self.assertIs(bus.motor(0), motor)
        self.assertIsNone(motor.last_state)
        self.assertIsNone(bus.recv(.123))
        self.assertEqual(transport.receive_calls, [.123])
        self.assertEqual(transport.sent, [])
        self.assertFalse(hasattr(bus, "close"))  # Borrowed connection remains caller-owned.

    def test_send_one_command_then_explicit_disable(self):
        transport = FakeTransport()
        motor = MotorBus(transport).motor(1)
        motor.send_mit(position=1, velocity=-2, torque=.5, kp=20, kd=1)
        self.assertEqual(len(transport.sent), 1)
        self.assertEqual(transport.sent[0].data.hex(), "0000803f000000c00000003fd0076400")
        self.assertEqual(transport.receive_calls, [])
        motor.disable()
        self.assertEqual(transport.sent[1].data.hex(), "2b02200002000000")
        self.assertIsNone(motor.last_state)
        self.assertFalse(hasattr(motor, "enabled"))

    def test_validation_is_atomic_and_never_sends_invalid_command(self):
        transport = FakeTransport()
        motor = MotorBus(transport).motor(1)
        for name in ("position", "velocity", "torque", "kp", "kd"):
            command = dict(position=0, velocity=0, torque=0, kp=0, kd=0)
            command[name] = math.nan
            with self.assertRaises(ValueError):
                motor.send_mit(**command)
        self.assertEqual(transport.sent, [])

    def test_interleaved_stream_keeps_all_10000_samples(self):
        frames = [feedback(i % 2 + 1, i) for i in range(10000)]
        transport = FakeTransport(frames)
        bus = MotorBus(transport)
        one, two = bus.motor(1), bus.motor(2)
        for i, expected in enumerate(frames):
            event = bus.recv()
            self.assertEqual(event.frame, expected)
            self.assertEqual(event.receive_index, i + 1)
            self.assertEqual(event.state.node_id, i % 2 + 1)
            self.assertEqual(event.state.position, i)
            self.assertEqual(event.state.sequence, i + 1)
            self.assertIsNone(event.decode_error)
        self.assertEqual(one.last_state.position, 9998)
        self.assertEqual(two.last_state.position, 9999)
        self.assertIsNone(bus.recv())
        self.assertEqual(transport.sent, [])

    def test_unknown_malformed_can_error_and_wrong_flags_stay_raw(self):
        valid = feedback(1)
        frames = [feedback(2), CanFrame(0x701, b"\0", is_fd=False),
                  CanFrame(8, bytes(8), is_error=True, is_fd=False),
                  replace(valid, data=bytes(12)), replace(valid, is_fd=False),
                  replace(valid, is_extended=True), replace(valid, is_remote=True),
                  CanFrame(0x581, bytes.fromhex("4300200000000000"))]
        bus = MotorBus(FakeTransport(frames))
        motor = bus.motor(1)
        events = [bus.recv() for _ in frames]
        self.assertEqual([event.frame for event in events], frames)
        self.assertEqual([i for i, event in enumerate(events) if event.decode_error], [3, 4])
        self.assertTrue(all(event.state is None for event in events))
        self.assertIsNone(motor.last_state)

    def test_bad_feedback_preserves_previous_cache_and_its_raw_payload(self):
        malformed = replace(feedback(1), data=struct.pack("<f", math.inf) + bytes(12))
        bus = MotorBus(FakeTransport([feedback(1), malformed]))
        motor = bus.motor(1)
        good = bus.recv()
        bad = bus.recv()
        self.assertIs(motor.last_state, good.state)
        self.assertIsNotNone(bad.decode_error)
        self.assertEqual(bad.frame.data, malformed.data)

    def test_sdo_reply_does_not_assert_disabled_state_or_match_a_transaction(self):
        ack = CanFrame(0x581, bytes.fromhex("6002200001000000"))
        bus = MotorBus(FakeTransport([ack]))
        motor = bus.motor(1)
        event = bus.recv()  # Even without a request, cannot infer what was written.
        self.assertTrue(event.control_reply.accepted)
        self.assertEqual(event.control_reply.raw_data, ack.data)
        self.assertIsNone(motor.last_state)

    def test_sdo_bad_response_preserved_with_decode_error(self):
        raw = CanFrame(0x581, bytes.fromhex("4302200001000000"))
        bus = MotorBus(FakeTransport([raw]))
        bus.motor(1)
        event = bus.recv()
        self.assertEqual(event.frame, raw)
        self.assertIsNotNone(event.decode_error)

    def test_transport_failure_propagates_once_without_retry(self):
        class Broken(FakeTransport):
            def send(self, frame):
                super().send(frame)
                raise OSError("write outcome uncertain")

        transport = Broken()
        motor = MotorBus(transport).motor(1)
        with self.assertRaisesRegex(OSError, "uncertain"):
            motor.send_mit(position=0, velocity=0, torque=0, kp=0, kd=0)
        self.assertEqual(len(transport.sent), 1)

    def test_thread_ownership_rejects_before_transport_access(self):
        transport = FakeTransport()
        bus = MotorBus(transport)
        motor = bus.motor(1)
        failures = []

        def wrong_thread():
            for operation in (lambda: bus.motor(2), bus.recv, motor.disable,
                              lambda: motor.send_mit(position=0, velocity=0, torque=0, kp=0, kd=0)):
                try:
                    operation()
                except RuntimeError as exc:
                    failures.append(str(exc))

        thread = threading.Thread(target=wrong_thread)
        thread.start()
        thread.join()
        self.assertEqual(len(failures), 4)
        self.assertEqual((transport.sent, transport.receive_calls), ([], []))

    def test_invalid_timeout_does_not_consume_a_frame(self):
        transport = FakeTransport([feedback(1)])
        bus = MotorBus(transport)
        for value in (0, -1, math.nan, math.inf, True, None, 10**1000):
            with self.assertRaises(ValueError):
                bus.recv(value)
        self.assertEqual(len(transport.frames), 1)


class AdapterTests(unittest.TestCase):
    def test_canable_module_mapping_borrows_lifecycle(self):
        sent = []
        raw = feedback(1)
        module = SimpleNamespace(send=lambda *args, **kw: sent.append((args, kw)),
                                 recv=lambda timeout: raw)
        adapter = CanableTransport(module)
        bus = MotorBus(adapter)
        motor = bus.motor(1)
        motor.send_mit(position=0, velocity=0, torque=.1, kp=0, kd=0)
        self.assertEqual(sent[0][0][0], 0x401)
        self.assertEqual(sent[0][1], {"brs": True, "is_extended": False})
        self.assertEqual(bus.recv().state.position, 0)
        for frame in (replace(raw, is_fd=False), replace(raw, is_error=True),
                      replace(raw, is_remote=True)):
            with self.assertRaises(ValueError):
                adapter.send(frame)
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
