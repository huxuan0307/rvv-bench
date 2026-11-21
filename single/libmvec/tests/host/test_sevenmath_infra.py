"""最小主机测试：网格、基础/扩展模式协议、接口映射与运行目录约束。"""
import argparse
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[2] / "script"
sys.path.insert(0, str(SCRIPT))

import build
import collect
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


def case_auto(function, lengths, batches, repeat=1):
    """Ten-mode manifest: direct LMUL4/8 plus compiler-generated auto LMUL4/8."""
    base = case(function, lengths, batches, repeat)
    base['expected']['modes'] = list(common.MODES_AUTO_LMUL48)
    base['expected']['sample_count'] = 100 * len(lengths) * len(batches)
    base['expected']['order_policy'] = 'rotate-all-13'
    return base


def csv3_text_auto(function, repeat=4):
    """Minimal complete csv3 block with the 13-column auto extension."""
    lines = [f'LIBMVEC_CSV,3,{function},128,123456789,grid,{repeat}',
             'M,strict,fast,auto1,auto2,direct1,direct2,direct4,direct8,'
             'auto4,auto8,rdcycle,empty,copy',
             'm,hash,' + ','.join(f'b{r},c{r}' for r in range(10)),
             'B,16,1']
    for mode in range(13):
        values = []
        for r in range(10):
            values += [100000 + mode * 1000 + r * 40, (300 + mode * 7) * repeat + r]
        lines.append('D,' + str(mode) + ',123456789,' +
                     ','.join(str(value) for value in values))
    lines += ['T,1000,0,130', 'E,perf-' + function + ',pass', '']
    return '\n'.join(lines)


