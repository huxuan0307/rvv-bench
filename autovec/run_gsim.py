#!/usr/bin/env python3
"""Run manifest TSVC images once through run_emu.sh and report 1024 repetitions.

Examples (from any directory):
    python3 autovec/run_gsim.py --case s000 --jobs 1 --timeout 10m
    python3 autovec/run_gsim.py --jobs 32 --timeout 0

Only manifest TSVC images are staged; old aggregate images are never discovered.
A successful runner exit alone is insufficient: each case needs GOOD TRAP and
an AV cycles/checksum marker, with no failure or timeout evidence in either log.
"""
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
RUNNER = Path('/nfs/home/huxuan/opt/bin/run_emu.sh')
EMU_DIR = Path('/nfs/share/huxuan/emus')
REF = Path('/nfs/share/huxuan/riscv64-nemu-interpreter-so-fixvfredusum')
REPETITIONS = 1024
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
FAILURE = re.compile(
    r'\b(?:BAD TRAP|CRITICAL ERROR|ABORT|ASSERTION|FAILED|ERROR)\b|'
    r'Assertion .* failed|difftest.*(?:mismatch|fail)|Segmentation fault', re.I)
TIMEOUT = re.compile(r'\bTIMEOUT\b|\btimed out\b', re.I)
FIELDS = ['suite', 'name', 'status', 'reason', 'total_cycles',
          'cycles_per_outer_repetition', 'outer_repetitions', 'checksum',
          'build_status', 'binary', 'stdout_logs', 'stderr_logs']


