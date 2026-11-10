"""Strict collector tests using synthetic immutable run directories.

No simulator is launched.  Fixtures are built with run.prepare(), then guest
logs and result files are synthesized to exercise every acceptance boundary.
"""
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import runner_common as common  # noqa: E402
import collect  # noqa: E402
import test_run  # noqa: E402
from test_run import prepare_run, write_runner_state, guest_text, trap_text  # noqa: E402


def case_by_name(document, name):
    return next(case for case in document['cases'] if case['name'] == name)


def read_lines(path):
    return Path(path).read_text().splitlines()


def write_lines(path, lines):
    Path(path).write_text('\n'.join(lines) + '\n')


class ValidRunTests(unittest.TestCase):
    def test_valid_nemu_run_and_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, snapshot = prepare_run(
                root, names=('perf-exp', 'perf-sin', 'abi-public'), mode='nemu')
            document = collect.collect(Path(config['run']))
            self.assertEqual(document['state'], 'passed')
            self.assertTrue(document['complete'])
            self.assertEqual(document['summary']['selected_cases'], 3)
            self.assertEqual(document['summary']['performance_samples'], 900)
            self.assertEqual(document['summary']['calibrations'], 450)
            self.assertEqual(document['summary']['suites']['performance']['passed'], 2)
            self.assertEqual(document['summary']['suites']['accuracy']['passed'], 1)
            reports = Path(config['run']) / 'reports'
            for name in ('results.json', 'results.csv', 'samples.csv',
                         'summary.md', 'dram-config.json'):
                self.assertTrue((reports / name).is_file(), name)
            self.assertEqual(collect.main(['--run', str(config['run']),
                                           '--require-complete']), 0)

    def test_uint64_cycle_fields_are_exact_in_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, _ = prepare_run(root, names=('perf-exp',), mode='nemu')
            collect.collect(Path(config['run']))
            with open(Path(config['run']) / 'reports' / 'samples.csv') as stream:
                rows = list(csv.DictReader(stream))
            sample = next(row for row in rows
                          if row['record_type'] == 'sample' and row['r'] == '0')
            self.assertEqual(sample['cycle_begin'], str(2 ** 60))
            self.assertEqual(sample['cycle_end'],
                             str(2 ** 60 + int(sample['cycles'])))
            self.assertNotIn('e+', sample['cycle_begin'].lower())

    def test_performance_groups_are_separated_by_mode_n_k_phase(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, _ = prepare_run(root, names=('perf-exp',), mode='gsim')
            document = collect.collect(Path(config['run']))
            case = document['cases'][0]
            metrics = case['metrics']
            self.assertNotIn('modes', metrics)
            self.assertNotIn('calibration_modes', metrics)
            self.assertEqual(len(metrics['groups']), 6 * 3 * 3 * 3)
            steady = next(group for group in metrics['groups']
                          if group['mode'] == 'scalar_strict' and group['n'] == 16
                          and group['k'] == 1 and group['phase'] == 'steady')
            self.assertEqual(steady['count'], 7)
            self.assertAlmostEqual(
                steady['median_cycles_per_call'],
                steady['median_cycles'] / steady['k'])
            self.assertAlmostEqual(
                steady['median_cycles_per_element'],
                steady['median_cycles'] / (steady['k'] * steady['n']))
            self.assertTrue(any(group['phase'] == 'warmup'
                                for group in metrics['groups']))
            self.assertTrue(any(group['phase'] == 'first'
                                for group in metrics['groups']))
            # Calibration records remain raw evidence only.
            self.assertEqual(len(metrics['calibrations']), 270)
            self.assertTrue(any(row['mode'] == 'rdcycle'
                                for row in metrics['calibrations']))

    def test_subset_selection_does_not_misalign_manifest_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, _ = prepare_run(
                root, names=('perf-exp', 'perf-sin', 'abi-public'), mode='nemu',
                selected=('perf-sin',))
            document = collect.collect(Path(config['run']))
            self.assertEqual(document['state'], 'passed')
            self.assertEqual(document['summary']['selected_cases'], 1)
            self.assertEqual(document['summary']['manifest_cases'], 3)
            self.assertEqual(document['cases'][0]['name'], 'perf-sin')

    def test_valid_gsim_marks_measurement_valid_and_keeps_rtl_trap(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, _ = prepare_run(root, names=('perf-sin',), mode='gsim')
            document = collect.collect(Path(config['run']))
            self.assertEqual(document['state'], 'passed')
            case = document['cases'][0]
            self.assertTrue(case['measurement_valid'])
            self.assertTrue(case['core_good_trap'])
            self.assertFalse(case['ref_only_trap'])

    def test_in_progress_is_a_progress_report_and_require_complete_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, _ = prepare_run(root, names=('perf-sin',), mode='nemu')
            write_runner_state(config['run'], 'in_progress')
            document = collect.collect(Path(config['run']))
            self.assertEqual(document['state'], 'in_progress')
            self.assertFalse(document['complete'])
            self.assertEqual(collect.main(['--run', str(config['run'])]), 0)
            self.assertEqual(collect.main(['--run', str(config['run']),
                                           '--require-complete']), 1)

    def test_collector_does_not_launch_any_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, _ = prepare_run(root, names=('perf-sin',), mode='nemu')
            with patch('subprocess.Popen') as popen:
                collect.collect(Path(config['run']))
            popen.assert_not_called()


class ParserBoundaryTests(unittest.TestCase):
    def test_duplicate_field_rejected(self):
        with self.assertRaises(ValueError):
            common.records('LIBMVEC_CHECK case=x checks=2 failed=1 failed=0', 'LIBMVEC_CHECK')

    def test_rtl_perf_timeout_counters_are_not_host_timeout(self):
        text='[PERF ][time=1692158] core.sbuffer: coh_timeout, 0\n[PERF ][time=1692158] core.ssit: reset_timeout, 16\n'
        self.assertFalse(common.error_markers(text))
        for diagnostic in ('timeout reached', 'command timed out', 'TIMEOUT'):
            self.assertTrue(common.error_markers(diagnostic))

    def test_explicit_fail_diagnostic_not_ignored(self):
        self.assertTrue(common.error_markers('FAIL symbol=x\nLIBMVEC_CHECK case=x failed=0'))
        self.assertFalse(common.error_markers('LIBMVEC_CHECK case=x checks=1 failed=0'))

    def test_missing_or_dead_launcher_is_aborted(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(collect.read_runner_state(d)['state'], 'aborted')
            common.write_json(Path(d)/'runner-state.json', {'state':'in_progress','launcher_pid':99999999})
            with patch.object(collect.os, 'kill', side_effect=ProcessLookupError):
                self.assertEqual(collect.read_runner_state(d)['state'], 'aborted')


class RecordIdentityTests(unittest.TestCase):
    def test_reject_wrong_sample_case_and_noninteger_checksum(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, _ = prepare_run(Path(d), names=('perf-sin',))
            case = cfg['metadata']['cases'][0]
            original = Path(case['stderr']).read_text()
            for mutated in (original.replace('LIBMVEC_SAMPLE case=perf-sin', 'LIBMVEC_SAMPLE case=wrong', 1),
                            original.replace('checksum=1\n', 'checksum=nan\n', 1)):
                Path(case['stderr']).write_text(mutated)
                self.assertEqual(collect.collect(Path(cfg['run']))['state'], 'failed')

    def test_config_required_for_accuracy_and_retained(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, _ = prepare_run(Path(d), names=('abi-public',))
            report = collect.collect(Path(cfg['run']))
            self.assertEqual(report['cases'][0]['vlen_bits'], 128)
            case = cfg['metadata']['cases'][0]
            text = Path(case['stderr']).read_text()
            Path(case['stderr']).write_text('\n'.join(line for line in text.splitlines() if not line.startswith('LIBMVEC_CONFIG')))
            self.assertEqual(collect.collect(Path(cfg['run']))['state'], 'failed')


class RejectionTests(unittest.TestCase):
    def _run(self, names=('perf-exp',), mode='nemu'):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config, _ = prepare_run(Path(temp.name), names=names, mode=mode)
        return config, Path(config['run'])

    def test_missing_sample_rejected(self):
        config, run = self._run(names=('perf-exp',))
        meta = case_by_name(
            json.loads((run / 'run-metadata.json').read_text()), 'perf-exp')
        lines = read_lines(meta['stderr'])
        lines = [line for line in lines if 'LIBMVEC_SAMPLE' not in line]
        write_lines(meta['stderr'], lines)
        document = collect.collect(run)
        self.assertEqual(document['state'], 'failed')
        self.assertTrue(any('missing' in reason or 'SAMPLE count' in reason
                            for reason in document['cases'][0]['reasons']))

    def test_duplicate_complete_and_check_rejected(self):
        for kind in ('COMPLETE', 'CHECK'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config, _ = prepare_run(root, names=('perf-sin',), mode='nemu')
                meta = case_by_name(
                    json.loads((Path(config['run']) / 'run-metadata.json').read_text()),
                    'perf-sin')
                if kind == 'COMPLETE':
                    extra = 'LIBMVEC_COMPLETE case=perf-sin status=pass'
                else:
                    extra = 'LIBMVEC_CHECK case=perf-sin checks=1000 failed=0'
                Path(meta['stderr']).write_text(
                    Path(meta['stderr']).read_text() + extra + '\n')
                document = collect.collect(Path(config['run']))
                self.assertEqual(document['state'], 'failed')
                self.assertTrue(any('exactly one' in reason
                                    for reason in document['cases'][0]['reasons']))

    def test_missing_guest_instruction_count_rejected(self):
        config, run = self._run(names=('perf-sin',), mode='nemu')
        meta = case_by_name(
            json.loads((run / 'run-metadata.json').read_text()), 'perf-sin')
        lines = [line for line in read_lines(meta['stdout'])
                 if 'total guest instructions' not in line]
        write_lines(meta['stdout'], lines)
        document = collect.collect(run)
        self.assertEqual(document['state'], 'failed')
        self.assertTrue(any('guest instruction' in reason
                            for reason in document['cases'][0]['reasons']))

    def test_reference_only_trap_rejected_for_gsim(self):
        config, run = self._run(names=('perf-sin',), mode='gsim')
        meta = case_by_name(
            json.loads((run / 'run-metadata.json').read_text()), 'perf-sin')
        Path(meta['stdout']).write_text('reference: HIT GOOD TRAP\n')
        document = collect.collect(run)
        self.assertEqual(document['state'], 'failed')
        case = document['cases'][0]
        self.assertTrue(case['ref_only_trap'])
        self.assertFalse(case['core_good_trap'])

    def test_nonzero_exit_and_timeout_rejected(self):
        for field, value in (('returncode', 1), ('timed_out', True)):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config, _ = prepare_run(root, names=('perf-sin',), mode='nemu')
                meta = case_by_name(
                    json.loads((Path(config['run']) / 'run-metadata.json').read_text()),
                    'perf-sin')
                result = json.loads(Path(meta['result']).read_text())
                result[field] = value
                common.write_json(meta['result'], result)
                document = collect.collect(Path(config['run']))
                self.assertEqual(document['state'], 'failed')

    def test_staged_hash_and_recorded_command_are_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, _ = prepare_run(root, names=('perf-sin',), mode='nemu')
            run = Path(config['run'])
            metadata_path = run / 'run-metadata.json'
            metadata = json.loads(metadata_path.read_text())
            meta = metadata['cases'][0]
            Path(meta['staged_bin']).write_bytes(b'tampered')
            document = collect.collect(run)
            self.assertEqual(document['state'], 'failed')
            self.assertTrue(any('sha256' in reason
                                for reason in document['cases'][0]['reasons']))

            config, _ = prepare_run(root, names=('perf-sin',), mode='nemu',
                                    output_name='run2')
            run = Path(config['run'])
            metadata_path = run / 'run-metadata.json'
            metadata = json.loads(metadata_path.read_text())
            metadata['cases'][0]['command'].append('--evil')
            common.write_json(metadata_path, metadata)
            document = collect.collect(run)
            self.assertEqual(document['state'], 'failed')
            self.assertTrue(any('command' in reason
                                for reason in document['cases'][0]['reasons']))

    def test_accuracy_missing_or_extra_abi_rejected(self):
        for mode in ('missing', 'extra'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config, _ = prepare_run(root, names=('abi-public',), mode='nemu')
                meta = case_by_name(
                    json.loads((Path(config['run']) / 'run-metadata.json').read_text()),
                    'abi-public')
                lines = read_lines(meta['stderr'])
                if mode == 'missing':
                    lines = [line for line in lines
                             if 'symbol=vec_exp_f64' not in line]
                else:
                    lines.append('LIBMVEC_ABI case=abi-public symbol=extra checks=1 failed=0')
                write_lines(meta['stderr'], lines)
                document = collect.collect(Path(config['run']))
                self.assertEqual(document['state'], 'failed')
                self.assertTrue(any('ABI' in reason
                                    for reason in document['cases'][0]['reasons']))

    def test_bad_cycle_arithmetic_rejected(self):
        config, run = self._run(names=('perf-sin',))
        meta = case_by_name(
            json.loads((run / 'run-metadata.json').read_text()), 'perf-sin')
        lines = read_lines(meta['stderr'])
        for index, line in enumerate(lines):
            if 'LIBMVEC_SAMPLE' in line:
                fields = line.split()
                cycles = next(field for field in fields if field.startswith('cycles='))
                lines[index] = line.replace(cycles, 'cycles=1')
                break
        write_lines(meta['stderr'], lines)
        document = collect.collect(run)
        self.assertEqual(document['state'], 'failed')
        self.assertTrue(any('cycles mismatch' in reason
                            for reason in document['cases'][0]['reasons']))


if __name__ == '__main__':
    unittest.main()
