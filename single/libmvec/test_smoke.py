"""小参数格式验证必须隔离于完整性能验收。"""
import copy
import unittest
import runner_common as common
import collect
from test_run import manifest_document, guest_text
import test_analyze
from pathlib import Path
import tempfile

class SmokeTests(unittest.TestCase):
    def plan(self):
        with tempfile.TemporaryDirectory() as d:
            path=manifest_document(Path(d),['perf-log'])
            return common.load_manifest(path)['cases'][0]

    def smoke(self):
        case=self.plan();entry=copy.deepcopy(case['entry'])
        entry['expected'].update(profile='format-smoke',lengths=[16],batches=[1],sample_count=60,calibration_count=30)
        case['expected']=common.expected_plan(entry,'performance','perf-log')
        return case

    def test_counts_and_rotation(self):
        case=self.smoke();plan=case['expected']
        self.assertEqual(common.expected_counts(plan),{'sample_count':60,'calibration_count':30})
        self.assertEqual(len(common.rotation_schedule(plan)),90)
        self.assertEqual(plan['lengths'],[16]);self.assertEqual(plan['batches'],[1])

    def test_smoke_requires_guest_profile(self):
        case=self.smoke();text=guest_text(case);issues=[]
        collect.audit_config(text,case,issues)
        self.assertIn('CONFIG profile mismatch',issues)
        text=text.replace('rounds=10','rounds=10 profile=format-smoke')
        issues=[];collect.audit_config(text,case,issues)
        self.assertFalse(issues)
        metrics=collect.audit_performance(text,case,issues)
        self.assertEqual(metrics['sample_count'],60);self.assertEqual(metrics['calibration_count'],30)
        self.assertFalse(issues)

    def test_no_silent_full_matrix_reduction(self):
        case=self.smoke()
        for profile in ('full','typo'):
            entry={'expected':{**case['expected'],'profile':profile}}
            with self.assertRaises(common.ManifestError):common.expected_plan(entry,'performance','perf-log')

    def test_smoke_not_counted_as_full_family(self):
        doc=test_analyze.document();doc['cases'][0]['profile']='format-smoke'
        result=test_analyze.AnalyzeTests().call(doc)
        self.assertFalse(result['full_acceptance']);self.assertIn('log',result['missing_functions'])
        self.assertEqual(result['pilot_runs']['log'],0)
        self.assertTrue(all(g['profile']=='format-smoke' for g in result['groups']))

if __name__=='__main__':unittest.main()
