import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cache_retention import prune_old_build_caches
from compat_cache import prepare_game_params_cache
from game_archive import prepare_korabli_cache


class CacheRetentionTests(unittest.TestCase):
    def create(self, root, *names):
        for name in names:
            folder = root / name
            folder.mkdir(parents=True)
            (folder / 'cached.data').write_bytes(b'cache')

    def test_removes_only_older_numeric_builds_in_selected_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.create(root / 'pc', '10', '20', '30', 'notes', '20.partial')
            self.create(root / 'Korabli', '10')
            report = prune_old_build_caches(root / 'pc', 20)
            self.assertEqual(report['removed'], ['10'])
            self.assertEqual(set(p.name for p in (root / 'pc').iterdir()), {'20', '30', 'notes', '20.partial'})
            self.assertTrue((root / 'Korabli/10/cached.data').is_file())

    def test_no_completed_cache_means_no_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.create(root, '10')
            self.assertEqual(prune_old_build_caches(root, 20)['removed'], [])
            self.assertTrue((root / '10/cached.data').exists())

    def test_redirected_tree_is_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.create(root, '10', '20')
            import cache_retention
            original = cache_retention._reparse
            with patch('cache_retention._reparse', side_effect=lambda path: path.name == 'cached.data' or original(path)):
                report = prune_old_build_caches(root, 20)
            self.assertEqual(report['removed'], [])
            self.assertEqual(report['skipped'], ['10'])
            self.assertTrue((root / '10/cached.data').exists())

    def test_locked_cache_does_not_fail_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.create(root, '10', '20')
            with patch('cache_retention.shutil.rmtree', side_effect=PermissionError('locked')):
                self.assertEqual(prune_old_build_caches(root, 20)['skipped'], ['10'])

    def test_successful_pc_cache_reuse_prunes_older_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            idx = root / 'game/bin/20/idx'
            idx.mkdir(parents=True)
            archive = idx / 'test.idx'
            archive.write_bytes(b'idx')
            cache = root / 'cache'
            self.create(cache / 'pc', '10', '20')
            latest = cache / 'pc/20'
            (latest / 'GameParams_compat.data').write_bytes(b'params')
            (latest / 'params_cache.json').write_text(json.dumps({'build': 20, 'source_stamp': archive.stat().st_mtime_ns, 'size': 6}))
            with patch('compat_cache.read_archive_index', return_value=(20, {})):
                result = prepare_game_params_cache(root / 'game', cache, 'pc')
            self.assertTrue(result['cached'])
            self.assertFalse((cache / 'pc/10').exists())
            self.assertTrue((latest / 'GameParams_compat.data').exists())

    def test_failed_pc_cache_creation_preserves_older_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            idx = root / 'game/bin/20/idx'
            idx.mkdir(parents=True)
            (idx / 'test.idx').write_bytes(b'idx')
            self.create(root / 'cache/pc', '10')
            with patch('compat_cache.read_archive_index', return_value=(20, {})), \
                    patch('compat_cache.find_entry', side_effect=ValueError('bad game data')):
                with self.assertRaises(ValueError):
                    prepare_game_params_cache(root / 'game', root / 'cache', 'pc')
            self.assertTrue((root / 'cache/pc/10/cached.data').exists())

    def test_successful_korabli_reuse_prunes_older_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            idx = root / 'game/bin/20/idx'
            idx.mkdir(parents=True)
            archive = idx / 'test.idx'
            archive.write_bytes(b'idx')
            cache = root / 'cache/Korabli'
            self.create(cache, '10', '20')
            latest = cache / '20'
            dll = root / 'oodle.dll'
            dll.write_bytes(b'dll')
            (latest / 'GameParams_compat.data').write_bytes(b'params')
            (latest / 'assets.bin').write_bytes(b'assets')
            (latest / 'cache.json').write_text(json.dumps({'build': 20, 'source_stamp': archive.stat().st_mtime_ns,
                'params_size': 6, 'assets_size': 6, 'oodle_dll': str(dll)}))
            with patch('game_archive.read_archive_index', return_value=(20, {})):
                result = prepare_korabli_cache(root / 'game', cache)
            self.assertTrue(result['cached'])
            self.assertFalse((cache / '10').exists())
            self.assertTrue((latest / 'assets.bin').exists())

    def test_failed_korabli_creation_preserves_older_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            idx = root / 'game/bin/20/idx'
            idx.mkdir(parents=True)
            (idx / 'test.idx').write_bytes(b'idx')
            self.create(root / 'cache/Korabli', '10')
            with patch('game_archive.read_archive_index', return_value=(20, {})), \
                    patch('game_archive.find_entry', side_effect=ValueError('bad game data')):
                with self.assertRaises(ValueError):
                    prepare_korabli_cache(root / 'game', root / 'cache/Korabli')
            self.assertTrue((root / 'cache/Korabli/10/cached.data').exists())


if __name__ == '__main__':
    unittest.main()
