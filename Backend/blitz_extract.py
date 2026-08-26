from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import re
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Any
import zipfile

from blitz_assets import (
    BlitzLayout,
    dereference,
    layout_signature,
    reader_name,
    resolve_blitz_layout,
    serialized_files,
)
from game_archive import progress


CAB_RE = re.compile(r"CAB-[0-9a-fA-F]{32}")
LOD_RE = re.compile(r"_LOD([0-2])$", re.IGNORECASE)
COLLISION_RE = re.compile(r"(?:_COL|COLLISION)$", re.IGNORECASE)
TEXTURE_ROLES = {
    "_MainTex": "albedo",
    "_Paint": "paint",
    "_BumpMap": "normal",
    "_MetallicGlossMap": "metallic_gloss",
    "_OcclusionMap": "ao",
    "_EmissionMap": "emission",
}

Matrix4 = list[list[float]]


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", value).strip("._")
    return (cleaned or "unnamed")[:140]


def _unitypy() -> Any:
    try:
        import UnityPy
    except ImportError as exc:
        raise RuntimeError(
            "WoWS Blitz Unity 런타임이 설치되지 않았어요. "
            "WoWS Toolbox 정식 런타임에서 다시 실행해 주세요."
        ) from exc
    return UnityPy


def _bundle_cabs(data: bytes) -> set[str]:
    environment = _unitypy().load(data)
    result: set[str] = set()
    for name, _ in serialized_files(environment):
        match = CAB_RE.search(name)
        if match:
            result.add(match.group(0))
    return result


def _external_cabs(serialized: Any) -> list[str]:
    result: list[str] = []
    for external in getattr(serialized, "externals", []):
        match = CAB_RE.search(str(getattr(external, "path", "")))
        if match:
            result.append(match.group(0))
    return list(dict.fromkeys(result))


def _body_serialized(environment: Any, cab_name: str) -> Any:
    expected = cab_name.casefold()
    candidates = serialized_files(environment)
    for name, serialized in candidates:
        if name.casefold() == expected or expected in name.casefold():
            return serialized
    available = ", ".join(name for name, _ in candidates[:12])
    raise RuntimeError(f"Blitz body CAB를 불러오지 못했어요: {cab_name} / {available}")


def _read_index(path: Path, signature: str) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if payload.get("schema") != "wows-blitz-cab-index/v1":
        return {}
    if payload.get("layout_signature") != signature:
        return {}
    entries = payload.get("entries", {})
    return entries if isinstance(entries, dict) else {}