def identity(path):
    stat = path.stat()
    return {'path': str(path), 'resolved_path': str(path.resolve()),
            'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'mtime': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}


def parse_case(case, logs, lists):
    """Inspect every matching stdout/stderr log, independent of runner exit."""
    name = case['name']
    out = sorted(logs.rglob(f'run-{name}.log'))
    err = sorted(logs.rglob(f'run-{name}.err'))
    row = {'suite': 'tsvc', 'name': name, 'status': 'failed', 'reason': '',
           'total_cycles': None, 'cycles_per_outer_repetition': None,
           'outer_repetitions': REPETITIONS, 'checksum': None,
           'build_status': case.get('status'), 'binary': str(case['_binary']),
           'stdout_logs': [str(p) for p in out],
           'stderr_logs': [str(p) for p in err]}
    # Command headers contain --timeout/pathnames, not simulator evidence.
    text = '\n'.join(ANSI.sub('', line)
                     for path in out + err
                     for line in path.read_text(errors='replace').splitlines()
                     if not line.startswith('cmd: '))
    markers = []
    invalid_marker = False
    for line in text.splitlines():
        marker = re.search(r'\bAV\s+(.*)', line)
        if not marker:
            continue
        fields = dict(re.findall(r'\b(cycles|checksum|iterations)=([^\s]+)', marker[1]))
        if not fields.get('cycles', '').isdigit() or not fields.get('checksum'):
            invalid_marker = True
            continue
        if fields.get('iterations') != str(REPETITIONS):
            invalid_marker = True
            continue
        markers.append((int(fields['cycles']), fields['checksum']))
    if markers and len(set(markers)) == 1:
        row['total_cycles'], row['checksum'] = markers[0]
        row['cycles_per_outer_repetition'] = row['total_cycles'] / REPETITIONS
    if name in lists['timeout'] or TIMEOUT.search(text):
        row.update(status='timeout', reason='runner or log reports timeout')
    elif case.get('status') != 'built':
        row.update(status='not-built', reason='manifest case is not built')
    elif not case['_binary'].is_file():
        row.update(status='missing', reason='manifest binary is missing')
    elif name in lists['fail'] or FAILURE.search(text):
        row['reason'] = 'runner or log reports failure'
    elif not out or not err:
        row.update(status='missing', reason='stdout or stderr log is missing')
    elif not re.search(r'\bGOOD TRAP\b', text):
        row['reason'] = 'GOOD TRAP is missing'
    elif invalid_marker or not markers or len(set(markers)) != 1:
        row['reason'] = 'AV cycles/checksum marker is missing, invalid, or conflicting'
    elif name not in lists['pass']:
        row['reason'] = 'runner did not record a successful case exit'
    else:
        row.update(status='passed', reason='')
    reference = Path(case['directory']) / 'run.log' if 'directory' in case else None
    if row['status'] == 'passed' and reference and reference.is_file():
        match = re.search(r'checksum=(\S+).*iterations=1024', reference.read_text(errors='replace'))
        if match and row['checksum'] != match[1]:
            row.update(status='failed', reason='checksum differs from same-build NEMU run')
    return row


def write_reports(directory, metadata, rows):
    directory.mkdir(parents=True, exist_ok=True)
    counts = dict(Counter(row['status'] for row in rows))
    passed = [row for row in rows if row['status'] == 'passed']
    summary = {'cases': len(rows), 'statuses': counts,
               'passed_total_cycles': sum(row['total_cycles'] for row in passed),
               'passed_cycles_per_outer_repetition': sum(
                   row['cycles_per_outer_repetition'] for row in passed)}
    (directory / 'results.json').write_text(json.dumps(
        {'metadata': metadata, 'summary': summary, 'results': rows}, indent=2) + '\n')
    # Repeat provenance in CSV so the file remains meaningful on its own.
    provenance = {key: metadata[key] for key in ('manifest', 'jobs', 'timeout', 'run_directory')}
    provenance.update(emulator=metadata['emulator']['path'],
                      emulator_mtime=metadata['emulator']['mtime'],
                      reference=metadata['reference']['path'],
                      compiler=metadata['build'].get('compiler', ''),
                      build_flags=json.dumps(metadata['build'].get('flags', [])))
    with (directory / 'results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS + list(provenance), lineterminator='\n')
        writer.writeheader()
        for row in rows:
            flat = dict(row, **provenance)
            for field in ('stdout_logs', 'stderr_logs'):
                flat[field] = json.dumps(flat[field])
            writer.writerow(flat)
    def cell(value):
        return str(value if value is not None else '').replace('|', '\\|').replace('\n', ' ')
    lines = ['# TSVC gsim: 1024 outer repetitions', '',
             f"Run: {metadata['started_at']}", '',
             f"Cases: {len(rows)}; statuses: {json.dumps(counts, sort_keys=True)}", '',
             f"Passed cases total cycles: {summary['passed_total_cycles']}; "
             f"sum of cycles / 1024: {summary['passed_cycles_per_outer_repetition']}", '',
             'Cycles are the AV driver total divided by 1024 outer repetitions; '
             'they are not cycles per inner kernel iteration. The raw interval includes dummy calls and CSR read overhead; initialization and checksum are outside. No hardware fence or warmup is added.', '',
              'Nonfinite checksum values (inf/NaN) indicate overflow or invalid arithmetic in the repeated workload; passing difftest does not establish a finite numerical result.', '',
             '| Case | Status | Total cycles | Cycles / 1024 | Checksum | Reason | Logs |',
             '| --- | --- | ---: | ---: | --- | --- | --- |']
    for row in rows:
        links = ' '.join(f'[{p.name}](<{p}>)' for p in
                         map(Path, row['stdout_logs'] + row['stderr_logs']))
        lines.append('| ' + ' | '.join(map(cell, [row['name'], row['status'],
                     row['total_cycles'], row['cycles_per_outer_repetition'],
                     row['checksum'], row['reason'], links])) + ' |')
    lines += ['', '## Run and build information', '', '```json',
              json.dumps(metadata, indent=2), '```', '']
    (directory / 'results.md').write_text('\n'.join(lines))
    return counts


def collect_run(run, manifest_path, manifest, manifest_bytes, cases):
    """Recover original run provenance; never invoke a runner or modify logs."""
    run = run.resolve()
    base = (ROOT / 'build/gsim-1024').resolve()
    if base not in run.parents or not run.is_dir() or not (run / 'logs').is_dir():
        raise ValueError('--collect-run must name a run directory under build/gsim-1024')
    recorded_path = run / 'run-metadata.json'
    metadata = json.loads(recorded_path.read_text()) if recorded_path.is_file() else {}
    snapshot_path = run / 'measurement-build.json'
    snapshot = json.loads(snapshot_path.read_text()) if snapshot_path.is_file() else {}
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    expected = metadata.get('manifest_sha256', snapshot.get('manifest_sha256'))
    if expected and expected != digest:
        raise ValueError('manifest hash differs from the recorded measurement build')
    staged = {p.stem for p in (run / 'bin').glob('*.bin')}
    if not staged or not {c['name'] for c in cases}.issubset(staged):
        raise ValueError('selected manifest cases do not match the staged run; use --case for a smoke run')
    hashes = metadata.get('binary_sha256') or {
        c['name']: c['binary_sha256'] for c in snapshot.get('cases', [])}
    for case in cases:
        staged_binary = run / 'bin' / (case['name'] + '.bin')
        if staged_binary.resolve() != case['_binary']:
            raise ValueError(f"{case['name']}: staged binary differs from manifest")
        if case['name'] in hashes and hashlib.sha256(staged_binary.read_bytes()).hexdigest() != hashes[case['name']]:
            raise ValueError(f"{case['name']}: binary hash differs from measurement build")
    runner_log = run / 'runner.log'
    text = runner_log.read_text(errors='replace')
    def header(key):
        match = re.search(r'^\s*' + re.escape(key) + r':\s*(.*?)\s*$', text, re.M)
        if not match:
            raise ValueError(f'cannot recover {key} from runner.log')
        return match[1]
    if not metadata:
        emulator = Path(header('emu'))
        ref_match = re.fullmatch(r'ON\s+\((.*)\)', header('diff'))
        if not ref_match:
            raise ValueError('recorded run does not enable a diff reference')
        reference = Path(ref_match[1])
        command = [str(RUNNER), '-e', str(emulator), '-r', str(reference),
                   '-t', str(run / 'bin'), '-j', header('parallel'),
                   '--timeout', header('timeout'), '-o', str(run / 'logs')]
        metadata = {'started_at': datetime.strptime(header('started'), '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat(),
                    'timing': 'inline csrr cycle around 1024 outer repetitions, including dummy; excludes initialization and checksum; raw delta without overhead subtraction',
                    'started_at_source': 'runner.log (runner local time)',
                    'manifest': str(manifest_path), 'manifest_sha256': digest,
                    'emulator': identity(emulator), 'reference': identity(reference),
                    'runner': identity(RUNNER), 'jobs': int(header('parallel')),
                    'timeout': header('timeout'), 'command': command,
                    'identity_observation': 'file identities observed at collection; paths recovered from runner.log',
                    'build': {k: v for k, v in manifest.items() if k != 'cases'},
                    'runner_returncode': None}
    metadata.setdefault('manifest', str(manifest_path))
    metadata.setdefault('manifest_sha256', digest)
    metadata.update(run_directory=str(run), staging_directory=str(run / 'bin'),
                    runner_stdout=str(runner_log), runner_stderr=str(run / 'runner.err'),
                    outer_repetitions=REPETITIONS, binary_sha256=hashes,
                    collected_at=datetime.now(timezone.utc).isoformat())
    if snapshot:
        metadata['measurement_build'] = snapshot
    finished = re.search(r'^\s*finished:\s*(.*?)\s*$', text, re.M)
    if not finished and not metadata.get('finished_at'):
        raise ValueError('batch is still running: collect after runner prints its final summary')
    if finished and 'finished_at' not in metadata:
        metadata['finished_at'] = finished[1]
    return run / 'logs', metadata


def finish_reports(logs, metadata, cases):
    lists = {kind: set() for kind in ('pass', 'fail', 'timeout')}
    for kind in lists:
        for path in logs.rglob(f'.{kind}_list'):
            lists[kind].update(path.read_text(errors='replace').splitlines())
    rows = [parse_case(case, logs, lists) for case in cases]
    reports = ROOT / 'reports/tsvc-gsim-1024'
    counts = write_reports(reports, metadata, rows)
    run_id = Path(metadata['run_directory']).name
    history = ROOT / 'reports/measurements' / run_id
    write_reports(history, metadata, rows)
    # Preserve configuration and each benchmark's raw cycles in an immutable run directory.
    configuration = {k: metadata.get(k) for k in ('build', 'manifest_sha256', 'binary_sha256', 'emulator', 'reference', 'outer_repetitions')}
    configuration_id = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
    records = [{'run_id': run_id, 'configuration_id': configuration_id,
                'compiler': metadata['build'].get('compiler'), 'flags': metadata['build'].get('flags'),
                'suite': row['suite'], 'name': row['name'], 'status': row['status'],
                'total_cycles': row['total_cycles'], 'cycles_per_outer_repetition': row['cycles_per_outer_repetition'],
                'outer_repetitions': REPETITIONS, 'checksum': row['checksum'],
                'binary_sha256': metadata.get('binary_sha256', {}).get(row['name']),
                'emulator': metadata['emulator']['path'], 'reference': metadata['reference']['path']} for row in rows]
    (history / 'comparison-records.json').write_text(json.dumps(records, indent=2) + '\n')
    print(f'Reports: {reports}; history: {history}; {json.dumps(counts, sort_keys=True)}', flush=True)
    return int(any(row['status'] != 'passed' for row in rows)
               or metadata['runner_returncode'] not in (None, 0))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'build/O3/manifest.json')
    parser.add_argument('--case', help='run or collect exactly one TSVC manifest case (smoke test)')
    parser.add_argument('--collect-run', type=Path,
                        help='collect existing build/gsim-1024 run logs without launching emulators')
    parser.add_argument('--jobs', type=int, default=32, help='runner concurrency: 1..64 (default: 32)')
    parser.add_argument('--timeout', default='0', help='GNU timeout duration per case; 0 disables timeout (default: 0)')
    args = parser.parse_args(argv)
    if not 1 <= args.jobs <= 64:
        parser.error('--jobs must be between 1 and 64')
    if not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)[smhd]?', args.timeout) or float(
            args.timeout.rstrip('smhd')) < 0:
        parser.error('--timeout must be 0 (disabled) or a positive duration, e.g. 180, 30m, or 2h')
    manifest_path = args.manifest.resolve()
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    cases = [dict(c) for c in manifest['cases'] if c['suite'] == 'tsvc'
             and (args.case is None or c['name'] == args.case)]
    if not cases:
        parser.error('no matching TSVC manifest cases')
    names = set()
    for case in cases:
        name = case['name']
        if case.get('outer_iterations') != REPETITIONS:
            parser.error(f'{name}: rebuild with outer_iterations=1024 before measuring')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name) or name.endswith('_'):
            parser.error(f'unsafe runner case name: {name!r}')
        if name in names:
            parser.error(f'duplicate TSVC manifest name: {name}')
        names.add(name)
        directory = Path(case['directory'])
        if not directory.is_absolute():
            directory = manifest_path.parent / directory
        case['directory'] = str(directory.resolve())
        case['_binary'] = (directory / f'{name}.bin').resolve()
    if args.collect_run is not None:
        logs, metadata = collect_run(args.collect_run, manifest_path, manifest, manifest_bytes, cases)
        return finish_reports(logs, metadata, cases)
    for path in (RUNNER, REF):
        if not path.is_file():
            parser.error(f'required file is missing: {path}')
    candidates = [p for p in EMU_DIR.glob('emu-gsim-*') if p.is_file()]
    if not candidates:
        parser.error(f'no emu-gsim-* files in {EMU_DIR}')
    emulator = max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name))
    if not os.access(emulator, os.X_OK):
        parser.error(f'latest emulator is not executable: {emulator}')
    base = ROOT / 'build/gsim-1024'
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    run = Path(tempfile.mkdtemp(prefix=stamp, dir=base))
    staging = run / 'bin'
    staging.mkdir()
    runnable = [c for c in cases if c.get('status') == 'built' and c['_binary'].is_file()]
    for case in runnable:
        (staging / f"{case['name']}.bin").symlink_to(case['_binary'])
    logs = run / 'logs'
    logs.mkdir()
    command = [str(RUNNER), '-e', str(emulator), '-r', str(REF), '-t', str(staging),
               '-j', str(args.jobs), '--timeout', args.timeout, '-o', str(logs)]
    binary_hashes = {c['name']: hashlib.sha256(c['_binary'].read_bytes()).hexdigest() for c in runnable}
    metadata = {'binary_sha256': binary_hashes, 'timing': 'inline csrr cycle around 1024 outer repetitions, including dummy; excludes initialization and checksum; raw delta without overhead subtraction',
                'started_at': datetime.now(timezone.utc).isoformat(),
                'manifest': str(manifest_path),
                'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                'emulator': identity(emulator), 'reference': identity(REF),
                'runner': identity(RUNNER), 'build': {k: v for k, v in manifest.items() if k != 'cases'},
                'jobs': args.jobs, 'timeout': args.timeout, 'outer_repetitions': REPETITIONS,
                'run_directory': str(run), 'staging_directory': str(staging),
                'runner_stdout': str(run / 'runner.log'),
                'runner_stderr': str(run / 'runner.err'), 'command': command,
                'runner_returncode': None}
    (run / 'run-metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'Logs: {run}', flush=True)
    if runnable:
        print(shlex.join(command), flush=True)
        with (run / 'runner.log').open('w') as stdout, (run / 'runner.err').open('w') as stderr:
            try:
                metadata['runner_returncode'] = subprocess.run(
                    command, cwd=ROOT.parent, stdout=stdout, stderr=stderr, check=False).returncode
            except OSError as exc:
                metadata['runner_error'] = str(exc)
                stderr.write(str(exc) + '\n')
    metadata['finished_at'] = datetime.now(timezone.utc).isoformat()
    (run / 'run-metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    return finish_reports(logs, metadata, cases)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        sys.exit(2)
