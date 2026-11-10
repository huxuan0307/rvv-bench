#!/usr/bin/env python3
"""CSV v2 主机解码测试；只使用内存，不创建临时文件、不运行仿真。

在仓库根目录运行：
  python3 -B -m unittest discover -s single/libmvec -p test_compact_csv.py -v
"""
import copy
import unittest

import compact_csv as compact
import collect
import runner_common as common


MATH_MODES = ['scalar_strict', 'scalar_fast', 'auto_m1', 'auto_m2',
              'direct_m1', 'direct_m2']
CONTROL_MODES = ['rdcycle', 'empty', 'copy']


def manifest_case(*, function='log', full=False):
    lengths = [16, 128, 1024] if full else [16]
    batches = ([1, 4, 16] if function in ('log', 'exp') else [1, 4]) if full else [1]
    blocks = len(lengths) * len(batches)
    return {
        'name': f'perf-{function}', 'suite': 'performance',
        'required_markers': [f'E,perf-{function},pass'],
        'expected': {
            'wire_format': 'csv2', 'function': function,
            'profile': 'full' if full else 'format-smoke',
            'lengths': lengths, 'batches': batches,
            'modes': MATH_MODES.copy(), 'calibration_modes': CONTROL_MODES.copy(),
            'phases': {'first': 1, 'warmup': 2, 'steady': 7}, 'rounds': 10,
            'order_policy': 'rotate-all-9', 'sample_count': 60 * blocks,
            'calibration_count': 30 * blocks, 'min_checks': 1,
        },
    }


def csv_text(case=None):
    case = case or manifest_case()
    plan = case['expected']
    lines = [
        f'LIBMVEC_CSV,2,{plan["function"]},128,123456789,{plan["profile"]}',
        'M,strict,fast,auto1,auto2,direct1,direct2,rdcycle,empty,copy',
        'm,hash,b0,c0,b1,c1,b2,c2,b3,c3,b4,c4,b5,c5,b6,c6,b7,c7,b8,c8,b9,c9',
    ]
    for ni, n in enumerate(plan['lengths']):
        for ki, k in enumerate(plan['batches']):
            lines.append(f'B,{n},{k}')
            for mode in range(9):
                fields = ['D', str(mode), str(7727865989143429598 + mode)]
                for r in range(10):
                    begin = 1000000 * ni + 100000 * ki + 1000 * r + 100 * mode
                    cycles = 0 if mode >= 6 else 10 + mode + r
                    fields.extend((str(begin), str(cycles)))
                lines.append(','.join(fields))
    total = plan['sample_count'] + plan['calibration_count']
    lines.extend((f'T,7680,0,{total}', f'E,{case["name"]},pass'))
    return '\n'.join(lines) + '\n'


def replace_field(text, line_index, field_index, value):
    lines = text.splitlines()
    fields = lines[line_index].split(',')
    fields[field_index] = value
    lines[line_index] = ','.join(fields)
    return '\n'.join(lines) + '\n'


def record_fields(line):
    return dict(field.split('=', 1) for field in line.split()[1:])


