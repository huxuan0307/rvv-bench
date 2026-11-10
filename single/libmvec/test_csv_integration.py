"""验证紧凑CSV贯穿真实manifest、collector及host CSV导出的完整边界。"""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import collect
import analyze
import recover
import test_run
import test_compact_csv

class CsvIntegrationTests(unittest.TestCase):
    def prepare(self, root):
        original=test_run.manifest_document
        def manifest(root,names):
            path=original(root,names);doc=json.loads(path.read_text())
            case=test_compact_csv.manifest_case()
            doc['cases'][0]['expected']=case['expected']
            doc['cases'][0]['required_markers']=case['required_markers']
            path.write_text(json.dumps(doc));return path
        with patch.object(test_run,'manifest_document',manifest):
            config,snapshot=test_run.prepare_run(root,names=('perf-log',),mode='gsim')
        meta=config['metadata']['cases'][0]
        text=test_compact_csv.csv_text(snapshot['cases'][0])
        Path(meta['stderr']).write_text(text)
        return config,meta,text

    def test_full_collection_preserves_original_csv_and_logical_samples(self):
        with tempfile.TemporaryDirectory() as d:
            config,meta,text=self.prepare(Path(d))
            doc=collect.collect(config['run']);self.assertEqual(doc['state'],'passed')
            case=doc['cases'][0];self.assertTrue(case['measurement_valid'])
            self.assertEqual(case['wire']['physical_rows'],15)
            reports=Path(config['run'])/'reports'
            self.assertEqual((reports/'perf-log.csv').read_text(),text)
            with (reports/'samples.csv').open() as f:rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),90)
            self.assertTrue(all(r['wire_format']=='csv2' and r['raw'].startswith('D,') for r in rows))
            self.assertEqual(collect.main(['--run',config['run'],'--require-complete']),0)

    def test_reject_partial_wrong_or_kv_replacement_despite_good_trap(self):
        with tempfile.TemporaryDirectory() as d:
            config,meta,text=self.prepare(Path(d))
            lines=text.splitlines()
            mutations=[text.replace('E,perf-log,pass','E,wrong,pass'),
                       '\n'.join(lines[:-1]),'\n'.join(lines[:5]+lines[6:]),
                       test_run.guest_text(test_compact_csv.manifest_case()),
                       text+'LIBMVEC_COMPLETE case=perf-log status=pass\n']
            for changed in mutations:
                Path(meta['stderr']).write_text(changed)
                report=collect.collect(config['run'])
                self.assertEqual(report['state'],'failed')
                self.assertFalse(report['cases'][0]['measurement_valid'])

    def test_partial_rows_survive_all_failure_early_returns(self):
        for state in ('missing-result', 'running', 'aborted', 'timeout', 'rtl-error', 'no-pass'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as d:
                config,meta,text=self.prepare(Path(d))
                # 有两行完整D和第三行的半行；只能回收20条。
                lines=text.splitlines(keepends=True)
                Path(meta['stderr']).write_text(''.join(lines[:6])+lines[6][:40])
                result=json.loads(Path(meta['result']).read_text())
                if state in ('missing-result','running'):
                    # 不删除文件；模拟result尚未存在。
                    metadata_path=Path(config['run'])/'run-metadata.json'
                    metadata=json.loads(metadata_path.read_text())
                    metadata['cases'][0]['result']=str(Path(meta['result']).with_name('not-created.json'))
                    metadata_path.write_text(json.dumps(metadata))
                    if state=='running':test_run.write_runner_state(config['run'],'in_progress')
                elif state=='aborted':result.update(state='aborted',aborted=True,returncode=-15)
                elif state=='timeout':result.update(timed_out=True,returncode=-15)
                elif state=='rtl-error':
                    Path(meta['stdout']).write_text('Assertion failed: RTL difftest mismatch\n')
                    result.update(returncode=1)
                Path(meta['result']).write_text(json.dumps(result))
                report=collect.collect(config['run']);case=report['cases'][0]
                self.assertNotEqual(report['state'],'passed')
                self.assertFalse(case['measurement_valid'])
                self.assertEqual(case['recovered_samples'],20)
                self.assertEqual(case['recovered_calibrations'],0)
                if state=='timeout':self.assertEqual(case['status'],'timeout')
                if state=='aborted':self.assertEqual(case['status'],'aborted')
                if state=='running':self.assertEqual(case['status'],'running')
                reports=Path(config['run'])/'reports'
                with (reports/'recovered-samples.csv').open() as f:rows=list(csv.DictReader(f))
                self.assertEqual(len(rows),20)
                self.assertEqual({r['source_line'] for r in rows},{'5','6'})
                self.assertTrue(all(r['measurement_valid']=='False' and r['row_status']=='parsed-unverified' for r in rows))
                self.assertEqual({r['source'] for r in rows},{meta['stderr']})
                self.assertEqual(collect.main(['--run',config['run'],'--require-complete']),1)
                with self.assertRaises(ValueError):analyze.analyze([reports/'results.json'])

    def test_all_rows_without_footer_are_recoverable_but_not_passed(self):
        with tempfile.TemporaryDirectory() as d:
            config,meta,text=self.prepare(Path(d))
            Path(meta['stderr']).write_text(''.join(text.splitlines(keepends=True)[:13]))
            report=collect.collect(config['run']);case=report['cases'][0]
            self.assertEqual((case['recovered_samples'],case['recovered_calibrations']),(60,30))
            self.assertEqual(report['state'],'failed')
            self.assertFalse(case['measurement_valid'])

    def test_complete_csv_with_rtl_error_is_recovered_not_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            config,meta,text=self.prepare(Path(d))
            Path(meta['stderr']).write_text(text+'Assertion failed: RTL difftest mismatch\n')
            report=collect.collect(config['run']);case=report['cases'][0]
            self.assertEqual(report['state'],'failed')
            self.assertFalse(case['measurement_valid'])
            self.assertEqual(case['recovered_samples']+case['recovered_calibrations'],90)
            with (Path(config['run'])/'reports/samples.csv').open() as f:
                self.assertTrue(all(r['measurement_valid']=='False' for r in csv.DictReader(f)))

    def test_recovery_never_stitches_streams_or_completes_final_line(self):
        with tempfile.TemporaryDirectory() as d:
            config,meta,text=self.prepare(Path(d));lines=text.splitlines(keepends=True)
            Path(meta['stdout']).write_text(''.join(lines[:4])+lines[4].rstrip('\n'))
            Path(meta['stderr']).write_text(''.join(lines[5:]))
            report=collect.collect(config['run'])
            self.assertEqual(report['cases'][0]['recovered_samples'],0)
            self.assertEqual(report['cases'][0]['recovered_calibrations'],0)

    def test_collector_preserves_cr_instead_of_creating_fake_newline(self):
        with tempfile.TemporaryDirectory() as d:
            config,meta,text=self.prepare(Path(d));lines=text.splitlines(keepends=True)
            # 裸CR不应被TextIO通用换行规则升级成已完整打印的D行。
            content=''.join(lines[:4])+lines[4].rstrip('\n')+'\r'
            Path(meta['stderr']).write_bytes(content.encode())
            report=collect.collect(config['run'])
            self.assertEqual(report['cases'][0]['recovered_samples'],0)
            Path(meta['stderr']).write_bytes((''.join(lines[:5])).replace('\n','\r\n').encode())
            report=collect.collect(config['run'])
            self.assertEqual(report['cases'][0]['recovered_samples'],10)

    def test_offline_recovery_exports_10_samples_without_run_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            config,meta,text=self.prepare(Path(d))
            Path(meta['stderr']).write_text(''.join(text.splitlines(keepends=True)[:5]))
            output=Path(d)/'recovered'
            doc=recover.recover_logs(Path(config['run'])/'manifests/manifest.json',
                                     'perf-log',[Path(meta['stderr'])],output)
            self.assertEqual(doc['logical_records'],10)
            self.assertFalse(doc['measurement_valid'])
            self.assertEqual(doc['run_status'],'not-audited')
            with (output/'recovered-samples.csv').open() as f:rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),10)
            self.assertEqual(rows[0]['source_line'],'5')
            with self.assertRaises(FileExistsError):
                recover.recover_logs(Path(config['run'])/'manifests/manifest.json',
                                     'perf-log',[Path(meta['stderr'])],output)

if __name__=='__main__':unittest.main()
