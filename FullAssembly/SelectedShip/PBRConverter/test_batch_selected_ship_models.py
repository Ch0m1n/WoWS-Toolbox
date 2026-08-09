#!/usr/bin/env python3
"""Unit tests for the generic selected-ship PBR batch."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("batch_selected_ship_models.py")
SPEC = importlib.util.spec_from_file_location("selected_ship_pbr_batch", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BATCH)


def render_set(
    name: str,
    *,
    semantic: str = "intact",
    include: bool | None = True,
) -> dict:
    return {
        "vertices_section": f"{name}.vertices",
        "indices_section": f"{name}.indices",
        "material_mfm_path": "content/materials/ship.mfm",
        "material_name": "ship",
        "material_prototype_index": 0,
        "lod_index": 0,
        "damage_semantic": semantic,
        "include_in_intact": include,
        "semantic_rule": "test fixture",
    }


def model_record(render_sets: list[dict]) -> dict:
    return {
        "model_uber": {
            "visual_prototypes": [
                {
                    "lod_index": 0,
                    "derived_geometry_path": "content/geometry/shared.geometry",
                    "render_sets": render_sets,
                }
            ],
            "material_prototypes": [
                {
                    "mfm_path": "content/materials/ship.mfm",
                    "fx_path": "shaders/ship.fx",
                    "properties": [
                        {
                            "name": "diffuseMap",
                            "type": "texture",
                            "value": {"path": "content/textures/ship_a.dds"},
                        }
                    ],
                }
            ],
        }
    }


class CollectUsedModelsTests(unittest.TestCase):
    def test_rejected_mapping_fails_closed(self) -> None:
        mapping = {
            "schema": "wows-legends-static-ship-assembly/v1",
            "validation": {"static_assembly_acceptance": False},
        }
        with self.assertRaisesRegex(
            BATCH.BatchError, "static assembly is not accepted"
        ):
            BATCH._require_accepted_mapping(mapping)

    def test_dynamic_unique_collection_ignores_non_rendering_hull_nodes(self) -> None:
        mapping = {
            "hull_parts": [
                {
                    "path": "content/ship/root.model",
                    "role": "root",
                    "render_required": False,
                },
                {
                    "path": "content/ship/bow.model",
                    "role": "mesh",
                    "render_required": True,
                },
            ],
            "combat_mounts": [
                {
                    "model_path": "content/gun/shared.model",
                    "render_required": True,
                },
                {
                    "model_path": "content/gun/shared.model",
                    "render_required": True,
                },
            ],
            "misc_instances": [
                {
                    "model_path": "content/misc/boat.model",
                    "render_required": True,
                }
            ],
            "runtime_action_overlays": [
                {
                    "model_path": "content/gun/shared.model",
                    "render_required": True,
                }
            ],
        }

        uses = BATCH.collect_used_models(mapping)

        self.assertEqual(
            [item["model_path"] for item in uses],
            [
                "content/ship/bow.model",
                "content/gun/shared.model",
                "content/misc/boat.model",
            ],
        )
        shared = uses[1]
        self.assertEqual(shared["references"], 3)
        self.assertEqual(shared["categories"], ["combat", "runtime_overlay"])

    def test_same_stem_paths_receive_distinct_output_keys(self) -> None:
        first = BATCH._safe_output_key("content/usa/gun.model")
        second = BATCH._safe_output_key("content/uk/gun.model")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("gun__"))
        self.assertTrue(second.startswith("gun__"))


class SemanticManifestTests(unittest.TestCase):
    def _manifest(self, render_sets: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            geometry = root / "content" / "geometry" / "shared.geometry"
            geometry.parent.mkdir(parents=True)
            geometry.write_bytes(b"GEOMETRY")
            use = {
                "model_path": "content/ship/part.model",
                "categories": ["hull"],
                "primary_category": "hull",
            }
            return BATCH.make_manifest(use, model_record(render_sets), root)

    def test_explicit_intact_patch_is_kept_and_damage_crack_is_excluded(self) -> None:
        manifest = self._manifest(
            [
                render_set(
                    "Hull_patch_JoinShape",
                    semantic="intact",
                    include=True,
                ),
                render_set(
                    "Hull_crack_JoinShape",
                    semantic="damage",
                    include=False,
                ),
            ]
        )

        selected = manifest["models"][0]["render_sets"]
        self.assertEqual(
            [item["vertices_section"] for item in selected],
            ["Hull_patch_JoinShape.vertices"],
        )
        self.assertEqual(manifest["source"]["excluded_render_sets"], 1)
        self.assertIn("no filename regex", manifest["source"]["selection_policy"])

    def test_unknown_semantic_is_rejected(self) -> None:
        with self.assertRaisesRegex(BATCH.BatchError, "unknown damage semantics"):
            self._manifest([render_set("Hull", semantic="unknown", include=None)])

    def test_conflicting_semantic_is_rejected(self) -> None:
        with self.assertRaisesRegex(BATCH.BatchError, "both included and damage"):
            self._manifest([render_set("Hull", semantic="damage", include=True)])


class ReuseIntegrityTests(unittest.TestCase):
    def test_previous_summary_requires_same_conversion_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            summary = root / "summary.json"
            payload = {
                "schema": "wows-legends-selected-ship-pbr-batch/v1",
                "source_mapping_sha256": "mapping",
                "conversion_engine_fingerprint": "engine-a",
                "extracted_root": str(root.resolve()),
                "strict_validation": {"accepted": True},
                "results": [
                    {
                        "status": "OK",
                        "model_path": "content/ship/hull.model",
                    }
                ],
            }
            BATCH._write_json(summary, payload)
            self.assertEqual(
                {},
                BATCH._load_reuse_results(
                    summary, "mapping", root.resolve(), "engine-b"
                ),
            )
            self.assertIn(
                "content/ship/hull.model",
                BATCH._load_reuse_results(
                    summary, "mapping", root.resolve(), "engine-a"
                ),
            )

    def test_summary_reuse_rejects_tampered_glb_or_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            glb = root / "model.glb"
            validation = root / "validation.json"
            glb.write_bytes(b"original-glb")
            validation.write_text('{"status":"OK"}', encoding="utf-8")
            previous = {
                "manifest_sha256": "manifest",
                "output_key": "hull",
                "output_glb_sha256": BATCH._sha256(glb),
                "validation_sha256": BATCH._sha256(validation),
            }
            self.assertTrue(
                BATCH._summary_result_matches(
                    previous, "manifest", "hull", glb, validation
                )
            )
            glb.write_bytes(b"tampered-glb")
            self.assertFalse(
                BATCH._summary_result_matches(
                    previous, "manifest", "hull", glb, validation
                )
            )


if __name__ == "__main__":
    unittest.main()
