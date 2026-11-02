import math
import struct
import unittest

from c2_sdk_v1 import (CanFrame, ErrorFlag, MitCommand, disable_frame, enable_frame, error_names,
                       mit_frame, mit_mode_frame, parse_control_reply, parse_state)


def feedback(node=1, error=None, **flags):
    data = struct.pack("<fffhh", 1.25, -2.5, 0.125, -123, 456)
    if error is not None:
        data += struct.pack("<H", error) + bytes.fromhex("dead")
    return CanFrame(0x190 + node, data, **flags)


class CommandTests(unittest.TestCase):
    def test_golden_frame_and_gain_scaling(self):
        frame = mit_frame(1, MitCommand(1, -2, .5, 20, 1))
        self.assertEqual(frame.can_id, 0x101)
        self.assertEqual(frame.data.hex(), "0000803f000000c00000003fd0076400")
        self.assertTrue(frame.is_fd and frame.brs)
        self.assertFalse(frame.is_extended or frame.is_error or frame.is_remote)
        self.assertEqual(struct.unpack("<fffHH", MitCommand(kp=.01, kd=655.35).pack())[3:],
                         (1, 65535))

    def test_each_node_uses_addition_and_zero_is_unicast(self):
        for node in range(128):
            self.assertEqual(mit_frame(node, MitCommand()).can_id, 0x100 + node)
            self.assertEqual(enable_frame(node).can_id, 0x600 + node)
            self.assertEqual(disable_frame(node).can_id, 0x600 + node)
        for bad in (-1, 128, True, 1.0, "1"):
            for make_frame in (lambda node: mit_frame(node, MitCommand()), enable_frame, disable_frame, mit_mode_frame):
                with self.subTest(node=bad, codec=make_frame), self.assertRaises(ValueError):
                    make_frame(bad)

    def test_nonfinite_overflow_and_wrong_types_rejected_in_all_fields(self):
        for name in ("position", "velocity", "torque", "kp", "kd"):
            for value in (math.nan, math.inf, -math.inf, 1e100, 10**1000, True, "1", None):
                with self.subTest(name=name, value=repr(value)[:30]), self.assertRaises(ValueError):
                    MitCommand(**{name: value}).pack()
        for name in ("kp", "kd"):
            for value in (-.01, 655.36):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    MitCommand(**{name: value}).pack()

    def test_gain_quantization_and_float32_extremes(self):
        data = MitCommand(3.4028234663852886e38, -1e-40, 0, .125, .135).pack()
        position, velocity, _, kp, kd = struct.unpack("<fffHH", data)
        self.assertTrue(math.isfinite(position))
        self.assertLess(velocity, 0)
        self.assertEqual((kp, kd), (12, 14))  # Python round: ties to even.

    def test_disable_is_exact_two_byte_sdo_write(self):
        frame = disable_frame(127)
        self.assertEqual((frame.can_id, frame.data.hex()), (0x67F, "2b02200002000000"))

    def test_enable_is_exact_two_byte_sdo_write(self):
        frame = enable_frame(0)
        self.assertEqual((frame.can_id, frame.data.hex()), (0x600, "2b02200001000000"))
        self.assertTrue(frame.is_fd and frame.brs)

    def test_gd_mit_mode_is_exact_one_byte_sdo_write(self):
        frame = mit_mode_frame(127)
        self.assertEqual((frame.can_id, frame.data.hex()), (0x67F, "2f03200003000000"))

    def test_mode_acceptance_and_adm_rejection_are_both_visible(self):
        for cs in (0x60, 0x80):
            reply = parse_control_reply(CanFrame(0x581, bytes([cs]) + bytes.fromhex("03200003000000")), 1)
            self.assertEqual(reply.index, 0x2003)
            self.assertEqual(reply.accepted, cs == 0x60)


