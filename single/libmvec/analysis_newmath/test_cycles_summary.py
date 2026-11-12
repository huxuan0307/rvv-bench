"""合成样本验证严格汇总；不依赖任务目录、工具链或模拟器。"""
import copy
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import analyze as a
import cycles_summary as summary
from test_analyze import fixture_rows


def merged_rows():
    result = []
    for factor, (family, profile) in enumerate(summary.SOURCES, 1):
        for row in fixture_rows(a.FUNCTIONS, factor):
            mode = row['mode']
            interface = ('scalar' if mode in a.MODES[:2] else
                         'array' if profile == 'aux-array' and mode in a.MODES[4:6] else
                         'Nx' if mode in a.MODES[2:6] else 'control')
            row.update(family=family, profile=profile, run_role='main', interface=interface)
            result.append(row)
    return result


class SummaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = merged_rows()

    def test_schema_family_baselines_and_modes(self):
        result = summary.summarize(reversed(self.rows))
        self.assertEqual(len(result), 192)
        self.assertEqual(tuple(result[0]), summary.FIELDS)
        self.assertEqual(result[0], dict(function='log', n=16, k=1, mode='auto_m1',
                                        scalar_strict=318, scalar_fast=348,
                                        veclibm=126, opt_nofrm=252,
                                        libm_vec_nx=378, libm_vec_array=504))
        self.assertEqual({r['mode'] for r in result}, set(a.MODES[2:6]))
        # array auto remains its own measured source, including codegen differences.
        self.assertNotEqual(result[0]['libm_vec_array'], result[0]['libm_vec_nx'])
        self.assertEqual(result[2]['mode'], 'direct_m1')
        self.assertEqual(result[2]['veclibm'], 146)

    def test_reject_partial_duplicate_unknown_profile_and_replay(self):
        for rows in (self.rows[:-1], self.rows + [self.rows[0]],
                     [r for r in self.rows if r['profile'] != 'aux-array']):
            with self.assertRaises(a.Rejected):
                summary.summarize(rows)
        for field, value in [('profile', 'historical'), ('run_role', 'replay'),
                             ('interface', 'array'), ('measurement_valid', 'False'),
                             ('order', '8'), ('cycle_end', '0')]:
            rows = list(self.rows)
            rows[0] = dict(rows[0], **{field: value})
            with self.subTest(field=field), self.assertRaises(a.Rejected):
                summary.summarize(rows)

    def test_cli_and_exclusive_output(self):
        with tempfile.TemporaryDirectory(prefix='newmath-summary-') as tmp:
            root = Path(tmp)
            source, target = root/'raw.csv', root/'summary.csv'
            a.write_csv(source, self.rows)
            command = [sys.executable, str(Path(summary.__file__)),
                       '--input', str(source), '--output', str(target)]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            with target.open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 192)
            self.assertEqual(tuple(rows[0]), summary.FIELDS)
            before = target.read_bytes()
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(target.read_bytes(), before)
            source.write_text('family,profile\nnew,main-nx\n')
            missing = root/'rejected.csv'
            with self.assertRaises(a.Rejected):
                summary.generate(source, missing)
            self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
