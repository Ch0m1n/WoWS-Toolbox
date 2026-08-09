#!/usr/bin/env python3
"""Validate the decoder against user-owned Legends ANCA samples."""

from __future__ import annotations

import argparse
from pathlib import Path

from decode_anca import DecodeError, decode


def validate(path: Path) -> list[str]:
    document = decode(path.read_bytes())
    animation = document["animation"]
    stream = animation["trailing_stream_payload"]
    errors: list[str] = []

    if animation["channel_count"] != len(animation["channels"]):
        errors.append("channel count mismatch")
    if not stream["payload_starts_at_preload_boundary"]:
        errors.append("stream payload does not start at preload boundary")

    selected_length = next(
        entry["data_length"]
        for entry in document["container"]["entries"]
        if entry["name"] == document["selected_section"]
    )
    if stream["offset"] + stream["length"] != selected_length:
        errors.append("stream payload does not end at entry boundary")
    if stream["declared_length"] != stream["length"]:
        errors.append("stream length prefix mismatch")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)

    failed = False
    for path in args.files:
        try:
            errors = validate(path)
        except (OSError, DecodeError) as exc:
            errors = [str(exc)]
        if errors:
            failed = True
            print(f"FAIL\t{path}\t" + "; ".join(errors))
        else:
            print(f"PASS\t{path}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