class FeedbackTests(unittest.TestCase):
    def test_units_signed_temperatures_and_transport_metadata(self):
        frame = feedback(timestamp_ns=123456, sequence=9)
        state = parse_state(frame, 1)
        self.assertEqual((state.position, state.velocity, state.torque), (1.25, -2.5, .125))
        self.assertEqual((state.motor_temperature, state.driver_temperature), (-12.3, 45.6))
        self.assertEqual((state.timestamp_ns, state.sequence), (123456, 9))
        self.assertEqual(state.raw_data, frame.data)
        self.assertEqual(state.error_code, 0)

    def test_error_raw_mask_and_undefined_tail_are_preserved(self):
        frame = feedback(error=0x0188)
        state = parse_state(frame, 1)
        self.assertEqual(state.error_code, 0x0188)  # Never strip heartbeat in host code.
        self.assertTrue(state.errors & ErrorFlag.HEARTBEAT_TIMEOUT)
        self.assertEqual(state.raw_data[-2:], bytes.fromhex("dead"))

    def test_exact_feedback_id_for_all_nodes(self):
        for node in range(128):
            self.assertEqual(parse_state(feedback(node), node).node_id, node)
        with self.assertRaises(ValueError):
            parse_state(feedback(1), 17)

    def test_invalid_feedback_is_not_interpreted_as_state(self):
        valid = feedback()
        bad = [CanFrame(valid.can_id, bytes(length)) for length in (0, 8, 12, 18, 24, 64)]
        bad += [feedback(**{flag: value}) for flag, value in
                (("is_fd", False), ("is_extended", True), ("is_error", True), ("is_remote", True))]
        for offset in (0, 4, 8):
            data = bytearray(valid.data)
            struct.pack_into("<f", data, offset, math.nan)
            bad.append(CanFrame(valid.can_id, data))
        for frame in bad:
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                parse_state(frame, 1)

    def test_payload_snapshot_is_immutable(self):
        payload = bytearray(feedback().data)
        frame = CanFrame(0x191, payload)
        payload[:] = bytes(16)
        self.assertEqual(parse_state(frame, 1).position, 1.25)

    def test_disable_ack_rejection_and_no_abort_code_inference(self):
        accepted = parse_control_reply(CanFrame(0x581, bytes.fromhex("6002200002000000")), 1)
        rejected = parse_control_reply(CanFrame(0x581, bytes.fromhex("8002200002000000")), 1)
        self.assertTrue(accepted.accepted)
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.raw_data.hex(), "8002200002000000")
        for frame in (CanFrame(0x582, accepted.raw_data),
                      CanFrame(0x581, bytes.fromhex("6002200102000000")),
                      CanFrame(0x581, bytes.fromhex("4302200002000000")),
                      CanFrame(0x581, accepted.raw_data[:7]),
                      CanFrame(0x581, accepted.raw_data, is_error=True)):
            with self.subTest(frame=frame), self.assertRaises(ValueError):
                parse_control_reply(frame, 1)


class ErrorTests(unittest.TestCase):
    def test_combined_common_bits_and_unknown_bits(self):
        self.assertEqual(error_names(0), ())
        self.assertEqual(error_names(0x0008 | 0x0010 | 0x2000),
                         ("OVER_TEMP_DRV", "OVER_TEMP_MOTOR", "UNKNOWN_0x2000"))
        self.assertEqual(int(ErrorFlag(0xFFFF)), 0xFFFF)

    def test_gd_only_names_require_explicit_firmware(self):
        self.assertEqual(error_names(0x0900), ("UNKNOWN_0x0900",))
        self.assertEqual(error_names(0x0900, firmware="adm"), ("UNKNOWN_0x0900",))
        self.assertEqual(error_names(0x0900, firmware="gd"), ("MULTI_CHECK_ERROR", "ENC_SLIP"))

    def test_invalid_error_arguments(self):
        for code in (-1, 65536, True, "1"):
            with self.assertRaises(ValueError):
                error_names(code)
        with self.assertRaises(ValueError):
            error_names(0, firmware="auto")


if __name__ == "__main__":
    unittest.main()
