"""The observed OCR runtime protobuf messages, without a protobuf dependency.

Field numbers and request flags follow swigger/wechat-ocr's
pb/ocr_wx4.proto and pb/ocr_common.proto at commit
e32d4af10d8045f8613078bac2df442662c76b03.  Unused fields are skipped;
their names or meanings are not inferred from their contents.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator


class ProtocolError(ValueError):
    """A protobuf payload is malformed or violates the OCR field contract."""


class _Reader:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.position = 0

    def take(self, count: int) -> bytes:
        end = self.position + count
        if end > len(self.payload):
            raise ProtocolError("Truncated protobuf field")
        value = self.payload[self.position:end]
        self.position = end
        return value

    def varint(self) -> int:
        value = 0
        for index in range(10):
            byte = self.take(1)[0]
            if index == 9 and byte > 1:
                raise ProtocolError("Protobuf varint exceeds uint64")
            value |= (byte & 0x7F) << (index * 7)
            if byte < 0x80:
                return value
        raise ProtocolError("Unterminated protobuf varint")

    def tag(self) -> tuple[int, int]:
        tag = self.varint()
        number, wire = tag >> 3, tag & 7
        if not 1 <= number <= 0x1FFFFFFF:
            raise ProtocolError("Invalid protobuf field number")
        if wire > 5:
            raise ProtocolError("Invalid protobuf wire type")
        return number, wire

    def skip_group(self, number: int, depth: int = 0) -> None:
        # Groups are obsolete, but an unknown field may still use this valid
        # protobuf encoding. Bound recursion independently of payload size.
        if depth >= 64:
            raise ProtocolError("Protobuf groups nested too deeply")
        while self.position < len(self.payload):
            child, wire = self.tag()
            if wire == 4:
                if child != number:
                    raise ProtocolError("Mismatched protobuf end group")
                return
            if wire == 3:
                self.skip_group(child, depth + 1)
            else:
                self.value(wire)
        raise ProtocolError("Unterminated protobuf group")

    def value(self, wire: int) -> int | bytes:
        if wire == 0:
            return self.varint()
        if wire == 1:
            return self.take(8)
        if wire == 2:
            return self.take(self.varint())
        if wire == 5:
            return self.take(4)
        raise ProtocolError("Unexpected protobuf group marker")


def _fields(
    payload: bytes, expected: dict[int, int]
) -> Iterator[tuple[int, int | bytes]]:
    reader = _Reader(payload)
    while reader.position < len(payload):
        number, wire = reader.tag()
        if number in expected and wire != expected[number]:
            raise ProtocolError(f"Wrong wire type for protobuf field {number}")
        if wire == 3:
            reader.skip_group(number)
            continue
        if wire == 4:
            raise ProtocolError("Unexpected protobuf end group")
        value = reader.value(wire)
        if number in expected:
            yield number, value


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _uint32(value: int, field: str) -> int:
    if value > 0xFFFFFFFF:
        raise ProtocolError(f"{field} exceeds uint32")
    return value


def _text(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProtocolError("Invalid UTF-8 in OCR text") from error


def encode_request(task_id: int, image_path: str) -> bytes:
    """Encode ParseOCRReqMessage for request id 10010.

    The underlying engine accepts positive 31-bit task IDs; callers should
    allocate from 2 because upstream reserves 1 for initialization.
    """
    if type(task_id) is not int or not 1 <= task_id <= 0x7FFFFFFF:
        raise ValueError("task_id must be an integer in [1, 2147483647]")
    if not isinstance(image_path, str) or not image_path or "\0" in image_path:
        raise ValueError("image_path must be a nonempty, NUL-free string")
    try:
        path = image_path.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("image_path must be valid UTF-8") from error
    # ReqType has optional fields, so explicitly transmit t3=false as upstream
    # does. No pic_data or undocumented request fields are populated.
    request_type = b"\x08\x01\x10\x01\x18\x00"
    return (
        b"\x08" + _varint(task_id)
        + b"\x12" + _varint(len(path)) + path
        + b"\x32" + _varint(len(request_type)) + request_type
    )


def decode_support(payload: bytes) -> bool:
    """Decode OCRSupportMessage (request id 10001)."""
    supported = False
    for _, value in _fields(payload, {1: 0}):
        supported = bool(value)
    return supported


def _validate_box(payload: bytes) -> None:
    # Coordinates are not emitted by the original Python wrapper, but their
    # declared nested messages must still be well-formed protobuf.
    for _, point in _fields(payload, {1: 2, 2: 2, 3: 2, 4: 2}):
        for _ in _fields(point, {1: 5, 2: 5}):
            pass


def _validate_character(payload: bytes) -> None:
    for number, value in _fields(payload, {1: 2, 2: 2}):
        if number == 1:
            _validate_box(value)
        elif number == 2:
            _text(value)


def _decode_line(payload: bytes) -> dict:
    line = {
        "text": "", "left": 0.0, "top": 0.0,
        "right": 0.0, "bottom": 0.0, "rate": 0.0,
    }
    float_fields = {3: "rate", 5: "left", 6: "top", 7: "right", 8: "bottom"}
    expected = {1: 2, 2: 2, 3: 5, 4: 2, 5: 5, 6: 5, 7: 5, 8: 5, 9: 0, 10: 2}
    for number, value in _fields(payload, expected):
        if number in (1, 10):
            _validate_box(value)
        elif number == 2:
            line["text"] = _text(value)
        elif number == 4:
            _validate_character(value)
        elif number in float_fields:
            line[float_fields[number]] = struct.unpack("<f", value)[0]
    return line


def _merge_result(payload: bytes, result: dict) -> None:
    for number, value in _fields(payload, {3: 2, 4: 0, 5: 0, 6: 2, 7: 0}):
        if number == 3:
            result["ocr_response"].append(_decode_line(value))
        elif number == 4:
            result["width"] = _uint32(value, "img_width")
        elif number == 5:
            result["height"] = _uint32(value, "img_height")
        elif number == 6:
            _text(value)  # Validate the declared string without exposing it.


def decode_response(payload: bytes) -> tuple[int, dict]:
    """Decode ParseOCRRespMessage (request id 10011) to the wcocr result shape.

    imgpath is supplied by the caller, which owns task ID/path correlation.
    Missing scalar fields have protobuf defaults. If neither res nor err_code
    is present, use the upstream wrapper's explicit failure code of -1.
    """
    task_id = 0
    has_error = False
    has_result = False
    result = {"errcode": 0, "width": 0, "height": 0, "ocr_response": []}
    for number, value in _fields(payload, {1: 0, 2: 0, 3: 2, 4: 2, 5: 2, 6: 2}):
        if number == 1:
            task_id = value
        elif number == 2:
            has_error = True
            signed = value & 0xFFFFFFFF
            result["errcode"] = signed - 0x100000000 if signed & 0x80000000 else signed
        elif number == 3:
            has_result = True
            # Singular embedded protobuf messages merge if sent more than once.
            _merge_result(value, result)
        elif number == 4:
            for _ in _fields(value, {1: 0, 2: 0, 3: 0}):
                pass
    if not has_result and not has_error:
        result["errcode"] = -1
    return task_id, result
