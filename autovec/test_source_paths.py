"""输入源码定位优先级测试，不依赖宿主固定checkout布局。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import source_paths


class SourcePathsTests(unittest.TestCase):
    def test_explicit_environment_wins(self):
        with patch.dict(os.environ, {'RVV_BENCH_SOURCES': '/custom/input'}):
            self.assertEqual(source_paths.default_sources(), Path('/custom/input'))

    def test_local_dependency_then_legacy(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {}, clear=True):
            root = Path(d) / 'bench'
            root.mkdir()
            with patch.object(source_paths, 'ROOT', root):
                self.assertEqual(source_paths.default_sources(), root.parent / 'compiler-experiment/benchmarks')
                local = root / '.dsh/deps/benchmarks'
                local.mkdir(parents=True)
                self.assertEqual(source_paths.default_sources(), local)


if __name__ == '__main__':
    unittest.main()
