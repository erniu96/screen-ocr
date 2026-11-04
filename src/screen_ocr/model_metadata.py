"""Read the verified XNET v26 prefix without loading the native engine.

This deliberately stops before operator parameters and weights. String field
layout is confirmed against the pinned native loader; see docs/model-format.md.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct


MAX_REPORTED_STRING_BYTES = 4096
MAX_CHARSET_BYTES = 16 * 1024 * 1024


def inspect_xnet(path: Path) -> dict:
    """Report a bounded prefix; unsupported versions are never guessed."""
    with path.open("rb") as source:
        length = source.seek(0, 2)
        source.seek(0)
        header = source.read(8)
        result = {"header_hex": header.hex()}
        if len(header) != 8 or header[:4] != b"XNET":
            return {**result, "status": "unrecognized_header"}
        version = struct.unpack("<I", header[4:])[0]
        result.update(format="XNET", format_version=version)
        if version != 26:
            return {**result, "status": "unsupported_version"}

        def take(count):
            if count > length - source.tell():
                raise ValueError("Declared prefix length exceeds file size")
            value = source.read(count)
            if len(value) != count:
                raise ValueError("Truncated prefix")
            return value

        def uint32():
            return struct.unpack("<I", take(4))[0]

        def string_field():
            size = uint32()
            if size > length - source.tell():
                raise ValueError("Declared string length exceeds file size")
            if size > MAX_REPORTED_STRING_BYTES:
                source.seek(size, 1)
                return size, None
            return size, take(size)

        try:
            metadata_length, metadata = string_field()
            result["metadata_field_bytes"] = metadata_length
            # The native metaData field is an opaque byte string. No guessed
            # JSON schema, text encoding, author or training date is attached.
            result["metadata_field_hex"] = None if metadata is None else metadata.hex()
            result["operator_count"] = uint32()
            result["first_operator_name"] = None
            result["first_operator_name_bytes"] = None
            if result["operator_count"]:
                size, name = string_field()
                result["first_operator_name_bytes"] = size
                # The verified model names are ASCII, hence valid UTF-8. Do
                # not replace invalid bytes and silently change an identifier.
                result["first_operator_name"] = None if name is None else name.decode("utf-8")
            result["status"] = "prefix_parsed"
        except (ValueError, UnicodeError) as error:
            result.update(status="invalid_prefix", error=str(error))
        return result


def inspect_charset(path: Path) -> dict:
    """Inspect the known UTF-8 character table, preserving a space entry."""
    with path.open("rb") as source:
        data = source.read(MAX_CHARSET_BYTES + 1)
    if len(data) > MAX_CHARSET_BYTES:
        raise ValueError("Charset exceeds this inspector's 16 MiB limit")
    text = data.decode("utf-8")
    entries = text.splitlines()
    return {
        "encoding": "UTF-8", "has_utf8_bom": data.startswith(b"\xef\xbb\xbf"),
        "bytes": len(data), "entry_count": len(entries),
        "unique_entry_count": len(set(entries)),
        "all_entries_single_codepoint": all(len(entry) == 1 for entry in entries),
        "last_entry": entries[-1] if entries else None,
    }


def _sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resources", type=Path, help="Directory containing .xnet files and the character table")
    args = parser.parse_args(argv)
    directory = args.resources.expanduser().resolve()
    if not directory.is_dir():
        parser.error("resources must be a directory")
    paths = sorted(directory.glob("*.xnet"))
    if not paths:
        parser.error("No .xnet files found")
    models = []
    failed = False
    for path in paths:
        try:
            metadata = inspect_xnet(path)
            models.append({"file": path.name, "bytes": path.stat().st_size,
                           "sha256": _sha256(path), "metadata": metadata})
            failed |= metadata["status"] != "prefix_parsed"
        except (OSError, ValueError) as error:
            failed = True
            models.append({"file": path.name, "error": str(error)})
    charset_path = directory / "charset_zh13562.txt"
    charset = None
    if charset_path.exists():
        try:
            charset = {"file": charset_path.name, "sha256": _sha256(charset_path),
                       **inspect_charset(charset_path)}
        except (OSError, ValueError) as error:
            failed = True
            charset = {"file": charset_path.name, "error": str(error)}
    print(json.dumps({"models": models, "charset": charset,
                      "scope": "XNET v26 prefix only; not a graph or weights decoder"},
                     ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
