#!/usr/bin/env python3
"""验证 CSV v2 诊断恢复的原子性、来源以及与严格验收的隔离。

在仓库根目录运行：
  python3 -B -m unittest discover -s single/libmvec -p 'test*csv*.py' -v
"""
import copy
from collections import Counter
import unittest

import compact_csv as compact
from test_compact_csv import csv_text, manifest_case, replace_field, record_fields


class CsvRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.case = manifest_case()
        self.text = csv_text(self.case)
        self.lines = self.text.splitlines()

    def recover_lines(self, lines, *, case=None, newline='\n'):
        return compact.recover(newline.join(lines) + newline, case or self.case)

    def assert_count(self, result, rows):
        self.assertEqual(result['wire_format'], 'csv2')
        self.assertEqual(result['data_rows'], rows)
        self.assertEqual(result['logical_records'], rows * 10)
        self.assertEqual(len(result['records']), rows * 10)
        for record in result['records']:
            self.assertEqual(record['row_status'], 'parsed-unverified')
            self.assertIs(record['measurement_valid'], False)
            self.assertIn(record['record_type'], ('sample', 'calibration'))
        for issue in result['issues']:
            self.assertEqual(set(issue), {'line', 'message'})
            self.assertIs(type(issue['line']), int)
            self.assertGreaterEqual(issue['line'], 1)
            self.assertIsInstance(issue['message'], str)

    def test_complete_smoke_records_are_exact_unverified_and_never_pass(self):
        result = compact.recover(self.text, self.case)
        self.assert_count(result, 9)
        self.assertEqual(result['issues'], [])
        self.assertNotIn('passed', result)
        self.assertNotIn('status', result)
        self.assertEqual(result['config'], result['headers'][0])
        self.assertEqual(result['config'], {
            'case': 'perf-log', 'suite': 'performance', 'function': 'log',
            'version': 2, 'vlen_bits': 128, 'seed': 123456789, 'rounds': 10,
            'profile': 'format-smoke', 'wire_format': 'csv2',
            'source_line': 1, '_raw': self.lines[0],
        })
        for index, record in enumerate(result['records']):
            mode, r = divmod(index, 10)
            phase, phase_round = (('first', 0) if r == 0 else
                                  ('warmup', r - 1) if r < 3 else ('steady', r - 3))
            begin = 1000 * r + 100 * mode
            cycles = 0 if mode >= 6 else 10 + mode + r
            self.assertEqual(record, {
                'record_type': 'sample' if mode < 6 else 'calibration',
                'case': 'perf-log', 'suite': 'performance', 'function': 'log',
                'mode': (compact.MODES + compact.CALIBRATION_MODES)[mode],
                'n': 16, 'k': 1, 'phase': phase, 'round': phase_round,
                'order': (mode - r) % 9, 'r': r, 'cycle_begin': begin,
                'cycle_end': begin + cycles, 'cycles': cycles,
                'checksum': 7727865989143429598 + mode, 'wire_format': 'csv2',
                '_raw': self.lines[mode + 4], 'source_line': mode + 5,
                'row_status': 'parsed-unverified', 'measurement_valid': False,
            })
            for key in ('n', 'k', 'round', 'order', 'r', 'cycle_begin', 'cycle_end',
                        'cycles', 'checksum', 'source_line'):
                self.assertIs(type(record[key]), int)

    def test_every_line_prefix_smoke_and_all_full_matrices_lf_and_crlf(self):
        cases = [self.case] + [manifest_case(function=function, full=True)
                               for function in compact.FUNCTIONS]
        for case in cases:
            lines = csv_text(case).splitlines()
            for newline in ('\n', '\r\n'):
                text = ''
                source_rows = {}
                for boundary in range(len(lines) + 1):
                    with self.subTest(case=case['name'], profile=case['expected']['profile'],
                                      newline=repr(newline), boundary=boundary):
                        result = compact.recover(text, case)
                        self.assert_count(result, len(source_rows))
                        self.assertEqual(Counter(row['source_line'] for row in result['records']),
                                         Counter({line: 10 for line in source_rows}))
                        self.assertTrue(all(row['_raw'] == source_rows[row['source_line']]
                                            for row in result['records']))
                        if boundary == len(lines):
                            self.assertEqual(result['issues'], [])
                    if boundary < len(lines):
                        line = lines[boundary]
                        text += line + newline
                        if line.startswith('D,'):
                            source_rows[boundary + 1] = line

    def test_every_character_prefix_of_smoke_only_recovers_terminated_rows(self):
        terminated_d_lines = []
        cursor = 0
        for line in self.text.splitlines(keepends=True):
            cursor += len(line)
            if line.startswith('D,'):
                terminated_d_lines.append(cursor)
        for boundary in range(len(self.text) + 1):
            with self.subTest(boundary=boundary):
                result = compact.recover(self.text[:boundary], self.case)
                self.assertEqual(result['data_rows'], sum(end <= boundary
                                                         for end in terminated_d_lines))
                self.assertEqual(result['logical_records'], result['data_rows'] * 10)

    def test_full_numeric_final_row_is_ignored_until_lf_including_crlf(self):
        prefix = '\n'.join(self.lines[:5]) + '\n'
        for ending in ('', '\r'):
            result = compact.recover(prefix + self.lines[5] + ending, self.case)
            self.assert_count(result, 1)
            self.assertTrue(any(issue['line'] == 6 and 'unterminated' in issue['message']
                                for issue in result['issues']))
        for ending in ('\n', '\r\n'):
            self.assert_count(compact.recover(prefix + self.lines[5] + ending, self.case), 2)

    def test_final_non_lf_separator_is_not_a_record_boundary(self):
        prefix = '\n'.join(self.lines[:4]) + '\n'
        for separator in ('\r', '\v', '\f', '\x85', '\u2028', '\u2029'):
            with self.subTest(separator=repr(separator)):
                self.assert_count(compact.recover(prefix + self.lines[4] + separator,
                                                 self.case), 0)
                self.assert_count(compact.recover(prefix + self.lines[4] + separator +
                                                 self.lines[5] + '\n', self.case), 0)

    def test_bad_middle_d_rejects_only_itself_then_explicit_mode_recovers(self):
        for value in ('nan', 'LIBMVEC_CSV', 'LIBMVEC_CONFIG', '0', str(1 << 64)):
            with self.subTest(value=value):
                text = replace_field(self.text, 7, 22, value)
                result = compact.recover(text, self.case)
                self.assert_count(result, 8)
                self.assertEqual({row['source_line'] for row in result['records']},
                                 set(range(5, 14)) - {8})
                self.assertTrue(any(issue['line'] == 8 for issue in result['issues']))
                with self.assertRaises(ValueError):
                    compact.expand(text, self.case)

    def test_missing_and_reordered_mode_rows_use_explicit_id(self):
        lines = self.lines[:4] + [self.lines[index] for index in (12, 6, 9)]
        result = self.recover_lines(lines)
        self.assert_count(result, 3)
        self.assertEqual([row['mode'] for row in result['records'][::10]],
                         ['copy', 'auto_m1', 'direct_m2'])
        for row in result['records']:
            mode = (compact.MODES + compact.CALIBRATION_MODES).index(row['mode'])
            self.assertEqual(row['order'], (mode - row['r']) % 9)

    def test_simulator_errors_between_any_lines_preserve_context(self):
        diagnostics = ['', 'simulator uart log', 'ERROR: RTL differential mismatch',
                       'LIBMVEC_ERROR case=perf-log kind=checksum',
                       'Core 0: HIT BAD TRAP', 'D cache miss', 'M cycle counter',
                       'T counter update', 'E simulator error',
                       '\x1b[31mDIFF FAILED\x1b[0m']
        for diagnostic in diagnostics:
            for boundary in range(len(self.lines) + 1):
                with self.subTest(diagnostic=diagnostic, boundary=boundary):
                    lines = self.lines[:boundary] + [diagnostic] + self.lines[boundary:]
                    result = self.recover_lines(lines)
                    self.assert_count(result, 9)
                    for row in result['records']:
                        self.assertEqual(row['_raw'], lines[row['source_line'] - 1])

    def test_valid_later_blocks_do_not_require_previous_matrix_blocks(self):
        case = manifest_case(full=True)
        lines = csv_text(case).splitlines()
        selected = lines[:3] + lines[63:66] + lines[13:15]
        result = self.recover_lines(selected, case=case)
        self.assert_count(result, 3)
        self.assertEqual([(row['n'], row['k']) for row in result['records'][::10]],
                         [(1024, 1), (1024, 1), (16, 4)])

    def test_bad_b_clears_context_until_a_valid_b_without_new_header(self):
        invalid = ['B,0,1', 'B,17,1', 'B,16,4', 'B,16,01', 'B,16', 'B',
                   'B,16,1,extra', ' B,16,1', 'B ,16,1', 'B,16,1 ',
                   '"B,16,1"', 'B;16;1', 'B\x00,16,1', '\x00B,16,1',
                   'B,16,nan', 'B,16,' + str(1 << 64)]
        for bad_b in invalid:
            with self.subTest(bad_b=bad_b):
                result = self.recover_lines(self.lines[:5] +
                                            [bad_b, self.lines[5], self.lines[3], self.lines[6]])
                self.assert_count(result, 2)
                self.assertEqual({row['source_line'] for row in result['records']}, {5, 9})
                self.assertTrue(any(issue['line'] == 6 for issue in result['issues']))
                self.assertTrue(any(issue['line'] == 7 and 'context' in issue['message']
                                    for issue in result['issues']))

    def test_bad_headers_disable_recovery_until_fresh_full_header(self):
        invalid = []
        for field, value in ((0, 'LIBMVEC_CSVX'), (1, '1'), (1, '3'), (1, '02'),
                             (2, 'exp'), (2, 'LOG'), (3, '64'), (3, '192'),
                             (3, '0128'), (3, '128.0'), (3, str(1 << 64)),
                             (4, '123456788'), (4, '+123456789'),
                             (5, 'full'), (5, 'unknown')):
            invalid.append(replace_field(self.text, 0, field, value).splitlines()[0])
        invalid.extend(('LIBMVEC_CSV', '[sim] ' + self.lines[0],
                        self.lines[0] + ',extra', 'M,bad', 'm,hash',
                        compact.MODE_HEADER, compact.COLUMN_HEADER,
                        'LIBMVEC_CONFIG case=perf-log function=log',
                        'LIBMVEC_SAMPLE case=perf-log mode=scalar_strict'))
        for bad in invalid:
            with self.subTest(bad=bad):
                lines = self.lines[:5] + [bad] + self.lines[1:6] + self.lines[:6]
                result = self.recover_lines(lines)
                self.assert_count(result, 3)
                self.assertEqual({row['source_line'] for row in result['records']}, {5, 16, 17})
                self.assertEqual([header['source_line'] for header in result['headers']], [1, 12])
                self.assertTrue(any(issue['line'] == 6 for issue in result['issues']))

    def test_no_recovery_with_incomplete_or_missing_header_and_b(self):
        for removed in range(4):
            with self.subTest(removed=removed):
                result = self.recover_lines(self.lines[:removed] + self.lines[removed + 1:])
                self.assert_count(result, 0)
        for index in (1, 2):
            text = '\n'.join(self.lines[:index] + [self.lines[index] + ',bad'] +
                             self.lines[index + 1:]) + '\n'
            self.assert_count(compact.recover(text, self.case), 0)

    def test_duplicates_preserve_all_occurrences_and_report_source(self):
        result = self.recover_lines(self.lines[:5] + [self.lines[4]] + self.lines[5:])
        self.assert_count(result, 10)
        self.assertEqual({row['source_line'] for row in result['records'][:20]}, {5, 6})
        self.assertTrue(any(issue['line'] == 6 and 'duplicate D' in issue['message']
                            for issue in result['issues']))
        repeated = self.recover_lines(self.lines[:5] + self.lines[3:5])
        self.assert_count(repeated, 2)
        self.assertEqual({row['source_line'] for row in repeated['records']}, {5, 7})
        self.assertTrue(any(issue['line'] == 6 and 'repeated B' in issue['message']
                            for issue in repeated['issues']))
        result = compact.recover(self.text + self.text, self.case)
        self.assert_count(result, 18)
        self.assertEqual([header['source_line'] for header in result['headers']], [1, 16])
        self.assertTrue(any(issue['line'] == 16 and 'repeated CSV header' in issue['message']
                            for issue in result['issues']))
        self.assertEqual(Counter(row['source_line'] for row in result['records']),
                         Counter({line: 10 for line in list(range(5, 14)) + list(range(20, 29))}))

    def test_changed_valid_repeated_header_preserves_both_configs(self):
        second = replace_field(self.text, 0, 3, '256')
        result = compact.recover(self.text + second, self.case)
        self.assert_count(result, 18)
        self.assertEqual([header['vlen_bits'] for header in result['headers']], [128, 256])
        self.assertEqual(result['config']['vlen_bits'], 128)

    def test_footer_and_end_errors_never_discard_good_rows_or_mark_pass(self):
        for suffix in ([], ['T,0,1,90'], ['T,7680,0,89'], ['T,not,a,count'],
                       ['E,perf-log,fail'], ['E,wrong,pass'], ['E,perf-log,pass'],
                       self.lines[-2:], self.lines[-2:] * 2):
            with self.subTest(suffix=suffix):
                result = self.recover_lines(self.lines[:-2] + suffix)
                self.assert_count(result, 9)
                self.assertNotIn('status', result)
                self.assertEqual({row['row_status'] for row in result['records']},
                                 {'parsed-unverified'})
        for end in ('E,perf-log,pass', 'E,perf-log,fail', 'E', ' E,bad', '"E,bad"'):
            with self.subTest(end=end):
                result = self.recover_lines(self.lines[:5] + [end, self.lines[5],
                                                             self.lines[3], self.lines[6]] +
                                            self.lines[:5])
                self.assert_count(result, 2)
                self.assertEqual({row['source_line'] for row in result['records']}, {5, 14})

    def test_footer_ends_b_context_but_later_valid_b_can_recover(self):
        result = self.recover_lines(self.lines[:5] +
                                    ['T,7680,0,10', self.lines[5], self.lines[3], self.lines[6]])
        self.assert_count(result, 2)
        self.assertEqual({row['source_line'] for row in result['records']}, {5, 9})

    def test_uint64_wrap_and_large_integers_remain_exact(self):
        text = replace_field(self.text, 4, 2, str(compact.U64_MAX))
        text = replace_field(text, 4, 3, str(compact.U64_MAX - 2))
        text = replace_field(text, 4, 4, '9')
        text = replace_field(text, 5, 4, str(compact.U64_MAX))
        result = compact.recover(text, self.case)
        self.assert_count(result, 9)
        row = result['records'][0]
        self.assertEqual((row['checksum'], row['cycle_begin'], row['cycles'], row['cycle_end']),
                         (compact.U64_MAX, compact.U64_MAX - 2, 9, 6))
        self.assertEqual(result['records'][10]['cycles'], compact.U64_MAX)
        self.assertEqual(result['records'][10]['cycle_end'], 99)
        self.assertTrue(all(row['cycles'] == 0 for row in result['records'][60:]))

    def test_every_d_integer_is_validated_before_atomic_recovery(self):
        invalid = ('', '-1', '+1', '01', '1.0', '1e2', '0x10', 'nan', 'inf',
                   ' 1', '1 ', '１', '١', str(1 << 64), '9' * 5000)
        for field in range(1, 23):
            for value in invalid:
                with self.subTest(field=field, value=value[:24]):
                    result = compact.recover(replace_field(self.text, 5, field, value), self.case)
                    self.assertEqual(result['data_rows'], 8)
                    self.assertEqual(result['logical_records'], 80)
                    self.assertNotIn(6, {row['source_line'] for row in result['records']})
                    self.assertTrue(any(issue['line'] == 6 for issue in result['issues']))
        for mode in ('9', str(compact.U64_MAX)):
            self.assert_count(compact.recover(replace_field(self.text, 5, 1, mode), self.case), 8)
        for field in range(4, 23, 2):
            self.assert_count(compact.recover(replace_field(self.text, 5, field, '0'), self.case), 8)

    def test_wrong_d_field_counts_and_whitespace_do_not_poison_next_row(self):
        raw = self.lines[5]
        for changed in (raw + ',extra', raw.rsplit(',', 1)[0], ' ' + raw,
                        raw + ' ', '"' + raw + '"', raw.replace(',', ',\t', 1)):
            with self.subTest(changed=changed[:40]):
                result = self.recover_lines(self.lines[:5] + [changed] + self.lines[6:])
                self.assert_count(result, 8)
                self.assertNotIn(6, {row['source_line'] for row in result['records']})

    def test_full_recovery_values_equal_strict_expansion_all_functions(self):
        for function in compact.FUNCTIONS:
            case = manifest_case(function=function, full=True)
            text = csv_text(case)
            expanded, metadata = compact.expand(text, case)
            recovered = compact.recover(text, case)
            self.assertEqual(recovered['logical_records'], metadata['logical_records'])
            expected = {}
            for line in expanded.splitlines():
                if line.startswith(('LIBMVEC_SAMPLE ', 'LIBMVEC_CALIBRATION ')):
                    fields = record_fields(line)
                    key = (fields['_wire'], fields['phase'], fields['round'])
                    expected[key] = fields
            for row in recovered['records']:
                fields = expected[(row['_raw'], row['phase'], str(row['round']))]
                for key in ('case', 'function', 'mode', 'n', 'k', 'phase', 'round',
                            'order', 'cycle_begin', 'cycle_end', 'cycles', 'checksum'):
                    self.assertEqual(str(row[key]), fields[key])

    def test_invalid_manifest_and_input_raise_instead_of_guessing(self):
        changes = {'wire_format': ('csv1', None, 2, []),
                   'function': ('abi', None), 'profile': ('unknown', None),
                   'modes': (None, []), 'calibration_modes': (None, []),
                   'lengths': ([], [16, 16], [True], [0], ['16']),
                   'batches': ([], None, [False]), 'phases': (None, []),
                   'rounds': (9, True), 'order_policy': ('none',),
                   'sample_count': (59, True), 'calibration_count': (29, False),
                   'min_checks': (-1, True)}
        for key, values in changes.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    case = copy.deepcopy(self.case)
                    case['expected'][key] = value
                    with self.assertRaises(ValueError):
                        compact.recover(self.text, case)
        for case in (None, [], {}, {'expected': []}, {**self.case, 'name': 'perf-exp'},
                     {**self.case, 'suite': 'accuracy'}):
            with self.assertRaises(ValueError):
                compact.recover(self.text, case)
        for text in (None, b'CSV', [], 1):
            with self.assertRaises(ValueError):
                compact.recover(text, self.case)

    def test_default_kv1_is_empty_and_strict_legacy_behavior_unchanged(self):
        for expected in ({}, {'wire_format': 'kv1'}):
            case = {'expected': expected}
            for text in ('', self.text, 'LIBMVEC_CONFIG case=legacy\n',
                         'LIBMVEC_SAMPLE mode=scalar_strict\n'):
                self.assertEqual(compact.recover(text, case), {
                    'wire_format': 'kv1', 'records': [], 'issues': [], 'data_rows': 0,
                    'logical_records': 0, 'config': None, 'headers': [],
                })
            with self.assertRaises(ValueError):
                compact.expand(self.text, case)
            legacy = 'LIBMVEC_CONFIG case=legacy\nLIBMVEC_COMPLETE case=legacy status=pass'
            self.assertEqual(compact.expand(legacy, case), (legacy, {'wire_format': 'kv1'}))

    def test_ansi_preserves_original_raw_and_physical_source_lines(self):
        decorated = ['\x1b[32m' + line + '\x1b[0m' for line in self.lines]
        text = 'cmd: simulator --image perf-log.bin\r\n\r\n' + '\r\n'.join(decorated) + '\r\n'
        result = compact.recover(text, self.case)
        self.assert_count(result, 9)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['config']['source_line'], 3)
        self.assertEqual(result['config']['_raw'], decorated[0])
        for row in result['records']:
            self.assertEqual(row['_raw'], decorated[row['source_line'] - 3])
        truncated = '\n'.join(decorated[:5])
        self.assert_count(compact.recover(truncated, self.case), 0)

    def test_nul_and_embedded_cr_are_never_sanitized_into_valid_data(self):
        for char in ('\x00', '\r'):
            with self.subTest(char=repr(char)):
                bad = self.lines[5].replace(',11,', ',1' + char + '1,', 1)
                result = self.recover_lines(self.lines[:5] + [bad] + self.lines[6:])
                self.assert_count(result, 8)
                self.assertNotIn(6, {row['source_line'] for row in result['records']})
                bad_header = self.lines[0].replace(',128,', ',1' + char + '28,')
                result = self.recover_lines([bad_header] + self.lines[1:])
                self.assert_count(result, 0)
                bad_b = self.lines[3].replace('16', '1' + char + '6')
                result = self.recover_lines(self.lines[:5] + [bad_b, self.lines[5],
                                                             self.lines[3], self.lines[6]])
                self.assert_count(result, 2)

    def test_separate_stream_fragments_cannot_become_a_sample(self):
        prefix = '\n'.join(self.lines[:4]) + '\n'
        split = len(self.lines[4]) // 2
        stdout = prefix + self.lines[4][:split]
        stderr = self.lines[4][split:] + '\n' + self.lines[5] + '\n'
        self.assert_count(compact.recover(stdout, self.case), 0)
        self.assert_count(compact.recover(stderr, self.case), 0)


if __name__ == '__main__':
    unittest.main()
