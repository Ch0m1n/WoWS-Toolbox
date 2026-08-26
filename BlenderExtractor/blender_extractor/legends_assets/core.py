"""Core parser and safe extraction primitives for WoWS: Legends packages.

The Steam Legends client observed in August 2026 uses an ``ISFP`` IDX variant
with marker ``0x01010005``.  This module intentionally supports only that
verified layout.  It never writes to the game directory.
"""

from __future__ import annotations

import binascii
import fnmatch
import json
import os
import struct
import subprocess
import zlib
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Sequence


IDX_MAGIC = b"ISFP"
LEGENDS_IDX_MARKER = 0x01010005
LEGENDS_IDX_VERSION = 0x40
IDX_HEADER_SIZE = 64
RESOURCE_ENTRY_SIZE = 32
FILE_INFO_ENTRY_SIZE = 48
VOLUME_ENTRY_SIZE = 32
MAX_TABLE_ENTRIES = 5_000_000
MAX_NAME_BYTES = 1 << 20
DEFAULT_MAX_UNPACKED_SIZE = 2 * 1024 * 1024 * 1024


class IdxFormatError(ValueError):
    """The IDX file is malformed or is not the verified Legends variant."""


class UnsafePathError(ValueError):
    """An archive or output path would escape the allowed root."""


class ExtractionError(RuntimeError):
    """An asset could not be verified or extracted."""


class FormatSupport(str, Enum):
    BLENDER_DIRECT = "blender-direct"
    BLENDER_TEXTURE = "blender-texture"
    EXTERNAL_CONVERTER = "external-converter-required"
    DESCRIPTOR_ONLY = "descriptor-only"
    MATERIAL_DESCRIPTOR = "material-descriptor"
    PACKAGE_INDEX = "package-index"
    UNKNOWN = "unknown"


BLENDER_DIRECT_EXTENSIONS = {".obj", ".fbx", ".gltf", ".glb"}
BLENDER_TEXTURE_EXTENSIONS = {
    ".dds",
    ".dd0",
    ".dd1",
    ".png",
    ".tga",
    ".jpg",
    ".jpeg",
    ".bmp",
}
BIGWORLD_MESH_EXTENSIONS = {
    ".geometry",
    ".primitive",
    ".primitives",
    ".primitives_processed",
}
BIGWORLD_DESCRIPTOR_EXTENSIONS = {".model", ".visual"}
MATERIAL_EXTENSIONS = {".mfm"}
PACKAGE_EXTENSIONS = {".idx", ".pkg"}


@dataclass(frozen=True)
class Resource:
    resource_id: int
    parent_id: int
    name: str


@dataclass(frozen=True)
class FileInfo:
    offset: int
    reserved: int
    packed_size: int
    crc32: int
    unpacked_size: int
    compression_type_1: int
    compression_type_2: int
    resource_id: int
    volume_id: int


@dataclass(frozen=True)
class Volume:
    volume_id: int
    unknown_id: int
    filename: str


@dataclass
class LegendsIndex:
    path: Path
    marker: int
    index_hash: int
    version: int
    resources: list[Resource]
    file_infos: list[FileInfo]
    volumes: list[Volume]

    def resource_paths(self) -> dict[int, str]:
        by_id = {resource.resource_id: resource for resource in self.resources}
        cache: dict[int, str] = {}
        visiting: set[int] = set()

        def resolve(resource_id: int) -> str:
            if resource_id in cache:
                return cache[resource_id]
            if resource_id in visiting:
                raise IdxFormatError(
                    f"{self.path.name}: cycle in resource parent links at "
                    f"0x{resource_id:016x}"
                )
            resource = by_id.get(resource_id)
            if resource is None:
                raise IdxFormatError(
                    f"{self.path.name}: missing resource 0x{resource_id:016x}"
                )

            visiting.add(resource_id)
            if resource.parent_id in by_id:
                parent = resolve(resource.parent_id)
                raw_path = f"{parent}/{resource.name}" if parent else resource.name
            else:
                # Root parent IDs are sentinels and are deliberately not records.
                raw_path = resource.name
            visiting.remove(resource_id)

            path = normalize_virtual_path(raw_path)
            cache[resource_id] = path
            return path

        for resource_id in by_id:
            resolve(resource_id)
        return cache


