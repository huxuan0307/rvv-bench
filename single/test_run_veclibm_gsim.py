"""Launcher contract tests; no simulator is run by these host-side tests."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('veclibm_gsim_launcher', HERE / 'run_veclibm_gsim.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_identity_records_content_and_resolved_path(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image.bin'
            path.write_bytes(b'veclibm')
            data = launcher.identity(path)
            self.assertEqual(data['size'], 7)
            self.assertEqual(data['sha256'], launcher.sha256(path))
            self.assertEqual(data['resolved_path'], str(path.resolve()))

    def test_jobs_validation(self):
        for jobs in ('0', '65'):
            with patch('sys.argv', ['launcher', '--jobs', jobs]), patch('sys.stderr'):
                with self.assertRaises(SystemExit) as error:
                    launcher.main()
                self.assertEqual(error.exception.code, 2)

    def test_rejects_stale_or_failed_manifest_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / 'input.bin'
            image.write_bytes(b'new content')
            executable = root / 'tool'
            executable.write_text('#!/bin/sh\nexit 0\n')
            executable.chmod(0o755)
            manifest = root / 'manifest.json'
            output = root / 'not-created'
            base = ['launcher', '--emu', str(executable), '--runner', str(executable),
                    '--reference', str(executable), '--performance-manifest', str(manifest),
                    '--output', str(output)]
            for status, digest in [('failed', launcher.sha256(image)), ('built', '0' * 64)]:
                entries = [dict(name=f'case{i}', bin=str(image), status=status, sha256=digest) for i in range(25)]
                manifest.write_text(json.dumps({'cases': entries}))
                with patch('sys.argv', base), patch('sys.stderr'), patch.object(launcher.subprocess, 'Popen') as process:
                    with self.assertRaises(SystemExit) as error:
                        launcher.main()
                    self.assertEqual(error.exception.code, 2)
                    process.assert_not_called()
                    self.assertFalse(output.exists())

    def test_stages_166_hash_verified_copies_and_uses_requested_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image = root / 'input.bin'
            image.write_bytes(b'input fixture')
            sha = launcher.sha256(image)
            runner, ref = root / 'run_emu.sh', root / 'ref.so'
            runner.write_text('#!/bin/sh\nexit 0\n')
            runner.chmod(0o755)
            ref.write_bytes(b'ref fixture')
            emus = root / 'emus'
            emus.mkdir()
            for name, when in [('emu-gsim-older', 1), ('emu-gsim-newer', 2)]:
                path = emus / name
                path.write_text('#!/bin/sh\nexit 0\n')
                path.chmod(0o755)
                launcher.os.utime(path, (when, when))
            manifests = []
            for suite, count in [('performance', 25), ('accuracy', 141)]:
                path = root / (suite + '.json')
                path.write_text(json.dumps({'cases': [dict(name=f'case{i}', bin=str(image), status='built', sha256=sha) for i in range(count)]}))
                manifests.append(path)
            out = root / 'run'
            argv = ['launcher', '--runner', str(runner), '--reference', str(ref), '--emu-dir', str(emus),
                    '--performance-manifest', str(manifests[0]), '--accuracy-manifest', str(manifests[1]),
                    '--output', str(out), '--jobs', '64', '--timeout', '48h']
            # Keep collection outside this isolated launcher contract test.
            with patch('sys.argv', argv), patch.object(launcher, 'HERE', root), \
                 patch.object(launcher.subprocess, 'Popen') as process, patch('builtins.print'):
                process.return_value.pid = 12345
                process.return_value.wait.return_value = 0
                self.assertEqual(launcher.main(), 2)  # no collector: run finished but unaudited
            meta = json.loads((out / 'run-metadata.json').read_text())
            self.assertEqual(len(meta['cases']), 166)
            self.assertEqual(meta['jobs'], 64)
            self.assertEqual(meta['timeout'], '48h')
            self.assertTrue(meta['emulator']['path'].endswith('emu-gsim-newer'))
            command = meta['command']
            self.assertEqual(command[command.index('-j') + 1], '64')
            self.assertEqual(command[command.index('--timeout') + 1], '48h')
            self.assertEqual(command[command.index('-r') + 1], str(ref))
            for case in meta['cases']:
                staged = Path(case['staged_bin'])
                self.assertFalse(staged.is_symlink())
                self.assertEqual(launcher.sha256(staged), sha)
            self.assertEqual(json.loads((out / 'runner-exit.json').read_text())['returncode'], 0)


if __name__ == '__main__':
    unittest.main()
