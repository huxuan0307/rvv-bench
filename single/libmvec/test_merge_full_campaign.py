"""Regression tests for strict four-family metadata and record mapping."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import merge_full_campaign as merge


class MergeTests(unittest.TestCase):
    def fixture(self):
        functions = merge.analyze.FUNCTIONS
        harness = ('kernel-loop.c', 'kernel-direct.c', 'kernel-array.c',
                   'kernel-calibration.c', 'kernel.h', 'perf.c', 'entry_rne.c',
                   'tls_anchor.c', 'av_runtime.c', 'av_runtime.h',
                   'veclibm-runtime.c', 'tls.ld', 'link.ld')
        ident = {'sha256': '1' * 64}
        manifests, docs = [], []
        for i, (_, _, interface) in enumerate(merge.LABELS):
            manifests.append({
                'direct_interface': interface,
                'cases': [{'name': 'perf-' + f,
                           'expected': {'function': f, 'profile': 'full'},
                           'observed_vector_calls': {
                               'auto_m1': ['_ZGV1Nxv_' + f],
                               'auto_m2': ['_ZGV2Nxv_' + f]}}
                          for f in functions],
                'math_archive': {'sha256': str(min(i + 2, 4)) * 64},
                'sources': {str(Path('source') / p): copy.deepcopy(ident)
                            for p in harness},
                'toolchain': {k: {'test': copy.deepcopy(ident)}
                              for k in ('tools', 'headers', 'libraries')},
                'support_archives': {'support': copy.deepcopy(ident)},
                'lto': False})
            docs.append({'cases': [{'name': 'perf-' + f,
                                    'config': {'function': f}, 'vlen_bits': 128}
                                   for f in functions],
                         'tools': {k: copy.deepcopy(ident)
                                   for k in ('gsim', 'reference', 'dram_config')}})
        return docs, manifests

    def test_metadata_and_fourth_family_identity_rejection(self):
        docs, manifests = self.fixture()
        self.assertEqual(len(merge.metadata(docs, manifests)['build_identities']), 4)
        for group in ('headers', 'tools', 'libraries'):
            with self.subTest(group=group):
                bad = copy.deepcopy(manifests)
                bad[3]['toolchain'][group]['test']['sha256'] = 'a' * 64
                with self.assertRaises(ValueError):
                    merge.metadata(docs, bad)

    def test_runtime_interface_and_auto_call_rejection(self):
        for kind in ('runtime', 'interface', 'auto', 'archive'):
            with self.subTest(kind=kind):
                docs, manifests = self.fixture()
                if kind == 'runtime':
                    docs[2]['tools']['gsim']['sha256'] = 'a' * 64
                elif kind == 'interface':
                    manifests[3]['direct_interface'] = 'nx'
                elif kind == 'auto':
                    manifests[3]['cases'][0]['observed_vector_calls']['auto_m1'] = ['logvec_m1']
                else:
                    manifests[3]['math_archive']['sha256'] = 'a' * 64
                with self.assertRaises(ValueError):
                    merge.metadata(docs, manifests)

    def test_array_record_mapping_preserves_rounds_and_controls(self):
        records = [{'mode': mode, 'round': 3, 'order': order, 'cycles': 100}
                   for order, mode in enumerate(merge.analyze.MODES)]
        doc = {'state': 'passed', 'cases': [{
            'suite': 'performance', 'status': 'passed', 'measurement_valid': True,
            'sample_records': records[:6], 'calibration_records': records[6:]}]}
        original = copy.deepcopy(doc)
        with patch.object(merge.analyze, 'validate_samples') as validate:
            rows = merge.source_rows(doc, 'new', 'aux-array')
        self.assertEqual(doc, original)
        self.assertEqual([r['mode'] for r in rows], list(merge.analyze.MODES))
        self.assertEqual([r['interface'] for r in rows],
                         ['scalar', 'scalar', 'Nx', 'Nx', 'array', 'array',
                          'control', 'control', 'control'])
        self.assertTrue(all(r['round'] == 3 for r in rows))
        validate.assert_called_once_with(rows, merge.analyze.FUNCTIONS)


if __name__ == '__main__':
    unittest.main()
