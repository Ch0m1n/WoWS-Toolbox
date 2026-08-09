from __future__ import annotations

import copy
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


sys.dont_write_bytecode = True


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "extract_ticonderoga_full", HERE / "extract_ticonderoga_full.py"
)
assert SPEC is not None and SPEC.loader is not None
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


class TiconderogaPipelineTests(unittest.TestCase):
    def test_bundled_profile_is_exact_and_profile_specific(self):
        profile = pipeline.load_object(pipeline.RESOURCE_PROFILE)
        resources = pipeline.validate_resource_profile(profile)
        counts = profile["expected_counts"]
        self.assertEqual(counts["system_sidecars"], 4)
        self.assertEqual(counts["lod0_geometry"], 20)
        self.assertEqual(counts["declared_textures"], 52)
        self.assertEqual(counts["total"], 76)
        self.assertEqual(len(resources), 76)
        self.assertFalse(profile["generic_ship_complete"])
        self.assertEqual(profile["profile_id"], pipeline.PROFILE_ID)

        texture_paths = {
            item["path"] for item in resources if item["kind"] == "texture"
        }
        self.assertEqual(len(texture_paths), 52)
        self.assertTrue(all(path.endswith(".dds") for path in texture_paths))
        self.assertIn(
            "content/gameplay/common/textures/transparent_glass_alpha_a.dds",
            texture_paths,
        )
        self.assertIn(
            "content/gameplay/common/textures/C004_Grid_1_alpha_a.dds",
            texture_paths,
        )
        self.assertIn(
            "content/gameplay/common/textures/American_wiresatlas_a.dds",
            texture_paths,
        )

    def test_generic_claim_or_wrong_count_is_rejected(self):
        profile = pipeline.load_object(pipeline.RESOURCE_PROFILE)
        generic = copy.deepcopy(profile)
        generic["generic_ship_complete"] = True
        with self.assertRaises(pipeline.PipelineError):
            pipeline.validate_resource_profile(generic)

        wrong_count = copy.deepcopy(profile)
        wrong_count["expected_counts"]["declared_textures"] = 51
        with self.assertRaises(pipeline.PipelineError):
            pipeline.validate_resource_profile(wrong_count)

    def test_command_plan_is_the_five_stage_packaged_pipeline(self):
        run_root = Path("C:/Exports/Ticonderoga1990_Verified")
        commands = pipeline.command_plan(
            Path("C:/Python/python.exe"),
            Path("C:/Blender/blender.exe"),
            run_root,
            "harbor_dock",
        )
        self.assertEqual(
            [item["step"] for item in commands],
            [
                "CRC extraction",
                "Ticon mapping v2",
                "20-model explicit PBR batch",
                "Ticon scene plan",
                "Blender scene assembly",
            ],
        )
        pbr_command = commands[2]["command"]
        decoder_index = pbr_command.index("--decoder-root") + 1
        self.assertEqual(
            Path(pbr_command[decoder_index]).resolve(),
            pipeline.GEOMETRY_DECODER.resolve(),
        )
        self.assertTrue((pipeline.GEOMETRY_DECODER / "decode_geometry.py").is_file())
        assembly_command = commands[4]["command"]
        obj_index = assembly_command.index("--obj") + 1
        self.assertEqual(
            Path(assembly_command[obj_index]).name,
            "Ticonderoga1990_Combined.obj",
        )

    def test_combined_obj_acceptance_requires_portable_complete_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            textures = root / "textures"
            textures.mkdir()
            obj_path = root / "Ticonderoga1990_Combined.obj"
            mtl_path = root / "Ticonderoga1990_Combined.mtl"
            texture_path = textures / "000_unique.png"
            obj_path.write_text(
                "mtllib Ticonderoga1990_Combined.mtl\n"
                "o Ticonderoga1990_Combined\nv 0 0 0\n"
                "vt 0 0\nvn 0 0 1\nusemtl Hull\nf 1/1/1 1/1/1 1/1/1\n",
                encoding="utf-8",
            )
            mtl_path.write_text(
                "newmtl Hull\nmap_Kd textures/000_unique.png\n",
                encoding="utf-8",
            )
            texture_path.write_bytes(b"synthetic-png")
            hidden = ["0027_RuntimeOverlay", "0028_RuntimeOverlay"]
            visible = [f"visible_{index}" for index in range(27)]
            checks = {
                "vertices_positive": True,
                "faces_positive": True,
                "one_obj_object": True,
                "uvs_preserved": True,
                "normals_preserved": True,
                "materials_preserved": True,
                "mtllib_relative_basename": True,
                "texture_refs_portable": True,
                "hidden_overlay_names_excluded": True,
                "source_occurrences_exact": True,
                "joined_bounds_match": True,
                "obj_bounds_match": True,
                "clean_reimport_one_mesh": True,
                "clean_reimport_bounds_match": True,
            }
            validation = {
                "ok": True,
                "mounts": {"default_visible": 27, "default_hidden": 2},
                "combined_obj": {
                    "ok": True,
                    "obj": str(obj_path),
                    "mtl": str(mtl_path),
                    "texture_directory": str(textures),
                    "mtllib": mtl_path.name,
                    "unified_mesh_objects": 1,
                    "vertices": 3,
                    "faces": 1,
                    "uv_records": 1,
                    "normal_records": 1,
                    "material_slots": 1,
                    "expected_materials": 1,
                    "usemtl": ["Hull"],
                    "mtl_materials": ["Hull"],
                    "map_references": ["textures/000_unique.png"],
                    "texture_files": ["textures/000_unique.png"],
                    "included_visible_mounts": visible,
                    "excluded_hidden_mounts": hidden,
                    "hidden_names_present_in_obj_or_mtl": [],
                    "obj_bounds_max_delta": 0.0,
                    "clean_reimport": {"mesh_objects": 1, "materials": 1},
                    "checks": checks,
                    "original_blend_preservation": {"ok": True},
                    "limitations": ["OBJ/MTL PBR limitation"],
                },
            }
            validation_path = root / "scene.validation.json"
            pipeline.write_json_atomic(validation_path, validation)
            accepted = pipeline._combined_obj_acceptance(validation_path)
            self.assertTrue(accepted["accepted"])
            self.assertEqual(accepted["included_visible_mounts"], 27)
            self.assertEqual(accepted["excluded_hidden_mounts"], 2)
            self.assertEqual(len(accepted["textures"]), 1)

            validation["combined_obj"]["map_references"] = [
                "C:/absolute/texture.png"
            ]
            pipeline.write_json_atomic(validation_path, validation)
            with self.assertRaisesRegex(pipeline.PipelineError, "non-portable"):
                pipeline._combined_obj_acceptance(validation_path)

    def test_mapping_acceptance_is_canonical_and_path_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mapping_path = root / "mapping.json"
            profile_path = root / "profile.json"
            expected_sources = (
                "GameParams.data",
                "assets.bin",
                "prototypes.index.data",
                "prototypes.data",
            )
            mapping = {
                "schema": "wows-legends-static-ship-assembly/v1",
                "source_files": {
                    name: {
                        "logical_source": f"content/{name}",
                        "size": 1,
                        "sha256": "0" * 64,
                        "access": "read-only",
                    }
                    for name in expected_sources
                },
                "validation": {
                    "resolved_combat_hardpoints": 17,
                    "hull_part_models": 10,
                    "render_sets_parsed": 194,
                    "texture_properties_parsed": 155,
                    "static_assembly_acceptance": True,
                    "unresolved_render_set_fields": [],
                    "unresolved_texture_paths": [],
                },
            }
            pipeline.write_json_atomic(mapping_path, mapping)
            profile = {
                "canonical_output": {
                    "sha256": pipeline.sha256_file(mapping_path)
                }
            }
            pipeline.write_json_atomic(profile_path, profile)

            original_profile = pipeline.ASSEMBLY_PROFILE
            pipeline.ASSEMBLY_PROFILE = profile_path
            try:
                accepted = pipeline._mapping_acceptance(mapping_path)
                self.assertEqual(
                    accepted["canonical_sha256"],
                    pipeline.sha256_file(mapping_path),
                )

                mapping["source_files"]["assets.bin"]["workspace_copy"] = (
                    "C:/machine-local/assets.bin"
                )
                pipeline.write_json_atomic(mapping_path, mapping)
                profile["canonical_output"]["sha256"] = pipeline.sha256_file(
                    mapping_path
                )
                pipeline.write_json_atomic(profile_path, profile)
                with self.assertRaisesRegex(
                    pipeline.PipelineError, "machine-local"
                ):
                    pipeline._mapping_acceptance(mapping_path)
            finally:
                pipeline.ASSEMBLY_PROFILE = original_profile

    def test_output_must_be_disjoint_from_game(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game = root / "Game"
            game.mkdir()
            pipeline.validate_output_location(game, root / "Exports")
            with self.assertRaises(pipeline.PipelineError):
                pipeline.validate_output_location(game, game / "Exports")
            with self.assertRaises(pipeline.PipelineError):
                pipeline.validate_output_location(game, root)

    @staticmethod
    def _package_bytecode_paths() -> list[Path]:
        paths = list(pipeline.TOOLBOX_ROOT.rglob("__pycache__"))
        paths.extend(pipeline.TOOLBOX_ROOT.rglob("*.pyc"))
        return sorted(paths, key=lambda path: str(path).casefold())

    def test_direct_import_and_cli_leave_package_tree_cache_free(self):
        self.assertEqual(self._package_bytecode_paths(), [])
        script = HERE / "extract_ticonderoga_full.py"
        environment = os.environ.copy()
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        import_probe = "\n".join(
            [
                "import importlib.util",
                "import os",
                "import sys",
                "spec = importlib.util.spec_from_file_location(",
                "    'pipeline_probe', sys.argv[1])",
                "assert spec is not None and spec.loader is not None",
                "module = importlib.util.module_from_spec(spec)",
                "spec.loader.exec_module(module)",
                "print(int(sys.dont_write_bytecode),",
                "      os.environ.get('PYTHONDONTWRITEBYTECODE'),",
                "      sep=':')",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            environment["PYTHONPYCACHEPREFIX"] = str(
                Path(directory) / "pycache-import"
            )
            imported = subprocess.run(
                [sys.executable, "-B", "-c", import_probe, str(script)],
                cwd=directory,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(
                imported.returncode, 0, imported.stdout + imported.stderr
            )
            self.assertEqual(imported.stdout.strip().splitlines()[-1], "1:1")
            self.assertEqual(self._package_bytecode_paths(), [])

            cli_environment = os.environ.copy()
            cli_environment.pop("PYTHONDONTWRITEBYTECODE", None)
            cli_environment["PYTHONPYCACHEPREFIX"] = str(
                Path(directory) / "pycache-cli"
            )
            cli = subprocess.run(
                [sys.executable, str(script), "--help"],
                cwd=directory,
                env=cli_environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(cli.returncode, 0, cli.stdout + cli.stderr)
            self.assertIn("verified Ticonderoga 1990", cli.stdout)
            self.assertEqual(self._package_bytecode_paths(), [])

    def test_child_processes_force_bytecode_off(self):
        commands = pipeline.command_plan(
            Path("C:/Python/python.exe"),
            Path("C:/Blender/blender.exe"),
            Path("C:/Exports/Ticonderoga1990_Verified"),
            "harbor_dock",
        )
        for item in commands[1:4]:
            self.assertEqual(item["command"][1], "-B")

        completed = subprocess.CompletedProcess(
            args=["child"], returncode=0, stdout="ok\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(
                pipeline.subprocess, "run", return_value=completed
            ) as mocked_run:
                pipeline.run_checked(
                    "child", ["child"], log_dir=Path(directory), timeout=10
                )
        child_environment = mocked_run.call_args.kwargs["env"]
        self.assertEqual(child_environment["PYTHONDONTWRITEBYTECODE"], "1")

    def test_overwrite_invalidates_stale_pass_before_extraction_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game_dir = root / "Game"
            output_root = root / "Exports"
            run_root = output_root / "Ticonderoga1990_Verified"
            manifest = run_root / "Ticonderoga1990.pipeline.json"
            blender = root / "blender.exe"
            game_dir.mkdir()
            run_root.mkdir(parents=True)
            blender.write_bytes(b"synthetic executable")
            pipeline.write_json_atomic(
                manifest,
                {
                    "status": "completed",
                    "acceptance": {"passed": True},
                    "stale": True,
                },
            )

            observed_before_extraction = []

            def fail_extraction(*_args, **_kwargs):
                observed_before_extraction.append(pipeline.load_object(manifest))
                raise RuntimeError("simulated interrupted overwrite")

            args = pipeline.argparse.Namespace(
                game_dir=game_dir,
                output_root=output_root,
                python=Path(sys.executable),
                blender=blender,
                visibility_profile="harbor_dock",
                max_single_mib=1,
                execute=True,
                overwrite=True,
            )
            with mock.patch.object(
                pipeline, "extract_asset", side_effect=fail_extraction
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "simulated interrupted overwrite"
                ):
                    pipeline.execute_pipeline(args, [], [({}, object())])

            self.assertEqual(len(observed_before_extraction), 1)
            entry_state = observed_before_extraction[0]
            final_state = pipeline.load_object(manifest)
            for state in (entry_state, final_state):
                self.assertEqual(state["status"], "in_progress")
                self.assertEqual(state["mode"], "executing")
                self.assertFalse(state["acceptance"]["passed"])
                self.assertTrue(state["previous_final_manifest_invalidated"])
                self.assertNotIn("stale", state)
            self.assertNotIn(
                '"passed": true', manifest.read_text(encoding="utf-8").casefold()
            )

    def test_cli_defaults_to_dry_run_and_no_overwrite(self):
        args = pipeline.build_parser().parse_args(
            ["--game-dir", "C:/Game", "--output-root", "C:/Exports"]
        )
        self.assertFalse(args.execute)
        self.assertFalse(args.overwrite)


if __name__ == "__main__":
    unittest.main()
