"""CSV format/terminal-state tests, using synthetic records rather than gsim."""
import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('veclibm_export', HERE / 'export_veclibm_gsim.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def document():
    cases, entries = [], []
    for i in range(25):
        name = f'performance-f{i}'
        metric = dict(n=16, repeats=3, vlen_bits=128, baseline='static-libm',
                      libm_to_rvvlm_cycle_ratio=4, metric_scope='gsim function-call intervals',
                      modes={m: dict(median_cycles=c, median_insns=20, median_cycles_per_element=c/16,
                                     median_elements_per_cycle=16/c) for m,c in [('libm',160),('rvvlm',40)]},
                      samples=[dict(mode=m, sample=r, cycles=c, insns=20, checksum=123,
                                    cycles_per_element=c/16, elements_per_cycle=16/c)
                               for m,c in [('libm',160),('rvvlm',40)] for r in range(3)])
        cases.append(dict(name=name, original_name=f'f{i}', suite='performance', status='passed',
                          functional_pass=True, failure_reasons=[], metrics=metric,
                          host_counters={'gsim_cores':[dict(core=0, retired_instructions=100, simulation_cycles=200, ipc=.5)]},
                          sha256='a'*64, log='/test/out.log', err='/test/out.err'))
        entries.append(dict(name=name, manifest_entry=dict(n=16,repeats=3)))
    return dict(cases=cases,run='/run/example',generated_at='test',audit_issues=[],complete=True,
                run_metadata=dict(cases=entries, emulator=dict(path='/emu',sha256='b'*64),
                                  reference=dict(path='/ref',sha256='c'*64),jobs=64,timeout='48h'))


class ExportTests(unittest.TestCase):
    def test_no_csv_until_all_performance_terminate(self):
        d = document()
        d['cases'][0]['status']='running'
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'nested/veclibm.csv'
            result=exporter.export_performance(d,out)
            self.assertFalse(result['exported'])
            self.assertFalse(out.exists())

    def test_exports_after_performance_completion_while_accuracy_still_runs(self):
        d=document(); d['complete']=False
        for case in d['cases']:
            case['list_membership']={'pass':1,'fail':0,'timeout':0}
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'veclibm.csv'
            self.assertTrue(exporter.export_performance(d,out)['exported'])

    def test_early_failure_without_terminal_list_does_not_export(self):
        d=document(); d['complete']=False
        for case in d['cases']:
            case['list_membership']={'pass':1,'fail':0,'timeout':0}
        d['cases'][0].update(status='failed',list_membership={'pass':0,'fail':0,'timeout':0})
        with tempfile.TemporaryDirectory() as temp:
            self.assertFalse(exporter.export_performance(d,Path(temp)/'out.csv')['exported'])

    def test_exports_50_summary_and_150_raw_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'veclibm.csv'
            result=exporter.export_performance(document(),out)
            self.assertEqual(result['summary_rows'],50)
            self.assertEqual(result['sample_rows'],150)
            with out.open() as stream:
                rows=list(csv.DictReader(stream))
            self.assertEqual(len(rows),200)
            self.assertEqual(len({r['function'] for r in rows}),25)
            self.assertTrue(all(r['measurement_valid']=='True' for r in rows))
            self.assertEqual(rows[0]['median_cycles_per_element'],'10.0')
            self.assertEqual(rows[0]['vlen_bits'],'128')
            # Same run can refresh; another run cannot overwrite existing data.
            exporter.export_performance(document(),out)
            other=document(); other['run']='/other/run'
            with self.assertRaises(ValueError):
                exporter.export_performance(other,out)

    def test_failed_cases_preserve_reason_and_mark_measurements_invalid(self):
        d=document()
        failed=d['cases'][0]
        failed.update(status='failed',functional_pass=False,failure_reasons=['RTL BAD TRAP'],metrics={})
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'veclibm.csv'
            result=exporter.export_performance(d,out)
            self.assertEqual(result['sample_rows'],144)
            with out.open() as stream:
                rows=list(csv.DictReader(stream))
            bad=[r for r in rows if r['function']=='f0']
            self.assertEqual(len(bad),2)
            self.assertTrue(all(r['measurement_valid']=='False' and not r['cycles'] for r in bad))
            self.assertIn('RTL BAD TRAP',bad[0]['failure_reasons'])

    def test_cli_exports_comparison_only_after_performance_terminal(self):
        for done in (False, True):
            d=document(); d.update(state='passed' if done else 'in_progress',summary={})
            if not done:
                d['cases'][0]['status']='running'
            with tempfile.TemporaryDirectory() as temp:
                output=Path(temp)/'performance.csv'
                comparison=Path(temp)/'comparison.csv'
                with patch.object(exporter,'collect',return_value=d), \
                     patch('compare_veclibm_gsim.compare_performance',return_value={'exported':True}) as compare, \
                     patch('builtins.print'):
                    status=exporter.main(['--run','/run/example','--output',str(output),
                        '--baseline-dir','/baseline','--comparison-output',str(comparison)])
                    self.assertEqual(status,0)
                if done:
                    compare.assert_called_once_with(d,Path('/baseline'),comparison)
                else:
                    compare.assert_not_called()

    def test_coverage_and_provenance_are_required(self):
        d=document(); d['cases'].pop()
        with self.assertRaises(ValueError):
            exporter.export_performance(d,Path('/unused'))


if __name__ == '__main__':
    unittest.main()
