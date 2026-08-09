#!/usr/bin/env python3
"""Build an independently verified Ticonderoga hull/component LOD0 manifest."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from modeluber_parser import (
    AssetsV0,
    ParseError,
    PrototypeIndex,
    hex64,
    parse_geometry_sections,
    parse_modeluber,
    sha256_file,
)


SHIP_DIRECTORY = "content/gameplay/usa/ship/cruiser/ASC307_Ticonderoga_1990"
MODEL_SPECS = [
    ("root", "ASC307_Ticonderoga_1990.model"),
    ("mesh", "ASC307_Ticonderoga_1990_Bow.model"),
    ("ports", "ASC307_Ticonderoga_1990_Bow_ports.model"),
    ("mesh", "ASC307_Ticonderoga_1990_MidFront.model"),
    ("ports", "ASC307_Ticonderoga_1990_MidFront_ports.model"),
    ("mesh", "ASC307_Ticonderoga_1990_MidBack.model"),
    ("ports", "ASC307_Ticonderoga_1990_MidBack_ports.model"),
    ("conditional_ports", "ASC307_Ticonderoga_1990_MidBack_ports_dock.model"),
    ("mesh", "ASC307_Ticonderoga_1990_Stern.model"),
    ("ports", "ASC307_Ticonderoga_1990_Stern_ports.model"),
]
COMBAT_HARDPOINT = re.compile(r"^HP_(?:ARS|AGA|AGS|AGM|AGR)_\d+$")


def source_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
        "access": "read-only",
    }


def section_pair_stem(vertices: str | None, indices: str | None) -> bool:
    if not vertices or not indices:
        return False
    if not vertices.endswith(".vertices") or not indices.endswith(".indices"):
        return False
    return (
        vertices[: -len(".vertices")]
        if vertices.endswith(".vertices")
        else vertices
    ) == (
        indices[: -len(".indices")]
        if indices.endswith(".indices")
        else indices
    )


def geometry_file_for(
    logical_path: str | None, geometry_dir: Path
) -> Path | None:
    if not logical_path or not logical_path.endswith(".geometry"):
        return None
    # The package consolidates all LOD sections into one component .geometry.
    # LOD0 paths are already at the component root, so basename resolution is
    # exact and does not require a heuristic LOD rewrite.
    return geometry_dir / Path(logical_path).name


def build_manifest(
    assets_path: Path,
    index_path: Path,
    prototypes_path: Path,
    geometry_dir: Path,
    reference_path: Path | None = None,
) -> dict[str, Any]:
    assets = AssetsV0(assets_path)
    prototype_index = PrototypeIndex(index_path, prototypes_path)
    errors: list[str] = []
    warnings: list[str] = []
    models: list[dict[str, Any]] = []
    hardpoints: list[dict[str, Any]] = []
    lod0_render_count = 0
    section_checks = 0
    palette_checks = 0
    geometry_files_checked = 0
    material_pair_checks = 0

    for role, filename in MODEL_SPECS:
        model_path = f"{SHIP_DIRECTORY}/{filename}"
        resource_id = assets.resource_id(model_path)
        if resource_id is None:
            errors.append(f"assets path not found: {model_path}")
            continue
        try:
            blob, location = prototype_index.blob(resource_id)
            parsed = parse_modeluber(
                blob,
                assets,
                resource_id=resource_id,
                resource_path=model_path,
            )
        except ParseError as exc:
            errors.append(f"{model_path}: {exc}")
            continue

        for chain_error in parsed["variant_chain_errors"]:
            errors.append(f"{filename}: {chain_error}")
        if not parsed["pointer_bounds_valid"]:
            errors.append(f"{filename}: parser did not validate pointer bounds")

        nodes = parsed["visual_nodes"]["nodes"]
        all_matrices_finite = all(
            node["local_matrix"]["finite"] and node["world_matrix"]["finite"]
            for node in nodes
        )
        if not all_matrices_finite:
            errors.append(f"{filename}: non-finite node matrix")
        node_by_name = defaultdict(list)
        for node in nodes:
            if node["name"] is not None:
                node_by_name[node["name"]].append(node)
            if node["name"] and COMBAT_HARDPOINT.fullmatch(node["name"]):
                hardpoints.append(
                    {
                        "name": node["name"],
                        "source_model": model_path,
                        "source_role": role,
                        "node_index": node["index"],
                        "parent_index": node["parent_index"],
                        "local_matrix": node["local_matrix"],
                        "world_matrix": node["world_matrix"],
                    }
                )

        lod0 = parsed["visual_descriptors"][0]
        logical_geometry = lod0["derived_geometry_path"]
        geometry_file = geometry_file_for(logical_geometry, geometry_dir)
        section_names: set[str] = set()
        geometry_sections: list[dict[str, Any]] = []
        if geometry_file is not None and geometry_file.is_file():
            try:
                sections = parse_geometry_sections(
                    geometry_file.read_bytes(), str(geometry_file)
                )
                geometry_files_checked += 1
                geometry_sections = [
                    {
                        "name": section.name,
                        "offset": section.offset,
                        "size": section.size,
                    }
                    for section in sections
                ]
                section_names = {section.name for section in sections}
            except ParseError as exc:
                errors.append(f"{filename}: {exc}")
        elif lod0["render_count"]:
            errors.append(
                f"{filename}: LOD0 geometry file missing for {logical_geometry}"
            )

        correction_usage: dict[str, dict[str, Any]] = {}
        exact_bindings = []
        for render in lod0["render_sets"]:
            lod0_render_count += 1
            vertices = render["vertices_section"]
            indices = render["indices_section"]
            vertex_exists = vertices in section_names if vertices else False
            index_exists = indices in section_names if indices else False
            pair_matches = section_pair_stem(vertices, indices)
            mfm_resolved = render["material_mfm_path"] is not None
            material_resolved = render["material_name"] is not None
            material_pair_matches = render["material_prototype_index"] is not None
            section_checks += 2
            material_pair_checks += 1
            if not vertex_exists:
                errors.append(
                    f"{filename}: missing geometry section {vertices!r}"
                )
            if not index_exists:
                errors.append(
                    f"{filename}: missing geometry section {indices!r}"
                )
            if not pair_matches:
                errors.append(
                    f"{filename}: vertices/indices stems differ "
                    f"({vertices!r}, {indices!r})"
                )
            if not mfm_resolved:
                errors.append(
                    f"{filename}: unresolved MFM "
                    f"{render['material_mfm_path_id']}"
                )
            if not material_resolved:
                errors.append(
                    f"{filename}: unresolved material "
                    f"{render['material_name_id']}"
                )
            if not material_pair_matches:
                errors.append(
                    f"{filename}: render material pair absent from "
                    "ModelUber material table"
                )

            palette_valid = True
            for bone in render["correction_bones"]:
                palette_checks += 1
                if bone["name"] is None or bone["node_match_count"] != 1:
                    palette_valid = False
                    errors.append(
                        f"{filename}: palette {bone['name_id']} resolves to "
                        f"{bone['node_match_count']} nodes"
                    )
                    continue
                node = node_by_name[bone["name"]][0]
                usage = correction_usage.setdefault(
                    bone["name"],
                    {
                        "name_id": bone["name_id"],
                        "name": bone["name"],
                        "node_index": node["index"],
                        "local_matrix": node["local_matrix"],
                        "world_matrix": node["world_matrix"],
                        "render_indices": [],
                        "sections": [],
                    },
                )
                usage["render_indices"].append(render["index"])
                usage["sections"].append(indices)

            exact_bindings.append(
                {
                    "render_index": render["index"],
                    "vertices_section": vertices,
                    "indices_section": indices,
                    "material_mfm_path_id": render["material_mfm_path_id"],
                    "material_mfm_path": render["material_mfm_path"],
                    "material_name_id": render["material_name_id"],
                    "material_name": render["material_name"],
                    "skinned": render["skinned"],
                    "correction_bones": render["correction_bones"],
                    "material_prototype_index": render[
                        "material_prototype_index"
                    ],
                    "validation": {
                        "vertices_section_exists": vertex_exists,
                        "indices_section_exists": index_exists,
                        "section_pair_stem_matches": pair_matches,
                        "mfm_path_resolved": mfm_resolved,
                        "material_name_resolved": material_resolved,
                        "material_pair_in_model_table": material_pair_matches,
                        "correction_bones_resolve_once": palette_valid,
                    },
                }
            )

        model_record = {
            "role": role,
            "model_path": model_path,
            "resource_id": hex64(resource_id),
            "prototype_location": {
                "index": location.index,
                "data_offset": location.data_offset,
                "size": location.size,
                "index_trailing_u32": f"0x{location.trailing_u32:08X}",
            },
            "pointer_bounds_valid": parsed["pointer_bounds_valid"],
            "variant_chain_valid": not parsed["variant_chain_errors"],
            "pointer_ranges": parsed["pointer_ranges"],
            "variants": parsed["variants"],
            "model_ids": parsed["model_ids"],
            "visual_nodes": parsed["visual_nodes"],
            "all_node_matrices_finite": all_matrices_finite,
            "lod0": {
                "descriptor_offset": lod0["header_offset"],
                "bounding_box": lod0["bounding_box"],
                "primitives_path_id": lod0["geometry_path_id"],
                "primitives_path": lod0["primitives_path"],
                "logical_geometry_path": logical_geometry,
                "extracted_geometry_file": (
                    str(geometry_file.resolve())
                    if geometry_file is not None and geometry_file.exists()
                    else None
                ),
                "geometry_sections": geometry_sections,
                "render_count": lod0["render_count"],
                "section_to_mfm": exact_bindings,
            },
            "correction_bones": sorted(
                correction_usage.values(), key=lambda item: item["name"]
            ),
            "material_prototypes": parsed["material_prototypes"],
        }
        models.append(model_record)

    duplicate_hardpoints = {
        name: [item["source_model"] for item in items]
        for name, items in _group_by_name(hardpoints).items()
        if len(items) != 1
    }
    if duplicate_hardpoints:
        errors.append(f"duplicate combat hardpoints: {duplicate_hardpoints}")
    if len(hardpoints) != 17:
        errors.append(f"expected 17 combat hardpoints, found {len(hardpoints)}")

    root_models = [item for item in models if item["role"] == "root"]
    root_lod0_empty = (
        len(root_models) == 1 and root_models[0]["lod0"]["render_count"] == 0
    )
    if not root_lod0_empty:
        errors.append("root LOD0 is not the expected render-empty assembly node")

    reference = None
    if reference_path is not None:
        if not reference_path.is_file():
            errors.append(f"reference manifest missing: {reference_path}")
        else:
            raw_reference = json.loads(reference_path.read_text(encoding="utf-8"))
            reference_validation = raw_reference.get("validation", {})
            hull_part_models = reference_validation.get("hull_part_models")
            expected_hardpoints = reference_validation.get(
                "expected_combat_hardpoints"
            )
            resolved_hardpoints = reference_validation.get(
                "resolved_combat_hardpoints"
            )
            comparisons = {
                "hull_part_model_count": {
                    "independent": len(models),
                    "reference": hull_part_models,
                    "matches": hull_part_models == len(models),
                },
                "combat_hardpoint_count": {
                    "independent": len(hardpoints),
                    "reference_expected": expected_hardpoints,
                    "reference_resolved": resolved_hardpoints,
                    "matches": (
                        expected_hardpoints
                        == resolved_hardpoints
                        == len(hardpoints)
                    ),
                },
                "matrix_finiteness": {
                    "independent": all(
                        item["all_node_matrices_finite"] for item in models
                    ),
                    "reference": reference_validation.get(
                        "all_output_matrices_finite"
                    ),
                    "matches": reference_validation.get(
                        "all_output_matrices_finite"
                    )
                    is True,
                },
            }
            if not all(item["matches"] for item in comparisons.values()):
                errors.append("independent/reference cross-check mismatch")
            reference = {
                "path": str(reference_path.resolve()),
                "sha256": sha256_file(reference_path),
                "scope_note": (
                    "Reference global render count (194) includes attached "
                    "combat/misc models; this manifest's 31 render sets are "
                    "only render-bearing hull component LOD0 records."
                ),
                "comparisons": comparisons,
                "reference_global_counts": {
                    "render_sets_parsed": reference_validation.get(
                        "render_sets_parsed"
                    ),
                    "texture_properties_parsed": reference_validation.get(
                        "texture_properties_parsed"
                    ),
                },
            }

    unresolved_bindings = [
        {
            "model": model["model_path"],
            "render_index": binding["render_index"],
            "validation": binding["validation"],
        }
        for model in models
        for binding in model["lod0"]["section_to_mfm"]
        if not all(binding["validation"].values())
    ]
    validation = {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "models_expected": len(MODEL_SPECS),
        "models_parsed": len(models),
        "model_pointer_bounds_valid": all(
            model["pointer_bounds_valid"] for model in models
        ),
        "variant_chains_valid": all(
            model["variant_chain_valid"] for model in models
        ),
        "root_lod0_render_empty_by_design": root_lod0_empty,
        "render_bearing_hull_components": sum(
            1 for model in models if model["lod0"]["render_count"]
        ),
        "lod0_render_sets": lod0_render_count,
        "geometry_files_checked": geometry_files_checked,
        "geometry_section_existence_checks": section_checks,
        "material_pair_checks": material_pair_checks,
        "correction_bone_to_node_checks": palette_checks,
        "combat_hardpoints": len(hardpoints),
        "duplicate_combat_hardpoints": duplicate_hardpoints,
        "all_node_matrices_finite": all(
            model["all_node_matrices_finite"] for model in models
        ),
        "unresolved_lod0_bindings": unresolved_bindings,
        "acceptance": (
            not errors
            and len(models) == len(MODEL_SPECS)
            and lod0_render_count == 31
            and len(hardpoints) == 17
            and not unresolved_bindings
        ),
    }
    if not validation["acceptance"] and not errors:
        validation["status"] = "FAIL"
        validation["errors"].append("acceptance predicate failed")

    return {
        "schema": "wows-legends-modeluber-lod0-bindings/v1",
        "generated_by": Path(__file__).name,
        "scope": {
            "ship": "ASC307_Ticonderoga_1990",
            "records": (
                "root, four render-bearing hull meshes, four ports models, "
                "and one conditional dock ports model"
            ),
            "selection": "LOD0 descriptor (visual_descriptors[0])",
        },
        "source_files": {
            "assets.bin": source_record(assets_path),
            "prototypes.index.data": source_record(index_path),
            "prototypes.data": source_record(prototypes_path),
            "geometry_directory": str(geometry_dir.resolve()),
        },
        "binary_layout": {
            "blob_prefix": "u32 lod_count, u32 material_count",
            "top_level_offsets": (
                "six blob-relative u64 offsets at 0x08..0x30"
            ),
            "visual_nodes": "base 0x38; five base-relative i64 pointers",
            "variant_stride": "0x48",
            "model_id_stride": "0x08",
            "visual_descriptor_stride": "0x30",
            "render_set_stride": "0x20",
            "render_set_fields": {
                "0x00": "u64 MFM resource ID",
                "0x08": "u32 material-name string ID",
                "0x0C": "u32 vertices-section string ID",
                "0x10": "u32 indices-section string ID",
                "0x14": "u8 skinned",
                "0x15": "u8 palette count",
                "0x16": "2 zero padding bytes",
                "0x18": "i64 palette pointer relative to render record",
            },
            "layout_clarification": (
                "A six-byte pad plus i64 pointer cannot fit stride 0x20. "
                "All inspected records verify a two-byte pad at 0x16 and "
                "the i64 relative pointer at 0x18."
            ),
            "palette_item": "u32 VisualNodes name string ID",
            "material_prototype_stride": "0x70",
        },
        "validation": validation,
        "independent_cross_check": reference,
        "combat_hardpoints": sorted(hardpoints, key=lambda item: item["name"]),
        "models": models,
    }


def _group_by_name(
    records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["name"]].append(record)
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--prototypes", required=True, type=Path)
    parser.add_argument("--geometry-dir", required=True, type=Path)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    manifest = build_manifest(
        args.assets,
        args.index,
        args.prototypes,
        args.geometry_dir,
        args.reference,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    validation = manifest["validation"]
    print(
        f"{validation['status']}: models={validation['models_parsed']}, "
        f"LOD0 renders={validation['lod0_render_sets']}, "
        f"hardpoints={validation['combat_hardpoints']}, "
        f"errors={len(validation['errors'])}"
    )
    print(args.output)
    return 0 if validation["acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
