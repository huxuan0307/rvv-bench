#!/usr/bin/env python3
"""Refresh a gsim run and export performance only when all 25 cases terminate.

The CSV contains one summary row per (function, mode) and every raw sample row.
Failed cases retain status and available values, never treated as valid results.
No output CSV is created while any performance case is running or pending.
"""
import argparse
from collections import Counter
import csv
import io
import json
from pathlib import Path
import sys

from collect_veclibm_gsim import collect

FIELDS = [
    'record_type', 'function', 'mode', 'status', 'measurement_valid', 'n', 'repeats',
    'sample', 'cycles', 'insns', 'checksum', 'cycles_per_element', 'elements_per_cycle',
    'median_cycles', 'median_insns', 'median_cycles_per_element',
    'median_elements_per_cycle', 'libm_to_rvvlm_cycle_ratio',
    'vlen_bits', 'baseline', 'core0_retired_instructions', 'core0_simulation_cycles', 'core0_ipc',
    'failure_reasons', 'run_directory', 'generated_at', 'metric_scope', 'binary_sha256',
    'emulator', 'emulator_sha256', 'reference', 'reference_sha256', 'jobs', 'timeout',
    'stdout_log', 'stderr_log',
]
TERMINAL = {'passed', 'failed', 'timeout'}


def export_performance(document, output):
    performance = [c for c in document['cases'] if c['suite'] == 'performance']
    if len(performance) != 25 or len({c['original_name'] for c in performance}) != 25:
        raise ValueError('export requires exactly 25 unique performance cases')
    if any(c['status'] not in TERMINAL or (not document.get('complete') and
               not sum(c.get('list_membership', {}).values())) for c in performance):
        return dict(exported=False, reason='performance tests still running or pending',
                    performance_status=dict(Counter(c['status'] for c in performance)))
    metadata = document['run_metadata']
    output = Path(output).absolute()
    if output.is_symlink():
        raise ValueError('refusing symlink output')
    # Read before replacing; refuse to replace a CSV from another run.
    if output.exists():
        previous = output.read_text()
        old_rows = list(csv.DictReader(io.StringIO(previous)))
        if old_rows and any(row.get('run_directory') != document['run'] for row in old_rows):
            raise ValueError('existing CSV belongs to a different run; choose a new output')
    rows = []
    for case in performance:
        metric = case.get('metrics', {})
        entry = next(c['manifest_entry'] for c in metadata['cases'] if c['name'] == case['name'])
        cores = case.get('host_counters', {}).get('gsim_cores', [])
        core = next((c for c in cores if c['core'] == 0), {})
        common = dict(function=case['original_name'], status=case['status'],
            measurement_valid=bool(case['functional_pass'] and not document['audit_issues']),
            n=entry['n'], repeats=entry['repeats'],
            libm_to_rvvlm_cycle_ratio=metric.get('libm_to_rvvlm_cycle_ratio'),
            vlen_bits=metric.get('vlen_bits'), baseline=metric.get('baseline'),
            core0_retired_instructions=core.get('retired_instructions'),
            core0_simulation_cycles=core.get('simulation_cycles'), core0_ipc=core.get('ipc'),
            failure_reasons=json.dumps(case.get('failure_reasons', []) + document['audit_issues'], ensure_ascii=False),
            run_directory=document['run'], generated_at=document['generated_at'],
            metric_scope=metric.get('metric_scope'), binary_sha256=case['sha256'],
            emulator=metadata['emulator']['path'], emulator_sha256=metadata['emulator']['sha256'],
            reference=metadata['reference']['path'], reference_sha256=metadata['reference']['sha256'],
            jobs=metadata['jobs'], timeout=metadata['timeout'], stdout_log=case.get('log'), stderr_log=case.get('err'))
        for mode in ('libm', 'rvvlm'):
            summary = metric.get('modes', {}).get(mode, {})
            rows.append(dict(common, record_type='summary', mode=mode,
                             **{key: value for key, value in summary.items() if key.startswith('median_')}))
            for sample in metric.get('samples', []):
                if sample.get('mode') == mode:
                    rows.append(dict(common, record_type='sample', mode=mode,
                                     **{key: sample.get(key) for key in ['sample', 'cycles', 'insns', 'checksum',
                                                                       'cycles_per_element', 'elements_per_cycle']}))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return dict(exported=True, output=str(output), functions=25,
                summary_rows=sum(r['record_type'] == 'summary' for r in rows),
                sample_rows=sum(r['record_type'] == 'sample' for r in rows),
                performance_status=dict(Counter(c['status'] for c in performance)),
                audit_issues=document['audit_issues'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent.parent / '.dsh/data/veclibm.csv')
    parser.add_argument('--baseline-dir', type=Path,
                        help='also compare all platform veclibm.html tables after performance completion')
    parser.add_argument('--comparison-output', type=Path,
                        default=Path(__file__).resolve().parent.parent / '.dsh/data/veclibm-comparison.csv')
    args = parser.parse_args(argv)
    try:
        document = collect(args.run)
        result = export_performance(document, args.output)
        if args.baseline_dir:
            if result['exported']:
                from compare_veclibm_gsim import compare_performance
                result['comparison'] = compare_performance(document, args.baseline_dir, args.comparison_output)
            else:
                result['comparison'] = dict(exported=False, reason=result['reason'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'export error: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(dict(state=document['state'], overall=document['summary'], **result), indent=2))
    comparison = result.get('comparison', {})
    comparison_failed = bool(args.baseline_dir and result.get('exported') and
                             (not comparison.get('exported') or comparison.get('invalid_current_rows')))
    return int(bool(comparison_failed or (result.get('exported', False) and
               (result.get('audit_issues') or result['performance_status'].get('passed') != 25))))


if __name__ == '__main__':
    sys.exit(main())
