#!/usr/bin/env python3
"""Audit an immutable 166-case veclibm gsim run without modifying its logs.

Usage: python3 single/collect_veclibm_gsim.py --run RUN_DIRECTORY

Read run-metadata.json, logs/*/run-NAME.{log,err}, completion lists and optional
runner-exit.json. Repeated calls replace only reports/{results.json,results.csv,
summary.md}. While the runner is active exit 0 means a progress report, NOT that
all tests passed. After runner-exit.json appears exit 0 requires all 25 performance
and 141 accuracy cases to pass strict evidence checks. Terminal failures return 1.
Performance cycles come from guest rdcycle under gsim, never host elapsed seconds.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import statistics
import sys

from run_veclibm import ANSI, ERRORS, LIMIT, markers_from, sha256

EXPECTED_SUITES = {'performance': 25, 'accuracy': 141}
GOOD_TRAP = re.compile(r'\bHIT\s+GOOD\s+TRAP\b', re.I)
CORE_GOOD_TRAP = re.compile(r'\bCore\s+0:\s*HIT\s+GOOD\s+TRAP\b', re.I)
TIMEOUT_ERROR = re.compile(r'\b(?:timed?\s+out|timeout(?:\s+(?:expired|reached))?)\b', re.I)
RECORD = re.compile(r'\b(VECLIBM_[A-Z_]+)\b([^\r\n]*)')
KEY_VALUE = re.compile(r'\b([A-Za-z_][A-Za-z_0-9]*)=([^\s]+)')


def clean_log(text):
    """Strip terminal controls and shell command headers, not guest diagnostics."""
    clean = ANSI.sub('', text).replace('\r', '')
    return '\n'.join(line for line in clean.splitlines() if not line.lstrip().startswith('cmd:'))


def error_markers(text):
    # The upstream success marker contains a legitimate failed=0 field.
    scanned = re.sub(r'\bfailed=0(?=$|[\s,;\]}])', '', text, flags=re.I)
    errors = [match.group(0) for match in ERRORS.finditer(scanned)]
    # A failed LT/EQ line is conclusive even if the completion marker is truncated.
    if re.search(r'\bVECLIBM_ASSERT\b[^\n]*\bpass=0\b', text):
        errors.append('VECLIBM_ASSERT pass=0')
    if TIMEOUT_ERROR.search(text):
        errors.append('timeout diagnostic')
    if LIMIT.search(text):
        errors.append('instruction limit diagnostic')
    return list(dict.fromkeys(errors))


def records(text, kind):
    return [dict(KEY_VALUE.findall(match.group(2))) for match in RECORD.finditer(text)
            if match.group(1) == kind]


def integer(record, name, issues, *, minimum=0):
    value = record.get(name, '')
    if not re.fullmatch(r'0|[1-9][0-9]*', value):
        issues.append(f'invalid {name}={value!r}')
        return None
    result = int(value)
    if result < minimum:
        issues.append(f'{name} must be >= {minimum}, got {result}')
        return None
    return result


def performance_metrics(text, case):
    entry = case['manifest_entry']
    name = case['original_name']
    n, repeats = int(entry['n']), int(entry['repeats'])
    issues, samples, modes = [], [], {}
    vlen_bits, baseline = None, None
    configs = records(text, 'VECLIBM_CONFIG')
    if len(configs) != 1:
        issues.append(f'expected one VECLIBM_CONFIG, found {len(configs)}')
    else:
        for field, value in [('n', n), ('repeats', repeats)]:
            if integer(configs[0], field, issues, minimum=1) != value:
                issues.append(f'configuration {field} differs from manifest ({value})')
        vlen_bits = integer(configs[0], 'vlen_bits', issues, minimum=1)
        baseline = configs[0].get('baseline')
        if baseline != 'static-libm':
            issues.append('configuration baseline is not static-libm')
    completions = records(text, 'VECLIBM_COMPLETE')
    if len(completions) != 1 or completions[0].get('functions') != '1':
        issues.append('expected exactly one VECLIBM_COMPLETE functions=1')
    function_ends = records(text, 'VECLIBM_FUNCTION_COMPLETE')
    if len(function_ends) != 1 or function_ends[0].get('function') != name:
        issues.append('missing, duplicated, or wrong function completion')
    seen = set()
    for sample in records(text, 'VECLIBM_SAMPLE'):
        row = dict(sample)
        if sample.get('function') != name:
            issues.append(f'sample function differs from {name}')
        mode = sample.get('mode')
        if mode not in ('libm', 'rvvlm'):
            issues.append(f'invalid sample mode {mode!r}')
        for field, minimum in [('sample', 0), ('n', 1), ('cycles', 1), ('insns', 1), ('checksum', 0)]:
            row[field] = integer(sample, field, issues, minimum=minimum)
        if row['n'] != n:
            issues.append(f'sample n differs from manifest ({n})')
        index = row['sample']
        if index is not None and not 0 <= index < repeats:
            issues.append(f'sample index {index} outside 0..{repeats-1}')
        key = (mode, index)
        if key in seen:
            issues.append(f'duplicate sample {key}')
        seen.add(key)
        if row['cycles'] is not None and row['n']:
            row['cycles_per_element'] = row['cycles'] / row['n']
            row['elements_per_cycle'] = row['n'] / row['cycles']
        samples.append(row)
    wanted = {(mode, i) for mode in ('libm', 'rvvlm') for i in range(repeats)}
    if seen != wanted or len(samples) != 2 * repeats:
        issues.append(f'expected {repeats} samples per mode; observed {len(samples)} samples')
    for mode in ('libm', 'rvvlm'):
        rows = [s for s in samples if s.get('mode') == mode and s.get('cycles_per_element') is not None]
        if rows:
            modes[mode] = {'samples': len(rows),
                'median_cycles': statistics.median(s['cycles'] for s in rows),
                'median_insns': statistics.median(s['insns'] for s in rows if s['insns'] is not None)
                    if any(s['insns'] is not None for s in rows) else None,
                'median_cycles_per_element': statistics.median(s['cycles_per_element'] for s in rows),
                'median_elements_per_cycle': statistics.median(s['elements_per_cycle'] for s in rows)}
    ratio = (modes['libm']['median_cycles'] / modes['rvvlm']['median_cycles']
             if 'libm' in modes and 'rvvlm' in modes else None)
    return {'samples': samples, 'modes': modes, 'libm_to_rvvlm_cycle_ratio': ratio,
            'n': n, 'repeats': repeats, 'vlen_bits': vlen_bits, 'baseline': baseline,
            'metric_scope': 'guest function-call intervals under gsim'}, issues


def accuracy_metrics(text, case):
    issues = []
    starts = records(text, 'VECLIBM_ACCURACY_START')
    ends = records(text, 'VECLIBM_ACCURACY_COMPLETE')
    entry = case['manifest_entry']
    if len(starts) != 1 or starts[0].get('case') != case['original_name']:
        issues.append('missing, duplicated, or wrong accuracy start marker')
    else:
        for field in ('points', 'grid_points', 'repeats'):
            if field in entry and integer(starts[0], field, issues, minimum=1) != int(entry[field]):
                issues.append(f'accuracy start {field} differs from manifest')
        if starts[0].get('ldbl_bits') != '113':
            issues.append('accuracy reference does not report binary128 ldbl_bits=113')
    result = {'checks': None, 'failed': None, 'body_instructions': None,
              'validation': entry.get('validation'),
              'metric_scope': 'whole test body including references/checks, not function throughput'}
    if len(ends) != 1 or ends[0].get('case') != case['original_name']:
        issues.append('missing, duplicated, or wrong accuracy completion marker')
        return result, issues
    for field in ('checks', 'failed', 'body_instructions'):
        result[field] = integer(ends[0], field, issues, minimum=1 if field == 'body_instructions' else 0)
    if result['failed'] != 0:
        issues.append('accuracy failed counter is not zero')
    if entry.get('validation') == 'upstream-assertions':
        if not result['checks']:
            issues.append('assertion-bearing test executed zero checks')
    elif entry.get('validation') == 'diagnostic-only':
        if result['checks'] != 0:
            issues.append('diagnostic-only test unexpectedly reports assertions')
    else:
        issues.append('unknown accuracy validation class')
    failed_asserts = [r for r in records(text, 'VECLIBM_ASSERT') if r.get('pass') != '1']
    if failed_asserts:
        issues.append('failed or malformed VECLIBM_ASSERT record')
    return result, issues


def host_counters(text):
    """Parse only explicitly labelled gsim host counters; never infer from time."""
    result = {}
    core_summary = list(re.finditer(r'Core-(\d+)\s+instrCnt\s*=\s*([0-9,]+),\s*cycleCnt\s*=\s*([0-9,]+),\s*IPC\s*=\s*([0-9.]+)', text))
    if core_summary:
        # Current workloads are single-hart. Preserve the explicit model-core
        # attribution rather than confusing these with NEMU-reference totals.
        result['gsim_cores'] = [dict(core=int(m[1]), retired_instructions=int(m[2].replace(',', '')),
                                    simulation_cycles=int(m[3].replace(',', '')), ipc=float(m[4]),
                                    evidence=m[0]) for m in core_summary]
    # Full log line evidence is retained to distinguish model counters from CSR
    # interval measurements. Unrecognized formats stay unavailable, not guessed.
    patterns = {
        'retired_instructions': r'\b(?:total\s+)?(?:retired\s+instructions|retiredinstr|total_instr)\s*[:=]\s*([0-9][0-9,]*)\b',
        'simulation_cycles': r'\b(?:sim(?:ulation)?\s*cycles|simcycles|total_cycles)\s*[:=]\s*([0-9][0-9,]*)\b',
        'ipc': r'\bIPC\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)\b',
    }
    for key, pattern in patterns.items():
        matches = list(re.finditer(pattern, text, re.I))
        if matches:
            match = matches[-1]
            value = match.group(1).replace(',', '')
            result[key] = float(value) if key == 'ipc' else int(value)
            result[key + '_evidence'] = text[text.rfind('\n', 0, match.start())+1:text.find('\n', match.end())
                if '\n' in text[match.end():] else len(text)]
    return result


def audit_case(case, log_dirs, final):
    name = case['name']
    attempts = []
    for directory in log_dirs:
        log, err = directory / f'run-{name}.log', directory / f'run-{name}.err'
        membership = {}
        for label in ('pass', 'fail', 'timeout'):
            listing = directory / f'.{label}_list'
            lines = listing.read_text(errors='replace').splitlines() if listing.is_file() else []
            membership[label] = lines.count(name)
        if log.exists() or err.exists() or any(membership.values()):
            attempts.append((log, err, membership))
    reasons, integrity = [], []
    staged = Path(case['staged_bin'])
    expected_sha = case.get('sha256')
    actual_sha = sha256(staged) if staged.is_file() else None
    if not actual_sha:
        integrity.append('staged BIN missing')
    elif not expected_sha or actual_sha != expected_sha:
        integrity.append('staged BIN SHA256 mismatch or expected hash missing')
    entry = case['manifest_entry']
    if entry.get('status') != 'built':
        integrity.append('manifest build status is not built')
    if entry.get('sha256', entry.get('bin_sha256', expected_sha)) != expected_sha:
        integrity.append('staging and manifest BIN hashes differ')
    base = dict(name=name, suite=case['suite'], original_name=case['original_name'],
                bin=case.get('bin'), staged_bin=str(staged), sha256=actual_sha,
                expected_sha256=expected_sha, validation=entry.get('validation'),
                log=None, err=None, list_membership={}, good_trap=False, any_good_trap=False,
                required_markers=markers_from(entry), marker_counts={}, error_markers=[],
                metrics={}, host_counters={}, validation_issues=[], failure_reasons=[])
    reasons += integrity
    if len(attempts) > 1:
        reasons.append('ambiguous multiple log attempts for staged name')
    if not attempts:
        if final:
            reasons.append('missing logs and terminal list entry after runner exit')
        base.update(status='failed' if reasons else 'pending', failure_reasons=reasons, functional_pass=False)
        return base
    log, err, membership = attempts[0]
    left = clean_log(log.read_text(errors='replace')) if log.is_file() else ''
    right = clean_log(err.read_text(errors='replace')) if err.is_file() else ''
    text = left + '\n' + right
    base.update(log=str(log.resolve()), err=str(err.resolve()), list_membership=membership,
                good_trap=bool(CORE_GOOD_TRAP.search(text)), any_good_trap=bool(GOOD_TRAP.search(text)), host_counters=host_counters(text))
    errors = error_markers(text)
    base['error_markers'] = errors
    if errors:
        reasons.append('error marker(s): ' + ', '.join(errors))
    listed = sum(membership.values())
    terminal = listed > 0 or final
    if listed > 1:
        reasons.append('conflicting or duplicate terminal list entries')
    if membership['timeout']:
        reasons.append('run_emu timeout list entry')
    if membership['fail']:
        reasons.append('run_emu fail list entry')
    for marker in base['required_markers']:
        # Command headers have already been removed. Require exactly one guest
        # occurrence; duplicated executions must not be merged into a pass.
        count = text.count(marker)
        base['marker_counts'][marker] = count
        if count != 1 and terminal:
            reasons.append(f'expected unique marker {marker!r}; found {count}')
    if not base['required_markers'] and terminal:
        reasons.append('manifest has no required completion marker')
    if case['suite'] == 'performance':
        metrics, issues = performance_metrics(text, case)
    else:
        metrics, issues = accuracy_metrics(text, case)
    base.update(metrics=metrics, validation_issues=issues)
    if terminal:
        if not log.is_file() or not err.is_file():
            reasons.append('missing one of the two log streams')
        if membership['pass'] != 1:
            reasons.append('missing unique run_emu pass list entry')
        if not base['good_trap']:
            reasons.append('missing RTL Core 0: HIT GOOD TRAP')
        reasons += issues
    if reasons:
        status = 'timeout' if membership['timeout'] else 'failed'
    elif terminal:
        status = 'passed'
    else:
        status = 'running'
    base.update(status=status, functional_pass=status == 'passed', failure_reasons=list(dict.fromkeys(reasons)))
    return base


def validate_metadata(metadata):
    cases = metadata.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('run-metadata.json must contain a nonempty cases list')
    names, originals = [], []
    for case in cases:
        for field in ('name', 'suite', 'original_name', 'staged_bin', 'sha256', 'manifest_entry'):
            if field not in case:
                raise ValueError(f'metadata case missing {field}')
        name = case['name']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
            raise ValueError(f'unsafe staged case name: {name!r}')
        if case['suite'] not in EXPECTED_SUITES:
            raise ValueError(f'unknown suite: {case["suite"]!r}')
        if not isinstance(case['manifest_entry'], dict):
            raise ValueError(f'{name}: manifest_entry must be an object')
        entry = case['manifest_entry']
        if case['suite'] == 'performance':
            for field in ('n', 'repeats'):
                if isinstance(entry.get(field), bool) or not isinstance(entry.get(field), int) or entry[field] < 1:
                    raise ValueError(f'{name}: performance {field} must be a positive integer')
        names.append(name)
        originals.append((case['suite'], case['original_name']))
    if len(set(names)) != len(names):
        raise ValueError('duplicate staged names in run metadata')
    if len(set(originals)) != len(originals):
        raise ValueError('duplicate original tests in run metadata')
    return cases


def save_reports(run, document):
    reports = run / 'reports'
    if reports.is_symlink():
        raise ValueError('refusing symlink reports directory')
    reports.mkdir(exist_ok=True)
    for filename in ('results.json', 'results.csv', 'summary.md'):
        if (reports / filename).is_symlink():
            raise ValueError(f'refusing symlink output {filename}')
    (reports / 'results.json').write_text(json.dumps(document, indent=2) + '\n')
    fields = ['name', 'suite', 'original_name', 'status', 'functional_pass', 'validation', 'checks',
              'failed', 'body_instructions', 'libm_cycles_per_element', 'rvvlm_cycles_per_element',
              'libm_elements_per_cycle', 'rvvlm_elements_per_cycle', 'libm_to_rvvlm_cycle_ratio',
              'sha256', 'good_trap', 'failure_reasons', 'log', 'err']
    with (reports / 'results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for case in document['cases']:
            row = {key: case.get(key) for key in fields}
            metric = case['metrics']
            for key in ('checks', 'failed', 'body_instructions', 'libm_to_rvvlm_cycle_ratio'):
                row[key] = metric.get(key)
            for mode in ('libm', 'rvvlm'):
                for key in ('cycles_per_element', 'elements_per_cycle'):
                    row[f'{mode}_{key}'] = metric.get('modes', {}).get(mode, {}).get('median_' + key)
            row['failure_reasons'] = json.dumps(case['failure_reasons'])
            writer.writerow(row)
    summary = document['summary']
    lines = ['# veclibm gsim run report', '', f"- State: **{document['state']}**",
             f"- Progress: {summary['passed']}/{summary['expected_cases']} passed; "
             f"{summary['failed']} failed/timeout; {summary['running']} running; {summary['pending']} pending.",
             f"- Coverage: performance {summary['suite_counts'].get('performance', 0)}/25; "
             f"accuracy {summary['suite_counts'].get('accuracy', 0)}/141.",
             f"- Original numerical assertions observed in passing tests: {summary['accuracy_checks_passed_cases']}; "
             f"diagnostic-only passing cases: {summary['diagnostic_only_passed']}.",
             '- in_progress is not a final success claim. Passing requires runner pass-list, GOOD TRAP, '
             'unique guest completion markers, valid samples/assertions and staged BIN hash.',
             '- Performance measurements are guest function-call CSR cycles under this gsim model. '
             'Host wall time is not hardware performance; accuracy body instructions include reference/check overhead.',
             '- Per-mode values below are medians over the recorded samples; cycle ratio = libm / RVV. '
             'Only passed rows support a final performance conclusion; running rows may contain partial samples. '
             'Sample checksums are not required to match across scalar and vector algorithms.', '',
             '| Case | Status | Checks | libm cycles/element | RVV cycles/element | libm/RVV cycles | Logs |',
             '|---|---|---:|---:|---:|---:|---|']
    def number(value):
        return f'{value:.6g}' if value is not None else '—'
    for case in document['cases']:
        metrics = case['metrics']
        modes = metrics.get('modes', {})
        links = ' / '.join(f'[{label}](<{case[key]}>)' for label, key in [('stdout', 'log'), ('stderr', 'err')] if case[key]) or '—'
        lines.append(f"| {case['name']} | {case['status']} | {metrics.get('checks', '—')} | "
            f"{number(modes.get('libm', {}).get('median_cycles_per_element'))} | "
            f"{number(modes.get('rvvlm', {}).get('median_cycles_per_element'))} | "
            f"{number(metrics.get('libm_to_rvvlm_cycle_ratio'))} | {links} |")
    lines += ['', '## Run-level audit', '']
    lines.extend('- ' + issue for issue in document['audit_issues'])
    if not document['audit_issues']:
        lines.append('- No run-level audit issues found.')
    lines += ['', '## Case failures', '']
    failures = [case for case in document['cases'] if case['failure_reasons']]
    for case in failures:
        lines.append(f"- {case['name']}: " + '; '.join(case['failure_reasons']))
    if not failures:
        lines.append('- No terminal case failure observed in this snapshot.')
    lines += ['', f"- Metadata: [run-metadata.json](<{run / 'run-metadata.json'}>)",
              f"- Runner stdout: [runner.log](<{run / 'runner.log'}>)",
              f"- Runner stderr: [runner.err](<{run / 'runner.err'}>)"]
    (reports / 'summary.md').write_text('\n'.join(lines) + '\n')


def collect(run):
    run = run.expanduser().resolve()
    metadata = json.loads((run / 'run-metadata.json').read_text())
    cases = validate_metadata(metadata)
    exit_path = run / 'runner-exit.json'
    runner_exit = json.loads(exit_path.read_text()) if exit_path.is_file() else None
    final = runner_exit is not None
    logs = run / 'logs'
    log_dirs = sorted(path for path in logs.iterdir() if path.is_dir()) if logs.is_dir() else []
    # Also tolerate an explicitly configured non-timestamped logs root.
    if logs.is_dir() and (list(logs.glob('run-*.log')) or any((logs / ('.' + x + '_list')).exists() for x in ('pass', 'fail', 'timeout'))):
        log_dirs.append(logs)
    results = [audit_case(case, log_dirs, final) for case in cases]
    issues = []
    counts = dict(Counter(c['suite'] for c in cases))
    if counts != EXPECTED_SUITES:
        issues.append(f'coverage mismatch: expected {EXPECTED_SUITES}, found {counts}')
    validation_counts = dict(Counter(c['manifest_entry'].get('validation') for c in cases if c['suite'] == 'accuracy'))
    if validation_counts != {'upstream-assertions': 131, 'diagnostic-only': 10}:
        issues.append(f'accuracy validation coverage mismatch: {validation_counts}')
    known = {c['name'] for c in cases}
    for directory in log_dirs:
        for label in ('pass', 'fail', 'timeout'):
            listing = directory / f'.{label}_list'
            if listing.is_file():
                unknown = set(listing.read_text(errors='replace').splitlines()) - known - {''}
                if unknown:
                    issues.append(f'{listing}: unknown case names {sorted(unknown)}')
    for key in ('emulator', 'reference', 'runner'):
        identity = metadata.get(key)
        if isinstance(identity, dict) and identity.get('sha256') and identity.get('path'):
            path = Path(identity['path'])
            if not path.is_file() or sha256(path) != identity['sha256']:
                issues.append(f'{key} identity no longer matches metadata SHA256')
    # Use the launcher's recorded options for configurable runs. Old metadata
    # without these fields retains the original 64-job / 48-hour contract.
    requested_jobs = metadata.get('jobs', 64)
    requested_timeout = metadata.get('timeout', '48h')
    if isinstance(requested_jobs, bool) or not isinstance(requested_jobs, int) or not 1 <= requested_jobs <= 64:
        issues.append('run metadata jobs must be an integer between 1 and 64')
    if not isinstance(requested_timeout, str) or not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)[smhd]?', requested_timeout):
        issues.append('run metadata timeout is not a valid GNU timeout duration')
    if 'diff_enabled' in metadata and metadata['diff_enabled'] is not True:
        issues.append('run metadata does not enable differential testing')
    if 'command' in metadata:
        command = metadata['command']
        if not isinstance(command, list) or any(not isinstance(x, str) for x in command):
            issues.append('run command must be an argument list')
        else:
            def option(*keys):
                for index, value in enumerate(command[:-1]):
                    if value in keys:
                        return command[index + 1]
                return None
            if option('-j') != str(requested_jobs) or option('--timeout') != requested_timeout:
                issues.append('run command differs from recorded jobs or timeout')
            reference = metadata.get('reference')
            if isinstance(reference, dict) and (not isinstance(reference.get('sha256'), str) or
                    not re.fullmatch(r'[0-9a-f]{64}', reference['sha256'])):
                issues.append('recorded reference identity lacks a valid SHA256')
            requested_ref = (reference.get('path') if isinstance(reference, dict) else
                             '/nfs/share/huxuan/riscv64-nemu-interpreter-so-fixvfredusum')
            if not requested_ref or option('-r', '--ref') != requested_ref or '--no-diff' in command:
                issues.append('run command does not use the recorded differential reference')
    if final and runner_exit.get('returncode') != 0:
        issues.append(f'runner exited {runner_exit.get("returncode")}')
    stats = Counter(c['status'] for c in results)
    summary = dict(expected_cases=166, metadata_cases=len(cases), suite_counts=counts,
                   passed=stats['passed'], failed=stats['failed'] + stats['timeout'],
                   timeout=stats['timeout'], running=stats['running'], pending=stats['pending'],
                   accuracy_checks_passed_cases=sum(c['metrics'].get('checks') or 0 for c in results if c['functional_pass'] and c['suite'] == 'accuracy'),
                   diagnostic_only_passed=sum(c['functional_pass'] and c['validation'] == 'diagnostic-only' for c in results))
    success = final and not issues and summary['passed'] == 166
    state = 'passed' if success else 'failed' if final else 'in_progress'
    document = dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(), run=str(run),
                    state=state, complete=final, all_passed=success, summary=summary,
                    runner_exit=runner_exit, audit_issues=issues, run_metadata=metadata, cases=results)
    save_reports(run, document)
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        document = collect(args.run)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'collector error: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'state': document['state'], **document['summary'], 'audit_issues': document['audit_issues']}, indent=2))
    return int(document['complete'] and not document['all_passed'])


if __name__ == '__main__':
    sys.exit(main())
