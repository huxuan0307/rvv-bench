#!/usr/bin/env python3
"""Three-mode profile schema, header and round-rotation regression tests."""
import copy
import unittest
import compact_csv as compact
import runner_common as common
from test_compact_csv import manifest_case


def fixture(function='log'):
    case = manifest_case(function=function, full=True)
    plan = case['expected']
    batches = [1, 4, 16] if function in ('log', 'exp') else [1, 4]
    plan.update(profile='n16-trim', lengths=[16], batches=batches,
                modes=['scalar_fast', 'direct_m1', 'direct_m2'],
                calibration_modes=[], order_policy='rotate-all-3',
                sample_count=30*len(batches), calibration_count=0)
    rows = [f'LIBMVEC_CSV,2,{function},128,123456789,n16-trim',
            'M,fast,direct1,direct2', compact.COLUMN_HEADER]
    for k in batches:
        rows.append(f'B,16,{k}')
        for m in range(3):
            rows.append(','.join(['D', str(m), '123'] +
                                 [str(v) for r in range(10) for v in (r*100+m*10, 7)]))
    rows += [f'T,3000,0,{30*len(batches)}', f'E,perf-{function},pass']
    return case, '\n'.join(rows)+'\n'


class TrimTests(unittest.TestCase):
    def test_all_seven_roundtrips(self):
        for function in common.PERF_FUNCTIONS:
            case, text = fixture(function)
            plan = common.expected_plan(case, 'performance', case['name'])
            expanded, metadata = compact.expand(text, case)
            recovered = compact.recover(text, case)
            self.assertEqual(recovered['issues'], [])
            self.assertEqual(metadata['logical_records'], plan['sample_count'])
            self.assertNotIn('LIBMVEC_CALIBRATION', expanded)
            got = sorted((r['n'], r['k'], r['phase'], r['round'], r['order'], r['mode'])
                         for r in recovered['records'])
            want = sorted((r['n'], r['k'], r['phase'], r['round'], r['order'], r['mode'])
                          for r in common.rotation_schedule(plan))
            self.assertEqual(got, want)

    def test_cross_profile_headers_and_extra_modes_rejected(self):
        case, text = fixture()
        for header in (compact.MODE_HEADER, 'M,direct1,fast,direct2', 'M,fast,direct1,direct2,copy'):
            with self.assertRaises(ValueError):
                compact.expand(text.replace('M,fast,direct1,direct2', header), case)
            self.assertEqual(compact.recover(text.replace('M,fast,direct1,direct2', header), case)['records'], [])
        for key, value in [('modes', list(common.MODES)), ('calibration_modes', ['copy']),
                           ('order_policy', 'rotate-all-9'), ('sample_count', 180),
                           ('calibration_count', 90), ('lengths', [16, 128])]:
            broken = copy.deepcopy(case)
            broken['expected'][key] = value
            with self.assertRaises(common.ManifestError):
                common.expected_plan(broken, 'performance', broken['name'])

if __name__ == '__main__':
    unittest.main()
