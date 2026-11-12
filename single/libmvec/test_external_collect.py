"""外部传输验收必须独立于真实emu退出与串口协议。"""
import tempfile
from pathlib import Path
import unittest
import collect
import runner_common as common
from test_run import prepare_run


class ExternalCollectTests(unittest.TestCase):
    def fixture(self, root):
        config, _ = prepare_run(root, names=('perf-sin',), mode='gsim')
        run = Path(config['run'])
        meta = common.read_json(run / 'run-metadata.json')
        runner = run / 'fake-runner'; runner.write_text('#!/bin/sh\nexit 0\n')
        proxy = run / 'run-emu-proxy'; proxy.write_text('#!/bin/sh\nexit 0\n')
        meta['tools']['runner'] = common.identity(runner)
        meta['policy'].update(backend='run-emu', jobs=32)
        command = [str(runner), '-e', str(proxy), '-r', meta['tools']['reference']['realpath'], '-t', str(run / 'staging'), '-j', '32', '--timeout', meta['policy']['timeout'], '-o', str(run / 'external-logs'), '--show-cmd']
        meta['external_runner'] = dict(runner=common.identity(runner), proxy=common.identity(proxy), hostname='fixture', command=command)
        log = run / 'external-logs' / 'one'; log.mkdir(parents=True)
        (log / '.pass_list').write_text('perf-sin\n')
        common.write_json(run / 'external-result.json', dict(returncode=0, command=command))
        return run, meta, log

    def test_valid_external_transport_32_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            run, meta, _ = self.fixture(Path(temp))
            self.assertEqual(collect.audit_external_runner(run, meta), [])

    def test_timeout_is_rejected_despite_zero_outer_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            run, meta, log = self.fixture(Path(temp))
            (log / '.timeout_list').write_text('perf-sin\n')
            self.assertTrue(collect.audit_external_runner(run, meta))

    def test_duplicate_pass_and_command_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            run, meta, log = self.fixture(Path(temp))
            (log / '.pass_list').write_text('perf-sin\nperf-sin\n')
            self.assertTrue(collect.audit_external_runner(run, meta))
            (log / '.pass_list').write_text('perf-sin\n')
            meta['external_runner']['command'] += ['--no-diff']
            self.assertTrue(collect.audit_external_runner(run, meta))

    def test_missing_receipt_and_proxy_change_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            run, meta, _ = self.fixture(Path(temp))
            (run / 'external-result.json').unlink()
            Path(meta['external_runner']['proxy']['path']).write_text('changed\n')
            issues = collect.audit_external_runner(run, meta)
            self.assertTrue(any('receipt' in x for x in issues))
            self.assertTrue(any('changed' in x for x in issues))


if __name__ == '__main__':
    unittest.main()
