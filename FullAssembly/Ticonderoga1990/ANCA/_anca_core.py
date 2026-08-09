#!/usr/bin/env python3
"""Bounded reader for the World of Warships: Legends ANCA container.

This decoder intentionally implements only structures verified against local
Legends files:

* ANCA v6 packed-entry metadata
* animation header and channel table
* inline interpolated/morph channel payloads
* streamed-channel fallback transforms

The trailing streamed keyframe payload is preserved and fingerprinted, but is
not claimed as decoded. Standalone ``.anim`` files are not supported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "wows-legends-anca-channels/v1"
ENTRY_DATA_MASK = 0x7FFFFFFF
MAX_COLLECTION_ITEMS = 10_000_000


class DecodeError(ValueError):
    """Raised when a file violates a checked ANCA structure."""


@dataclass
class Reader:
    data: bytes
    offset: int = 0
    label: str = "data"

    def remaining(self) -> int:
        return len(self.data) - self.offset

    def _take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise DecodeError(
                f"{self.label}: need {size} bytes at 0x{self.offset:x}, "
                f"only {self.remaining()} remain"
            )
        start = self.offset
        self.offset += size
        return self.data[start : start + size]

    def unpack(self, fmt: str) -> tuple[Any, ...]:
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self._take(size))

    def u32(self) -> int:
        return self.unpack("<I")[0]

    def i32(self) -> int:
        return self.unpack("<i")[0]

    def u64(self) -> int:
        return self.unpack("<Q")[0]

    def f32(self) -> float:
        return self.unpack("<f")[0]

    def ascii(self, size: int) -> str:
        raw = self._take(size)
        try:
            return raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise DecodeError(
                f"{self.label}: non-ASCII string at 0x{self.offset - size:x}"
            ) from exc

    def counted_ascii(self) -> str:
        return self.ascii(self.u32())

    def rows_f32(self, count: int, width: int, field: str) -> list[list[float]]:
        _check_count(count, field)
        if width <= 0:
            raise DecodeError(f"{field}: invalid row width {width}")
        if count == 0:
            return []
        flat = self.unpack("<" + "f" * (count * width))
        return [
            [float(v) for v in flat[row * width : (row + 1) * width]]
            for row in range(count)
        ]

    def array_u32(self, count: int, field: str) -> list[int]:
        _check_count(count, field)
        if count == 0:
            return []
        return [int(v) for v in self.unpack("<" + "I" * count)]


def _check_count(count: int, field: str) -> None:
    if count < 0 or count > MAX_COLLECTION_ITEMS:
        raise DecodeError(f"{field}: unreasonable item count {count}")


def _finite_list(values: Iterable[float], field: str) -> list[float]:
    result = [float(value) for value in values]
    if not all(math.isfinite(value) for value in result):
        raise DecodeError(f"{field}: non-finite float encountered")
    return result


def _key_rows(rows: list[list[float]], width: int, field: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if len(row) != width + 1:
            raise DecodeError(f"{field}[{index}]: expected {width + 1} floats")
        values = _finite_list(row, f"{field}[{index}]")
        result.append({"time_raw": values[0], "value": values[1:]})
    return result


def parse_container(data: bytes) -> dict[str, Any]:
    if len(data) < 4:
        raise DecodeError("ANCA container is shorter than its trailer pointer")

    metadata_size = struct.unpack_from("<I", data, len(data) - 4)[0]
    metadata_end = len(data) - 4
    metadata_start = metadata_end - metadata_size
    if metadata_start < 0 or metadata_start > metadata_end:
        raise DecodeError(
            f"invalid metadata size {metadata_size} for {len(data)}-byte file"
        )

    metadata = Reader(
        data[metadata_start:metadata_end],
        label=f"ANCA metadata @0x{metadata_start:x}",
    )
    entries: list[dict[str, Any]] = []
    data_position = 0

    while metadata.remaining():
        if metadata.remaining() < 24:
            raise DecodeError(
                f"ANCA metadata has a {metadata.remaining()}-byte partial entry"
            )
        data_length_raw = metadata.u32()
        data_length = data_length_raw & ENTRY_DATA_MASK
        preload_length = metadata.u32()
        version = metadata.u32()
        modified_raw = metadata.u64()
        name_length = metadata.u32()
        name = metadata.ascii(name_length)
        padding = (-name_length) & 3
        if padding:
            pad_bytes = metadata._take(padding)
            if any(pad_bytes):
                raise DecodeError(f"entry {name!r}: nonzero name padding")

        if version != 6:
            raise DecodeError(f"entry {name!r}: unsupported ANCA version {version}")
        if data_position + data_length > metadata_start:
            raise DecodeError(
                f"entry {name!r}: data range exceeds metadata boundary"
            )

        entries.append(
            {
                "name": name,
                "data_offset": data_position,
                "data_length": data_length,
                "data_length_high_bit": bool(data_length_raw & 0x80000000),
                "preload_length": preload_length,
                "version": version,
                "modified_raw": modified_raw,
            }
        )
        data_position += (data_length + 3) & ~3

    if data_position > metadata_start:
        raise DecodeError("aligned entry data overlaps ANCA metadata")

    return {
        "file_length": len(data),
        "metadata_offset": metadata_start,
        "metadata_length": metadata_size,
        "unclaimed_gap_length": metadata_start - data_position,
        "entries": entries,
    }


def _parse_interpolated(reader: Reader, type_id: int) -> dict[str, Any]:
    channel: dict[str, Any] = {}
    if type_id == 4:
        channel["compression_error"] = {
            "scale": reader.f32(),
            "position": reader.f32(),
            "rotation": reader.f32(),
        }

    scale_rows = reader.rows_f32(reader.u32(), 4, "scale_keys")
    position_rows = reader.rows_f32(reader.u32(), 4, "position_keys")
    rotation_rows = reader.rows_f32(reader.u32(), 5, "rotation_keys")

    channel["keys"] = {
        "scale_xyz": _key_rows(scale_rows, 3, "scale_keys"),
        "position_xyz": _key_rows(position_rows, 3, "position_keys"),
        "rotation_xyzw_raw": _key_rows(rotation_rows, 4, "rotation_keys"),
    }
    channel["indices"] = {
        "scale": reader.array_u32(reader.u32(), "scale_indices"),
        "position": reader.array_u32(reader.u32(), "position_indices"),
        "rotation": reader.array_u32(reader.u32(), "rotation_indices"),
    }
    channel["animation_support"] = "inline_keys_decoded"
    return channel


def _parse_morph(reader: Reader) -> dict[str, Any]:
    count = reader.u32()
    rows = reader.rows_f32(count, 1, "morph_influences")
    return {
        "influences": [row[0] for row in rows],
        "animation_support": "morph_values_decoded",
    }


def _parse_streamed(reader: Reader) -> dict[str, Any]:
    scale = _finite_list(reader.unpack("<3f"), "streamed scale fallback")
    position = _finite_list(reader.unpack("<3f"), "streamed position fallback")
    rotation = _finite_list(reader.unpack("<4f"), "streamed rotation fallback")
    return {
        "fallback": {
            "scale_xyz": scale,
            "position_xyz": position,
            "rotation_xyzw_raw": rotation,
        },
        "animation_support": "fallback_only_stream_payload_not_decoded",
    }


CHANNEL_TYPES = {
    1: ("interpolated", _parse_interpolated),
    2: ("morph", lambda reader, _type_id: _parse_morph(reader)),
    3: ("interpolated_compressed_variant", _parse_interpolated),
    4: ("interpolated_with_error", _parse_interpolated),
    5: ("streamed", lambda reader, _type_id: _parse_streamed(reader)),
}


def parse_animation(entry_data: bytes, preload_length: int) -> dict[str, Any]:
    reader = Reader(entry_data, label="ANCA animation entry")
    duration = reader.f32()
    if not math.isfinite(duration) or duration < 0:
        raise DecodeError(f"invalid animation duration {duration!r}")

    identifier = reader.counted_ascii()
    internal_identifier = reader.counted_ascii()
    channel_count = reader.i32()
    _check_count(channel_count, "animation channels")

    channels: list[dict[str, Any]] = []
    for channel_index in range(channel_count):
        type_id = reader.i32()
        descriptor = CHANNEL_TYPES.get(type_id)
        if descriptor is None:
            raise DecodeError(
                f"channel {channel_index}: unsupported channel type {type_id}"
            )
        type_name, parser = descriptor
        node_name = reader.counted_ascii()
        parsed = parser(reader, type_id)
        parsed.update(
            {
                "index": channel_index,
                "type_id": type_id,
                "type_name": type_name,
                "target_name": node_name,
                "blender_binding": {
                    "match_by": "object_or_pose_bone_name",
                    "target_name": node_name,
                    "source_space": "BigWorld_raw_unconverted",
                    "rotation_source_order": "xyzw_assumed_from_engine_type",
                    "rotation_blender_order": "wxyz",
                },
            }
        )
        channels.append(parsed)

    stream_offset = reader.offset
    stream_payload = entry_data[stream_offset:]
    preload_match = len(stream_payload) == preload_length

    pivots = []
    for channel in channels:
        fallback = channel.get("fallback")
        if fallback is not None:
            pivots.append(
                {
                    "target_name": channel["target_name"],
                    "channel_index": channel["index"],
                    "transform_raw": fallback,
                    "safe_application": (
                        "name binding only; coordinate-space conversion and "
                        "hierarchy must be supplied by the model importer"
                    ),
                }
            )

    return {
        "duration_raw": duration,
        "duration_unit": "engine_time_unit_not_assumed_to_be_seconds",
        "identifier": identifier,
        "internal_identifier": internal_identifier,
        "channel_count": channel_count,
        "channel_table_end_offset": stream_offset,
        "trailing_stream_payload": {
            "offset": stream_offset,
            "length": len(stream_payload),
            "preload_length_from_container": preload_length,
            "length_matches_preload": preload_match,
            "sha256": hashlib.sha256(stream_payload).hexdigest(),
            "decode_status": "preserved_not_decoded",
        },
        "channels": channels,
        "blender_pivot_channels": pivots,
    }


def decode(data: bytes, section_name: str | None = None) -> dict[str, Any]:
    container = parse_container(data)
    entries = container["entries"]
    if not entries:
        raise DecodeError("ANCA container has no entries")

    if section_name is None:
        if len(entries) != 1:
            names = ", ".join(repr(entry["name"]) for entry in entries)
            raise DecodeError(f"choose a section explicitly; available: {names}")
        selected = entries[0]
    else:
        matches = [entry for entry in entries if entry["name"] == section_name]
        if not matches:
            names = ", ".join(repr(entry["name"]) for entry in entries)
            raise DecodeError(
                f"section {section_name!r} not found; available: {names}"
            )
        selected = matches[0]

    start = selected["data_offset"]
    end = start + selected["data_length"]
    animation = parse_animation(data[start:end], selected["preload_length"])

    return {
        "schema": SCHEMA,
        "support": {
            "container_v6": "verified",
            "channel_header_and_fallback_pivots": "verified",
            "inline_interpolated_keys": "decoded_raw",
            "streamed_channel_keyframes": "not_decoded",
            "standalone_anim_files": "unsupported",
            "complete_blender_animation": False,
        },
        "container": container,
        "selected_section": selected["name"],
        "animation": animation,
    }


def _list_sections(path: Path, data: bytes) -> int:
    container = parse_container(data)
    for entry in container["entries"]:
        print(
            f"{entry['name']}\t"
            f"length={entry['data_length']}\t"
            f"preload={entry['preload_length']}\t"
            f"version={entry['version']}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Decode verified World of Warships: Legends .anca headers and "
            "emit Blender-binding channel/pivot JSON."
        )
    )
    parser.add_argument("input", type=Path, help="Legends .anca file")
    parser.add_argument("--section", help="exact packed animation section name")
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON output path; omit to write JSON to stdout",
    )
    parser.add_argument(
        "--list-sections",
        action="store_true",
        help="list packed section names without decoding an animation",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="write compact JSON instead of an indented document",
    )
    args = parser.parse_args(argv)

    if args.input.suffix.lower() == ".anim":
        parser.error(
            "standalone .anim is intentionally unsupported; this tool only "
            "claims verified .anca structures"
        )
    try:
        data = args.input.read_bytes()
        if args.list_sections:
            return _list_sections(args.input, data)
        document = decode(data, args.section)
    except (OSError, DecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    document["source"] = {
        "name": args.input.name,
        "length": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    text = json.dumps(
        document,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        separators=(",", ":") if args.compact else None,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
