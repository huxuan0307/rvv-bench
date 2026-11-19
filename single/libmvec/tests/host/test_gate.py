#!/usr/bin/env python3
"""主机自包含单测：不依赖 RISC-V、NEMU 或真实 oracle。"""
import argparse
import csv
from pathlib import Path
import shlex
import tempfile
import types
import unittest
from unittest.mock import patch

import importlib.util
import sys
from unittest.mock import patch as _module_patch


def _load_script(name, relative):
    path = Path(__file__).resolve().parents[2] / 'script' / relative
    spec = importlib.util.spec_from_file_location('_libmvec_host_' + name, path)
    module = importlib.util.module_from_spec(spec)
    # dataclass 检查需要临时模块登记，不修改 sys.path。
    with _module_patch.dict(sys.modules, {spec.name: module}):
        spec.loader.exec_module(module)
    return module

gate = _load_script('gate', 'accuracy/gate.py')


def nx_log(f):
    n, limit = gate.FIXTURES[f][1:3]
    ns = len(gate.semantic_pairs(f)[1])
    lines = []
    for m in (1, 2):
        lines.append(f'NX_MPFR fn={f} lmul={m} count={n} limit={limit} forbidden_calls=0')
        lines.append(f'NX_ABI fn={f} lmul={m} semantics={ns} calls={ns * 8} incoming_vl=0,1,vlmax-1,vlmax mixed=1 preserved=v1-v7,v24-v31 caller_strict=1')
    lines.append(f'NX_COMPLETE fn={f} checks=19 failed=0 status=pass')
    return '\n'.join(lines)


def array_log(f):
    n, limit = gate.FIXTURES[f][1:3]
    ns = len(gate.semantic_pairs(f)[1])
    lines = []
    for m in (1, 2):
        lines.append(f'UNIT_MPFR fn={f} mode=m{m} count={n} limit={limit} hot_fallback=0')
        lines.append(f'UNIT_SEMANTICS fn={f} mode=m{m} isolated={ns} mixed={ns} inplace={ns} failed=0')
    lines.append(f'UNIT_COMPLETE fn={f} checks=19 failed=0 status=pass')
    return '\n'.join(lines)


def trig_log():
    n, limit = gate.FIXTURES['sin'][1:3]
    ns = len(gate.semantic_pairs('sin')[1])
    lines = []
    for f in (0, 1):
        for m in (1, 2):
            lines.append(f'TRIG_MPFR function={f} mode=m{m} count={n} limit={limit} ordinary_fallback=0')
            lines.append(f'TRIG_SEMANTICS function={f} mode=m{m} isolated={ns} mixed={ns} tails=66 failed=0')
    lines.append('TRIG_COMPLETE checks=42 failed=0 status=pass')
    return '\n'.join(lines)


def pow_log():
    n, limit = gate.FIXTURES['pow'][1:3]
    ns = len(gate.semantic_pairs('pow')[1])
    lines = []
    for m in (1, 2):
        lines.append(f'POWVEC_MPFR mode=m{m} count={n} limit={limit} normal_fallback=0')
        lines.append(f'POWVEC_SEMANTICS mode=m{m} isolated={ns} mixed={ns} tails={ns} failed=0')
    lines.append('POWVEC_COMPLETE checks=42 failed=0 status=pass')
    return '\n'.join(lines)


