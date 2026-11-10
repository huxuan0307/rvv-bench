"""Host-side checks for the small performance build's parameter contract."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('build_veclibm', HERE / 'build_veclibm.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class PerformanceConfig(unittest.TestCase):
    def test_profile_covers_every_function_exactly(self):
        profile = json.loads((HERE / 'veclibm-quick.json').read_text())
        self.assertEqual(len(build.FUNCS), 25)
        self.assertEqual(set(profile), set(build.FUNCS))
        for params in profile.values():
            self.assertGreater(params['n'], 0)
            self.assertLessEqual(params['n'], 131072)
            self.assertEqual(params['n'] % 64, 0)
            self.assertEqual(params['repeats'], 3)

    def test_invalid_sizes_rejected_before_build(self):
        for option in ('--n', '--repeats', '--jobs'):
            result = subprocess.run([sys.executable, str(HERE / 'build_veclibm.py'), option, '0'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('must be positive', result.stderr)


if __name__ == '__main__':
    unittest.main()