@dataclass(frozen=True)
class AssetEntry:
    idx_path: Path
    package_path: Path
    virtual_path: str
    file_info: FileInfo

    @property
    def extension(self) -> str:
        return Path(self.virtual_path).suffix.casefold()

    @property
    def support(self) -> FormatSupport:
        return classify_asset(self.virtual_path)

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.idx_path.name,
            "package": self.package_path.name,
            "path": self.virtual_path,
            "extension": self.extension,
            "support": self.support.value,
            "packed_size": self.file_info.packed_size,
            "unpacked_size": self.file_info.unpacked_size,
            "crc32": f"{self.file_info.crc32:08x}",
            "compression": [
                self.file_info.compression_type_1,
                self.file_info.compression_type_2,
            ],
        }


@dataclass(frozen=True)
class ExtractionResult:
    entry: AssetEntry
    target: Path
    status: str
    bytes_written: int = 0

    def to_dict(self) -> dict[str, object]:
        data = self.entry.to_dict()
        data.update(
            {
                "target": str(self.target),
                "status": self.status,
                "bytes_written": self.bytes_written,
            }
        )
        return data


def classify_asset(path: str | Path) -> FormatSupport:
    extension = Path(str(path)).suffix.casefold()
    if extension in BLENDER_DIRECT_EXTENSIONS:
        return FormatSupport.BLENDER_DIRECT
    if extension in BLENDER_TEXTURE_EXTENSIONS:
        return FormatSupport.BLENDER_TEXTURE
    if extension in BIGWORLD_MESH_EXTENSIONS:
        return FormatSupport.EXTERNAL_CONVERTER
    if extension in BIGWORLD_DESCRIPTOR_EXTENSIONS:
        return FormatSupport.DESCRIPTOR_ONLY
    if extension in MATERIAL_EXTENSIONS:
        return FormatSupport.MATERIAL_DESCRIPTOR
    if extension in PACKAGE_EXTENSIONS:
        return FormatSupport.PACKAGE_INDEX
    return FormatSupport.UNKNOWN


def normalize_virtual_path(raw_path: str) -> str:
    raw_path = raw_path.replace("\\", "/")
    parts: list[str] = []
    for part in raw_path.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise UnsafePathError(f"parent traversal in archive path: {raw_path!r}")
        if "\x00" in part or ":" in part:
            raise UnsafePathError(f"unsafe archive path component: {part!r}")
        parts.append(part)
    if not parts:
        raise UnsafePathError(f"empty archive path: {raw_path!r}")
    return PurePosixPath(*parts).as_posix()


def _comparison_path(path: Path) -> Path:
    """Normalize equivalent Win32 and extended-length path spellings."""

    value = os.path.normcase(str(path))
    if os.name == "nt":
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
    return Path(value)