def _write_index(
    path: Path,
    signature: str,
    entries: dict[str, dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(
            {
                "schema": "wows-blitz-cab-index/v1",
                "layout_signature": signature,
                "entries": dict(sorted(entries.items())),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _index_local_bundle(
    path: Path,
    layout: BlitzLayout,
    entries: dict[str, dict[str, str]],
) -> None:
    try:
        cabs = _bundle_cabs(path.read_bytes())
    except Exception:
        return
    relative = path.relative_to(layout.bundle_root).as_posix()
    for cab in cabs:
        entries.setdefault(cab, {"source": "bundle", "path": relative})


def _model_candidates(layout: BlitzLayout, ship_id: str) -> list[Path]:
    pattern = f"*/ship/*/model/{ship_id}.ab"
    return sorted(layout.bundle_root.glob(pattern), key=lambda item: item.as_posix().casefold())


def _priority_obb_names(names: list[str], ship_id: str) -> list[str]:
    normalized_ship = ship_id.casefold()
    suffixes = {
        "assets/bundle/shaders.ab",
        "assets/bundle/artist/animation.ab",
        f"/model/{normalized_ship}.ab",
    }
    prefix_to_artist = {
        "cn": "cn",
        "cw": "uk",
        "eu": "eu",
        "fr": "france",
        "ge": "germany",
        "it": "italy",
        "jp": "japan",
        "nl": "netherlands",
        "pa": "panamerica",
        "pl": "poland",
        "ru": "russia",
        "sp": "spain",
        "uk": "uk",
        "us": "usa",
    }
    prefix = normalized_ship.split("_", 1)[0]
    artist = prefix_to_artist.get(prefix)
    if artist:
        suffixes.update(
            {
                f"assets/bundle/artist/{artist}/gun.ab",
                f"assets/bundle/artist/{artist}/misc.ab",
            }
        )
    return [
        name
        for name in names
        if any(name.casefold().endswith(suffix) for suffix in suffixes)
    ]


def _scan_obb(
    layout: BlitzLayout,
    ship_id: str,
    missing: set[str],
    entries: dict[str, dict[str, str]],
) -> None:
    if not missing or layout.obb_path is None:
        return
    with zipfile.ZipFile(layout.obb_path, "r") as archive:
        names = [name for name in archive.namelist() if name.casefold().endswith(".ab")]
        priority = _priority_obb_names(names, ship_id)
        priority_set = set(priority)
        ordered_names = [*priority, *(name for name in names if name not in priority_set)]
        for number, name in enumerate(ordered_names, start=1):
            try:
                for cab in _bundle_cabs(archive.read(name)):
                    entries.setdefault(cab, {"source": "obb", "path": name})
                    missing.discard(cab)
            except Exception:
                pass
            if number % 500 == 0:
                progress(
                    "blitz_dependencies",
                    min(35, 10 + number * 25 // max(1, len(ordered_names))),
                    f"기본 OBB 의존성 검색 {number}/{len(ordered_names)} · 남은 CAB {len(missing)}개",
                )
            if not missing:
                return


def _scan_downloaded_bundles(
    layout: BlitzLayout,
    missing: set[str],
    entries: dict[str, dict[str, str]],
) -> None:
    if not missing:
        return
    bundles = sorted(
        layout.bundle_root.rglob("*.ab"),
        key=lambda item: item.as_posix().casefold(),
    )
    for number, path in enumerate(bundles, start=1):
        _index_local_bundle(path, layout, entries)
        missing.difference_update(entries)
        if number % 500 == 0:
            progress(
                "blitz_dependencies",
                min(45, 35 + number * 10 // max(1, len(bundles))),
                f"다운로드 번들 검색 {number}/{len(bundles)} · 남은 CAB {len(missing)}개",
            )
        if not missing:
            return


def _safe_archive_target(root: Path, archive_name: str) -> Path:
    parts = PurePosixPath(archive_name).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"안전하지 않은 OBB 번들 경로예요: {archive_name}")
    target = root.joinpath(*parts).resolve()
    target.relative_to(root.resolve())
    return target


def _materialize_dependencies(
    layout: BlitzLayout,
    dependencies: list[str],
    entries: dict[str, dict[str, str]],
    cache_dir: Path,
) -> list[Path]:
    paths: list[Path] = []
    obb_needed = [
        (cab, entries[cab]["path"])
        for cab in dependencies
        if entries[cab].get("source") == "obb"
    ]
    if obb_needed:
        if layout.obb_path is None:
            raise FileNotFoundError("Blitz 기본 OBB가 없어서 공통 번들을 읽지 못했어요.")
        with zipfile.ZipFile(layout.obb_path, "r") as archive:
            for _, archive_name in obb_needed:
                target = _safe_archive_target(cache_dir, archive_name)
                if not target.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_suffix(target.suffix + ".part")
                    with archive.open(archive_name, "r") as source, temporary.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
                    temporary.replace(target)

    for cab in dependencies:
        entry = entries[cab]
        if entry.get("source") == "bundle":
            path = (layout.bundle_root / entry["path"]).resolve()
            path.relative_to(layout.bundle_root)
        else:
            path = _safe_archive_target(cache_dir, entry["path"])
        if not path.is_file():
            raise FileNotFoundError(f"Blitz CAB 번들이 사라졌어요: {cab} / {path}")
        paths.append(path)
    return list(dict.fromkeys(paths))


def _dependency_paths(
    layout: BlitzLayout,
    body_path: Path,
    ship_id: str,
    cache_root: Path,
) -> tuple[str, list[str], list[Path], Path]:
    body_environment = _unitypy().load(str(body_path))
    body_candidates = serialized_files(body_environment)
    if not body_candidates:
        raise RuntimeError(f"Blitz body 번들에 직렬화 데이터가 없어요: {body_path.name}")
    body_name, body_serialized = body_candidates[0]
    match = CAB_RE.search(body_name)
    if match is None:
        raise RuntimeError(f"Blitz body CAB 이름을 확인하지 못했어요: {body_name}")
    body_cab = match.group(0)
    dependencies = _external_cabs(body_serialized)

    signature = layout_signature(layout)
    key = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
    index_path = cache_root / "Blitz" / key / "cab-index.json"
    bundle_cache = cache_root / "Blitz" / key / "obb-bundles"
    entries = _read_index(index_path, signature)
    for candidate in _model_candidates(layout, ship_id):
        _index_local_bundle(candidate, layout, entries)

    missing = {cab for cab in dependencies if cab not in entries}
    if missing:
        progress(
            "blitz_dependencies",
            10,
            f"처음 보는 Blitz 빌드라 CAB 의존성 {len(missing)}개를 찾는 중이에요.",
        )
        _scan_obb(layout, ship_id, missing, entries)
        _scan_downloaded_bundles(layout, missing, entries)
    if missing:
        raise RuntimeError("Blitz CAB 의존 번들을 찾지 못했어요: " + ", ".join(sorted(missing)))
    _write_index(index_path, signature, entries)
    paths = _materialize_dependencies(
        layout,
        dependencies,
        entries,
        bundle_cache,
    )
    return body_cab, dependencies, paths, index_path


def _local_matrix(transform: Any) -> Matrix4:
    position = transform.m_LocalPosition
    rotation = transform.m_LocalRotation
    scale = transform.m_LocalScale
    x, y, z, w = map(float, (rotation.x, rotation.y, rotation.z, rotation.w))
    sx, sy, sz = map(float, (scale.x, scale.y, scale.z))
    return [
        [(1 - 2 * (y * y + z * z)) * sx, (2 * (x * y - z * w)) * sy, (2 * (x * z + y * w)) * sz, float(position.x)],
        [(2 * (x * y + z * w)) * sx, (1 - 2 * (x * x + z * z)) * sy, (2 * (y * z - x * w)) * sz, float(position.y)],
        [(2 * (x * z - y * w)) * sx, (2 * (y * z + x * w)) * sy, (1 - 2 * (x * x + y * y)) * sz, float(position.z)],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _multiply(left: Matrix4, right: Matrix4) -> Matrix4:
    return [
        [sum(left[row][item] * right[item][column] for item in range(4)) for column in range(4)]
        for row in range(4)
    ]


def _world_matrices(transforms: dict[int, dict[str, Any]]) -> dict[int, Matrix4]:
    identity: Matrix4 = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
    result: dict[int, Matrix4] = {}
    active: set[int] = set()

    def resolve(path_id: int) -> Matrix4:
        if path_id in result:
            return result[path_id]
        if path_id in active:
            raise RuntimeError(f"Blitz Transform 순환 참조가 있어요: {path_id}")
        active.add(path_id)
        record = transforms[path_id]
        parent_id = record["parent_id"]
        parent = resolve(parent_id) if parent_id in transforms else identity
        result[path_id] = _multiply(parent, record["local"])
        active.remove(path_id)
        return result[path_id]

    for path_id in transforms:
        resolve(path_id)
    return result


def _point(matrix: Matrix4, values: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = values
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3],
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3],
    )


def _inverse_transpose(matrix: Matrix4) -> list[list[float]]:
    a, b, c = matrix[0][:3]
    d, e, f = matrix[1][:3]
    g, h, i = matrix[2][:3]
    determinant = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(determinant) < 1e-12:
        return [[a, b, c], [d, e, f], [g, h, i]]
    inverse = [
        [(e * i - f * h) / determinant, (c * h - b * i) / determinant, (b * f - c * e) / determinant],
        [(f * g - d * i) / determinant, (a * i - c * g) / determinant, (c * d - a * f) / determinant],
        [(d * h - e * g) / determinant, (b * g - a * h) / determinant, (a * e - b * d) / determinant],
    ]
    return [[inverse[column][row] for column in range(3)] for row in range(3)]


def _normal(matrix: list[list[float]], values: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = values
    transformed = (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z,
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z,
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z,
    )
    length = math.sqrt(sum(value * value for value in transformed))
    return tuple(value / length for value in transformed) if length else transformed


def _offset_token(token: str, offsets: list[int]) -> str:
    parts = token.split("/")
    for index, offset in enumerate(offsets):
        if index < len(parts) and parts[index]:
            parts[index] = str(int(parts[index]) + offset)
    return "/".join(parts)


def _texture_identity(reader: Any) -> tuple[str, int]:
    asset = str(getattr(getattr(reader, "assets_file", None), "name", "unknown"))
    return asset, int(reader.path_id)


def _material_payload(
    material_reader: Any,
    texture_dir: Path,
    texture_max_size: int,
    textures: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    material = material_reader.parse_as_object()
    material_name = safe_name(reader_name(material_reader))
    material_id = safe_name(f"{material_name}_{int(material_reader.path_id)}")
    result: dict[str, Any] = {
        "id": material_id,
        "name": material_name,
        "textures": {},
        "color": (1.0, 1.0, 1.0),
    }
    saved = getattr(material, "m_SavedProperties", None)
    for key, color in getattr(saved, "m_Colors", []) if saved is not None else []:
        if key == "_Color":
            result["color"] = (float(color.r), float(color.g), float(color.b))
    for key, texture_env in getattr(saved, "m_TexEnvs", []) if saved is not None else []:
        role = TEXTURE_ROLES.get(str(key))
        if role is None:
            continue
        texture_reader = dereference(texture_env.m_Texture)
        if texture_reader is None:
            continue
        identity = _texture_identity(texture_reader)
        item = textures.get(identity)
        if item is None:
            texture = texture_reader.parse_as_object()
            texture_name = safe_name(str(getattr(texture, "m_Name", reader_name(texture_reader))))
            filename = safe_name(f"{texture_name}_{role}_{int(texture_reader.path_id)}") + ".png"
            target = texture_dir / filename
            image = texture.image
            original_size = tuple(image.size)
            if texture_max_size > 0 and max(image.size) > texture_max_size:
                image.thumbnail((texture_max_size, texture_max_size))
            image.save(target, format="PNG", compress_level=9)
            item = {
                "name": texture_name,
                "role": role,
                "file": target.name,
                "width": image.width,
                "height": image.height,
                "original_width": original_size[0],
                "original_height": original_size[1],
                "bytes": target.stat().st_size,
            }
            textures[identity] = item
        result["textures"].setdefault(role, item["file"])
    return result


def _include_mesh(mesh_name: str, lod: int) -> bool:
    if COLLISION_RE.search(mesh_name):
        return False
    match = LOD_RE.search(mesh_name)
    return match is None or int(match.group(1)) == lod


def _assemble(
    serialized: Any,
    output_dir: Path,
    stem: str,
    lod: int,
    texture_max_size: int,
) -> dict[str, Any]:
    readers = list(serialized.objects.values())
    transforms: dict[int, dict[str, Any]] = {}
    game_object_transforms: dict[int, int] = {}
    renderers: dict[int, list[Any]] = {}
    for reader in readers:
        if reader.type.name == "Transform":
            transform = reader.parse_as_object()
            path_id = int(reader.path_id)
            game_object_id = int(transform.m_GameObject.m_PathID)
            transforms[path_id] = {
                "parent_id": int(transform.m_Father.m_PathID),
                "local": _local_matrix(transform),
            }
            game_object_transforms[game_object_id] = path_id
        elif reader.type.name == "MeshRenderer":
            renderer = reader.parse_as_object()
            renderers[int(renderer.m_GameObject.m_PathID)] = [
                material
                for pointer in renderer.m_Materials
                if (material := dereference(pointer)) is not None
            ]
    worlds = _world_matrices(transforms)

    texture_dir = output_dir / "textures"
    texture_dir.mkdir(parents=True, exist_ok=True)
    textures: dict[tuple[str, int], dict[str, Any]] = {}
    material_payloads: dict[tuple[str, int], dict[str, Any]] = {}
    renderer_materials: dict[int, list[dict[str, Any]]] = {}
    for game_object_id, materials in renderers.items():
        payloads: list[dict[str, Any]] = []
        for material_reader in materials:
            identity = _texture_identity(material_reader)
            if identity not in material_payloads:
                material_payloads[identity] = _material_payload(
                    material_reader,
                    texture_dir,
                    texture_max_size,
                    textures,
                )
            payloads.append(material_payloads[identity])
        renderer_materials[game_object_id] = payloads

    default_material = {
        "id": "blitz_default",
        "name": "blitz_default",
        "textures": {},
        "color": (1.0, 1.0, 1.0),
    }
    all_materials = [default_material, *material_payloads.values()]
    mtl_path = output_dir / f"{stem}.mtl"
    mtl_lines: list[str] = []
    for item in all_materials:
        red, green, blue = item["color"]
        mtl_lines.extend(
            (
                f"newmtl {item['id']}\n",
                "Ka 1.000000 1.000000 1.000000\n",
                f"Kd {red:.6f} {green:.6f} {blue:.6f}\n",
                "Ks 0.000000 0.000000 0.000000\n",
                "d 1.000000\n",
            )
        )
        albedo = item["textures"].get("paint") or item["textures"].get("albedo")
        if albedo:
            mtl_lines.append(f"map_Kd textures/{albedo}\n")
        normal = item["textures"].get("normal")
        if normal:
            mtl_lines.append(f"map_Bump textures/{normal}\n")
        ao = item["textures"].get("ao")
        if ao:
            mtl_lines.append(f"map_Ka textures/{ao}\n")
        mtl_lines.append("\n")
    mtl_path.write_text("".join(mtl_lines), encoding="utf-8", newline="\n")

    obj_path = output_dir / f"{stem}.obj"
    lines = [f"mtllib {mtl_path.name}\n"]
    offsets = [0, 0, 0]
    mesh_counts: Counter[str] = Counter()
    instances = 0
    vertices = 0
    faces = 0
    bounds_min = [float("inf"), float("inf"), float("inf")]
    bounds_max = [float("-inf"), float("-inf"), float("-inf")]

    for reader in readers:
        if reader.type.name != "MeshFilter":
            continue
        mesh_filter = reader.parse_as_object()
        mesh_reader = dereference(mesh_filter.m_Mesh)
        game_object_reader = dereference(mesh_filter.m_GameObject)
        if mesh_reader is None or game_object_reader is None:
            continue
        mesh_name = reader_name(mesh_reader)
        if not _include_mesh(mesh_name, lod):
            continue
        game_object_id = int(mesh_filter.m_GameObject.m_PathID)
        transform_id = game_object_transforms.get(game_object_id)
        if transform_id is None:
            continue
        mesh_text = mesh_reader.parse_as_object().export()
        if isinstance(mesh_text, bytes):
            mesh_text = mesh_text.decode("utf-8", errors="replace")
        if not mesh_text:
            continue
        instances += 1
        mesh_counts[mesh_name] += 1
        object_name = safe_name(f"{reader_name(game_object_reader)}_{game_object_id}")
        matrix = worlds[transform_id]
        normal_matrix = _inverse_transpose(matrix)
        local_counts = [0, 0, 0]
        materials = renderer_materials.get(game_object_id) or [default_material]
        submesh_index = -1
        material_written = False
        lines.extend((f"o {object_name}\n", f"g {object_name}\n"))
        for raw in str(mesh_text).splitlines():
            if raw.startswith("v "):
                values = tuple(float(item) for item in raw.split()[1:4])
                converted = _point(matrix, values)
                for axis, value in enumerate(converted):
                    bounds_min[axis] = min(bounds_min[axis], value)
                    bounds_max[axis] = max(bounds_max[axis], value)
                lines.append("v {:.9G} {:.9G} {:.9G}\n".format(*converted))
                local_counts[0] += 1
                vertices += 1
            elif raw.startswith("vt "):
                lines.append(raw + "\n")
                local_counts[1] += 1
            elif raw.startswith("vn "):
                values = tuple(float(item) for item in raw.split()[1:4])
                converted = _normal(normal_matrix, values)
                lines.append("vn {:.9G} {:.9G} {:.9G}\n".format(*converted))
                local_counts[2] += 1
            elif raw.startswith("g ") and local_counts[0] > 0:
                submesh_index += 1
                selected = materials[min(submesh_index, len(materials) - 1)]
                lines.append(f"usemtl {selected['id']}\n")
                material_written = True
            elif raw.startswith("f "):
                if not material_written:
                    lines.append(f"usemtl {materials[0]['id']}\n")
                    material_written = True
                adjusted = [_offset_token(token, offsets) for token in raw.split()[1:]]
                lines.append("f " + " ".join(adjusted) + "\n")
                faces += 1
        offsets = [offsets[index] + local_counts[index] for index in range(3)]

    if instances == 0:
        raise RuntimeError(f"Blitz LOD{lod} 메시를 하나도 조립하지 못했어요.")
    obj_path.write_text("".join(lines), encoding="utf-8", newline="\n")
    return {
        "obj_path": obj_path,
        "mtl_path": mtl_path,
        "instances": instances,
        "vertices": vertices,
        "faces": faces,
        "mesh_instances": dict(sorted(mesh_counts.items())),
        "materials": list(material_payloads.values()),
        "textures": list(textures.values()),
        "bounds": {
            "min": bounds_min,
            "max": bounds_max,
            "size": [bounds_max[index] - bounds_min[index] for index in range(3)],
        },
    }


def extract_blitz(args: Any, output_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    layout = resolve_blitz_layout(args.game_dir, require_obb=True)
    selected = str(args.selected_model_path or "").replace("\\", "/").strip("/")
    ship_id = str(args.ship_resource or Path(selected).stem).lower()
    if not ship_id:
        raise ValueError("Blitz 함선 리소스 식별자가 비어 있어요.")
    body_path = (layout.bundle_root / selected).resolve() if selected else None
    camouflage = str(getattr(args, "camouflage", "default") or "default")
    if camouflage not in {"default", "native"}:
        candidate = layout.body_root / f"{ship_id}_{camouflage}.ab"
        if candidate.is_file():
            body_path = candidate.resolve()
    if body_path is None or not body_path.is_file():
        candidate = layout.body_root / f"{ship_id}.ab"
        body_path = candidate.resolve()
    body_path.relative_to(layout.bundle_root)
    if not body_path.is_file():
        raise FileNotFoundError(f"Blitz 함선 body 번들이 없어요: {body_path}")

    cache_root = Path(args.cache_root).resolve() if args.cache_root else output_dir.parent / ".blitz-cache"
    progress("blitz_prepare", 5, f"Blitz body와 기본 OBB를 확인했어요: {body_path.name}")
    body_cab, dependencies, dependency_paths, index_path = _dependency_paths(
        layout,
        body_path,
        ship_id,
        cache_root,
    )
    progress(
        "blitz_load",
        48,
        f"Blitz Unity 번들 {len(dependency_paths) + 1}개를 함께 읽는 중이에요.",
    )
    environment = _unitypy().load(str(body_path), *(str(path) for path in dependency_paths))
    serialized = _body_serialized(environment, body_cab)
    stem = safe_name(
        f"{args.ship_index}_{args.display_name or ship_id}_{camouflage}_Editable"
    )
    progress("blitz_assemble", 62, f"LOD{args.lod} 메시와 재질을 조립하는 중이에요.")
    assembled = _assemble(
        serialized,
        output_dir,
        stem,
        int(args.lod),
        int(args.texture_max_size),
    )
    elapsed = time.perf_counter() - started
    report = {
        "schema": "wows-toolbox-blitz-extraction/v1",
        "source": "blitz",
        "output_dir": str(output_dir),
        "ship_id": ship_id,
        "ship_index": str(args.ship_index),
        "display_name": str(args.display_name),
        "camouflage": camouflage,
        "body_bundle": str(body_path.relative_to(layout.bundle_root).as_posix()),
        "body_cab": body_cab,
        "dependency_cabs": dependencies,
        "dependency_bundle_count": len(dependency_paths),
        "cab_index": str(index_path),
        "lod": int(args.lod),
        "formats": ["obj"],
        "obj_file": str(assembled["obj_path"]),
        "mtl_file": str(assembled["mtl_path"]),
        "object_count": assembled["instances"],
        "vertex_count": assembled["vertices"],
        "face_count": assembled["faces"],
        "material_count": len(assembled["materials"]),
        "texture_count": len(assembled["textures"]),
        "mesh_instances": assembled["mesh_instances"],
        "materials": assembled["materials"],
        "textures": assembled["textures"],
        "bounds": assembled["bounds"],
        "timings": {"total_seconds": round(elapsed, 3)},
    }
    report_path = output_dir / f"{stem}.model.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    report["report_file"] = str(report_path)
    progress(
        "complete",
        100,
        f"Blitz 편집 파트 {report['object_count']}개와 텍스처 {report['texture_count']}개를 추출했어요.",
    )
    return report
