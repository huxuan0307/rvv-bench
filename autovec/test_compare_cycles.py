"""Synthetic measurement fixtures only: never build or launch emulators."""
import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import compare_cycles


class CycleComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.measurements = self.root / 'measurements'
        fixture = json.loads((Path(__file__).parent / 'fixtures/cycle-comparison.json').read_text())
        for run in fixture['runs']:
            directory = self.measurements / run['run_id']
            directory.mkdir(parents=True)
            metadata = copy.deepcopy(fixture['metadata'])
            metadata.update(started_at=run['started_at'], manifest_sha256=run['configuration_id'],
                            binary_sha256={r['name']: run['run_id'] for r in run['records']},
                            timeout='1m' if 'timeout' in run['run_id'] else '0')
            metadata['build']['flags'] = run.get('flags', metadata['build']['flags'])
            records = [dict(record, run_id=run['run_id'], configuration_id=run['configuration_id'],
                            outer_repetitions=1024, checksum='1.0',
                            cycles_per_outer_repetition=(record['total_cycles'] / 1024
                                                         if record['total_cycles'] else None))
                       for record in run['records']]
            (directory / 'comparison-records.json').write_text(json.dumps(records))
            (directory / 'results.json').write_text(json.dumps({'metadata': metadata}))

    def test_normalizes_retries_but_keeps_different_flags(self):
        report = compare_cycles.merge_records(self.measurements)
        self.assertEqual(len(report['configurations']), 2)
        self.assertEqual(len(report['audit']), 7)
        self.assertEqual(len(report['records']), 4)
        o3 = next(c for c in report['configurations'] if '-O3' in c['label'])
        self.assertEqual(len(o3['configuration_id']), 12)
        rows = {r['name']: r for r in report['records']
                if r['configuration_id'] == o3['configuration_id']}
        self.assertEqual(rows['s000']['run_id'], '02-pass')
        self.assertEqual(rows['s000']['total_cycles'], 4096)
        self.assertEqual(rows['s001']['run_id'], '05-latest-pass')
        self.assertEqual(rows['s002']['status'], 'failed')
        timeouts = [r for r in report['audit'] if r['status'] == 'timeout']
        self.assertEqual(len(timeouts), 2)
        self.assertTrue(all(not r['selected'] for r in timeouts))
        self.assertEqual(sum(r['selected'] for r in report['audit']), 4)

    def test_signature_sensitive_to_provenance_not_artifact_hashes(self):
        path = self.measurements / '02-pass/results.json'
        metadata = json.loads(path.read_text())['metadata']
        row = {'outer_repetitions': 1024}
        original = compare_cycles.configuration_signature(metadata, row)
        for section, key, value in [('build', 'compiler', 'new compiler version'),
                                    ('build', 'gcc', '/new/gcc'),
                                    ('build', 'sources', {'TSVC_2': {'commit': 'new'}}),
                                    ('emulator', 'mtime_ns', 999),
                                    ('reference', 'size', 999)]:
            changed = copy.deepcopy(metadata)
            changed[section][key] = value
            self.assertNotEqual(original, compare_cycles.configuration_signature(changed, row))
        for key, value in [('timing', 'different interval'), ('outer_repetitions', 32)]:
            changed = copy.deepcopy(metadata)
            changed[key] = value
            self.assertNotEqual(original, compare_cycles.configuration_signature(changed, {}))
        metadata.update(manifest_sha256='new', binary_sha256={'s000': 'new'}, timeout='99h', jobs=64)
        self.assertEqual(original, compare_cycles.configuration_signature(metadata, row))

    def test_cli_outputs_long_csv_json_audit_and_blank_markdown(self):
        output = self.root / 'output'
        with mock.patch('builtins.print'):
            self.assertEqual(compare_cycles.main(['--measurements', str(self.measurements),
                                                  '--output', str(output)]), 0)
        report = json.loads((output / 'comparison.json').read_text())
        with (output / 'comparison.csv').open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 4)
        self.assertEqual(next(r for r in rows if r['name'] == 's002')['total_cycles'], '')
        text = (output / 'comparison.md').read_text()
        self.assertIn('no speedup claims', text)
        self.assertIn('no scalar baseline', text)
        self.assertIn('| tsvc/s002 |  |  |', text)
        self.assertIn('4096', text)
        self.assertNotIn('999', text)
        self.assertEqual(len(report['audit']), 7)

    def test_requires_sibling_metadata_instead_of_unsafe_grouping(self):
        path = self.measurements / '02-pass/results.json'
        # Modify a fixture rather than deleting any path.
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'metadata.build'):
            compare_cycles.merge_records(self.measurements)

    def test_empty_input_is_actionable_error(self):
        with self.assertRaisesRegex(ValueError, 'no .*comparison-records.json'):
            compare_cycles.merge_records(self.root / 'empty')


if __name__ == '__main__':
    unittest.main()