class InfraTests(unittest.TestCase):
    def test_direct_gsim_timeout_can_be_explicitly_disabled(self):
        parser = run.build_parser()
        base = ['--manifest', 'm', '--output', 'o']
        gsim = ['gsim', *base, '--emu', 'e', '--reference', 'r', '--dramsim3-ini', 'd']
        default = run.validate_arguments(parser, parser.parse_args(gsim))
        self.assertEqual(default.timeout_seconds, 24 * 3600)
        direct = run.validate_arguments(parser, parser.parse_args(
            [*gsim, '--timeout', 'none', '--jobs', '2', '--no-smoke']))
        self.assertIsNone(direct.timeout_seconds)
        self.assertTrue(direct.no_smoke)
        self.assertEqual(direct.jobs, 2)
        for args in (['nemu', *base, '--nemu', 'n', '--timeout', 'none'],
                     [*gsim, '--backend', 'run-emu', '--runner', 'r', '--timeout', 'none']):
            with mock.patch.object(parser, 'error', side_effect=ValueError) as error:
                with self.assertRaises(ValueError):
                    run.validate_arguments(parser, parser.parse_args(args))
                self.assertIn('invalid duration', error.call_args.args[0])

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

    def test_csv2_roundtrip_recovery_and_version_mismatch(self):
        modern = case('log', [16], [1])
        old = case('log', [16], [1])
        old['expected']['wire_format'] = 'csv2'
        del old['expected']['timing_repeat']
        text = csv3_text('log', repeat=1)
        legacy = text.replace('LIBMVEC_CSV,3,log,256,123456789,grid,1',
                              'LIBMVEC_CSV,2,log,256,123456789,grid', 1)
        expanded, meta = compact_csv.expand(legacy, old)
        self.assertEqual(meta['wire_format'], 'csv2')
        self.assertEqual(meta['timing_repeat'], 1)
        self.assertIn('timing_repeat=1 wire_format=csv2', expanded)
        recovered = compact_csv.recover(legacy, old)
        self.assertEqual(recovered['config']['wire_format'], 'csv2')
        self.assertEqual(len(recovered['records']), 90)
        self.assertTrue(all(r['wire_format'] == 'csv2' and not r['measurement_valid']
                            for r in recovered['records']))
        for log, manifest in ((legacy, modern), (text, old),
                              (text.replace('CSV,3,', 'CSV,2,'), old)):
            with self.subTest(header=log.splitlines()[0]):
                with self.assertRaisesRegex(ValueError, 'version/arity'):
                    compact_csv.expand(log, manifest)
                self.assertFalse(compact_csv.recover(log, manifest)['records'])

    def test_csv_framing_never_recovers_a_fake_complete_block(self):
        text = csv3_text('log')
        manifest = case('log', [16], [1], repeat=4)
        expanded, meta = compact_csv.expand_streams(
            ('simulator diagnostic without a final newline', text.replace('\n', '\r\n')), manifest)
        self.assertIn('LIBMVEC_COMPLETE case=perf-log status=pass', expanded)
        self.assertEqual(meta['wire_format'], 'csv3')
        header, rows = text.split('\n', 1)
        for streams in ((text.rstrip('\n'), 'Core 0: HIT GOOD TRAP\n'),
                        (text.replace('\n', '\r'),),
                        (text.replace('\n', '\v'),),
                        (header + '\n', rows),
                        (text, text),
                        (text, 'LIBMVEC_COMPLETE case=perf-log status=pass\n')):
            with self.subTest(stream_count=len(streams)), self.assertRaises(ValueError):
                compact_csv.expand_streams(streams, manifest)

    def test_collector_keeps_raw_controls_and_flags_short_steady_window(self):
        manifest = case('log', [16], [1], repeat=4)
        lines = csv3_text('log', repeat=4).splitlines()
        for i, line in enumerate(lines):
            if line.startswith('D,'):
                parts = line.split(',')
                mode = int(parts[1])
                if mode < 6:
                    parts[4::2] = ['40000'] * 10
                    if mode == 2:
                        parts[-1] = '19999'
                else:
                    parts[4::2] = ['3'] * 10
                lines[i] = ','.join(parts)
        expanded, _ = compact_csv.expand('\n'.join(lines) + '\n', manifest)
        issues = []
        manifest['expected'] = common.expected_plan(manifest, 'performance', manifest['name'])
        metrics = collect.audit_performance(expanded, manifest, issues)
        self.assertFalse(issues)
        self.assertTrue(all(r['cycles_per_round'] is None and r['cycles'] == 3
                            for r in metrics['calibrations']))
        groups = {(g['mode'], g['phase']): g for g in metrics['groups']}
        short = groups['auto_m1', 'steady']
        self.assertEqual(short['window_cycles'], 40000)
        self.assertEqual(short['min_window_cycles'], 19999)
        self.assertFalse(short['window_ok'])
        self.assertFalse(short['quality_ok'])
        good = groups['auto_m2', 'steady']
        self.assertTrue(good['quality_ok'])
        self.assertEqual(good['median_cycles'], 10000)
        self.assertIsNone(groups['auto_m2', 'first']['window_ok'])
        self.assertFalse(metrics['measurement_valid'])

        for invalid in ('timing_repeat=bad', 'n=0', 'k=0', 'cycles=bad', 'cycles=' + '9' * 5000):
            with self.subTest(field=invalid.split('=')[0]):
                rows = expanded.splitlines()
                index = next(i for i, row in enumerate(rows) if row.startswith('LIBMVEC_SAMPLE '))
                field = invalid.split('=')[0] + '='
                rows[index] = ' '.join(invalid if token.startswith(field) else token
                                       for token in rows[index].split(' '))
                bad_issues = []
                bad = collect.audit_performance('\n'.join(rows), manifest, bad_issues)
                self.assertTrue(bad_issues)
                self.assertEqual(bad['groups'], [])
                self.assertFalse(bad['measurement_valid'])

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

    def test_ten_mode_matrix_roundtrip(self):
        manifest = case_auto('log', [16], [1], repeat=4)
        expanded, metadata = compact_csv.expand(csv3_text_auto('log'), manifest)
        self.assertEqual(metadata['timing_repeat'], 4)
        for mode in ('auto_m4', 'auto_m8'):
            self.assertIn(mode, expanded)
        plan = common.expected_plan(manifest, 'performance', 'perf-log')
        self.assertEqual(len(plan['modes']), 10)
        self.assertEqual(len(list(common.rotation_schedule(plan))), 130)
        # The eight-mode tuple must stay a valid (prefix) manifest.
        self.assertEqual(common.expected_plan(case('log', [16], [1]), 'performance',
                                              'perf-log')['modes'], list(common.MODES))
        broken = case_auto('log', [16], [1], repeat=4)
        broken['expected']['modes'] = list(common.MODES_LMUL48) + ['auto_m8', 'auto_m4']
        with self.assertRaises(common.ManifestError):
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
