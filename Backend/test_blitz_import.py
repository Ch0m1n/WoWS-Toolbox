from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from blitz_import import Adb, APP, BUNDLE, PACKAGE, import_data, safe_tar_copy


FAKE_ADB = r'''
import io, os, re, shlex, sys, tarfile
args = sys.argv[1:]
if args[:1] == ['-s']:
    args = args[2:]
if args == ['devices']:
    print('List of devices attached\nemulator-5554\tdevice')
elif args == ['shell', 'id']:
    print('uid=0(root) gid=0(root)')
elif args[:1] in (['exec-out'], ['shell']):
    cmd = args[-1] if args[0] == 'exec-out' else shlex.split(args[-1])[0]
    if cmd == 'id':
        print('uid=0(root) gid=0(root)')
    elif cmd.startswith('command -v pidof'):
        pass
    elif cmd.startswith('test -d'):
        pass
    elif " -exec stat " in cmd:
        print('bundle/file|13|1000')
    elif cmd.startswith('find /sdcard/'):
        print('/sdcard/Android/obb/net.wargaming.wows.blitz/main.123.net.wargaming.wows.blitz.obb')
    elif cmd.startswith('find /data/'):
        print('/data/data/net.wargaming.wows.blitz/files/DesignData')
    elif cmd.startswith('if test -f /sdcard/'):
        print('/sdcard/Android/data/net.wargaming.wows.blitz/files/Assets/Resources/DesignData')
    elif cmd.startswith('tar '):
        entry = shlex.split(cmd.split('; rc=', 1)[0])[-1]
        name = 'prefab/ship/body/us_bb_example.ab' if entry == '.' else entry
        payload = b'asset-content'
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as archive:
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        sys.stdout.buffer.write(stream.getvalue())
        token = re.search(r'BLITZ_TAR_EXIT_[a-f0-9]+', cmd).group(0)
        sys.stdout.buffer.write(('\n' + token + ':0\n').encode('ascii'))
    else:
        sys.exit(3)
else:
    sys.exit(4)
'''


class FakeAdb(Adb):
    def command(self, *args):
        return [sys.executable, self.executable] + (["-s", self.device] if self.device else []) + list(args)


class BlitzImportTests(unittest.TestCase):
    def test_end_to_end_stream_copy_preserves_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "fake_adb.py"
            script.write_text(FAKE_ADB, encoding="utf-8")
            old = root / "imports" / "existing"
            old.mkdir(parents=True)
            (old / "keep.txt").write_text("keep")
            with contextlib.redirect_stdout(io.StringIO()):
                result = import_data(FakeAdb(str(script)), old.parent)
            target = Path(result["path"])
            self.assertTrue(target.is_dir())
            self.assertFalse(target.name.endswith(".partial"))
            self.assertEqual(result["body_count"], 1)
            self.assertEqual((target / "DesignData").read_bytes(), b"asset-content")
            self.assertEqual((target / "downloads" / result["obb"]).read_bytes(), b"asset-content")
            self.assertEqual((old / "keep.txt").read_text(), "keep")
            self.assertTrue((target / "blitz-import.json").is_file())

    def test_refuses_no_or_multiple_devices(self):
        for rows in ("List of devices attached", "List of devices attached\na\tdevice\nb\tdevice",
                     "List of devices attached\na\tunauthorized"):
            with self.subTest(rows=rows), patch.object(Adb, "run", return_value=rows):
                with self.assertRaises(RuntimeError):
                    Adb("adb").select("")

    def test_transfer_exit_failure_never_publishes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "fake_adb.py"
            script.write_text(FAKE_ADB.replace("sys.stdout.buffer.write(stream.getvalue())",
                                              "sys.stdout.buffer.write(stream.getvalue()); sys.exit(7)"))
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    import_data(FakeAdb(str(script)), root / "imports")
            self.assertTrue(all(p.name.endswith(".partial") for p in (root / "imports").iterdir()))

    def test_source_changes_never_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "fake_adb.py"
            script.write_text(FAKE_ADB)
            adb = FakeAdb(str(script))
            with patch.object(adb, "fingerprint", side_effect=["old", "new"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    import_data(adb, root / "imports")
            entries = list((root / "imports").iterdir())
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0].name.endswith(".partial"))
            self.assertFalse((entries[0] / "blitz-import.json").exists())

    def test_rejects_remote_tar_error_even_when_adb_exits_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "fake_adb.py"
            script.write_text(FAKE_ADB.replace("token + ':0", "token + ':1"))
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    import_data(FakeAdb(str(script)), root / "imports")
            entries = list((root / "imports").iterdir())
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0].name.endswith(".partial"))

    def test_explicit_device_selection(self):
        with patch.object(Adb, "run", return_value="List of devices attached\na\tdevice\nb\tdevice"):
            adb = Adb("adb")
            adb.select("b")
            self.assertEqual(adb.device, "b")

    def test_root_su_fallback(self):
        adb = Adb("adb", "a")
        with patch.object(adb, "run", return_value="uid=2000(shell)"), \
                patch.object(adb, "shell", return_value="uid=0(root)"):
            adb.root()
        self.assertFalse(adb.root_shell)
        self.assertIn("su", adb.shell_command("id"))

    def test_refuses_running_game(self):
        with patch.object(Adb, "shell", return_value="1234"):
            with self.assertRaises(RuntimeError):
                Adb("adb").stopped()

    def test_failed_copy_never_publishes(self):
        with tempfile.TemporaryDirectory() as temp:
            adb = Adb("adb")
            responses = ["", f"/sdcard/Android/obb/{PACKAGE}/main.123.{PACKAGE}.obb", ""]
            with patch.object(adb, "select"), patch.object(adb, "root"), patch.object(adb, "stopped"), \
                    patch.object(adb, "fingerprint", return_value="stable"), \
                    patch.object(adb, "shell", side_effect=responses), \
                    patch("blitz_import.copy_remote", side_effect=RuntimeError("connection lost")), \
                    contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "connection lost"):
                    import_data(adb, Path(temp))
            entries = list(Path(temp).iterdir())
            self.assertEqual(len(entries), 1)
            self.assertTrue(entries[0].name.endswith(".partial"))
            self.assertFalse((entries[0] / "blitz-import.json").exists())

    def test_rejects_unsafe_archive_entries(self):
        cases = [("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                 ("C:/escape", tarfile.REGTYPE), ("a\\escape", tarfile.REGTYPE),
                 ("link", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE),
                 ("NUL.txt", tarfile.REGTYPE), ("trailing. ", tarfile.REGTYPE)]
        for name, kind in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    archive.addfile(member)
                stream.seek(0)
                with tarfile.open(fileobj=stream, mode="r|") as archive:
                    with self.assertRaises(RuntimeError):
                        safe_tar_copy(archive, Path(temp))

    def test_rejects_case_collisions(self):
        with tempfile.TemporaryDirectory() as temp:
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                archive.addfile(tarfile.TarInfo("a.ab"))
                archive.addfile(tarfile.TarInfo("A.ab"))
            stream.seek(0)
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                with self.assertRaisesRegex(RuntimeError, "Duplicate"):
                    safe_tar_copy(archive, Path(temp))


if __name__ == "__main__":
    unittest.main()
