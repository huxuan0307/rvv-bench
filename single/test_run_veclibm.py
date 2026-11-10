#!/usr/bin/env python3
"""Unit tests for evidence parsing and process handling, not NEMU substitutes.

Run: python3 -m unittest discover -s single -p test_run_veclibm.py -v
Real simulator smoke evidence is produced separately by run_veclibm.py.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('run_veclibm', Path(__file__).with_name('run_veclibm.py'))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class LogTests(unittest.TestCase):
    def audit(self, stdout='', stderr='', **overrides):
        args = dict(returncode=0, timed_out=False, max_instr=50_000_000,
                    required_markers=['VECLIBM_COMPLETE'], range_min=1_000_000,
                    range_max=30_000_000)
        args.update(overrides)
        return runner.analyze_logs(stdout, stderr, **args)

    def test_real_nemu_style_ansi_comma_count_and_uart_stderr(self):
        # Format transcribed from the real exp-n1 smoke; marker is test input.
        result = self.audit('\x1b[1;32mHIT GOOD TRAP\x1b[0m\n'
                            'total guest instructions = 6,004\n', 'VECLIBM_COMPLETE\n')
        self.assertTrue(result['functional_pass'])
        self.assertEqual(result['guest_instructions'], 6004)
        self.assertFalse(result['budget_met'])
        self.assertEqual(result['instruction_range_status'], 'below_range')

    def test_count_and_marker_on_either_stream(self):
        result = self.audit('VECLIBM_COMPLETE\n', 'HIT GOOD TRAP\ntotal guest instructions = 10,000,001\n')
        self.assertTrue(result['functional_pass'])
        self.assertTrue(result['budget_met'])

    def test_good_trap_alone_is_not_success(self):
        result = self.audit('HIT GOOD TRAP\ntotal guest instructions = 10,000,000')
        self.assertEqual(result['status'], 'missing_marker')
        self.assertFalse(result['functional_pass'])

    def test_missing_host_count_is_not_success(self):
        result = self.audit('HIT GOOD TRAP\nVECLIBM_COMPLETE')
        self.assertEqual(result['status'], 'missing_instruction_count')

    def test_error_in_other_stream_overrides_good_trap(self):
        for error in ['HIT CRITICAL ERROR', 'HIT BAD TRAP', 'ERROR: mismatch',
                      '[  FAILED  ] exp.special', 'Assertion failed', 'DIFFTEST FAILED']:
            with self.subTest(error=error):
                result = self.audit('HIT GOOD TRAP\nVECLIBM_COMPLETE\ntotal guest instructions = 10000000', error)
                self.assertEqual(result['status'], 'error')
                self.assertFalse(result['functional_pass'])

    def test_numeric_error_diagnostics_are_not_failure_labels(self):
        for diagnostic in ['Maximum observed absolute error is 1.0e-16',
                           'Maximum observed relative error is 2.0e-16',
                           'maximum observed absolute Error is 0.0',
                           'ULP error is 0.25']:
            with self.subTest(diagnostic=diagnostic):
                result = self.audit('HIT GOOD TRAP\nVECLIBM_COMPLETE\ntotal guest instructions = 10000000\n',
                                    diagnostic)
                self.assertTrue(result['functional_pass'])
                self.assertEqual(result['error_markers'], [])

    def test_error_labels_and_critical_errors_still_rejected(self):
        for diagnostic in ['ERROR: mismatch', 'error: mismatch', 'Error mismatch',
                           '[check] error: mismatch', '[src/file.c:20] [check] error mismatch',
                           'numeric check: ERROR detected', 'HIT CRITICAL ERROR',
                           'hit critical error']:
            with self.subTest(diagnostic=diagnostic):
                result = self.audit('HIT GOOD TRAP\nVECLIBM_COMPLETE\ntotal guest instructions = 10000000\n',
                                    diagnostic)
                self.assertFalse(result['functional_pass'])
                self.assertEqual(result['status'], 'error')

    def test_accuracy_failed_zero_counter_is_not_error(self):
        result = self.audit('HIT GOOD TRAP\ntotal guest instructions = 10000000\n',
                            'ACCURACY_COMPLETE passed=1 failed=0\n',
                            required_markers=['ACCURACY_COMPLETE'])
        self.assertTrue(result['functional_pass'])
        self.assertEqual(result['error_markers'], [])

    def test_accuracy_nonzero_and_malformed_failed_counters_rejected(self):
        for count in ['1', '01', '0x1', '0.1']:
            with self.subTest(count=count):
                result = self.audit('HIT GOOD TRAP\ntotal guest instructions = 10000000\n',
                                    f'ACCURACY_COMPLETE failed={count}\n',
                                    required_markers=['ACCURACY_COMPLETE'])
                self.assertEqual(result['status'], 'error')
                self.assertFalse(result['functional_pass'])

    def test_accuracy_zero_counter_does_not_hide_independent_fail(self):
        result = self.audit('HIT GOOD TRAP\ntotal guest instructions = 10000000\n',
                            'FAIL: wrong value\nACCURACY_COMPLETE failed=0\n',
                            required_markers=['ACCURACY_COMPLETE'])
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['functional_pass'])

    def test_timeout_overrides_good_trap(self):
        result = self.audit('HIT GOOD TRAP\nVECLIBM_COMPLETE\ntotal guest instructions = 10000000',
                            timed_out=True, returncode=-9)
        self.assertEqual(result['status'], 'timeout')
        self.assertFalse(result['functional_pass'])

    def test_limit_without_trap_is_budget_interruption(self):
        result = self.audit('total guest instructions = 50,000,000')
        self.assertEqual(result['status'], 'instruction_limit')
        self.assertTrue(result['instruction_budget_interrupted'])

    def test_explicit_limit_is_not_pass_even_with_good_trap(self):
        result = self.audit('HIT GOOD TRAP\nVECLIBM_COMPLETE\ntotal guest instructions = 10000000\n'
                            'instruction limit reached')
        self.assertEqual(result['status'], 'instruction_limit')

    def test_count_over_hard_limit_is_rejected(self):
        result = self.audit('HIT GOOD TRAP\nVECLIBM_COMPLETE\ntotal guest instructions = 50000001')
        self.assertEqual(result['status'], 'instruction_limit')

    def test_nonzero_exit_overrides_success(self):
        result = self.audit('HIT GOOD TRAP\nVECLIBM_COMPLETE\ntotal guest instructions = 10000000', returncode=1)
        self.assertEqual(result['status'], 'nonzero_exit')

    def test_multiple_markers_required(self):
        result = self.audit('HIT GOOD TRAP\nACCURACY_COMPLETE\ntotal guest instructions = 10000000',
                            required_markers=['ACCURACY_COMPLETE', 'cases=141'])
        self.assertEqual(result['missing_markers'], ['cases=141'])

    def test_range_boundaries(self):
        for count, within in [(999999, False), (1000000, True), (30000000, True), (30000001, False)]:
            with self.subTest(count=count):
                result = self.audit(f'HIT GOOD TRAP\nVECLIBM_COMPLETE\ntotal guest instructions = {count}')
                self.assertTrue(result['functional_pass'])
                self.assertEqual(result['budget_met'], within)


class ManifestTests(unittest.TestCase):
    def args(self, manifest=None, bins=None, globs=None, markers=None):
        return argparse.Namespace(manifest=manifest, bins=bins, glob=globs, require_marker=markers)

    def test_cases_relative_bin_and_inherited_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'x.bin').write_bytes(b'raw')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'completion_marker': 'ACCURACY_COMPLETE',
                                           'cases': [{'name': 'exp.special', 'bin': 'x.bin'}]}))
            cases = runner.load_cases(self.args(manifest))
            self.assertEqual(cases[0]['bin'], str(root / 'x.bin'))
            self.assertEqual(cases[0]['required_markers'], ['ACCURACY_COMPLETE'])

    def test_entries_elf_and_directory_resolution(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'x.bin').write_bytes(b'raw')
            (root / 'x.elf').write_bytes(b'\x7fELF')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'entries': [{'name': 'x', 'elf': 'x.elf'},
                                                        {'id': 'x', 'directory': '.'}]}))
            cases = runner.load_cases(self.args(manifest))
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]['bin'], str(root / 'x.bin'))
            self.assertEqual(cases[0]['required_markers'], ['VECLIBM_COMPLETE'])

    def test_default_marker_replaced_and_manifest_marker_added(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'cases': [{'bin': 'x.bin', 'marker': 'ACCURACY_COMPLETE'}]}))
            cases = runner.load_cases(self.args(manifest, markers=['PASS']))
            self.assertEqual(cases[0]['required_markers'], ['ACCURACY_COMPLETE', 'PASS'])

    def test_directory_multiple_bins_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ['a.bin', 'b.bin']:
                (root / name).write_bytes(b'raw')
            with self.assertRaises(ValueError):
                runner.image_from({'directory': '.'}, root)

    def test_unmatched_glob_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                runner.load_cases(self.args(globs=[str(Path(temp) / '*.bin')]))

    def test_no_selection_rejected(self):
        with self.assertRaises(ValueError):
            runner.load_cases(self.args())


class ProcessTests(unittest.TestCase):
    def test_timeout_kills_and_reaps_process_group(self):
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            with patch.object(runner.subprocess, 'Popen') as popen, patch.object(runner.os, 'killpg') as killpg:
                process = popen.return_value
                process.pid = 1234
                process.wait.side_effect = [subprocess.TimeoutExpired(['nemu'], 0.1), -9]
                result = runner.run_command(['nemu', '-b', 'x.bin'], stdout, stderr, 0.1)
                self.assertTrue(result['timed_out'])
                self.assertEqual(result['returncode'], -9)
                killpg.assert_called_once_with(1234, runner.signal.SIGKILL)
                self.assertEqual(process.wait.call_count, 2)

    def test_launch_error_preserved(self):
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            with patch.object(runner.subprocess, 'Popen', side_effect=FileNotFoundError('missing nemu')):
                result = runner.run_command(['missing'], stdout, stderr, 1)
                self.assertIn('missing nemu', result['launch_error'])
                self.assertIsNone(result['returncode'])

    def test_full_cli_serializes_explicit_bin_paths_and_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / 'tiny.bin'
            image.write_bytes(b'raw image')
            output = root / 'evidence'
            def fake_run(command, stdout, stderr, timeout):
                stdout.write('HIT GOOD TRAP\ntotal guest instructions = 10000000\n')
                stderr.write('VECLIBM_COMPLETE\n')
                return {'returncode': 0, 'timed_out': False, 'launch_error': None, 'elapsed_seconds': 0.1}
            with patch.object(runner, 'run_command', side_effect=fake_run), patch('builtins.print'):
                status = runner.main(['--bins', str(image), '--nemu', str(root / 'nemu'), '--output', str(output)])
            self.assertEqual(status, 0)
            result = json.loads((output / 'results.json').read_text())
            self.assertEqual(result['config']['bins'], [str(image)])
            self.assertTrue(result['cases'][0]['functional_pass'])
            self.assertEqual(result['cases'][0]['guest_instructions'], 10000000)
            self.assertTrue((output / 'results.csv').is_file())
            self.assertTrue((output / 'summary.md').is_file())

    def test_manifest_failed_build_does_not_execute_stale_bin(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / 'stale.bin'
            image.write_bytes(b'previous successful build')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'cases': [{'name': 'exp', 'bin': str(image), 'status': 'failed'}]}))
            output = root / 'evidence'
            with patch.object(runner, 'run_command') as execute, patch('builtins.print'):
                status = runner.main(['--manifest', str(manifest), '--output', str(output)])
            self.assertEqual(status, 1)
            execute.assert_not_called()
            result = json.loads((output / 'results.json').read_text())['cases'][0]
            self.assertIn('not built', result['launch_error'])
            self.assertFalse(result['functional_pass'])

    def test_manifest_built_matching_sha256_executes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / 'good.bin'
            image.write_bytes(b'fixed contents')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'cases': [{'bin': str(image), 'status': 'built', 'sha256': runner.sha256(image)}]}))
            output = root / 'evidence'
            def fake_run(command, stdout, stderr, timeout):
                stdout.write('HIT GOOD TRAP\ntotal guest instructions = 10000000\n')
                stderr.write('VECLIBM_COMPLETE\n')
                return {'returncode': 0, 'timed_out': False, 'launch_error': None, 'elapsed_seconds': 0.1}
            with patch.object(runner, 'run_command', side_effect=fake_run) as execute, patch('builtins.print'):
                status = runner.main(['--manifest', str(manifest), '--output', str(output)])
            self.assertEqual(status, 0)
            execute.assert_called_once()

    def test_manifest_sha256_mismatch_does_not_execute(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / 'changed.bin'
            image.write_bytes(b'changed contents')
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'cases': [{'bin': str(image), 'status': 'built', 'sha256': '0' * 64}]}))
            output = root / 'evidence'
            with patch.object(runner, 'run_command') as execute, patch('builtins.print'):
                status = runner.main(['--manifest', str(manifest), '--output', str(output)])
            self.assertEqual(status, 1)
            execute.assert_not_called()
            result = json.loads((output / 'results.json').read_text())['cases'][0]
            self.assertIn('sha256 mismatch', result['launch_error'])
            self.assertEqual(result['sha256'], runner.sha256(image))

    def test_existing_output_directory_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(runner, 'execute') as execute, patch.object(sys, 'stderr'):
                with self.assertRaises(SystemExit) as failure:
                    runner.main(['--bins', 'nonexistent.bin', '--output', temp])
                self.assertEqual(failure.exception.code, 2)
                execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
