#!/usr/bin/env python3
"""Extract P1 CSV samples and audit them against an explicit case manifest.

A successful simulator exit alone is not coverage. Missing/duplicate samples,
failed checks and unexpected case IDs make the command fail. Raw logs stay intact.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import sys


def extract(manifest, logs, expected_samples=None):
    expected = {str(c['id']): c for c in manifest['cases']}
    if not expected:
        raise ValueError('manifest must contain at least one case')
    if expected_samples is not None and 'samples' in manifest and expected_samples != manifest['samples']:
        raise ValueError('--samples differs from manifest.samples')
    if len(expected) != len(manifest['cases']):
        raise ValueError('duplicate case id in manifest')
    if expected_samples is None:
        expected_samples = manifest.get('samples')
    if not isinstance(expected_samples, int) or expected_samples < 1:
        raise ValueError('positive --samples or manifest.samples is required')
    rows, errors, fields = [], [], None
    seen = set()
    for path in logs:
        last_sample_line = 0
        terminal_lines = []
        header = None
        text = path.read_text(errors='replace')
        if not any(line.strip() == 'P1_COMPLETE' for line in text.splitlines()):
            errors.append(f'{path}: missing P1_COMPLETE end marker')
        for marker in ('HIT CRITICAL ERROR', 'HIT BAD TRAP', 'ABORT', 'Assertion failed', 'DIFFTEST FAILED', 'Segmentation fault', 'core dumped'):
            if marker in text:
                errors.append(f'{path}: simulator reported {marker}')
        for line_number, line in enumerate(text.splitlines(), 1):
            if line.strip() == 'P1_COMPLETE':
                terminal_lines.append(line_number)
            # Serial output may have a simulator prefix before the CSV header.
            if 'case_id,kind,opcode,' in line:
                header = next(csv.reader([line[line.index('case_id,kind,opcode,'):]]))
                if fields is None:
                    fields = header
                elif fields != header:
                    errors.append(f'{path}:{line_number}: incompatible header')
                continue
            if header is None:
                continue
            values = next(csv.reader([line]))
            if len(values) != len(header):
                if line.startswith('"') or line.split(',', 1)[0] in expected:
                    errors.append(f'{path}:{line_number}: malformed CSV row')
                continue
            row = dict(zip(header, values))
            cid = row.get('case_id', '')
            if cid not in expected:
                # Accept only CSV-looking rows as unexpected cases, not footer text.
                if row.get('status') in ('pass', 'fail', 'skip', 'pending'):
                    errors.append(f'{path}:{line_number}: unexpected case {cid}')
                continue
            last_sample_line = line_number
            row_error_start = len(errors)
            key = cid, row.get('sample', '')
            if key in seen:
                errors.append(f'{path}:{line_number}: duplicate sample {key}')
            seen.add(key)
            case = expected[cid]
            for field in ('kind', 'opcode', 'sew', 'data_eew', 'lmul', 'index_eew', 'mask_pattern', 'chains',
                          'tail_policy', 'mask_policy', 'index_pattern', 'seed'):
                if field in case and str(case[field]) != row.get(field):
                    errors.append(f'{cid}: {field} differs from manifest')
            status = row.get('status')
            if status == 'skip':
                if case.get('status') != 'skip':
                    errors.append(f'{cid}: unexpected runtime skip')
                if not row.get('skip_reason') or row.get('skip_reason') != case.get('skip_reason'):
                    errors.append(f'{cid}: skip reason differs from manifest')
            elif case.get('status') == 'skip':
                errors.append(f'{cid}: manifest skip was executed')
            elif status != 'pass' or row.get('check_status') != 'pass':
                errors.append(f'{cid}: status={status}, check={row.get("check_status")}')
            else:
                try:
                    cycles, count = int(row['cycles']), int(row['target_insns'])
                    vl, active = int(row['actual_vl']), int(row['active'])
                    if cycles <= 0 or count <= 0 or vl <= 0 or not 0 <= active <= vl:
                        raise ValueError('invalid counters')
                    if 'target_insns' in case and count != int(case['target_insns']):
                        raise ValueError('target instruction count differs from manifest')
                    if 'active_expected' in case and active != int(case['active_expected']):
                        raise ValueError('active count differs from manifest')
                    if 'expected_vl' in case and vl != int(case['expected_vl']):
                        raise ValueError('actual VL differs from manifest expected_vl')
                    if int(case.get('avl', 0)) > 0 and vl != int(case['avl']):
                        raise ValueError('actual VL differs from requested profile')
                except (KeyError, ValueError) as exc:
                    errors.append(f'{cid}: invalid measurement: {exc}')
            row['cpi'] = ''
            row['active_elements_per_cycle'] = ''
            row['active_bytes_per_cycle'] = ''
            row['measurement_valid'] = status == 'pass' and row.get('check_status') == 'pass' and len(errors) == row_error_start
            if row['measurement_valid']:
                try:
                    cycles = int(row['cycles'])
                    count = int(row['target_insns'])
                    active = int(row['active'])
                    if cycles > 0:
                        row['cpi'] = count and cycles / count
                        row['active_elements_per_cycle'] = active * count / cycles
                        data_eew = case.get('data_eew')
                        if data_eew is None and row.get('opcode', '').startswith('vle8.'):
                            data_eew = 8
                        if data_eew is None:
                            data_eew = int(row['sew'])
                        row['active_bytes_per_cycle'] = active * count * int(data_eew) / 8 / cycles
                except (KeyError, ValueError):
                    pass
            row['source_log'] = str(path)
            row['source_line'] = line_number
            rows.append(row)
        if len(terminal_lines) != 1 or terminal_lines[0] <= last_sample_line:
            errors.append(f'{path}: expected one P1_COMPLETE marker after all samples')
        if header is None:
            errors.append(f'{path}: missing CSV header')
    grouped = {cid: [] for cid in expected}
    for row in rows:
        grouped[row['case_id']].append(row)
    for cid, samples in grouped.items():
        if not samples:
            errors.append(f'{cid}: missing case')
        elif expected_samples is not None:
            if all(r['status'] == 'skip' for r in samples):
                if len(samples) != 1:
                    errors.append(f'{cid}: expected one skip row')
            else:
                actual = {r['sample'] for r in samples}
                wanted = {str(i) for i in range(expected_samples)}
                if actual != wanted:
                    errors.append(f'{cid}: samples {sorted(actual)} != {sorted(wanted)}')
    coverage = {'schema_version': 1, 'expected_cases': len(expected),
                'observed_cases': sum(bool(v) for v in grouped.values()),
                'rows': len(rows),
                'pass_rows': sum(r['measurement_valid'] for r in rows),
                'expected_samples': expected_samples,
                'skip_rows': sum(r['status'] == 'skip' for r in rows),
                'errors': errors, 'complete': not errors}
    return fields, rows, coverage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--samples', type=int)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--coverage', required=True, type=Path)
    parser.add_argument('logs', nargs='+', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    fields, rows, coverage = extract(manifest, args.logs, args.samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=(fields or []) + [
            'measurement_valid', 'cpi', 'active_elements_per_cycle', 'active_bytes_per_cycle',
            'source_log', 'source_line'])
        writer.writeheader()
        writer.writerows(rows)
    args.coverage.parent.mkdir(parents=True, exist_ok=True)
    args.coverage.write_text(json.dumps(coverage, indent=2) + '\n')
    print(json.dumps(coverage, indent=2))
    return 0 if coverage['complete'] else 1


if __name__ == '__main__':
    sys.exit(main())
