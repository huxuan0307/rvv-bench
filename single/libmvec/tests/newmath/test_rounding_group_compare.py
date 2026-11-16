#!/usr/bin/env python3
"""Host-only synthetic tests for the independent group comparator."""
import array
import csv
import gzip
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import rounding_accuracy as observer
import rounding_group_compare as group


class GroupCompareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='20261114-190000-group-host-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'baseline' / 'analysis'
        self.cand = self.root / 'candidate' / 'analysis'
        self.base.mkdir(parents=True)
        self.cand.mkdir(parents=True)
        self.output = self.base / 'group-compare.csv'
        self.make_run(self.base, 'baseline')
        self.make_run(self.cand, 'candidate')

    def make_run(self, directory, role):
        functions = ('expf', 'pow')
        group_rows, lane_rows, coverage = [], [], []
        for fn in functions:
            width = 32 if fn == 'expf' else 64
            ty = 'I' if width == 32 else 'Q'
            digits = width // 4
            one = 0x3f800000 if width == 32 else 0x3ff0000000000000
            xs = array.array(ty, (one, one + 1, one + 2))
            zs = array.array(ty, (one + 3, one + 4, one + 5)) if fn == 'pow' else None
            refs = array.array(ty, xs)
            corpus = (xs, zs, refs)
            sem = [(0, one if zs is not None else 0), (1, one + 1 if zs is not None else 0)]
            total_groups = emitted = 0
            for iface_num, iface in enumerate(('Nx', 'array')):
                for m in (1, 2):
                    specs = list(observer.expected_groups(fn, iface_num, m, 16, corpus, sem))
                    for rm in range(5):
                        for kind, gid, n, emit, incoming, inputs in specs:
                            change = role == 'candidate' and fn == 'expf' and iface == 'Nx' and m == 1 and rm == 2 and kind == 'C'
                            group_rows.append(dict(function=fn, interface=iface, lmul=m, frm=rm,
                                kind=kind, group_id=gid, n=n, emitted=emit, incoming_vl=incoming,
                                frm_after=rm, flags=17 if change else 1, errno=34 if change else 0))
                            total_groups += 1
                            emitted += emit
                    samples = [('C' + str(i), xs[i], zs[i] if zs is not None else None, refs[i]) for i in range(len(xs))]
                    for kind, gid, n, emit, incoming, inputs in specs:
                        if kind == 'S':
                            samples.extend((f'S{gid}:{i}', x, z if zs is not None else None, None)
                                           for i, (x, z) in enumerate(inputs))
                    for sid, x, z, ref in samples:
                        for rm in range(5):
                            y = (x + rm + (1 if role == 'candidate' else 0)) & ((1 << width) - 1)
                            lane_rows.append(dict(function=fn, interface=iface, lmul=m, sample_id=sid, frm=rm,
                                x_bits=f'{x:0{digits}x}', z_bits='' if z is None else f'{z:0{digits}x}',
                                y_bits=f'{y:0{digits}x}', ref_bits='' if ref is None else f'{ref:0{digits}x}',
                                rne_ref_ulp=''))
            coverage.append(dict(function=fn, vlenb=16, groups=total_groups,
                                 emitted_lanes=emitted, corpus_samples=len(xs)))
        self.csv_gz(directory / 'groups.csv.gz', group.GROUP_FIELDS, group_rows)
        self.csv_gz(directory / 'lanes.csv.gz', group.LANE_FIELDS, lane_rows)
        self.csv_file(directory / 'coverage.csv', group.COVERAGE_FIELDS, coverage)
        (directory / 'summary.csv').write_text('ok\n', encoding='ascii')
        (directory / 'paired.csv').write_text('ok\n', encoding='ascii')
        info = dict(run_role=role, archive_sha256=('a' if role == 'baseline' else 'b') * 64,
                    functions=','.join(functions), environment_sha256='c' * 64, observer_sha256='d' * 64)
        self.seal(directory, info)

    @staticmethod
    def csv_gz(path, fields, rows):
        with gzip.open(path, 'wt', encoding='ascii', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def csv_file(path, fields, rows):
        with path.open('w', encoding='ascii', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def seal(self, directory, info=None):
        if info is None:
            info = {r['role']: r['value'] for r in group.rows(directory / 'COMPLETE.csv', ('role', 'value'))}
        for label, filename in group.FILES.items():
            info[label + '_sha256'] = hashlib.sha256((directory / filename).read_bytes()).hexdigest()
        self.csv_file(directory / 'COMPLETE.csv', ('role', 'value'),
                      [{'role': k, 'value': v} for k, v in info.items()])

    def change_gz(self, directory, name, fields, operation):
        path = directory / name
        data = list(group.rows(path, fields, compressed=True))
        operation(data)
        self.csv_gz(path, fields, data)
        self.seal(directory)

    def test_success_reports_groups_flags_errno_and_lane_counts(self):
        groups, lanes, changed = group.compare(self.base, self.cand, self.output)
        data = list(group.rows(self.output, group.OUT_FIELDS))
        differences = [r for r in data if r['record_type'] == 'group']
        summaries = [r for r in data if r['record_type'] == 'summary']
        self.assertGreater(groups, 0)
        self.assertGreater(lanes, 0)
        self.assertEqual(changed, 1)
        self.assertEqual(len(differences), 1)
        row = differences[0]
        self.assertEqual((row['function'], row['interface'], row['lmul'], row['frm'], row['kind'], row['group_id']),
                         ('expf', 'Nx', '1', '2', 'C', '0'))
        self.assertEqual((row['baseline_fflags'], row['candidate_fflags'], row['fflags_xor']), ('1', '17', '16'))
        self.assertEqual((row['baseline_errno'], row['candidate_errno']), ('0', '34'))
        self.assertEqual((row['frm_after_differences'], row['fflags_differences'], row['errno_differences']), ('0', '1', '1'))
        summary = next(r for r in summaries if tuple(r[k] for k in ('function', 'interface', 'lmul', 'frm', 'kind')) == ('expf', 'Nx', '1', '2', 'C'))
        self.assertEqual((summary['groups_compared'], summary['any_state_differences'], summary['lane_bit_differences']), ('1', '1', '3'))
        self.assertEqual(sum(int(r['groups_compared']) for r in summaries), groups)
        self.assertTrue(all(len(r['comparator_sha256']) == 64 for r in data))
        self.assertFalse(list(self.base.glob('*.partial')))

    def test_all_groups_and_frm_after_difference(self):
        def change(data):
            data[0]['frm_after'] = '4'
        self.change_gz(self.cand, 'groups.csv.gz', group.GROUP_FIELDS, change)
        groups, _, changed = group.compare(self.base, self.cand, self.output, all_groups=True)
        data = list(group.rows(self.output, group.OUT_FIELDS))
        records = [r for r in data if r['record_type'] == 'group']
        self.assertEqual(len(records), groups)
        self.assertEqual(changed, 2)
        self.assertEqual(records[0]['frm_after_differences'], '1')

    def test_hash_mismatch_rejected_without_output(self):
        with (self.cand / 'summary.csv').open('a') as stream:
            stream.write('corrupt\n')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            group.compare(self.base, self.cand, self.output)
        self.assertFalse(self.output.exists())

    def test_changed_group_identity_rejected_even_if_resealed(self):
        self.change_gz(self.cand, 'groups.csv.gz', group.GROUP_FIELDS, lambda data: data[0].update(group_id='1'))
        with self.assertRaisesRegex(ValueError, '无法对齐'):
            group.compare(self.base, self.cand, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob('*.partial')))

    def test_missing_extra_or_reordered_groups_rejected(self):
        for operation in (lambda data: data.pop(0), lambda data: data.append(data[-1]),
                          lambda data: data.__setitem__(slice(0, 2), list(reversed(data[:2])))):
            with self.subTest(operation=operation):
                self.make_run(self.cand, 'candidate')
                self.change_gz(self.cand, 'groups.csv.gz', group.GROUP_FIELDS, operation)
                with self.assertRaises(ValueError):
                    group.compare(self.base, self.cand, self.output)
                self.assertFalse(self.output.exists())

    def test_lane_input_mismatch_rejected_even_if_resealed(self):
        self.change_gz(self.cand, 'lanes.csv.gz', group.LANE_FIELDS,
                       lambda data: data[0].update(x_bits='00000000'))
        with self.assertRaisesRegex(ValueError, '输入或参考不一致'):
            group.compare(self.base, self.cand, self.output)
        self.assertFalse(self.output.exists())

    def test_cross_mode_input_mismatch_even_if_both_runs_match(self):
        for directory in (self.base, self.cand):
            self.change_gz(directory, 'lanes.csv.gz', group.LANE_FIELDS,
                           lambda data: data[2].update(x_bits='00000000'))
        with self.assertRaisesRegex(ValueError, '模式之间输入不一致'):
            group.compare(self.base, self.cand, self.output)

    def test_missing_lane_and_extra_lane_rejected(self):
        for operation in (lambda data: data.pop(), lambda data: data.append(data[-1])):
            with self.subTest(operation=operation):
                self.make_run(self.cand, 'candidate')
                self.change_gz(self.cand, 'lanes.csv.gz', group.LANE_FIELDS, operation)
                with self.assertRaises(ValueError):
                    group.compare(self.base, self.cand, self.output)
                self.assertFalse(self.output.exists())

    def test_identity_role_environment_observer_and_archive(self):
        original = {r['role']: r['value'] for r in group.rows(self.cand / 'COMPLETE.csv', ('role', 'value'))}
        for key, value in (('run_role', 'baseline'), ('environment_sha256', 'e' * 64),
                           ('observer_sha256', 'e' * 64), ('archive_sha256', 'a' * 64),
                           ('functions', 'expf')):
            with self.subTest(field=key):
                info = dict(original)
                info[key] = value
                self.seal(self.cand, info)
                with self.assertRaises(ValueError):
                    group.compare(self.base, self.cand, self.output)
                self.assertFalse(self.output.exists())

    def test_changed_input_during_comparison_rejected(self):
        original = group.check_lanes
        def changed(*args):
            result = original(*args)
            with (self.cand / 'paired.csv').open('a') as stream:
                stream.write('changed\n')
            return result
        with mock.patch.object(group, 'check_lanes', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                group.compare(self.base, self.cand, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob('*.partial')))

    def test_invalid_coverage_and_output_no_clobber(self):
        output = self.base / 'already.csv'
        output.write_text('preserve', encoding='ascii')
        with self.assertRaises(ValueError):
            group.compare(self.base, self.cand, output)
        self.assertEqual(output.read_text(), 'preserve')
        with self.assertRaises(ValueError):
            group.compare(self.base, self.cand, self.root / 'outside.csv')
        cover = list(group.rows(self.cand / 'coverage.csv', group.COVERAGE_FIELDS))
        cover[0]['vlenb'] = '32'
        self.csv_file(self.cand / 'coverage.csv', group.COVERAGE_FIELDS, cover)
        self.seal(self.cand)
        with self.assertRaisesRegex(ValueError, 'coverage'):
            group.compare(self.base, self.cand, self.output)

    def test_cli_and_failure_to_publish(self):
        with mock.patch.object(group.os, 'link', side_effect=OSError('test failure')):
            with self.assertRaisesRegex(OSError, 'test failure'):
                group.compare(self.base, self.cand, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob('*.partial')))
        command = [sys.executable, str(Path(group.__file__).resolve()), '--baseline', str(self.base),
                   '--candidate', str(self.cand), '--output', str(self.output)]
        result = subprocess.run(command, cwd=self.root, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('不构成 sticky/ABI', result.stdout)
        self.assertTrue(self.output.is_file())


if __name__ == '__main__':
    unittest.main()
