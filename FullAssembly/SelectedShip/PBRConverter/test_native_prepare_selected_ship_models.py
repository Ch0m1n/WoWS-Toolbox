#!/usr/bin/env python3
"""Regression tests for the native selected-ship converter watchdog."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import time
import unittest


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
MODULE_PATH = HERE / "native_prepare_selected_ship_models.py"
SPEC = importlib.util.spec_from_file_location(
    "native_prepare_selected_ship_models_tested", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NativeConverterWatchdogTests(unittest.TestCase):
    def test_safe_defaults_limit_parallel_desktop_load(self) -> None:
        args = MODULE.parse_args(
            [
                "--mapping",
                "mapping.json",
                "--extracted-root",
                "extracted",
                "--output-root",
                "output",
            ]
        )
        self.assertEqual(2, args.workers)
        self.assertEqual(300.0, args.model_timeout_seconds)

    def test_converter_success_captures_utf8_output(self) -> None:
        code, stdout, stderr, timed_out = MODULE.run_converter(
            [
                sys.executable,
                "-c",
                "print('변환 완료', flush=True)",
            ],
            5,
        )
        self.assertEqual(0, code)
        self.assertIn("변환 완료", stdout)
        self.assertEqual("", stderr)
        self.assertFalse(timed_out)

    def test_converter_timeout_reaps_child_and_reports_it(self) -> None:
        started = time.monotonic()
        code, stdout, stderr, timed_out = MODULE.run_converter(
            [
                sys.executable,
                "-c",
                (
                    "import time; "
                    "print('converter-started', flush=True); "
                    "time.sleep(30)"
                ),
            ],
            0.15,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(MODULE.TIMEOUT_EXIT_CODE, code)
        self.assertIn("converter-started", stdout)
        self.assertIn("timed out", stderr)
        self.assertTrue(timed_out)
        self.assertLess(elapsed, 5)

    @unittest.skipUnless(os.name == "nt", "Windows priority class only")
    def test_windows_converter_runs_below_normal_priority(self) -> None:
        self.assertEqual(
            MODULE.subprocess.BELOW_NORMAL_PRIORITY_CLASS,
            MODULE._process_creation_flags(),
        )


if __name__ == "__main__":
    unittest.main()
