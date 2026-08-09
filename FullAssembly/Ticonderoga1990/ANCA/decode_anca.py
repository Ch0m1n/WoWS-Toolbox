#!/usr/bin/env python3
"""Verified Legends ANCA entry point.

The shared bounded readers live in ``_anca_core.py``. This entry point corrects
the meaning of the ANCA preload field observed in real Legends samples: it is
the byte boundary where the streamed payload begins, not the payload length.
"""

from __future__ import annotations

import hashlib
import sys
from typing import Any

import _anca_core as core


def parse_animation(entry_data: bytes, preload_length: int) -> dict[str, Any]:
    reader = core.Reader(entry_data, label="ANCA animation entry")
    duration = reader.f32()
    if not core.math.isfinite(duration) or duration < 0:
        raise core.DecodeError(f"invalid animation duration {duration!r}")

    identifier = reader.counted_ascii()
    internal_identifier = reader.counted_ascii()
    channel_count = reader.i32()
    core._check_count(channel_count, "animation channels")

    channels: list[dict[str, Any]] = []
    for channel_index in range(channel_count):
        type_id = reader.i32()
        descriptor = core.CHANNEL_TYPES.get(type_id)
        if descriptor is None:
            raise core.DecodeError(
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

    channel_table_end = reader.offset
    stream_reader = core.Reader(
        entry_data[channel_table_end:],
        label="ANCA streamed block",
    )
    if stream_reader.remaining() == 0:
        declared_stream_length = 0
        stream_payload = b""
        stream_payload_offset = channel_table_end
    else:
        declared_stream_length = stream_reader.u32()
        if declared_stream_length != stream_reader.remaining():
            raise core.DecodeError(
                "ANCA streamed block length prefix says "
                f"{declared_stream_length}, but {stream_reader.remaining()} "
                "bytes follow"
            )
        stream_payload_offset = channel_table_end + 4
        stream_payload = stream_reader._take(declared_stream_length)

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
        "channel_table_end_offset": channel_table_end,
        "trailing_stream_payload": {
            "length_prefix_offset": channel_table_end,
            "declared_length": declared_stream_length,
            "offset": stream_payload_offset,
            "length": len(stream_payload),
            "preload_boundary_from_container": preload_length,
            "payload_starts_at_preload_boundary": (
                preload_length == stream_payload_offset
            ),
            "sha256": hashlib.sha256(stream_payload).hexdigest(),
            "decode_status": "preserved_not_decoded",
        },
        "channels": channels,
        "blender_pivot_channels": pivots,
    }


# core.decode/core.main resolve this global at call time.
core.parse_animation = parse_animation

DecodeError = core.DecodeError
decode = core.decode
parse_container = core.parse_container
SCHEMA = core.SCHEMA


def main(argv: list[str] | None = None) -> int:
    return core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
