"""Synthetic HTML/CSV regressions; no emulators or log collection are run."""
import copy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('veclibm_compare', HERE / 'compare_veclibm_gsim.py')
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)
FUNCTIONS = 'exp exp2 expm1 log log10 log2 log1p sqrt cbrt sin cos tan asin acos atan sinh cosh tanh asinh acosh atanh erf erfc tgamma lgamma'.split()
HEADER = '<thead><tr><th>function</th><th>scalar libc fp64/cycle</th><th>RVV veclibm fp64/cycle</th><th>speedup</th></thead>'


def html(functions=FUNCTIONS, title='Actual platform', scalar='0.250000', rvv='0.500000', speed='1.99x'):
    # Real pages omit </tr> before </thead>; use that form in every fixture.
    return ('<!DOCTYPE html><html><head><title>Wrong copied platform</title></head><body>'
            f'<h1>{title}</h1><table class="tabPage">{HEADER}<tbody>' +
            ''.join(f'<tr><td>{f}</td><td>{scalar}</td><td>{rvv}</td><td>{speed}</td></tr>' for f in functions) +
            '</tbody></table></body></html>')


def document():
    cases, entries = [], []
    for function in FUNCTIONS:
        name = 'performance-' + function
        metric = dict(n=16, repeats=3, vlen_bits=128, baseline='static-libm',
            libm_to_rvvlm_cycle_ratio=4,
            modes={'libm':dict(median_elements_per_cycle=.5, median_cycles_per_element=2, median_cycles=32),
                   'rvvlm':dict(median_elements_per_cycle=2, median_cycles_per_element=.5, median_cycles=8)})
        cases.append(dict(name=name, original_name=function, suite='performance', status='passed',
                          functional_pass=True, metrics=metric, failure_reasons=[], validation_issues=[],
                          list_membership={'pass':1, 'fail':0, 'timeout':0},
                          sha256='a'*64, log='/run/out.log', err='/run/out.err'))
        entries.append(dict(name=name, manifest_entry=dict(n=16,repeats=3)))
    return dict(cases=cases, run='/run/example', complete=False, generated_at='test', audit_issues=[],
                run_metadata=dict(cases=entries,emulator=dict(path='/emu',sha256='b'*64),
                                  reference=dict(path='/ref',sha256='c'*64)))


