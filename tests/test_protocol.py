import json
from pathlib import Path
import struct
import unittest

from wxocr_native.protocol import (
    ProtocolError,
    decode_response,
    decode_support,
    encode_request,
)


def length_field(number: int, payload: bytes) -> bytes:
    # Fixtures here are short enough to use one-byte tags and lengths. This
    # helper is independent of the production varint encoder.
    assert number < 16 and len(payload) < 128
    return bytes((number * 8 + 2, len(payload))) + payload


class RequestTests(unittest.TestCase):
    def test_exact_wire_request(self):
        self.assertEqual(
            encode_request(2, "/tmp/a.png"),
            bytes.fromhex("0802120a2f746d702f612e706e673206080110011800"),
        )

    def test_unicode_path_length_counts_bytes(self):
        path = "/tmp/中文.png"
        encoded = encode_request(128, path)
        self.assertEqual(encoded[:4], bytes.fromhex("08800112"))
        self.assertEqual(encoded[4], len(path.encode("utf-8")))
        self.assertIn(path.encode("utf-8"), encoded)

    def test_long_path_uses_multibyte_length(self):
        encoded = encode_request(0x7FFFFFFF, "/" + "x" * 127)
        self.assertEqual(encoded[:9], bytes.fromhex("08ffffffff07128001"))

    def test_invalid_task_ids(self):
        for value in (0, -1, 0x80000000, True, 2.0, "2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                encode_request(value, "/tmp/a.png")

    def test_invalid_paths(self):
        for value in ("", None, "a\0b", "\ud800"):
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                encode_request(2, value)


class SupportTests(unittest.TestCase):
    def test_presence_and_boolean_values(self):
        self.assertFalse(decode_support(b""))
        self.assertFalse(decode_support(b"\x08\x00"))
        self.assertTrue(decode_support(b"\x08\x01"))
        self.assertTrue(decode_support(b"\x08\x02"))
        self.assertFalse(decode_support(b"\x08\x01\x08\x00"))

    def test_unknown_fields_are_skipped(self):
        unknown = (
            b"\x10\x96\x01"  # varint
            + b"\x19" + b"12345678"  # fixed64
            + b"\x22\x02ab"  # length-delimited
            + b"\x2d" + b"1234"  # fixed32
            + b"\x33\x08\x01\x34"  # unknown group
        )
        self.assertTrue(decode_support(unknown + b"\x08\x01"))


class ResponseTests(unittest.TestCase):
    def test_hand_constructed_response_fixture(self):
        # Matches the independently documented WeChat 4 schema, not generated
        # by this codec. The payload includes Unicode, floats and two lines.
        line = length_field(2, "微信 OCR".encode())
        for number, value in ((3, 0.875), (5, 10.5), (6, 20.25), (7, 80.0), (8, 40.5)):
            line += bytes((number * 8 + 5,)) + struct.pack("<f", value)
        info = (
            length_field(3, line)
            + length_field(3, length_field(2, b"second"))
            + bytes.fromhex("20c00228f001")  # width=320, height=240
        )
        task_id, result = decode_response(b"\x08\x02\x10\x00" + length_field(3, info))
        self.assertEqual(task_id, 2)
        self.assertEqual(result, {
            "errcode": 0,
            "width": 320,
            "height": 240,
            "ocr_response": [
                {"text": "微信 OCR", "rate": 0.875, "left": 10.5, "top": 20.25,
                 "right": 80.0, "bottom": 40.5},
                {"text": "second", "rate": 0.0, "left": 0.0, "top": 0.0,
                 "right": 0.0, "bottom": 0.0},
            ],
        })

    def test_negative_int32_error(self):
        task_id, result = decode_response(bytes.fromhex("080210ffffffffffffffffff01"))
        self.assertEqual(task_id, 2)
        self.assertEqual(result["errcode"], -1)
        self.assertEqual(result["ocr_response"], [])
        self.assertEqual(decode_response(bytes.fromhex("1080808080f8ffffffff01"))[1]["errcode"], -2147483648)

    def test_missing_result_and_protobuf_defaults(self):
        self.assertEqual(decode_response(b"")[1]["errcode"], -1)
        self.assertEqual(decode_response(b"\x10\x00")[1]["errcode"], 0)
        self.assertEqual(decode_response(b"\x1a\x00")[1]["errcode"], 0)

    def test_duplicate_singular_messages_merge(self):
        response = length_field(3, b"\x20\x64") + length_field(3, b"\x28\x32")
        result = decode_response(response)[1]
        self.assertEqual((result["width"], result["height"]), (100, 50))

    def test_unknown_fields_do_not_override_known_result(self):
        line = length_field(2, b"hello") + length_field(15, b"unrelated")
        info = length_field(3, line) + length_field(15, b"ignored")
        response = b"\x08\x05" + length_field(3, info) + length_field(15, b"ignored")
        task_id, result = decode_response(response)
        self.assertEqual(task_id, 5)
        self.assertEqual(result["ocr_response"][0]["text"], "hello")


class CapturedProtocolTests(unittest.TestCase):
    """Regression against messages captured from the actual Linux image.

    The fixture preserves the original capture, including protobuf decoding
    performed independently with the upstream generated message definitions.
    """

    capture_name = "linux-capture.jsonl"

    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / "fixtures" / cls.capture_name
        cls.messages = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if "request_id" in record:
                cls.messages[record["request_id"]] = record

    def test_request_is_byte_identical_to_capture(self):
        message = self.messages[10010]
        decoded = message["protobuf"]
        self.assertEqual(
            encode_request(int(decoded["task_id"]), decoded["pic_path"]),
            bytes.fromhex(message["hex"]),
        )

    def test_support_capture(self):
        message = self.messages[10001]
        self.assertEqual(decode_support(bytes.fromhex(message["hex"])), message["protobuf"]["supported"])

    def test_response_matches_independent_protobuf_decode(self):
        message = self.messages[10011]
        payload = bytes.fromhex(message["hex"])
        self.assertEqual(len(payload), message["size"])
        task_id, result = decode_response(payload)
        expected = message["protobuf"]["res"]
        self.assertEqual(task_id, int(message["protobuf"]["task_id"]))
        self.assertEqual(result["errcode"], 0)  # Omitted proto3 error is zero.
        self.assertEqual(result["width"], expected["img_width"])
        self.assertEqual(result["height"], expected["img_height"])
        self.assertEqual(len(result["ocr_response"]), len(expected["lines"]))
        for actual_line, expected_line in zip(result["ocr_response"], expected["lines"]):
            self.assertEqual(actual_line["text"], expected_line["text"])
            for key in ("left", "top", "right", "bottom", "rate"):
                # protobuf JSON prints float32 values with fewer digits than
                # Python's exact float64 representation of the float32 value.
                self.assertAlmostEqual(actual_line[key], expected_line[key], places=4)


class MacOSCapturedProtocolTests(CapturedProtocolTests):
    """Same contract checks against the native WeChat 4.2.0 arm64 engine.

    This response contains new top-level fields 7 and 8. They are skipped
    rather than assigning them semantics inferred from their numeric values.
    """

    capture_name = "macos-capture.jsonl"


class MalformedPayloadTests(unittest.TestCase):
    def test_truncated_and_invalid_wire_encodings(self):
        payloads = (
            b"\x08",  # missing varint
            b"\x80",  # truncated tag
            b"\x00",  # invalid field 0
            b"\x0e",  # invalid wire type 6
            b"\x08" + b"\x80" * 10,  # uint64 overflow
            b"\x08" + b"\xff" * 9 + b"\x02",  # uint64 overflow
            b"\x11abc",  # truncated unknown fixed64
            b"\x15abc",  # truncated unknown fixed32
            b"\x12\x04abc",  # truncated unknown length-delimited
            b"\x13\x08\x01",  # unterminated group
            b"\x13\x1c",  # mismatched end group
            b"\x14",  # top-level end group
            b"\x13" * 65 + b"\x14" * 65,  # excessive group nesting
        )
        for payload in payloads:
            with self.subTest(payload=payload.hex()), self.assertRaises(ProtocolError):
                decode_support(payload)

    def test_wrong_known_wire_type(self):
        with self.assertRaises(ProtocolError):
            decode_support(b"\x0a\x00")
        with self.assertRaises(ProtocolError):
            decode_response(b"\x0a\x00")
        with self.assertRaises(ProtocolError):
            decode_response(length_field(3, b"\x25\x00\x00\x00\x00"))
        with self.assertRaises(ProtocolError):
            decode_response(length_field(3, length_field(3, b"\x18\x01")))

    def test_nested_truncation(self):
        line = b"\x1d\x00\x00\x00"  # rate missing its fourth float byte
        with self.assertRaises(ProtocolError):
            decode_response(length_field(3, length_field(3, line)))

    def test_malformed_declared_fields_are_validated_even_when_not_returned(self):
        malformed_lines = (
            length_field(1, length_field(1, b"\x0d\x00")),  # truncated Point.x
            length_field(4, length_field(2, b"\xff")),  # invalid character UTF-8
            length_field(10, b"\x08\x00"),  # Box.topleft must be a message
        )
        for line in malformed_lines:
            with self.subTest(line=line.hex()), self.assertRaises(ProtocolError):
                decode_response(length_field(3, length_field(3, line)))
        with self.assertRaises(ProtocolError):
            decode_response(length_field(4, b"\x0a\x00"))  # ReqType.t1 must be a bool

    def test_invalid_utf8(self):
        line = length_field(2, b"\xff")
        with self.assertRaises(ProtocolError):
            decode_response(length_field(3, length_field(3, line)))

    def test_dimension_overflow(self):
        with self.assertRaises(ProtocolError):
            decode_response(length_field(3, bytes.fromhex("208080808010")))


if __name__ == "__main__":
    unittest.main()
