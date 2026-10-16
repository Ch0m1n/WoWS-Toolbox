from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zlib

import game_archive as archive


class ArchiveReaderTests(unittest.TestCase):
    def test_two_passes_share_index_and_handle_and_close_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'res_packages').mkdir()
            raw = b'normal texture' * 50
            packed = zlib.compress(raw)
            (root / 'res_packages/textures.pkg').write_bytes(b'prefix' + packed + b'DDS')
            entries = {
                '/paint_n.dd0': archive.ArchiveEntry('/Paint_n.dd0', 'textures.pkg', 6, len(packed), len(raw), 0),
                '/paint_n.dds': archive.ArchiveEntry('/Paint_n.dds', 'textures.pkg', 6 + len(packed), 3, 3, 0),
            }
            with mock.patch.object(archive, 'read_archive_index', return_value=(1, entries)) as index:
                with self.assertRaisesRegex(RuntimeError, 'stop'):
                    with archive.ArchiveReader(root) as reader:
                        self.assertEqual(reader.extract(['/paint_n.dd0', '/missing.dd0'], root/'out'), [])
                        stream = next(iter(reader.streams.values()))
                        self.assertEqual(reader.extract(['/Paint_n.dds'], root/'out'), [])
                        self.assertIs(stream, next(iter(reader.streams.values())))
                        self.assertEqual(len(reader.streams), 1)
                        raise RuntimeError('stop')
                index.assert_called_once()
                self.assertTrue(stream.closed)
            self.assertEqual((root/'out/paint_n.dd0').read_bytes(), raw)
            self.assertEqual((root/'out/Paint_n.dds').read_bytes(), b'DDS')

    def test_index_failure_falls_back_once_and_warm_session_is_lazy(self):
        with mock.patch.object(archive, 'read_archive_index', side_effect=ValueError('unsupported')) as index:
            with archive.ArchiveReader(Path('game')):
                index.assert_not_called()
            with archive.ArchiveReader(Path('game')) as reader:
                self.assertEqual(reader.extract(['/a'], Path('out')), ['/a'])
                self.assertEqual(reader.extract(['/b'], Path('out')), ['/b'])
                index.assert_called_once()

    def test_failed_decompression_returns_native_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'res_packages').mkdir()
            (root/'res_packages/a.pkg').write_bytes(b'bad')
            entry = archive.ArchiveEntry('/a.dd0', 'a.pkg', 0, 3, 20, 0)
            with mock.patch.object(archive, 'read_archive_index', return_value=(1, {'/a.dd0': entry})), mock.patch.object(archive, 'extract_entry', side_effect=RuntimeError('unsupported')):
                with archive.ArchiveReader(root) as reader:
                    self.assertEqual(reader.extract(['/a.dd0'], root/'out'), ['/a.dd0'])

    def test_rejects_paths_outside_output_or_packages(self):
        for logical, volume in [('/../outside', 'a.pkg'), ('/a', '../outside.pkg')]:
            entry = archive.ArchiveEntry(logical, volume, 0, 1, 1, 0)
            with mock.patch.object(archive, 'read_archive_index', return_value=(1, {logical: entry})):
                with archive.ArchiveReader(Path('game')) as reader:
                    with self.assertRaisesRegex(ValueError, 'escapes'):
                        reader.extract([logical], Path('out'))


if __name__ == '__main__':
    unittest.main()
