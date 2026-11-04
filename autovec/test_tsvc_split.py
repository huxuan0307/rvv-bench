"""Regression checks for pinned TSVC source extraction (no compiler needed)."""
import re
import tempfile
import unittest
from pathlib import Path
from prepare_tsvc import prepare

SOURCE = Path(__file__).resolve().parents[2] / 'compiler-experiment/benchmarks/TSVC_2'


class SplitTests(unittest.TestCase):
    def test_entries_and_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = prepare(SOURCE, Path(tmp))
            self.assertEqual(len(records), 151)
            self.assertEqual(len({r['name'] for r in records}), 151)
            by_name = {r['name']: r for r in records}
            for r in records:
                body = Path(r['sources'][0]).read_text()
                entries = re.findall(r'^real_t (\w+)\(struct args_t', body, re.M)
                self.assertEqual(entries, [r['name']])
                self.assertEqual(body.count('csrr %0, cycle'), 2)
                self.assertEqual(body.count('nl < 1024;'), 1)
                self.assertNotIn('gettimeofday', body)
                self.assertEqual(r['outer_iterations'], 1024)
                self.assertTrue(all(Path(s).is_file() for s in r['sources']))
            for name, helper in [('s151', 's151s'), ('s152', 's152s'), ('s31111', 'test'), ('s471', 's471s'), ('s4121', 'f')]:
                self.assertIn(helper, by_name[name]['helper_functions'])
            self.assertEqual(by_name['s4116']['upstream_argument'], '&(struct{int * a; int b; int c;}){ip, LEN_2D/2, n1}')


if __name__ == '__main__':
    unittest.main()
