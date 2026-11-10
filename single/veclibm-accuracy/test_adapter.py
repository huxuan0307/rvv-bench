#!/usr/bin/env python3
"""Host-side invariant checks; these do not replace target numerical tests."""
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('accuracy_build', HERE / 'build.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class AdapterInvariants(unittest.TestCase):
    def test_complete_catalog(self):
        cases = build.catalog()
        self.assertEqual(141, len(cases))
        self.assertEqual(81, len({c['source'] for c in cases}))
        self.assertIn('exp.special', {c['id'] for c in cases})

    def test_sampling_changes_no_assertion_or_special_vectors(self):
        original = (build.UPSTREAM / 'test/src/test_infra.cpp').read_text()
        adapted = build.adapted_infra(16, 4)
        assertions = r'EXPECT_(?:LT|EQ)\([^;]+;'
        self.assertEqual(re.findall(assertions, original), re.findall(assertions, adapted))
        self.assertEqual(10, adapted.count('nb_pts = nb_pts > 16 ? 16 : nb_pts;'))
        self.assertEqual(2, adapted.count('nb_pts_x = nb_pts_x > 4 ? 4 : nb_pts_x;'))
        self.assertEqual(2, adapted.count('nb_pts_y = nb_pts_y > 4 ? 4 : nb_pts_y;'))
        self.assertEqual(original[original.index('void test_vectors_fp64'):],
                         adapted[adapted.index('void test_vectors_fp64'):])
        self.assertNotIn('x[cnt++] = x_val;', adapted)

    def test_quick_catalog_and_no_repetition_padding(self):
        config = json.loads((HERE / 'quick.json').read_text())
        self.assertEqual({c['id'] for c in build.catalog()}, set(config))
        for scale in config.values():
            self.assertGreaterEqual(scale['points'], 2)
            self.assertGreaterEqual(scale['grid_points'], 2)
            self.assertEqual(1, scale.get('repeats', 1))

    def test_make_uniform_override_and_full(self):
        uniform = subprocess.run(['make', '-n', 'all', 'POINTS=32'], cwd=HERE,
                                 capture_output=True, text=True, check=True).stdout
        self.assertIn('--points 32', uniform)
        self.assertNotIn('--config quick.json', uniform)
        full = subprocess.run(['make', '-n', 'full'], cwd=HERE,
                              capture_output=True, text=True, check=True).stdout
        self.assertIn('--build build-full', full)
        self.assertIn('--points 100000', full)
        self.assertNotIn('--config quick.json', full)

    def test_failure_accounting_and_single_evaluation(self):
        source = r'''
#include <gtest/gtest.h>
static int seen, failed;
extern "C" void accuracy_check(int ok, const char *, const char *, const char *,
 const char *, int, double, double, unsigned long long, unsigned long long) {
 ++seen; if (!ok) ++failed;
}
int main() {
 int i=0;
 EXPECT_LT(++i, 2); EXPECT_LT(1, 1);
 EXPECT_EQ(0x8000000000000000ULL, 0ULL);
 return i == 1 && seen == 3 && failed == 2 ? 0 : 1;
}
'''
        directory = HERE / 'build/host-tests'
        directory.mkdir(parents=True, exist_ok=True)
        src, exe = directory / 'assertions.cpp', directory / 'assertions'
        src.write_text(source)
        subprocess.run(['g++', '-std=c++17', '-nostdinc++', '-I' + str(HERE / 'include'),
                        '-I' + str(build.AUTOVEC), str(src), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
