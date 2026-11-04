"""Validate simulator evidence classification without running an emulator."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import run_gsim
from run_gsim import parse_case


class EvidenceTests(unittest.TestCase):
    def classify(self, marker='AV cycles=2048 checksum=1.0 iterations=1024', stderr='', timeout=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 's000.bin'
            binary.write_bytes(b'')
            (root / 'run-s000.log').write_text('HIT GOOD TRAP\n' + marker + '\n')
            (root / 'run-s000.err').write_text(stderr)
            return parse_case({'name': 's000', 'status': 'built', '_binary': binary}, root,
                              {'pass': {'s000'}, 'fail': set(), 'timeout': {'s000'} if timeout else set()})

    def test_valid(self):
        row = self.classify()
        self.assertEqual(row['status'], 'passed')
        self.assertEqual(row['cycles_per_outer_repetition'], 2.0)

    def test_timeout_overrides_success(self):
        self.assertEqual(self.classify(timeout=True)['status'], 'timeout')

    def test_stderr_failure(self):
        self.assertEqual(self.classify(stderr='ERROR difftest failed')['status'], 'failed')

    def test_missing_or_wrong_marker(self):
        for marker in ('', 'AV cycles=2048 checksum=1.0', 'AV cycles=2048 checksum=1.0 iterations=32'):
            with self.subTest(marker=marker):
                self.assertEqual(self.classify(marker=marker)['status'], 'failed')


class CollectRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.run = self.root / 'build/gsim-1024/recorded-run'
        (self.run / 'bin').mkdir(parents=True)
        (self.run / 'logs').mkdir()
        self.binary = self.root / 'build/O3/s000/s000.bin'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'original measurement binary')
        (self.run / 'bin/s000.bin').symlink_to(self.binary)
        self.manifest = {
            'compiler': 'recorded-compiler', 'flags': ['-O3'],
            'cases': [{'suite': 'tsvc', 'name': 's000', 'status': 'built',
                       'directory': str(self.binary.parent), 'outer_iterations': 1024}],
        }
        self.manifest_bytes = json.dumps(self.manifest).encode()
        self.manifest_path = self.root / 'build/O3/manifest.json'
        self.manifest_path.write_bytes(self.manifest_bytes)
        self.cases = [dict(self.manifest['cases'][0], _binary=self.binary)]
        self.hashes = {'s000': hashlib.sha256(self.binary.read_bytes()).hexdigest()}
        self.manifest_hash = hashlib.sha256(self.manifest_bytes).hexdigest()
        self.emulator = self.root / 'recorded-emu'
        self.reference = self.root / 'recorded-reference'
        self.runner = self.root / 'recorded-runner'
        for path in (self.emulator, self.reference, self.runner):
            path.write_bytes(b'fixture, not an executable')
        self.started = '2026-10-01 12:34:56'
        self.finished = '2026-10-01 12:35:56'
        self.runner_text = (
            f'started: {self.started}\n'
            f'emu: {self.emulator}\n'
            f'diff: ON ({self.reference})\n'
            'parallel: 7\n'
            'timeout: 13m\n'
        )
        (self.run / 'runner.log').write_text(self.runner_text)
        (self.run / 'runner.err').write_text('recorded runner stderr\n')
        self.metadata_path = self.run / 'run-metadata.json'
        self.metadata = {'manifest_sha256': self.manifest_hash,
                         'binary_sha256': self.hashes, 'runner_returncode': None}
        self.enterContext(mock.patch.object(run_gsim, 'ROOT', self.root))
        self.enterContext(mock.patch.object(run_gsim, 'RUNNER', self.runner))
        # Guard every subprocess entry point, not only subprocess.run.
        self.subprocess = self.enterContext(mock.patch.object(run_gsim, 'subprocess'))
        self.addCleanup(self.assert_no_subprocess)

    def assert_no_subprocess(self):
        self.assertEqual(self.subprocess.mock_calls, [])

    def collect(self):
        return run_gsim.collect_run(self.run, self.manifest_path, self.manifest,
                                    self.manifest_bytes, self.cases)

    def finish_runner_log(self):
        (self.run / 'runner.log').write_text(
            self.runner_text + f'finished: {self.finished}\n')

    def test_rejects_unfinished_run(self):
        # Both current metadata and legacy runner-only runs need completion evidence.
        for recorded_metadata in (False, True):
            with self.subTest(recorded_metadata=recorded_metadata):
                if recorded_metadata:
                    self.metadata_path.write_text(json.dumps(self.metadata))
                with self.assertRaisesRegex(ValueError, 'batch is still running'):
                    self.collect()
        # The same fixture becomes collectable only after the final summary.
        self.finish_runner_log()
        _, metadata = self.collect()
        self.assertEqual(metadata['finished_at'], self.finished)

    def test_rejects_modified_binary(self):
        self.finish_runner_log()
        snapshot = {'manifest_sha256': self.manifest_hash,
                    'cases': [{'name': 's000', 'binary_sha256': self.hashes['s000']}]}
        (self.run / 'measurement-build.json').write_text(json.dumps(snapshot))
        # Exercise hashes recovered from both the snapshot and current metadata.
        for recorded_metadata in (False, True):
            with self.subTest(recorded_metadata=recorded_metadata):
                if recorded_metadata:
                    self.metadata_path.write_text(json.dumps(self.metadata))
                self.binary.write_bytes(b'original measurement binary')
                self.collect()  # The original binary is accepted.
                self.binary.write_bytes(b'modified measurement binary')
                with self.assertRaisesRegex(
                        ValueError, 's000: binary hash differs from measurement build'):
                    self.collect()

    def test_legacy_runner_log_provenance_without_subprocess(self):
        self.finish_runner_log()
        stdout = self.run / 'logs/run-s000.log'
        stderr = self.run / 'logs/run-s000.err'
        stdout.write_text('HIT GOOD TRAP\nAV cycles=2048 checksum=1.0 iterations=1024\n')
        stderr.write_text('')
        before = {path.relative_to(self.root): path.read_bytes()
                  for path in self.root.rglob('*') if path.is_file()}

        logs, metadata = self.collect()

        self.assertEqual(logs, self.run / 'logs')
        self.assertEqual(metadata['started_at'], self.started)
        self.assertEqual(metadata['started_at_source'], 'runner.log (runner local time)')
        self.assertEqual(metadata['finished_at'], self.finished)
        self.assertEqual(metadata['jobs'], 7)
        self.assertEqual(metadata['timeout'], '13m')
        self.assertEqual(metadata['manifest'], str(self.manifest_path))
        self.assertEqual(metadata['manifest_sha256'], self.manifest_hash)
        self.assertEqual(metadata['build'], {'compiler': 'recorded-compiler', 'flags': ['-O3']})
        self.assertIsNone(metadata['runner_returncode'])
        self.assertEqual(metadata['identity_observation'],
                         'file identities observed at collection; paths recovered from runner.log')
        for key, path in (('emulator', self.emulator), ('reference', self.reference),
                          ('runner', self.runner)):
            self.assertEqual(metadata[key], run_gsim.identity(path))
        self.assertEqual(metadata['command'], [
            str(self.runner), '-e', str(self.emulator), '-r', str(self.reference),
            '-t', str(self.run / 'bin'), '-j', '7', '--timeout', '13m',
            '-o', str(self.run / 'logs')])
        self.assertEqual(metadata['run_directory'], str(self.run))
        self.assertEqual(metadata['runner_stdout'], str(self.run / 'runner.log'))
        self.assertEqual(metadata['runner_stderr'], str(self.run / 'runner.err'))
        self.assertEqual(metadata['outer_repetitions'], 1024)
        self.assertIn('collected_at', metadata)
        after = {path.relative_to(self.root): path.read_bytes()
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(after, before, 'collection must not create or modify evidence files')


if __name__ == '__main__':
    unittest.main()