class HostTests(unittest.TestCase):
    def test_nx_pass_and_reject_missing_lmul(self):
        for f in gate.FUNCTIONS:
            text = nx_log(f)
            gate.validate_log('nx-' + f, text, gate.FUNCTIONS)
            lines = text.splitlines()
            with self.assertRaises(ValueError):
                gate.validate_log('nx-' + f, '\n'.join(lines[2:]), gate.FUNCTIONS)
            with self.assertRaises(ValueError):
                gate.validate_log('nx-' + f, text.replace('count=', 'count=0', 1), gate.FUNCTIONS)
            with self.assertRaises(ValueError):
                gate.validate_log('nx-' + f, text + '\n' + lines[0], gate.FUNCTIONS)
            with self.assertRaises(ValueError):
                gate.validate_log('nx-' + f, text + '\nNX_ERROR kind=test', gate.FUNCTIONS)

    def test_array_pass_and_reject_missing_cases(self):
        for f in ('exp', 'expf', 'log', 'logf'):
            text = array_log(f)
            gate.validate_log('array-' + f, text, gate.FUNCTIONS)
            with self.assertRaises(ValueError):
                gate.validate_log('array-' + f, text.replace('hot_fallback=0', 'hot_fallback=1', 1), gate.FUNCTIONS)
        for tag, text in [('array-trig', trig_log()), ('array-pow', pow_log())]:
            gate.validate_log(tag, text, gate.FUNCTIONS)
            with self.assertRaises(ValueError):
                gate.validate_log(tag, '\n'.join(text.splitlines()[1:]), gate.FUNCTIONS)
            with self.assertRaises(ValueError):
                gate.validate_log(tag, text.replace('tails=', 'tails=0', 1), gate.FUNCTIONS)

    def test_trap_and_summary_rejections(self):
        for suffix in ('\nHIT BAD TRAP', '\nNX_UNSUPPORTED reason=x', '\nNX_COMPLETE fn=sin checks=0 failed=0 status=pass'):
            with self.assertRaises(ValueError):
                gate.validate_log('nx-sin', nx_log('sin') + suffix, gate.FUNCTIONS)
        with self.assertRaises(ValueError):
            gate.validate_log('nx-sin', nx_log('sin').replace('checks=19', 'checks=0'), gate.FUNCTIONS)

    def test_function_selection(self):
        self.assertEqual(gate.choose_functions('exp'), ['exp'])
        for invalid in ('sin', 'cos', 'sin,sin', 'exp,unknown', ''):
            with self.assertRaises(ValueError):
                gate.choose_functions(invalid)

    def test_semantic_sources(self):
        self.assertEqual({f: len(gate.semantic_pairs(f)[1]) for f in gate.FUNCTIONS},
                         {'sin': 28, 'cos': 28, 'log': 24, 'logf': 25, 'exp': 23, 'expf': 25, 'pow': 30})

    def test_path_rules(self):
        self.assertFalse(gate.path_not_build('/a/build/x'))
        self.assertFalse(gate.path_not_build('/a/my-build/x'))
        self.assertTrue(gate.path_not_build('/a/runs/20261114-164600-gate'))
        self.assertIsNotNone(gate.DATE_DIR.search('20261114-164600-gate'))
        self.assertIsNone(gate.DATE_DIR.search('gate'))

    def test_unknown_or_corrupted_header(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'exp-unit.h'
            p.write_text('#define UNIT_COUNT 102039\n#define UNIT_LIMIT 1\n')
            with self.assertRaises(ValueError):
                gate.verify_header(p, 'exp')

    def test_generated_pow_header(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'case.h'
            self.assertEqual(gate.create_nx_header(p, Path(d), 'pow'), 30)
            self.assertIn('#define ORACLE_Z powvec_unit_z', p.read_text())
            self.assertIn('#define NX2 _ZGV2Nxvv_pow', p.read_text())

    @staticmethod
    def stage_fixture(root):
        root = Path(root)
        run = root / '20261114-190000-stage-test'
        (run / 'staging').mkdir(parents=True)
        elf, binary, archive = root/'unit.elf', root/'unit.bin', root/'math.a'
        elf.write_bytes(b'elf'); binary.write_bytes(b'bin'); archive.write_bytes(b'archive')
        record = dict(test='nx-log', elf=str(elf), elf_sha256=gate.sha(elf), binary=str(binary),
                      binary_sha256=gate.sha(binary), staged_binary=str(run/'staging/nx-log.bin'),
                      staged_binary_sha256=gate.sha(binary))
        return run, record, archive, gate.sha(archive)

    def test_staged_binary_copy_and_identity(self):
        with tempfile.TemporaryDirectory() as d:
            run, record, archive, archive_hash = self.stage_fixture(d)
            staged = gate.stage_binary(run, record['test'], record['binary'], record['binary_sha256'])
            self.assertEqual(staged, run/'staging/nx-log.bin')
            self.assertEqual(staged.read_bytes(), b'bin')
            gate.verify_artifacts(run, record, archive, archive_hash)
            with self.assertRaises(FileExistsError):
                gate.stage_binary(run, record['test'], record['binary'], record['binary_sha256'])

    def test_each_artifact_mutation_is_rejected(self):
        for changed in ('elf', 'binary', 'staged_binary', 'archive'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as d:
                run, record, archive, archive_hash = self.stage_fixture(d)
                gate.stage_binary(run, record['test'], record['binary'], record['binary_sha256'])
                Path(archive if changed == 'archive' else record[changed]).write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    gate.verify_artifacts(run, record, archive, archive_hash)

    def test_stage_paths_reject_traversal_symlinks_and_build(self):
        with tempfile.TemporaryDirectory() as d:
            run, record, archive, archive_hash = self.stage_fixture(d)
            for tag in ('../escape', '/absolute', 'a/b', 'a\\\\b', '', '.', '..'):
                with self.subTest(tag=tag), self.assertRaises(ValueError):
                    gate.staged_path(run, tag)
            other = Path(d)/'outside.bin'
            other.write_bytes(b'outside')
            (run/'staging/nx-log.bin').symlink_to(other)
            with self.assertRaises(ValueError):
                gate.stage_binary(run, 'nx-log', record['binary'], record['binary_sha256'])
            self.assertEqual(other.read_bytes(), b'outside')
            build_run = Path(d)/'build'/'20261114-190000-unsafe'
            (build_run/'staging').mkdir(parents=True)
            with self.assertRaises(ValueError):
                gate.staged_path(build_run, 'nx-log')
            other_run = Path(d)/'20261114-190001-unsafe'
            other_run.mkdir()
            (other_run/'staging').symlink_to(run/'staging', target_is_directory=True)
            with self.assertRaises(ValueError):
                gate.staged_path(other_run, 'nx-log')

    def test_staging_detects_wrong_record_or_initial_hash(self):
        with tempfile.TemporaryDirectory() as d:
            run, record, archive, archive_hash = self.stage_fixture(d)
            with self.assertRaises(ValueError):
                gate.stage_binary(run, 'nx-log', record['binary'], 'bad-sha')
            gate.stage_binary(run, 'nx-log', record['binary'], record['binary_sha256'])
            record['staged_binary'] = str(run/'staging/other.bin')
            with self.assertRaises(ValueError):
                gate.verify_artifacts(run, record, archive, archive_hash)

    def run_mock_gate(self, root, *, build_only=False, mutate=False):
        root = Path(root)
        gcc, archive, libm, nemu = (root/name for name in ('gcc', 'math.a', 'libm.a', 'nemu'))
        for p in (gcc, archive, libm, nemu):
            p.write_bytes(b'dummy')
        tools = {name: root/name for name in ('readelf', 'objcopy', 'objdump', 'libc.a', 'libgcc.a')}
        for p in tools.values():
            p.write_bytes(b'dummy')
        headers = root/'headers'
        headers.mkdir()
        (headers/'log-unit.h').write_text('mock oracle')
        run = root/'20261114-190100-host-mock'
        args = argparse.Namespace(functions='log', build_dir=root/'artifacts', run_dir=run, headers_dir=headers,
                                  new_archive=archive, gcc=gcc, scalar_libm=libm, nemu=None if build_only else nemu,
                                  max_instr=123, timeout=5, lto=False, build_only=build_only)
        calls = []

        def fake(argv, *, cwd, env, stdout, stderr, timeout):
            calls.append(list(argv))
            self.assertEqual(Path(cwd), run)
            for key in ('TMPDIR', 'TMP', 'TEMP'):
                self.assertEqual(Path(env[key]).parent, run/'tmp')
            if argv[0] == str(gcc):
                query = argv[1]
                if query.startswith('-print-prog-name='):
                    stdout.write(str(tools[query.split('=', 1)[1]]))
                elif query.startswith('-print-file-name='):
                    stdout.write(str(tools[query.split('=', 1)[1]]))
                elif '-o' in argv:
                    Path(argv[argv.index('-o')+1]).write_bytes(b'fake elf/object')
            elif argv[0] == str(tools['readelf']):
                stdout.write(' 1: 00000008 4 TLS GLOBAL DEFAULT 1 errno\n')
            elif argv[0] == str(tools['objcopy']):
                Path(argv[-1]).write_bytes(b'fake binary')
            elif argv[0] == str(nemu):
                staged = Path(argv[-1])
                self.assertEqual(staged.parent, run/'staging')
                self.assertEqual(staged.read_bytes(), b'fake binary')
                if mutate in ('stage', 'stage-and-rc'):
                    staged.write_bytes(b'tampered')
                if mutate == 'prior-case' and staged.name == 'array-log.bin':
                    (run/'staging/nx-log.bin').write_bytes(b'tampered after prior case')
                stdout.write('HIT GOOD TRAP\n')
                stderr.write(nx_log('log') if staged.name == 'nx-log.bin' else array_log('log'))
                return types.SimpleNamespace(returncode=1 if mutate == 'stage-and-rc' else 0)
            return types.SimpleNamespace(returncode=0)

        with patch.object(gate, 'verify_header'), patch.object(gate.subprocess, 'run', side_effect=fake):
            if mutate:
                with self.assertRaises(ValueError):
                    gate.run_gate(args)
            else:
                gate.run_gate(args)
        with (run/'tests.csv').open(newline='') as f:
            tests = list(csv.DictReader(f))
        with (run/'commands.csv').open(newline='') as f:
            commands = list(csv.DictReader(f))
        with (run/'coverage.csv').open(newline='') as f:
            coverage = list(csv.DictReader(f))
        return run, tests, commands, coverage, calls

    def test_mocked_runs_use_staged_images_and_report(self):
        for build_only in (False, True):
            with self.subTest(build_only=build_only), tempfile.TemporaryDirectory() as d:
                run, tests, commands, coverage, calls = self.run_mock_gate(d, build_only=build_only)
                self.assertEqual(2, len(tests))
                for record in tests:
                    self.assertEqual(record['status'], 'built' if build_only else 'pass')
                    self.assertEqual(Path(record['staged_binary']), run/'staging'/f"{record['test']}.bin")
                    self.assertEqual(record['binary_sha256'], record['staged_binary_sha256'])
                    self.assertEqual(gate.sha(record['staged_binary']), record['staged_binary_sha256'])
                nemu = [row for row in commands if row['label'].endswith('-nemu')]
                self.assertEqual(len(nemu), 0 if build_only else 2)
                for row in nemu:
                    self.assertEqual(Path(shlex.split(row['command'])[-1]).parent, run/'staging')
                self.assertTrue(all(row['status'] == ('not-run' if build_only else 'pass') for row in coverage))
                if build_only:
                    self.assertIn('仅构建，未运行，不是 pass', (run/'summary.md').read_text())

    def test_mocked_run_rejects_stage_mutation_after_launch(self):
        with tempfile.TemporaryDirectory() as d:
            run, tests, commands, coverage, calls = self.run_mock_gate(d, mutate='stage')
            self.assertEqual(len(tests), 1)
            self.assertEqual(tests[0]['status'], 'fail')
            self.assertTrue(all(row['status'] == 'not-run' for row in coverage))
            self.assertFalse((run/'summary.md').exists())
            nemu = [row for row in commands if row['label'] == 'nx-log-nemu']
            self.assertEqual(len(nemu), 1)
            self.assertEqual(nemu[0]['status'], '0')
            self.assertEqual(Path(shlex.split(nemu[0]['command'])[-1]), run/'staging/nx-log.bin')

    def test_mocked_failure_still_rechecks_staged_image(self):
        with tempfile.TemporaryDirectory() as d:
            run, tests, commands, coverage, calls = self.run_mock_gate(d, mutate='stage-and-rc')
            self.assertEqual(tests[0]['status'], 'fail')
            self.assertEqual([r['status'] for r in commands if r['label'] == 'nx-log-nemu'], ['1'])
            self.assertTrue(all(row['status'] == 'not-run' for row in coverage))
            self.assertFalse((run/'summary.md').exists())

    def test_final_audit_rechecks_earlier_case(self):
        with tempfile.TemporaryDirectory() as d:
            run, tests, commands, coverage, calls = self.run_mock_gate(d, mutate='prior-case')
            self.assertEqual([(r['test'], r['status']) for r in tests], [('nx-log', 'fail'), ('array-log', 'pass')])
            self.assertEqual(len([r for r in commands if r['label'].endswith('-nemu')]), 2)
            self.assertFalse((run/'summary.md').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
