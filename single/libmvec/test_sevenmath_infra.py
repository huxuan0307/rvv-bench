"""最小主机测试：网格、基础/扩展模式协议、接口映射与运行目录约束。"""
import argparse
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import build
import compact_csv
import runner_common as common
import run


def case(function, lengths, batches, repeat=1):
    return {'name': 'perf-' + function, 'suite': 'performance', 'expected': {
        'wire_format': 'csv3', 'profile': 'grid', 'function': function,
        'modes': list(common.MODES), 'calibration_modes': list(common.CALIBRATION_MODES),
        'lengths': lengths, 'batches': batches, 'phases': common.PHASES.copy(),
        'sample_count': 60 * len(lengths) * len(batches),
        'calibration_count': 30 * len(lengths) * len(batches),
        'order_policy': 'rotate-all-9', 'min_checks': 0, 'timing_repeat': repeat}}


def case_ext(function, lengths, batches, repeat=1):
    """Same manifest with the direct_m4/direct_m8 extension (11 columns)."""
    base = case(function, lengths, batches, repeat)
    base['expected']['modes'] = list(common.MODES_LMUL48)
    base['expected']['sample_count'] = 80 * len(lengths) * len(batches)
    base['expected']['order_policy'] = 'rotate-all-11'
    return base


def csv3_text_ext(function, repeat=4):
    """Minimal complete csv3 block with the 11-column extended matrix."""
    lines = [f'LIBMVEC_CSV,3,{function},128,123456789,grid,{repeat}',
             'M,strict,fast,auto1,auto2,direct1,direct2,direct4,direct8,rdcycle,empty,copy',
             'm,hash,' + ','.join(f'b{r},c{r}' for r in range(10)),
             'B,16,1']
    for mode in range(11):
        values = []
        for r in range(10):
            values += [100000 + mode * 1000 + r * 40, (300 + mode * 7) * repeat + r]
        lines.append('D,' + str(mode) + ',123456789,' +
                     ','.join(str(value) for value in values))
    lines += ['T,1000,0,110', 'E,perf-' + function + ',pass', '']
    return '\n'.join(lines)


def csv3_text(function, repeat=4, header_repeat=None):
    """Construct a minimal complete csv3 block (one N/K block, nine modes)."""
    header_repeat = repeat if header_repeat is None else header_repeat
    lines = [f'LIBMVEC_CSV,3,{function},256,123456789,grid,{header_repeat}',
             'M,strict,fast,auto1,auto2,direct1,direct2,rdcycle,empty,copy',
             'm,hash,' + ','.join(f'b{r},c{r}' for r in range(10)),
             'B,16,1']
    for mode in range(9):
        values = []
        for r in range(10):
            begin = 100000 + mode * 1000 + r * 40
            # Raw intervals deliberately do not all divide by R.
            cycles = (300 + mode * 7) * repeat + r
            values += [begin, cycles]
        lines.append('D,' + str(mode) + ',123456789,' +
                     ','.join(str(value) for value in values))
    lines += ['T,1000,0,90', 'E,perf-' + function + ',pass', '']
    return '\n'.join(lines)


class InfraTests(unittest.TestCase):
    def test_grid_validation_and_seven_function_names(self):
        self.assertEqual(build.grid_values('16,128', max_value=1024), [16, 128])
        for invalid in ('', '0', '16,16', '1,2,3,4', '1025', 'x'):
            with self.subTest(invalid=invalid), self.assertRaises(argparse.ArgumentTypeError):
                build.grid_values(invalid, max_value=1024)
        for function in build.FUNCS:
            plan = common.expected_plan(case(function, [128], [1, 4]), 'performance', 'perf-' + function)
            self.assertEqual(plan['rounds'], 10)
            self.assertEqual(len(list(common.rotation_schedule(plan))), 180)
            compact_csv._plan(case(function, [128], [1, 4]), case(function, [128], [1, 4])['expected'])

    def test_csv3_repeat_roundtrip_and_manifest_gate(self):
        manifest = case('log', [16], [1], repeat=4)
        expanded, metadata = compact_csv.expand(csv3_text('log'), manifest)
        self.assertEqual(metadata['timing_repeat'], 4)
        self.assertIn('timing_repeat=4', expanded)
        self.assertIn('LIBMVEC_CONFIG', expanded)
        with self.assertRaises(ValueError):
            compact_csv.expand(csv3_text('log', header_repeat=2), manifest)
        with self.assertRaises(ValueError):
            compact_csv.expand(csv3_text('log'), case('log', [16], [1], repeat=2))
        missing = case('log', [16], [1], repeat=4)
        del missing['expected']['timing_repeat']
        with self.assertRaises(common.ManifestError):
            common.expected_plan(missing, 'performance', 'perf-log')
        # csv2 manifests predate R and must stay at R=1.
        old = case('log', [16], [1], repeat=1)
        old['expected']['wire_format'] = 'csv2'
        del old['expected']['timing_repeat']
        self.assertEqual(common.expected_plan(old, 'performance', 'perf-log')['timing_repeat'], 1)

    def test_extended_direct_matrix_roundtrip(self):
        manifest = case_ext('log', [16], [1], repeat=4)
        expanded, metadata = compact_csv.expand(csv3_text_ext('log'), manifest)
        self.assertEqual(metadata['timing_repeat'], 4)
        self.assertIn('direct_m4', expanded)
        self.assertIn('direct_m8', expanded)
        plan = common.expected_plan(manifest, 'performance', 'perf-log')
        self.assertEqual(len(plan['modes']), 8)
        self.assertEqual(len(list(common.rotation_schedule(plan))), 110)
        # Truncated or reordered matrices must not silently pass.
        for bad in (list(common.MODES[:5]) + ['direct_m4', 'direct_m8'],
                    ['direct_m4', 'direct_m8'] + list(common.MODES)):
            broken = case('log', [16], [1], repeat=4)
            broken['expected']['modes'] = bad
            with self.subTest(bad=bad), self.assertRaises(common.ManifestError):
                common.expected_plan(broken, 'performance', 'perf-log')

    def test_direct_mapping_auto_is_always_nx(self):
        for function in build.FUNCS:
            for mode in ('direct_m1', 'direct_m2'):
                self.assertEqual(build.direct_symbol(function, mode, 'array'), function + 'vec_' + mode[-2:])
                self.assertTrue(build.direct_symbol(function, mode, 'nx').endswith('_' + function))

    def test_bad_grid_and_output_paths_are_rejected(self):
        for bad in ([0], [16, 16], [16] * 4, [1025], [True]):
            with self.subTest(bad=bad), self.assertRaises(common.ManifestError):
                common.expected_plan(case('log', bad, [1]), 'performance', 'perf-log')
        for path in ('/tmp/build/20261114-170000-run', '/tmp/runs/not-dated'):
            with self.subTest(path=path), self.assertRaises(common.ManifestError):
                run.make_output(SimpleNamespace(output=Path(path)))
        with mock.patch.object(Path, 'mkdir') as mkdir:
            got = run.make_output(SimpleNamespace(output=Path('/tmp/runs/20261114-170000-nemu')))
            self.assertEqual(got.name, '20261114-170000-nemu')
            mkdir.assert_called_once()


if __name__ == '__main__':
    unittest.main()