class ParserTests(unittest.TestCase):
    def test_actual_page_shape_uses_h1_not_wrong_title(self):
        parsed=compare.parse_baseline_html(html(title='AX45MPV (AndeSim simulator) veclibm benchmarks'))
        self.assertEqual(len(parsed['rows']),25)
        self.assertTrue(parsed['title'].startswith('AX45MPV'))
        self.assertEqual(parsed['rows']['exp']['scalar_text'],'0.250000')
        self.assertEqual(parsed['rows']['exp']['published_speedup'],1.99)
        self.assertEqual(parsed['rows']['exp']['recomputed_speedup'],2)

    def test_empty_table_is_not_zero_throughput(self):
        parsed=compare.parse_baseline_html(html(functions=[]))
        self.assertEqual(parsed['rows'],{})

    def test_nested_markup_and_header_whitespace_supported(self):
        text=html(functions=['exp'],title='Platform &amp; model')
        text=text.replace('<td>0.500000</td>','<td><span>0.500000</span></td>')
        text=text.replace('scalar libc fp64/cycle',' scalar\n libc fp64/cycle ')
        self.assertEqual(compare.parse_baseline_html(text)['title'],'Platform & model')

    def test_duplicate_functions_rejected(self):
        with self.assertRaisesRegex(ValueError,'duplicate function'):
            compare.parse_baseline_html(html(functions=['exp','exp']))

    def test_missing_columns_and_header_typo_rejected(self):
        for text in [html().replace('<th>speedup</th>',''),
                     html().replace('scalar libc fp64/cycle','scalar cycles'),
                     html().replace('<td>1.99x</td>','')]:
            with self.subTest(text=text[:80]):
                with self.assertRaises(ValueError):
                    compare.parse_baseline_html(text)

    def test_nan_negative_zero_infinite_and_missing_values_rejected(self):
        for bad in ['NaN','nan','-0.1','0','inf','Infinity','1e309','','garbage']:
            for field in ('scalar','rvv','speed'):
                with self.subTest(value=bad,field=field):
                    values={field:bad+'x' if field=='speed' else bad}
                    with self.assertRaises(ValueError):
                        compare.parse_baseline_html(html(functions=['exp'],**values))

    def test_finite_inputs_overflowing_derived_metrics_rejected(self):
        for scalar,rvv in [('1e-320','1'),('1e308','1e-308')]:
            with self.assertRaises(ValueError):
                compare.parse_baseline_html(html(functions=['exp'],scalar=scalar,rvv=rvv))

    def test_truncated_and_ambiguous_tables_rejected(self):
        for text in [html().replace('</table>',''), html().replace('</h1>',''),
                     html()+html(), html().replace('<h1>Actual platform</h1>','')]:
            with self.assertRaises(ValueError):
                compare.parse_baseline_html(text)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.baselines=self.root/'baselines'
        self.baselines.mkdir()
        templates=self.baselines/'templates'; templates.mkdir()
        (templates/'veclibm.js').write_text('All input values are uniformly sampled from [0,1).')
        self.out=self.root/'data/comparison.csv'

    def platform(self,name,functions=FUNCTIONS):
        directory=self.baselines/name; directory.mkdir()
        (directory/'veclibm.html').write_text(html(functions=functions,title=name+' title'))
        return directory

    def rows(self):
        with self.out.open() as stream:
            return list(csv.DictReader(stream))

    def test_alias_dedup_and_empty_platform_yield_125_rows(self):
        for name in ['andesim_ax45mpv','spacemit_x60','tt_asc_s','tt_asc_x']:
            self.platform(name)
        self.platform('tt_x280',functions=[])
        (self.baselines/'bpi_f3').symlink_to('spacemit_x60',target_is_directory=True)
        result=compare.compare_performance(document(),self.baselines,self.out)
        self.assertEqual(result['platforms'],5)
        self.assertEqual(result['rows'],125)
        self.assertEqual(result['matched_rows'],100)
        self.assertEqual(result['missing_rows'],25)
        rows=self.rows()
        x60=[r for r in rows if r['baseline_platform']=='spacemit_x60']
        self.assertEqual(len(x60),25)
        self.assertIn(str(self.baselines/'bpi_f3/veclibm.html'),json.loads(x60[0]['baseline_alias_paths']))
        missing=[r for r in rows if r['baseline_platform']=='tt_x280']
        self.assertTrue(all(r['baseline_status']=='baseline_missing' and r['comparison_status']=='baseline_missing' for r in missing))
        self.assertTrue(all(r['baseline_rvv_fp64_per_cycle']=='' and r['rvv_current_to_baseline_throughput_ratio']=='' for r in missing))
        self.assertEqual(len({r['baseline_sha256'] for r in x60}),1)

    def test_ratio_direction_percent_change_and_precision_caveat(self):
        self.platform('board')
        result=compare.compare_performance(document(),self.baselines,self.out)
        self.assertTrue(result['exported'])
        row=self.rows()[0]
        self.assertEqual(float(row['scalar_current_to_baseline_throughput_ratio']),2)
        self.assertEqual(float(row['scalar_throughput_pct_change']),100)
        self.assertEqual(float(row['rvv_current_to_baseline_throughput_ratio']),4)
        self.assertEqual(float(row['rvv_throughput_pct_change']),300)
        self.assertEqual(float(row['baseline_scalar_cycles_per_element']),4)
        self.assertEqual(float(row['baseline_rvv_cycles_per_element']),2)
        self.assertEqual(float(row['current_to_baseline_recomputed_speedup_ratio']),2)
        self.assertEqual(row['baseline_published_speedup_text'],'1.99x')
        self.assertEqual(row['baseline_scalar_fp64_per_cycle'],'0.250000')
        self.assertEqual(row['comparability'],compare.COMPARABILITY)
        self.assertEqual(row['baseline_n'],'unknown')
        self.assertEqual(row['baseline_input_interval'],'[0,1)')
        self.assertEqual(row['current_input_interval'],'[0.125,0.875)')
        acosh=next(r for r in self.rows() if r['function']=='acosh')
        self.assertEqual(acosh['current_input_interval'],'[1.125,1.875)')

    def test_failure_keeps_status_and_baseline_but_no_fake_current_ratios(self):
        self.platform('board')
        d=document(); d['cases'][0].update(status='failed',functional_pass=False,
            failure_reasons=['RTL BAD TRAP'],list_membership={'pass':0,'fail':1,'timeout':0})
        result=compare.compare_performance(d,self.baselines,self.out)
        self.assertEqual(result['invalid_current_rows'],1)
        row=self.rows()[0]
        self.assertEqual(row['comparison_status'],'invalid_current')
        self.assertEqual(row['current_valid'],'False')
        self.assertEqual(row['baseline_rvv_fp64_per_cycle'],'0.500000')
        self.assertEqual(row['current_rvv_fp64_per_cycle'],'')
        self.assertEqual(row['rvv_current_to_baseline_throughput_ratio'],'')
        self.assertIn('BAD TRAP',row['current_failure_reasons'])

    def test_missing_current_metric_and_audit_issues_invalidate_current(self):
        self.platform('board')
        for mode in ('missing','audit'):
            with self.subTest(mode=mode):
                d=document()
                if mode=='missing':
                    d['cases'][0]['metrics']['modes']['rvvlm'].pop('median_elements_per_cycle')
                else:
                    d['audit_issues']=['wrong refmodel']
                compare.compare_performance(d,self.baselines,self.out)
                self.assertEqual(self.rows()[0]['comparison_status'],'invalid_current')

    def test_inconsistent_current_metrics_are_not_valid_comparisons(self):
        self.platform('board')
        d=document()
        d['cases'][0]['metrics']['modes']['libm']['median_elements_per_cycle']=9
        result=compare.compare_performance(d,self.baselines,self.out)
        self.assertEqual(result['invalid_current_rows'],1)
        self.assertEqual(self.rows()[0]['current_valid'],'False')

    def test_pending_gate_does_not_read_baselines_or_create_csv(self):
        for status,membership in [('pending',{}),('running',{}),('failed',{})]:
            d=document(); d['cases'][0].update(status=status,list_membership=membership)
            with patch.object(compare,'load_baselines') as load:
                result=compare.compare_performance(d,self.root/'missing',self.out)
                self.assertFalse(result['exported'])
                load.assert_not_called()
                self.assertFalse(self.out.exists())

    def test_document_complete_allows_final_missing_logs_failure(self):
        self.platform('board')
        d=document(); d['complete']=True
        d['cases'][0].update(status='failed',functional_pass=False,list_membership={})
        self.assertTrue(compare.compare_performance(d,self.baselines,self.out)['exported'])

    def test_partial_baseline_rows_are_missing_not_zero(self):
        self.platform('partial',functions=['exp'])
        result=compare.compare_performance(document(),self.baselines,self.out)
        self.assertEqual(result['matched_rows'],1)
        self.assertEqual(result['missing_rows'],24)
        self.assertEqual(self.rows()[1]['baseline_scalar_fp64_per_cycle'],'')

    def test_invalid_baseline_does_not_create_or_overwrite_csv(self):
        directory=self.platform('board')
        compare.compare_performance(document(),self.baselines,self.out)
        original=self.out.read_text()
        (directory/'veclibm.html').write_text(html(scalar='NaN'))
        with self.assertRaises(ValueError):
            compare.compare_performance(document(),self.baselines,self.out)
        self.assertEqual(self.out.read_text(),original)

    def test_existing_same_run_may_refresh_other_run_and_symlinks_rejected(self):
        self.platform('board')
        compare.compare_performance(document(),self.baselines,self.out)
        compare.compare_performance(document(),self.baselines,self.out)
        other=document(); other['run']='/different/run'
        with self.assertRaisesRegex(ValueError,'does not belong'):
            compare.compare_performance(other,self.baselines,self.out)
        link=self.root/'link.csv'; link.symlink_to(self.out)
        with self.assertRaisesRegex(ValueError,'symlink'):
            compare.compare_performance(document(),self.baselines,link)

    def test_exact_25_unique_current_functions_required(self):
        d=document(); d['cases'].pop()
        with self.assertRaises(ValueError):
            compare.compare_performance(d,self.baselines,self.out)
        d=document(); d['cases'][0]['original_name']=d['cases'][1]['original_name']
        with self.assertRaises(ValueError):
            compare.compare_performance(d,self.baselines,self.out)

    def test_cli_reads_results_not_live_logs(self):
        self.platform('board')
        path=self.root/'results.json'; path.write_text(json.dumps(document()))
        with patch('builtins.print'):
            status=compare.main(['--results',str(path),'--baseline-dir',str(self.baselines),'--output',str(self.out)])
        self.assertEqual(status,0)
        self.assertTrue(self.out.is_file())


if __name__ == '__main__':
    unittest.main()
