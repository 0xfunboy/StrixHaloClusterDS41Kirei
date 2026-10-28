#!/usr/bin/env python3
"""Minimal, bounded GGUF metadata reader used for tokenizer/template attestation.

It reads only the key/value header and never parses tensor descriptors or data.
The implementation intentionally supports the scalar and array types defined by
GGUF v2/v3, with explicit size limits suitable for tokenizer metadata.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
from typing import BinaryIO, Any

MAGIC = b"GGUF"
MAX_STRING_BYTES = 64 * 1024 * 1024
MAX_ARRAY_ITEMS = 2_000_000
MAX_KV = 100_000

UINT8 = 0
INT8 = 1
UINT16 = 2
INT16 = 3
UINT32 = 4
INT32 = 5
FLOAT32 = 6
BOOL = 7
STRING = 8
ARRAY = 9
UINT64 = 10
INT64 = 11
FLOAT64 = 12

SCALARS: dict[int, tuple[str, int]] = {
    UINT8: ("<B", 1),
    INT8: ("<b", 1),
    UINT16: ("<H", 2),
    INT16: ("<h", 2),
    UINT32: ("<I", 4),
    INT32: ("<i", 4),
    FLOAT32: ("<f", 4),
    BOOL: ("<B", 1),
    UINT64: ("<Q", 8),
    INT64: ("<q", 8),
    FLOAT64: ("<d", 8),
}


class GGUFError(ValueError):
    pass


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise GGUFError("truncated GGUF metadata")
    return data


def _u32(stream: BinaryIO) -> int:
    return struct.unpack("<I", _read_exact(stream, 4))[0]


def _u64(stream: BinaryIO) -> int:
    return struct.unpack("<Q", _read_exact(stream, 8))[0]


def _string(stream: BinaryIO) -> str:
    size = _u64(stream)
    if size > MAX_STRING_BYTES:
        raise GGUFError(f"GGUF string exceeds {MAX_STRING_BYTES} bytes")
    try:
        return _read_exact(stream, size).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GGUFError("GGUF metadata string is not UTF-8") from exc


def _scalar(stream: BinaryIO, value_type: int) -> Any:
    if value_type == STRING:
        return _string(stream)
    spec = SCALARS.get(value_type)
    if spec is None:
        raise GGUFError(f"unsupported GGUF value type {value_type}")
    fmt, size = spec
    value = struct.unpack(fmt, _read_exact(stream, size))[0]
    return bool(value) if value_type == BOOL else value


def _array(stream: BinaryIO) -> list[Any]:
    element_type = _u32(stream)
    count = _u64(stream)
    if count > MAX_ARRAY_ITEMS:
        raise GGUFError(f"GGUF array exceeds {MAX_ARRAY_ITEMS} items")
    if element_type == ARRAY:
        raise GGUFError("nested GGUF arrays are not valid")
    return [_scalar(stream, element_type) for _ in range(count)]


def read_metadata(path: Path) -> dict[str, Any]:
    with path.open("rb", buffering=8 * 1024 * 1024) as stream:
        if _read_exact(stream, 4) != MAGIC:
            raise GGUFError("not a GGUF file")
        version = _u32(stream)
        if version not in (2, 3):
            raise GGUFError(f"unsupported GGUF version {version}")
        tensor_count = _u64(stream)
        kv_count = _u64(stream)
        if kv_count > MAX_KV:
            raise GGUFError(f"GGUF metadata count exceeds {MAX_KV}")
        values: dict[str, Any] = {
            "_gguf.version": version,
            "_gguf.tensor_count": tensor_count,
            "_gguf.kv_count": kv_count,
        }
        for _ in range(kv_count):
            key = _string(stream)
            if key in values:
                raise GGUFError(f"duplicate GGUF metadata key {key!r}")
            value_type = _u32(stream)
            values[key] = _array(stream) if value_type == ARRAY else _scalar(stream, value_type)
        return values


def selected_metadata(path: Path, prefixes: tuple[str, ...]) -> dict[str, Any]:
    values = read_metadata(path)
    return {
        key: value
        for key, value in values.items()
        if any(key.startswith(prefix) for prefix in prefixes)
    }


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def per_key_fingerprints(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key in sorted(value):
        payload = canonical_bytes(value[key])
        row: dict[str, Any] = {
            "canonical_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        if not isinstance(value[key], (list, dict)):
            row["value"] = value[key]
        result[key] = row
    return result


def fingerprint(
    path: Path,
    prefixes: tuple[str, ...] = ("tokenizer.",),
    *,
    include_per_key: bool = False,
) -> dict[str, Any]:
    selected = selected_metadata(path, prefixes)
    payload = canonical_bytes(selected)
    result: dict[str, Any] = {
        "schema": "haloclu-gguf-metadata-fingerprint-v1",
        "path": str(path),
        "prefixes": list(prefixes),
        "keys": sorted(selected),
        "key_count": len(selected),
        "canonical_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if include_per_key:
        result["per_key"] = per_key_fingerprints(selected)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--prefix", action="append", default=[])
    parser.add_argument("--values", action="store_true")
    parser.add_argument("--per-key", action="store_true")
    args = parser.parse_args()
    prefixes = tuple(args.prefix or ["tokenizer."])
    result = fingerprint(args.file, prefixes, include_per_key=args.per_key)
    if args.values:
        result["values"] = selected_metadata(args.file, prefixes)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
