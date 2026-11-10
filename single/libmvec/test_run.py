"""Host-side tests for the libmvec runner and shared strict helpers.

No NEMU or gsim process is ever launched.  Fake ELF files satisfy the tool
identity checks; the runner's subprocess boundary is patched where needed.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import runner_common as common  # noqa: E402
import run as runner  # noqa: E402


def make_elf(path, *, etype=3, interp=True, machine=243):
    """Write a minimal 64-bit little-endian ELF accepted by the validators."""
    path = Path(path)
    header = bytearray(64)
    header[0:4] = b'\x7fELF'
    header[4] = 2  # 64-bit
    header[5] = 1  # little endian
    header[6] = 1
    header[16:18] = etype.to_bytes(2, 'little')
    header[18:20] = machine.to_bytes(2, 'little')
    header[20:24] = (1).to_bytes(4, 'little')
    header[32:40] = (64 if interp else 0).to_bytes(8, 'little')
    header[54:56] = (56).to_bytes(2, 'little')
    header[56:58] = (1 if interp else 0).to_bytes(2, 'little')
    body = b''
    if interp:
        program = bytearray(56)
        program[0:4] = (3).to_bytes(4, 'little')  # PT_INTERP
        body = bytes(program)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(header) + body)
    path.chmod(0o755)
    return path


def make_tools(root):
    root = Path(root)
    emu = make_elf(root / 'emu-gsim-test', etype=3, interp=True)
    reference = make_elf(root / 'reference.so', etype=3, interp=False)
    nemu = make_elf(root / 'nemu-test', etype=2, interp=False)
    dram = root / 'XiangShan.ini'
    dram.write_text('[DRAMsim3]\n')
    return {'emu': emu, 'reference': reference, 'nemu': nemu, 'dram': dram}


def fake_reference_validation(path=None):
    """Pretend the synthetic ET_DYN exports the required difftest interface."""
    return {'required': list(common.REFERENCE_REQUIRED_SYMBOLS),
            'found': list(common.REFERENCE_REQUIRED_SYMBOLS),
            'nm': 'fake-nm', 'nm_sha256': '0' * 64}


def manifest_document(root, names):
    root = Path(root)
    bin_dir = root / 'bin'
    bin_dir.mkdir(parents=True, exist_ok=True)
    document = {
        'schema_version': 1, 'suite': 'libmvec-xs', 'build_status': 'built',
        'created_at': '2026-11-10T00:00:00+00:00',
        'toolchain': {'version': 'host-test'},
        'sources': {'fixture.c': {'path': 'fixture.c'}},
        'cases': [],
    }
    for name in names:
        image = bin_dir / f'{name}.bin'
        image.write_bytes(('fixture ' + name).encode())
        digest = common.sha256(image)
        if name.startswith('perf-'):
            function = name[5:]
            wide = function in ('exp', 'log')
            batches = [1, 4, 16] if wide else [1, 4]
            rounds = 10
            sample_count = 6 * 3 * len(batches) * rounds
            expected = {
                'function': function, 'modes': list(common.MODES),
                'lengths': [16, 128, 1024], 'batches': batches,
                'phases': {'first': 1, 'warmup': 2, 'steady': 7},
                'sample_count': sample_count,
                'calibration_count': 3 * 3 * len(batches) * rounds,
                'calibration_modes': ['rdcycle', 'empty', 'copy'],
                'order_policy': 'rotate-all-9', 'min_checks': 1000,
            }
            suite = 'performance'
        elif name.startswith('abi-'):
            expected = {'min_checks': 100,
                        'abi_symbols': ['vec_exp_f64', 'vec_sin_f64']}
            suite = 'accuracy'
        else:
            raise ValueError(name)
        document['cases'].append({
            'name': name, 'suite': suite, 'status': 'built',
            'bin': f'bin/{name}.bin', 'sha256': digest,
            'required_markers': [f'LIBMVEC_COMPLETE case={name} status=pass'],
            'expected': expected,
        })
    path = root / 'manifest.json'
    path.write_text(json.dumps(document, indent=2))
    return path


def guest_text(snapshot_case, cycle_base=None):
    name = snapshot_case['name']
    plan = snapshot_case['expected']
    if cycle_base is None:
        cycle_base = 2 ** 60
    lines = []
    if snapshot_case['suite'] == 'performance':
        lines.append(
            f'LIBMVEC_CONFIG case={name} function={plan["function"]} '
            'vlen_bits=128 seed=123456789 rounds=10')
        index = 0
        for row in common.rotation_schedule(plan):
            begin = cycle_base + index * 1000000
            if row['kind'] == 'sample':
                cycles = row['n'] * 3 + row['k'] * 7 + index + 1
            elif row['mode'] == 'rdcycle':
                cycles = 0
            else:
                cycles = row['n'] + index + 2
            end = begin + cycles
            prefix = 'LIBMVEC_SAMPLE' if row['kind'] == 'sample' else 'LIBMVEC_CALIBRATION'
            function = f' function={plan["function"]}' if row['kind'] == 'sample' or True else ''
            lines.append(
                f'{prefix} case={name}{function} mode={row["mode"]} '
                f'n={row["n"]} k={row["k"]} phase={row["phase"]} '
                f'round={row["round"]} order={row["order"]} '
                f'cycle_begin={begin} cycle_end={end} cycles={cycles} '
                f'checksum={index + 1}')
            index += 1
    else:
        lines.append(f'LIBMVEC_CONFIG case={name} function=abi vlen_bits=128 seed=123456789 rounds=0')
        for symbol in plan['abi_symbols']:
            lines.append(
                f'LIBMVEC_ABI case={name} symbol={symbol} checks=100 failed=0')
    lines.append(f'LIBMVEC_CHECK case={name} checks={plan["min_checks"]} failed=0')
    lines.append(f'LIBMVEC_COMPLETE case={name} status=pass')
    return '\n'.join(lines) + '\n'


def trap_text(mode, guest):
    if mode == 'gsim':
        return ('Core 0: HIT GOOD TRAP at pc = 0x0000000080000000\n', guest)
    return ('nemu: HIT GOOD TRAP at pc = 0x0000000080000000\n'
            'total guest instructions = 12,345\n'
            'NEMU exit with good state\n', guest)


def args_from_argv(argv):
    parser = runner.build_parser()
    args = parser.parse_args(argv)
    return runner.validate_arguments(parser, args)


def base_argv(mode, manifest, output, tools, selected=(), smoke=False):
    argv = [mode, '--manifest', str(manifest), '--output', str(output),
            '--jobs', '1']
    if mode == 'gsim':
        argv += ['--emu', str(tools['emu']), '--reference', str(tools['reference']),
                 '--dramsim3-ini', str(tools['dram']), '--timeout', '48h']
        if not smoke:
            argv.append('--no-smoke')
    else:
        argv += ['--nemu', str(tools['nemu']), '--timeout', '120s',
                 '--max-instr', '1000000']
    for name in selected:
        argv += ['--case', name]
    return argv


def prepare_run(root, names=('perf-exp', 'perf-sin', 'abi-public'), mode='nemu',
                selected=(), tools=None, output_name='run'):
    root = Path(root)
    tools = tools or make_tools(root)
    manifest = manifest_document(root, names)
    args = args_from_argv(base_argv(mode, manifest, root / output_name, tools, selected))
    with patch.object(common, 'validate_reference', fake_reference_validation):
        config = runner.prepare(args)
    run_dir = Path(config['run'])
    snapshot = common.load_manifest(run_dir / 'manifests' / 'manifest.json',
                                    check_files=False)
    by_name = {case['name']: case for case in snapshot['cases']}
    for meta_case in config['metadata']['cases']:
        snapshot_case = by_name[meta_case['name']]
        stdout, stderr = trap_text(mode, guest_text(snapshot_case))
        Path(meta_case['stdout']).write_text(stdout)
        Path(meta_case['stderr']).write_text(stderr)
        common.write_json(meta_case['result'], {
            'schema_version': 1, 'name': meta_case['name'],
            'suite': meta_case['suite'], 'state': 'completed',
            'command': meta_case['command'], 'cwd': meta_case['workdir'],
            'stdout': meta_case['stdout'], 'stderr': meta_case['stderr'],
            'returncode': 0, 'timed_out': False, 'launch_error': None,
            'aborted': False, 'elapsed_seconds': 0.01,
            'started_at': common.utc_now(), 'finished_at': common.utc_now(),
        })
    write_runner_state(run_dir, 'terminated', returncode=0)
    return config, snapshot


def write_runner_state(run_dir, state, returncode=0):
    run_dir = Path(run_dir)
    record = {'schema_version': 1, 'state': state, 'mode': 'test',
              'selected_cases': len(json.loads((run_dir / 'run-metadata.json').read_text())['cases']),
              'started_at': common.utc_now(), 'finished_at': common.utc_now()}
    if state != 'in_progress':
        record['returncode'] = returncode
    common.write_json(run_dir / 'runner-state.json', record)


class FakeLaunch:
    def __init__(self, snapshot, failure_name=None):
        self.by_name = {case['name']: case for case in snapshot['cases']}
        self.failure_name = failure_name

    def __call__(self, command, cwd, stdout_path, stderr_path, timeout, registry):
        name = Path(command[-1]).name[:-4]
        snapshot_case = self.by_name[name]
        mode = 'gsim' if '--diff' in command else 'nemu'
        stdout, stderr = trap_text(mode, guest_text(snapshot_case))
        if name == self.failure_name:
            stdout, stderr = trap_text(mode, 'LIBMVEC_ERROR case=%s status=fail\n' % name)
        Path(stdout_path).write_text(stdout)
        Path(stderr_path).write_text(stderr)
        return {'returncode': 0, 'timed_out': False, 'launch_error': None,
                'elapsed_seconds': 0.01, 'aborted': False}


class ManifestTests(unittest.TestCase):
    def test_requires_explicit_manifest(self):
        with redirect_stderr(io_stub()):
            with self.assertRaises(SystemExit) as error:
                runner.build_parser().parse_args(['nemu'])
        self.assertEqual(error.exception.code, 2)

    def test_strict_manifest_rejects_bad_sample_count_and_batches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = manifest_document(root, ('perf-exp',))
            doc = json.loads(path.read_text())
            doc['cases'][0]['expected']['sample_count'] = 1
            path.write_text(json.dumps(doc))
            with self.assertRaises(common.ManifestError):
                common.load_manifest(path)
            path = manifest_document(root, ('perf-exp',))
            doc = json.loads(path.read_text())
            doc['cases'][0]['expected']['batches'] = [1]
            path.write_text(json.dumps(doc))
            with self.assertRaises(common.ManifestError):
                common.load_manifest(path)

    def test_manifest_hash_and_marker_are_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = manifest_document(root, ('perf-sin',))
            doc = json.loads(path.read_text())
            doc['cases'][0]['sha256'] = '0' * 64
            path.write_text(json.dumps(doc))
            with self.assertRaises(common.ManifestError):
                common.load_manifest(path)
            path = manifest_document(root, ('perf-sin',))
            doc = json.loads(path.read_text())
            doc['cases'][0]['required_markers'] = ['LIBMVEC_COMPLETE']
            path.write_text(json.dumps(doc))
            with self.assertRaises(common.ManifestError):
                common.load_manifest(path)

    def test_manifest_rejects_duplicate_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = manifest_document(root, ('perf-sin',))
            doc = json.loads(path.read_text())
            doc['cases'].append(dict(doc['cases'][0]))
            path.write_text(json.dumps(doc))
            with self.assertRaises(common.ManifestError):
                common.load_manifest(path)


class ToolAndScheduleTests(unittest.TestCase):
    def test_select_gsim_excludes_static_and_json_and_picks_newest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            older = make_elf(root / 'emu-gsim-old')
            newest = make_elf(root / 'emu-gsim-new')
            static = make_elf(root / 'emu-gsim-new-static')
            json_file = root / 'emu-gsim-new.json'
            json_file.write_text('{}')
            os.utime(older, (1, 1))
            os.utime(newest, (2, 2))
            os.utime(static, (3, 3))
            os.utime(json_file, (4, 4))
            self.assertEqual(common.select_gsim(root), newest.resolve())

    def test_select_reference_requires_et_dyn(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            make_elf(root / 'not-shared', etype=2, interp=False)
            good = make_elf(root / 'ref.bin', etype=3, interp=False)
            with patch.object(common, 'validate_reference', fake_reference_validation):
                self.assertEqual(common.select_reference(root), good.resolve())

    def test_rotation_schedule_uses_nine_slots_and_correct_r(self):
        plan = common.expected_plan(
            {'name': 'perf-exp', 'expected': {
                'function': 'exp', 'modes': list(common.MODES),
                'lengths': [16, 128, 1024], 'batches': [1, 4, 16],
                'phases': {'first': 1, 'warmup': 2, 'steady': 7},
                'sample_count': 540, 'calibration_count': 270,
                'calibration_modes': list(common.CALIBRATION_MODES),
                'order_policy': 'rotate-all-9', 'min_checks': 1}},
            'performance', 'perf-exp')
        counts = common.expected_counts(plan)
        self.assertEqual(counts, {'sample_count': 540, 'calibration_count': 270})
        rows = [row for row in common.rotation_schedule(plan)
                if row['n'] == 16 and row['k'] == 1 and row['phase'] == 'steady'
                and row['round'] == 5]
        self.assertEqual(len(rows), 9)
        by_order = {row['order']: row for row in rows}
        self.assertEqual(by_order[0]['mode'], 'copy')
        self.assertEqual(by_order[0]['r'], 8)
        self.assertEqual(by_order[8]['mode'], 'empty')
        self.assertEqual(len({(row['order'], row['mode']) for row in rows}), 9)


class RunnerLifecycleTests(unittest.TestCase):
    def test_prepare_default_selection_is_an_empty_requested_list(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, _ = prepare_run(root, names=('perf-sin',), mode='nemu')
            selection = config['metadata']['selection']
            self.assertEqual(selection['requested'], [])
            self.assertEqual(selection['selected_cases'], ['perf-sin'])
            self.assertEqual(selection['manifest_cases'], 1)

    def test_prepare_explicit_subset_and_exact_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config, snapshot = prepare_run(
                root, names=('perf-exp', 'perf-sin'), mode='gsim',
                selected=('perf-sin',))
            metadata = config['metadata']
            self.assertEqual(metadata['selection']['requested'], ['perf-sin'])
            self.assertEqual(metadata['selection']['selected_cases'], ['perf-sin'])
            case = metadata['cases'][0]
            command = case['command']
            self.assertEqual(command[-2], '-i')
            self.assertEqual(command[-1], case['staged_bin'])
            self.assertIn('--seed=123456789', command)
            self.assertIn(
                '--dramsim3-ini=' + metadata['tools']['dram_config']['realpath'],
                command)
            self.assertIn('--dramsim3-outdir=' + case['dram_outdir'], command)
            self.assertTrue(Path(case['workdir']).is_dir())
            self.assertTrue(Path(case['dram_outdir']).is_dir())
            self.assertEqual(common.sha256(case['staged_bin']), case['sha256'])

    def test_prepare_rejects_static_gsim_and_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = make_tools(root)
            static = make_elf(root / 'emu-gsim-static')
            manifest = manifest_document(root, ('perf-sin',))
            args = args_from_argv(base_argv('gsim', manifest, root / 'run', tools))
            args.emu = static
            with self.assertRaises(common.ManifestError):
                runner.prepare(args)
            config, _ = prepare_run(root, names=('perf-sin',), mode='nemu')
            args = args_from_argv(base_argv(
                'nemu', Path(root / 'manifest.json'), Path(config['run']), tools))
            with self.assertRaises(common.ManifestError):
                runner.prepare(args)

    def test_duplicate_and_unknown_case_selection_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = make_tools(root)
            manifest = manifest_document(root, ('perf-sin',))
            parser = runner.build_parser()
            argv = base_argv('nemu', manifest, root / 'run', tools,
                             selected=('perf-sin', 'perf-sin'))
            args = parser.parse_args(argv)
            with redirect_stderr(io_stub()):
                with self.assertRaises(SystemExit):
                    runner.validate_arguments(parser, args)
            args = parser.parse_args(base_argv(
                'nemu', manifest, root / 'run', tools, selected=('missing',)))
            runner.validate_arguments(parser, args)
            with self.assertRaises(common.ManifestError):
                runner.prepare(args)

    def test_launch_process_timeout_reclaims_the_group(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            registry = common.ProcessRegistry()
            result = common.launch_process(
                [sys.executable, '-c', 'import time; time.sleep(30)'], root,
                root / 'stdout.log', root / 'stderr.log', 0.25, registry)
            self.assertTrue(result['timed_out'])
            self.assertIsNotNone(result['returncode'])
            self.assertLess(result['elapsed_seconds'], 10)
            self.assertEqual(registry.live(), [])

    def test_launch_error_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = common.launch_process(
                [str(root / 'missing')], root, root / 'stdout.log',
                root / 'stderr.log', 5, common.ProcessRegistry())
            self.assertIsNotNone(result['launch_error'])
            self.assertIsNone(result['returncode'])


class RunnerIntegrationTests(unittest.TestCase):
    def test_end_to_end_strict_smoke_and_collector(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = make_tools(root)
            manifest = manifest_document(
                root, ('perf-exp', 'perf-sin', 'abi-public'))
            snapshot = common.load_manifest(manifest)
            args = base_argv('nemu', manifest, root / 'run', tools, smoke=True)
            fake = FakeLaunch(snapshot)
            with patch.object(common, 'validate_reference', fake_reference_validation), \
                    patch.object(runner.common, 'launch_process', fake):
                rc = runner.main(args)
            self.assertEqual(rc, 0)
            metadata = json.loads((root / 'run' / 'run-metadata.json').read_text())
            self.assertEqual(metadata['policy']['smoke'], False)
            self.assertEqual(len(metadata['cases']), 3)
            for case in metadata['cases']:
                self.assertEqual(json.loads(Path(case['result']).read_text())['returncode'], 0)

    def test_strict_smoke_failure_skips_the_rest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = make_tools(root)
            manifest = manifest_document(root, ('perf-exp', 'perf-sin'))
            snapshot = common.load_manifest(manifest)
            args = base_argv('gsim', manifest, root / 'run', tools, smoke=True)
            fake = FakeLaunch(snapshot, failure_name='perf-exp')
            with patch.object(common, 'validate_reference', fake_reference_validation), \
                    patch.object(runner.common, 'launch_process', fake):
                rc = runner.main(args)
            self.assertEqual(rc, 1)
            metadata = json.loads((root / 'run' / 'run-metadata.json').read_text())
            states = [json.loads(Path(case['result']).read_text())['state']
                      for case in metadata['cases']]
            self.assertEqual(states, ['completed', 'skipped'])

    def test_valid_gsim_strict_smoke_allows_the_remaining_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tools = make_tools(root)
            manifest = manifest_document(root, ('perf-exp', 'perf-sin'))
            snapshot = common.load_manifest(manifest)
            args = base_argv('gsim', manifest, root / 'run', tools, smoke=True)
            fake = FakeLaunch(snapshot)
            with patch.object(common, 'validate_reference', fake_reference_validation), \
                    patch.object(runner.common, 'launch_process', fake):
                rc = runner.main(args)
            self.assertEqual(rc, 0)
            metadata = json.loads((root / 'run' / 'run-metadata.json').read_text())
            self.assertTrue(metadata['policy']['smoke'])
            self.assertEqual(metadata['policy']['smoke_gate'], 'strict-collector')
            for case in metadata['cases']:
                self.assertEqual(json.loads(Path(case['result']).read_text())['state'],
                                 'completed')


class _IOStub:
    def write(self, _value):
        return len(_value)

    def flush(self):
        pass


def io_stub():
    return _IOStub()


if __name__ == '__main__':
    unittest.main()