class CompactCsvTests(unittest.TestCase):
    def setUp(self):
        self.case = manifest_case()
        self.text = csv_text(self.case)

    def reject(self, text, case=None):
        with self.assertRaises(ValueError):
            compact.expand(text, self.case if case is None else case)

    def test_smoke_expands_all_90_records_and_current_audit_accepts(self):
        expanded, metadata = compact.expand(self.text, self.case)
        self.assertEqual(metadata, {
            'wire_format': 'csv2', 'physical_rows': 15, 'data_rows': 9,
            'logical_records': 90, 'csv_text': self.text,
            'end_counter': 'derived-modulo-2^64',
            'checksum': 'shared-after-guest-equality-check',
        })
        lines = expanded.splitlines()
        self.assertEqual(len(lines), 93)
        self.assertEqual(lines[0], 'LIBMVEC_CONFIG case=perf-log function=log '
                         'vlen_bits=128 seed=123456789 rounds=10 '
                         'profile=format-smoke wire_format=csv2')
        source_lines = self.text.splitlines()
        for index, line in enumerate(lines[1:91]):
            r, order = divmod(index, 9)
            mode = (order + r) % 9
            fields = record_fields(line)
            phase, phase_round = (('first', 0) if r == 0 else
                                  ('warmup', r - 1) if r < 3 else ('steady', r - 3))
            begin, cycles = 1000 * r + 100 * mode, (0 if mode >= 6 else 10 + mode + r)
            expected_tag = 'LIBMVEC_SAMPLE' if mode < 6 else 'LIBMVEC_CALIBRATION'
            self.assertTrue(line.startswith(expected_tag + ' '))
            self.assertEqual(fields, {
                'case': 'perf-log', 'function': 'log',
                'mode': (MATH_MODES + CONTROL_MODES)[mode], 'n': '16', 'k': '1',
                'phase': phase, 'round': str(phase_round), 'order': str(order),
                'cycle_begin': str(begin), 'cycle_end': str(begin + cycles),
                'cycles': str(cycles), 'checksum': str(7727865989143429598 + mode),
                'wire_format': 'csv2', '_wire': source_lines[4 + mode],
            })
        self.assertEqual(lines[-2], 'LIBMVEC_CHECK case=perf-log checks=7680 failed=0')
        self.assertEqual(lines[-1], 'LIBMVEC_COMPLETE case=perf-log status=pass')
        self.assertNotIn('LIBMVEC_CSV', expanded)
        issues = []
        config = collect.audit_config(expanded, self.case, issues)
        metrics = collect.audit_performance(expanded, self.case, issues)
        collect.audit_check(expanded, self.case, issues)
        canonical_case = copy.deepcopy(self.case)
        canonical_case['required_markers'] = ['LIBMVEC_COMPLETE case=perf-log status=pass']
        collect.audit_complete(expanded, canonical_case, issues)
        self.assertEqual(issues, [])
        self.assertEqual(config['profile'], 'format-smoke')
        self.assertEqual(metrics['sample_count'], 60)
        self.assertEqual(metrics['calibration_count'], 30)

    def test_multiple_expected_blocks_and_all_functions(self):
        for function in ('log', 'exp', 'sin', 'cos', 'pow', 'logf', 'expf'):
            with self.subTest(function=function):
                case = manifest_case(function=function, full=True)
                expanded, metadata = compact.expand(csv_text(case), case)
                blocks = 9 if function in ('log', 'exp') else 6
                self.assertEqual(metadata['physical_rows'], 5 + 10 * blocks)
                self.assertEqual(metadata['data_rows'], 9 * blocks)
                self.assertEqual(metadata['logical_records'], 90 * blocks)
                issues = []
                collect.audit_config(expanded, case, issues)
                collect.audit_performance(expanded, case, issues)
                self.assertEqual(issues, [])

    def test_uint64_wrap_large_hash_and_calibration_zero(self):
        text = replace_field(self.text, 4, 2, str((1 << 64) - 1))
        text = replace_field(text, 4, 3, str((1 << 64) - 3))
        text = replace_field(text, 4, 4, '9')
        text = replace_field(text, 5, 4, str((1 << 64) - 1))
        expanded, _ = compact.expand(text, self.case)
        rows = common.records(expanded, 'LIBMVEC_SAMPLE')
        self.assertEqual(rows[0]['cycle_begin'], '18446744073709551613')
        self.assertEqual(rows[0]['cycle_end'], '6')
        self.assertEqual(rows[0]['cycles'], '9')
        self.assertEqual(rows[0]['checksum'], '18446744073709551615')
        self.assertEqual(rows[1]['cycles'], '18446744073709551615')
        self.assertEqual(rows[1]['cycle_end'], '99')
        issues = []
        collect.audit_performance(expanded, self.case, issues)
        self.assertEqual(issues, [])
        self.assertTrue(all(row['cycles'] == '0' for row in
                            common.records(expanded, 'LIBMVEC_CALIBRATION')))

    def test_preserves_simulator_logs_errors_and_csv_provenance(self):
        prefix = '\x1b[32mSimulator startup\x1b[0m\r\nLIBMVEC_ERROR case=perf-log kind=test\n'
        suffix = 'Core 0: HIT GOOD TRAP\r\nPERF counters,M,D,B,T,E\ntrailer without newline'
        expanded, metadata = compact.expand(prefix + self.text + suffix, self.case)
        self.assertTrue(expanded.startswith(prefix + 'LIBMVEC_CONFIG'))
        self.assertTrue(expanded.endswith('\n' + suffix))
        self.assertEqual(metadata['csv_text'], self.text)
        self.assertEqual(metadata['physical_rows'], 15)
        self.assertEqual(expanded.count('LIBMVEC_ERROR'), 1)

    def test_crlf_and_missing_final_newline(self):
        for text in (self.text.replace('\n', '\r\n'), self.text.rstrip('\n')):
            with self.subTest(crlf='\r' in text):
                expanded, metadata = compact.expand(text, self.case)
                self.assertEqual(metadata['physical_rows'], 15)
                self.assertTrue(metadata['csv_text'].endswith('\n'))
                self.assertEqual(expanded.endswith('\n'), text.endswith('\n'))
                self.assertNotIn('\r', expanded)

    def test_bad_header_fields_and_unknown_version(self):
        changes = ((0, ''), (0, 'LIBMVEC_CSVX'), (1, '1'), (1, '3'), (1, '02'),
                   (2, 'exp'), (2, 'LOG'), (3, '0'), (3, '64'), (3, '192'),
                   (3, '0128'), (3, '128.0'), (3, str(1 << 64)),
                   (4, '123456788'), (4, '+123456789'),
                   (5, 'full'), (5, 'unknown'))
        for index, value in changes:
            with self.subTest(index=index, value=value):
                self.reject(replace_field(self.text, 0, index, value))
        for tag in ('M,strict,fast,auto2,auto1,direct1,direct2,rdcycle,empty,copy',
                    'M,scalar_strict,fast,auto1,auto2,direct1,direct2,rdcycle,empty,copy'):
            self.reject(self.text.replace(self.text.splitlines()[1], tag))
        self.reject(self.text.replace('b9,c9', 'b10,c10'))
        self.reject(self.text.replace('b0,c0', 'c0,b0'))

    def test_every_missing_or_duplicate_line_is_rejected(self):
        lines = self.text.splitlines()
        for index in range(len(lines)):
            with self.subTest(missing=index):
                self.reject('\n'.join(lines[:index] + lines[index + 1:]) + '\n')
            with self.subTest(duplicate=index):
                self.reject('\n'.join(lines[:index] + [lines[index]] + lines[index:]) + '\n')
        for index in range(len(lines)):
            with self.subTest(truncated=index):
                self.reject('\n'.join(lines[:index]))

    def test_rows_blocks_out_of_order_or_out_of_range(self):
        lines = self.text.splitlines()
        lines[4], lines[5] = lines[5], lines[4]
        self.reject('\n'.join(lines) + '\n')
        for value in ('9', '18446744073709551615', '-1', '01'):
            self.reject(replace_field(self.text, 4, 1, value))
        for field, value in ((1, '0'), (1, '17'), (2, '0'), (2, '4')):
            self.reject(replace_field(self.text, 3, field, value))
        case = manifest_case(full=True)
        lines = csv_text(case).splitlines()
        self.reject('\n'.join(lines[:3] + lines[13:23] + lines[3:13] + lines[23:]) + '\n', case)
        self.reject('\n'.join(lines[:13] + lines[23:]) + '\n', case)
        self.reject('\n'.join(lines[:13] + lines[3:13] + lines[13:]) + '\n', case)

    def test_multiple_csv_sections_and_extra_reserved_records(self):
        self.reject(self.text + self.text)
        self.reject(self.text + '\n' + self.text)
        extra_lines = self.text.splitlines() + [
            'D', 'B,', 'M,bad', 'T,1,0,1', 'E,perf-log,pass',
            'm,hash', 'LIBMVEC_CSV,3,log,128,123456789,full',
            ' D,0,1', 'D ,0,1', '[sim] LIBMVEC_CSV,2',
        ]
        for line in extra_lines:
            with self.subTest(line=line):
                self.reject(line + '\n' + self.text)
                self.reject(self.text + line + '\n')

    def test_every_extra_in_block_line_is_rejected(self):
        lines = self.text.splitlines()
        for junk in ('', 'simulator log', 'LIBMVEC_ERROR case=perf-log kind=test',
                     'X,1', 'LIBMVEC_ABI symbol=anything checks=1 failed=0'):
            for index in range(1, len(lines)):
                with self.subTest(junk=junk, index=index):
                    self.reject('\n'.join(lines[:index] + [junk] + lines[index:]) + '\n')

    def test_mixed_kv_rejected_inside_and_outside(self):
        for tag in ('CONFIG', 'SAMPLE', 'CALIBRATION', 'CHECK', 'COMPLETE'):
            row = f'LIBMVEC_{tag} case=perf-log status=pass\n'
            for text in (row + self.text, self.text + row,
                         self.text.replace('B,16,1\n', 'B,16,1\n' + row),
                         '[sim] ' + row + self.text):
                with self.subTest(tag=tag, text=text[:30]):
                    self.reject(text)
        self.reject('LIBMVEC_CONFIG case=perf-log function=log\n'
                    'LIBMVEC_CHECK case=perf-log checks=7680 failed=0\n'
                    'LIBMVEC_COMPLETE case=perf-log status=pass\n')

    def test_footer_count_case_and_status(self):
        for field, values in ((1, ('0', '-1', '1.0', '07680')),
                              (2, ('1', '-1', '00')),
                              (3, ('89', '91', '9', '090', 'nan', str(1 << 64)))):
            for value in values:
                self.reject(replace_field(self.text, 13, field, value))
        for field, value in ((1, 'perf-exp'), (1, 'log'), (2, 'fail'), (2, 'PASS')):
            self.reject(replace_field(self.text, 14, field, value))

    def test_all_numeric_columns_reject_noncanonical_uint64(self):
        bad_values = ('', '-1', '+1', '01', '1.0', '1e2', '0x10', 'nan',
                      'inf', ' 1', '1 ', '１', '١', str(1 << 64), '9' * 5000)
        locations = [(0, 3), (0, 4), (3, 1), (3, 2)]
        locations += [(4, field) for field in range(1, 23)]
        locations += [(13, field) for field in (1, 2, 3)]
        for line_index, field_index in locations:
            for value in bad_values:
                with self.subTest(line=line_index, field=field_index, value=value[:25]):
                    self.reject(replace_field(self.text, line_index, field_index, value))
        self.reject(replace_field(self.text, 4, 4, '0'))

    def test_wrong_field_counts_quotes_and_whitespace(self):
        lines = self.text.splitlines()
        for index, line in enumerate(lines):
            for changed in (line + ',extra', line.rsplit(',', 1)[0],
                            ' ' + line, line + ' ', '"' + line + '"',
                            line.replace(',', ',\t', 1)):
                with self.subTest(index=index, changed=changed[:35]):
                    altered = lines.copy()
                    altered[index] = changed
                    self.reject('\n'.join(altered) + '\n')

    def test_manifest_validation_and_type_errors_are_value_errors(self):
        changes = {
            'wire_format': (None, 'csv1', 2, []),
            'function': ('abi', [], None), 'profile': ('unknown', None),
            'modes': (None, MATH_MODES[::-1], MATH_MODES[:-1]),
            'calibration_modes': (None, CONTROL_MODES[::-1]),
            'lengths': ([], [16, 16], [0], [-1], [True], ['16'], [1 << 64]),
            'batches': ([], [1, 1], [False], ['1'], None),
            'phases': (None, [], {'first': True, 'warmup': 2, 'steady': 7},
                       {'first': 1, 'warmup': 1, 'steady': 8}),
            'rounds': (9, 11, True, '10'),
            'sample_count': (59, 61, True, '60', None),
            'calibration_count': (29, 31, False, '30'),
            'min_checks': (-1, True, '1'), 'order_policy': ('none', None),
        }
        for key, values in changes.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    case = copy.deepcopy(self.case)
                    case['expected'][key] = value
                    self.reject(self.text, case)
        for key in ('function', 'modes', 'calibration_modes', 'lengths', 'batches',
                    'phases', 'sample_count', 'calibration_count'):
            case = copy.deepcopy(self.case)
            del case['expected'][key]
            self.reject(self.text, case)
        for case in ([], 'case', {}, {'expected': None}, {'expected': []},
                     {**self.case, 'name': 'perf-exp'},
                     {**self.case, 'suite': 'accuracy'}):
            self.reject(self.text, case)
        for text in (None, b'CSV', [], 1):
            self.reject(text)

    def test_raw_manifest_can_derive_rounds_and_default_full_profile(self):
        case = manifest_case(full=True)
        text = csv_text(case)
        for key in ('rounds', 'profile', 'min_checks', 'order_policy'):
            del case['expected'][key]
        expanded, metadata = compact.expand(text, case)
        self.assertIn('rounds=10 profile=full', expanded)
        self.assertEqual(metadata['logical_records'], 810)

    def test_legacy_passthrough_and_rejects_all_compact_tags(self):
        for wire in ('kv1', None):
            case = {'expected': {} if wire is None else {'wire_format': wire}}
            for text in ('', 'simulator log\r\n', 'LIBMVEC_ERROR case=bad\n',
                         'LIBMVEC_CONFIG case=legacy\nLIBMVEC_COMPLETE case=legacy status=pass'):
                with self.subTest(wire=wire, text=text):
                    self.assertEqual(compact.expand(text, case), (text, {'wire_format': 'kv1'}))
            self.reject(self.text, case)
            for line in self.text.splitlines():
                self.reject(line + '\n', case)
            self.reject('LIBMVEC_CSV,999,anything\n', case)


if __name__ == '__main__':
    unittest.main()