def ensure_within_root(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        _comparison_path(resolved_path).relative_to(
            _comparison_path(resolved_root)
        )
    except ValueError as exc:
        raise UnsafePathError(
            f"output path escapes allowed root: {resolved_path} (root: {resolved_root})"
        ) from exc
    return resolved_path


def safe_output_path(output_root: Path, virtual_path: str) -> Path:
    normalized = normalize_virtual_path(virtual_path)
    candidate = output_root.joinpath(*PurePosixPath(normalized).parts)
    return ensure_within_root(candidate, output_root)


def _u32(data: bytes, offset: int) -> int:
    try:
        return struct.unpack_from("<I", data, offset)[0]
    except struct.error as exc:
        raise IdxFormatError(f"truncated u32 at 0x{offset:x}") from exc


def _u64(data: bytes, offset: int) -> int:
    try:
        return struct.unpack_from("<Q", data, offset)[0]
    except struct.error as exc:
        raise IdxFormatError(f"truncated u64 at 0x{offset:x}") from exc


def _i64(data: bytes, offset: int) -> int:
    try:
        return struct.unpack_from("<q", data, offset)[0]
    except struct.error as exc:
        raise IdxFormatError(f"truncated i64 at 0x{offset:x}") from exc


def _check_table(
    data_length: int,
    count: int,
    base: int,
    entry_size: int,
    label: str,
) -> None:
    if count > MAX_TABLE_ENTRIES:
        raise IdxFormatError(f"{label} count is implausibly large: {count}")
    end = base + count * entry_size
    if base < IDX_HEADER_SIZE or end < base or end > data_length:
        raise IdxFormatError(
            f"{label} table outside IDX: base=0x{base:x}, count={count}, "
            f"entry_size={entry_size}, file_size={data_length}"
        )


def _packed_string(
    data: bytes,
    packed_string_base: int,
    length: int,
    relative_pointer: int,
    label: str,
) -> str:
    if length > MAX_NAME_BYTES:
        raise IdxFormatError(f"{label} is implausibly long: {length} bytes")
    start = packed_string_base + relative_pointer
    end = start + length
    if start < 0 or end < start or end > len(data):
        raise IdxFormatError(
            f"{label} outside IDX: start=0x{start:x}, length={length}"
        )
    raw = data[start:end]
    if raw.endswith(b"\x00"):
        raw = raw[:-1]
    if b"\x00" in raw:
        raise IdxFormatError(f"{label} contains an embedded NUL")
    return raw.decode("utf-8", errors="replace")


def parse_legends_idx(path: Path | str) -> LegendsIndex:
    idx_path = Path(path)
    data = idx_path.read_bytes()
    if len(data) < IDX_HEADER_SIZE:
        raise IdxFormatError(f"{idx_path.name}: IDX is shorter than 64 bytes")
    if data[:4] != IDX_MAGIC:
        raise IdxFormatError(f"{idx_path.name}: expected ISFP magic")

    marker = _u32(data, 4)
    index_hash = _u32(data, 8)
    version = _u32(data, 12)
    if marker != LEGENDS_IDX_MARKER or version != LEGENDS_IDX_VERSION:
        raise IdxFormatError(
            f"{idx_path.name}: unsupported IDX variant "
            f"(marker=0x{marker:08x}, version=0x{version:x}); "
            "this tool only accepts the verified Steam Legends layout"
        )

    metadata_base = 16
    resource_count = _u64(data, 16)
    resource_base = metadata_base + _u64(data, 24)
    file_count = _u64(data, 32)
    file_base = metadata_base + _u64(data, 40)
    volume_count = _u64(data, 48)
    volume_base = metadata_base + _u64(data, 56)

    _check_table(
        len(data),
        resource_count,
        resource_base,
        RESOURCE_ENTRY_SIZE,
        "resource",
    )
    _check_table(
        len(data), file_count, file_base, FILE_INFO_ENTRY_SIZE, "file info"
    )
    _check_table(
        len(data), volume_count, volume_base, VOLUME_ENTRY_SIZE, "volume"
    )

    resources: list[Resource] = []
    for index in range(resource_count):
        offset = resource_base + index * RESOURCE_ENTRY_SIZE
        resource_id = _u64(data, offset)
        parent_id = _u64(data, offset + 8)
        name_len = _u64(data, offset + 16)
        name_ptr = _i64(data, offset + 24)
        name = _packed_string(
            data,
            offset + 16,
            name_len,
            name_ptr,
            f"resource[{index}] name",
        )
        resources.append(Resource(resource_id, parent_id, name))

    file_infos: list[FileInfo] = []
    for index in range(file_count):
        offset = file_base + index * FILE_INFO_ENTRY_SIZE
        try:
            values = struct.unpack_from("<QIIIIIIQQ", data, offset)
        except struct.error as exc:
            raise IdxFormatError(f"truncated file info {index}") from exc
        file_infos.append(FileInfo(*values))

    volumes: list[Volume] = []
    for index in range(volume_count):
        offset = volume_base + index * VOLUME_ENTRY_SIZE
        volume_id = _u64(data, offset)
        unknown_id = _u64(data, offset + 8)
        name_len = _u64(data, offset + 16)
        name_ptr = _i64(data, offset + 24)
        filename = _packed_string(
            data,
            offset + 16,
            name_len,
            name_ptr,
            f"volume[{index}] name",
        )
        volumes.append(Volume(volume_id, unknown_id, filename))

    return LegendsIndex(
        path=idx_path,
        marker=marker,
        index_hash=index_hash,
        version=version,
        resources=resources,
        file_infos=file_infos,
        volumes=volumes,
    )


def _volume_basename(name: str) -> str:
    normalized = name.replace("\\", "/")
    basename = PurePosixPath(normalized).name
    if not basename or basename in {".", ".."}:
        raise UnsafePathError(f"unsafe package volume name: {name!r}")
    if "/" in basename or "\\" in basename or ":" in basename:
        raise UnsafePathError(f"unsafe package volume name: {name!r}")
    if not basename.casefold().endswith(".pkg"):
        raise IdxFormatError(f"volume is not a .pkg file: {name!r}")
    return basename


def assets_from_index(
    index: LegendsIndex,
    package_dir: Path,
    package_lookup: dict[str, Path] | None = None,
) -> Iterator[AssetEntry]:
    paths = index.resource_paths()
    volumes = {volume.volume_id: volume for volume in index.volumes}
    if package_lookup is None:
        package_lookup = {
            path.name.casefold(): path
            for path in package_dir.glob("*.pkg")
            if path.is_file()
        }

    for file_info in index.file_infos:
        virtual_path = paths.get(file_info.resource_id)
        if virtual_path is None:
            raise IdxFormatError(
                f"{index.path.name}: file references unknown resource "
                f"0x{file_info.resource_id:016x}"
            )
        volume = volumes.get(file_info.volume_id)
        if volume is None:
            raise IdxFormatError(
                f"{index.path.name}: file references unknown volume "
                f"0x{file_info.volume_id:016x}"
            )
        package_name = _volume_basename(volume.filename)
        package_path = package_lookup.get(package_name.casefold())
        if package_path is None:
            raise FileNotFoundError(
                f"{index.path.name}: package volume not found: {package_name}"
            )
        yield AssetEntry(index.path, package_path, virtual_path, file_info)


def iter_assets(
    game_dir: Path | str,
    *,
    index_pattern: str = "*.idx",
    skip_errors: bool = False,
    errors: list[str] | None = None,
) -> Iterator[AssetEntry]:
    game_path = Path(game_dir)
    package_dir = game_path / "res_packages"
    if not package_dir.is_dir():
        raise FileNotFoundError(f"res_packages directory not found: {package_dir}")
    package_lookup = {
        path.name.casefold(): path
        for path in package_dir.glob("*.pkg")
        if path.is_file()
    }
    for idx_path in sorted(package_dir.glob(index_pattern)):
        if not idx_path.is_file():
            continue
        try:
            index = parse_legends_idx(idx_path)
            yield from assets_from_index(index, package_dir, package_lookup)
        except (IdxFormatError, UnsafePathError, FileNotFoundError) as exc:
            if not skip_errors:
                raise
            if errors is not None:
                errors.append(str(exc))


def matches_asset(
    entry: AssetEntry,
    *,
    patterns: Sequence[str] = (),
    extensions: Sequence[str] = (),
    package_filters: Sequence[str] = (),
) -> bool:
    folded_path = entry.virtual_path.casefold()
    if patterns and not any(
        fnmatch.fnmatchcase(folded_path, pattern.casefold()) for pattern in patterns
    ):
        return False
    normalized_exts = {
        extension.casefold()
        if extension.startswith(".")
        else f".{extension.casefold()}"
        for extension in extensions
    }
    if normalized_exts and entry.extension not in normalized_exts:
        return False
    package_name = entry.package_path.name.casefold()
    if package_filters and not any(
        value.casefold() in package_name for value in package_filters
    ):
        return False
    return True


def unpack_pkg_container(
    container: bytes,
    *,
    expected_unpacked_size: int,
    expected_crc32: int,
    max_unpacked_size: int = DEFAULT_MAX_UNPACKED_SIZE,
) -> bytes:
    if expected_unpacked_size > max_unpacked_size:
        raise ExtractionError(
            f"asset expands to {expected_unpacked_size} bytes, over safety limit "
            f"{max_unpacked_size}"
        )
    if len(container) < 8:
        raise ExtractionError("package entry is shorter than its block header")
    reserved, chunk_count = struct.unpack_from("<II", container, 0)
    if reserved != 0:
        raise ExtractionError(f"unexpected package entry marker: 0x{reserved:08x}")
    if chunk_count == 0 or chunk_count > 1_000_000:
        raise ExtractionError(f"invalid package chunk count: {chunk_count}")
    header_size = 8 + chunk_count * 4
    if header_size > len(container):
        raise ExtractionError("truncated package chunk table")

    descriptors = struct.unpack_from(f"<{chunk_count}I", container, 8)
    cursor = header_size
    output = bytearray()
    for index, descriptor in enumerate(descriptors):
        unknown_flags = descriptor & ~0x1FFFF
        if unknown_flags:
            raise ExtractionError(
                f"chunk {index} has unsupported flags 0x{unknown_flags:x}"
            )
        chunk_size = (descriptor & 0xFFFF) + 1
        chunk_end = cursor + chunk_size
        if chunk_end > len(container):
            raise ExtractionError(f"chunk {index} extends beyond package entry")
        chunk = container[cursor:chunk_end]
        cursor = chunk_end
        if descriptor & 0x10000:
            try:
                decoded = zlib.decompress(chunk, wbits=-15)
            except zlib.error as exc:
                raise ExtractionError(
                    f"raw DEFLATE failed for chunk {index}: {exc}"
                ) from exc
        else:
            decoded = chunk
        output.extend(decoded)
        if len(output) > max_unpacked_size:
            raise ExtractionError("decoded data crossed the safety limit")

    if cursor != len(container):
        raise ExtractionError(
            f"package entry has {len(container) - cursor} unexpected trailing bytes"
        )
    if len(output) != expected_unpacked_size:
        raise ExtractionError(
            f"size mismatch: decoded {len(output)}, expected {expected_unpacked_size}"
        )
    actual_crc = binascii.crc32(output) & 0xFFFFFFFF
    if actual_crc != expected_crc32:
        raise ExtractionError(
            f"CRC mismatch: decoded {actual_crc:08x}, expected {expected_crc32:08x}"
        )
    return bytes(output)


def read_asset_bytes(
    entry: AssetEntry,
    *,
    max_unpacked_size: int = DEFAULT_MAX_UNPACKED_SIZE,
) -> bytes:
    package_size = entry.package_path.stat().st_size
    start = entry.file_info.offset
    end = start + entry.file_info.packed_size
    if start < 0 or end < start or end > package_size:
        raise ExtractionError(
            f"{entry.package_path.name}: entry range {start}:{end} is outside "
            f"package size {package_size}"
        )
    with entry.package_path.open("rb") as package:
        package.seek(start)
        container = package.read(entry.file_info.packed_size)
    if len(container) != entry.file_info.packed_size:
        raise ExtractionError("short read from package")
    return unpack_pkg_container(
        container,
        expected_unpacked_size=entry.file_info.unpacked_size,
        expected_crc32=entry.file_info.crc32,
        max_unpacked_size=max_unpacked_size,
    )


def extract_asset(
    entry: AssetEntry,
    output_root: Path | str,
    *,
    execute: bool = False,
    overwrite: bool = False,
    max_unpacked_size: int = DEFAULT_MAX_UNPACKED_SIZE,
) -> ExtractionResult:
    root = Path(output_root)
    target = safe_output_path(root, entry.virtual_path)
    if not execute:
        return ExtractionResult(entry, target, "dry-run", 0)

    if target.exists() and not overwrite:
        raise ExtractionError(f"target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = read_asset_bytes(entry, max_unpacked_size=max_unpacked_size)
    temporary = target.with_name(f".{target.name}.part")
    ensure_within_root(temporary, root)
    try:
        mode = "wb" if overwrite else "xb"
        with temporary.open(mode) as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if target.exists() and not overwrite:
            raise ExtractionError(f"target appeared during extraction: {target}")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return ExtractionResult(entry, target, "extracted", len(payload))


def summarize_assets(entries: Iterable[AssetEntry]) -> dict[str, object]:
    count = 0
    packed_bytes = 0
    unpacked_bytes = 0
    extension_counts: Counter[str] = Counter()
    support_counts: Counter[str] = Counter()
    package_names: set[str] = set()
    for entry in entries:
        count += 1
        packed_bytes += entry.file_info.packed_size
        unpacked_bytes += entry.file_info.unpacked_size
        extension_counts[entry.extension or "<none>"] += 1
        support_counts[entry.support.value] += 1
        package_names.add(entry.package_path.name)
    return {
        "asset_count": count,
        "package_count": len(package_names),
        "packed_bytes": packed_bytes,
        "unpacked_bytes": unpacked_bytes,
        "extensions": dict(extension_counts.most_common()),
        "support": dict(support_counts.most_common()),
    }


def write_manifest(
    results: Iterable[ExtractionResult],
    output_root: Path | str,
    filename: str = "extraction_manifest.json",
) -> Path:
    root = Path(output_root)
    target = safe_output_path(root, filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": 1,
        "results": [result.to_dict() for result in results],
    }
    temporary = target.with_name(f".{target.name}.part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return target


def validate_glb(path: Path | str) -> None:
    candidate = Path(path)
    data = candidate.read_bytes()
    if len(data) < 12 or data[:4] != b"glTF":
        raise ExtractionError(f"converter output is not a GLB file: {candidate}")
    version, declared_length = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise ExtractionError(f"unsupported GLB version {version}: {candidate}")
    if declared_length != len(data):
        raise ExtractionError(
            f"GLB length mismatch: header {declared_length}, file {len(data)}"
        )


def run_geometry_converter(
    input_path: Path | str,
    output_path: Path | str,
    converter_path: Path | str,
    output_root: Path | str,
    *,
    execute: bool = False,
) -> dict[str, object]:
    root = Path(output_root).resolve()
    source = ensure_within_root(Path(input_path), root)
    target = ensure_within_root(Path(output_path), root)
    converter = Path(converter_path).resolve()
    allowed_names = {"wows-geometry-cli", "wows-geometry-cli.exe"}
    if converter.name.casefold() not in allowed_names:
        raise ValueError(
            "converter must be named wows-geometry-cli or wows-geometry-cli.exe"
        )
    if source.suffix.casefold() != ".geometry":
        raise ValueError("only an extracted .geometry file can use this converter")
    if target.suffix.casefold() != ".glb":
        raise ValueError("geometry converter output must use .glb")
    command = [str(converter), "-i", str(source), "-o", str(target)]
    result: dict[str, object] = {
        "status": "dry-run",
        "command": command,
        "input": str(source),
        "output": str(target),
    }
    if not execute:
        return result
    if not converter.is_file():
        raise FileNotFoundError(f"converter not found: {converter}")
    if not source.is_file():
        raise FileNotFoundError(f"geometry input not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command,
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    result.update(
        {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    )
    if completed.returncode != 0:
        result["status"] = "converter-failed"
        return result
    if not target.is_file():
        result["status"] = "converter-produced-no-file"
        return result
    try:
        validate_glb(target)
    except ExtractionError as exc:
        result["status"] = "invalid-converter-output"
        result["validation_error"] = str(exc)
        return result
    result["status"] = "converted-and-validated"
    result["output_bytes"] = target.stat().st_size
    return result
