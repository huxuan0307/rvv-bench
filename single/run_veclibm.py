#!/usr/bin/env python3
"""Run bare-metal veclibm images under NEMU and preserve immutable evidence.

Examples (run from the repository root)::

  python3 single/run_veclibm.py --glob 'single/build/veclibm-*-n*.bin' \
      --require-marker VECLIBM_COMPLETE --output single/build/run-NEW
  python3 single/run_veclibm.py --manifest path/to/manifest.json \
      --output single/build/accuracy-run-NEW

A manifest may be a list, or contain ``cases``/``entries``. Each entry accepts
name/id, bin/binary, elf (a sibling .bin must exist), or directory (containing
name.bin or exactly one .bin). Paths resolve relative to the manifest first,
then the working directory and repository root. Entries/top-level may declare
require_marker/required_markers/completion_marker/marker. Explicit CLI markers
replace the default VECLIBM_COMPLETE and are added to manifest markers.

Functional success requires exit 0, GOOD TRAP, all required markers, a parsed
host-reported guest instruction count, and no error or instruction-limit stop.
The 1M--30M default calibration range is separate: short special-value tests are
reported honestly, never padded. --require-budget optionally makes an out-of-
range but functionally successful run return nonzero. NEMU counts include boot,
initialization, checks and printing, not just the function's measured region.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import time

DEFAULT_NEMU = '/nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter'
REPO_ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
COUNT = re.compile(r'total\s+guest\s+instructions\s*=\s*([\d,]+)', re.I)
ERRORS = re.compile(
    # Numerical diagnostics legitimately contain lowercase "absolute error".
    # Recognize uppercase ERROR anywhere, or a case-insensitive error label at
    # the start of a line (optionally following bracketed logger prefixes).
    r'HIT\s+(?:BAD\s+TRAP|CRITICAL\s+ERROR)|(?-i:\bERROR\b)|'
    r'(?m:^[ \t]*(?:\[[^\]\n]*\][ \t]*)*error\b(?:[ \t]*:|[ \t]+))|'
    r'\bFAIL(?:ED|URE)?\b|'
    r'\bABORT(?:ED)?\b|assertion[^\n]*failed|assert[^\n]*fail|'
    r'DIFFTEST\s+(?:FAIL|ERROR)|Segmentation fault|AddressSanitizer|'
    r'VECLIBM_(?:ACCURACY_)?(?:FAIL|ERROR)', re.I)
LIMIT = re.compile(
    r'(?:reached|exceeded|hit)[^\n]{0,80}(?:instruction|instr)[^\n]{0,40}(?:limit|budget)|'
    r'(?:instruction|instr)[^\n]{0,40}(?:limit|budget)[^\n]{0,80}(?:reached|exceeded)|'
    r'max(?:imum)?(?:[ _-]+number[ _-]+of)?[ _-]+instr(?:uctions)?[^\n]{0,40}(?:reached|exceeded)', re.I)
MARKER_KEYS = ('require_marker', 'require_markers', 'required_marker',
               'required_markers', 'completion_marker', 'complete_marker', 'marker')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def markers_from(record: dict) -> list[str]:
    markers = []
    for key in MARKER_KEYS:
        value = record.get(key)
        if value is not None:
            values = [value] if isinstance(value, str) else value
            if not isinstance(values, list) or not all(isinstance(x, str) and x for x in values):
                raise ValueError(f'{key} must be a nonempty string or list of nonempty strings')
            markers.extend(values)
    return list(dict.fromkeys(markers))


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    candidates = [base / path, Path.cwd() / path, REPO_ROOT / path]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def image_from(entry: dict, base: Path) -> Path:
    value = entry.get('bin') or entry.get('binary') or entry.get('bin_path')
    if value:
        return resolve_path(str(value), base)
    value = entry.get('elf') or entry.get('elf_path')
    if value:
        # NEMU's IMAGE interface loads raw bytes, not ELF program segments.
        return resolve_path(str(value), base).with_suffix('.bin')
    value = entry.get('directory')
    if not value:
        raise ValueError(f'entry has no bin/elf/directory: {entry!r}')
    directory = resolve_path(str(value), base)
    name = entry.get('name') or entry.get('id')
    if name and (directory / (str(name) + '.bin')).is_file():
        return (directory / (str(name) + '.bin')).resolve()
    candidates = sorted(directory.glob('*.bin'))
    if len(candidates) != 1:
        raise ValueError(f'{directory}: expected name.bin or exactly one .bin, found {len(candidates)}')
    return candidates[0].resolve()


def load_cases(args) -> list[dict]:
    cases = []
    if args.manifest:
        manifest_path = args.manifest.resolve()
        manifest = json.loads(manifest_path.read_text())
        if isinstance(manifest, list):
            entries, inherited = manifest, []
        elif isinstance(manifest, dict):
            entries = manifest.get('cases', manifest.get('entries'))
            inherited = markers_from(manifest)
        else:
            raise ValueError('manifest must be a JSON object or list')
        if not isinstance(entries, list):
            raise ValueError('manifest must contain a cases or entries list')
        for entry in entries:
            entry = {'bin': entry} if isinstance(entry, str) else entry
            if not isinstance(entry, dict):
                raise ValueError('manifest entries must be objects or path strings')
            image = image_from(entry, manifest_path.parent)
            markers = list(dict.fromkeys(inherited + markers_from(entry) + (args.require_marker or [])))
            cases.append({'name': str(entry.get('name') or entry.get('id') or image.stem),
                          'suite': entry.get('suite', ''), 'bin': str(image),
                          'required_markers': markers or ['VECLIBM_COMPLETE'],
                          'source': {'manifest': str(manifest_path), 'entry': entry}})
    paths = list(args.bins or [])
    for pattern in args.glob or []:
        matches = sorted(glob.glob(pattern, recursive=True))
        if not matches:
            raise ValueError(f'glob matched no files: {pattern}')
        paths.extend(Path(match) for match in matches)
    for path in paths:
        image = path.expanduser().resolve()
        if image.suffix == '.elf':
            image = image.with_suffix('.bin')
        cases.append({'name': image.stem, 'suite': '', 'bin': str(image),
                      'required_markers': args.require_marker or ['VECLIBM_COMPLETE'],
                      'source': {'explicit_path': str(path)}})
    if not cases:
        raise ValueError('select at least one case using --manifest, --bins or --glob')
    seen = set()
    unique = []
    for case in cases:
        key = (case['name'], case['bin'], tuple(case['required_markers']))
        if key not in seen:
            seen.add(key)
            unique.append(case)
    for index, case in enumerate(unique, 1):
        safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', case['name']).strip('.') or 'case'
        case['case_id'] = f'{index:04d}-{safe[:140]}'
    return unique


def analyze_logs(stdout: str, stderr: str, *, returncode, timed_out: bool,
                 max_instr: int, required_markers: list[str],
                 range_min: int, range_max: int, launch_error=None) -> dict:
    """Audit both streams; error/budget/timeout override a superficially good trap."""
    text = ANSI.sub('', stdout + '\n' + stderr)
    counts = [int(value.replace(',', '')) for value in COUNT.findall(text)]
    guest = counts[-1] if counts else None
    good = 'HIT GOOD TRAP' in text
    # Accuracy emits a structured failed=0 success counter. Exempt only the
    # exact integer zero token; failed=01/1 and independent FAIL still fail.
    error_text = re.sub(r'\bfailed=0(?=$|[\s,;\]}])', '', text, flags=re.I)
    errors = list(dict.fromkeys(match.group(0) for match in ERRORS.finditer(error_text)))
    missing = [marker for marker in required_markers if marker not in text]
    limit_message = bool(LIMIT.search(text))
    # Some NEMU versions simply stop the CPU at -I without an explicit message.
    budget_interrupted = limit_message or (guest is not None and guest >= max_instr and not good)
    hard_limit_exceeded = guest is not None and guest > max_instr
    reasons = []
    if launch_error:
        reasons.append(f'launch error: {launch_error}')
    if timed_out:
        reasons.append('wall-clock timeout')
    if returncode != 0:
        reasons.append(f'exit code {returncode}')
    if errors:
        reasons.append('error marker(s): ' + ', '.join(errors))
    if budget_interrupted:
        reasons.append('instruction budget interruption')
    if hard_limit_exceeded:
        reasons.append('reported count exceeds hard instruction limit')
    if not good:
        reasons.append('missing HIT GOOD TRAP')
    if missing:
        reasons.append('missing completion marker(s): ' + ', '.join(missing))
    if guest is None:
        reasons.append('missing NEMU total guest instruction count')
    passed = not reasons
    if launch_error:
        status = 'launch_error'
    elif timed_out:
        status = 'timeout'
    elif budget_interrupted or hard_limit_exceeded:
        status = 'instruction_limit'
    elif errors:
        status = 'error'
    elif returncode != 0:
        status = 'nonzero_exit'
    elif not good:
        status = 'missing_good_trap'
    elif missing:
        status = 'missing_marker'
    elif guest is None:
        status = 'missing_instruction_count'
    else:
        status = 'passed'
    within = guest is not None and range_min <= guest <= range_max
    range_status = ('unavailable' if guest is None else
                    'below_range' if guest < range_min else
                    'above_range' if guest > range_max else 'within_range')
    return {'status': status, 'functional_pass': passed, 'passed': passed,
            'good_trap': good, 'error_markers': errors, 'missing_markers': missing,
            'guest_instructions': guest, 'guest_instruction_counts': counts,
            'instruction_budget_interrupted': budget_interrupted,
            'hard_limit_exceeded': hard_limit_exceeded,
            'within_instruction_range': within, 'budget_met': passed and within,
            'instruction_range_status': range_status, 'failure_reasons': reasons}


def run_command(command: list[str], stdout, stderr, timeout: float) -> dict:
    started = time.monotonic()
    returncode, timed_out, error = None, False, None
    try:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            returncode = process.wait()
    except OSError as exc:
        error = str(exc)
    return {'returncode': returncode, 'timed_out': timed_out, 'launch_error': error,
            'elapsed_seconds': time.monotonic() - started}


def execute(case: dict, args, output: Path) -> dict:
    directory = output / 'cases' / case['case_id']
    directory.mkdir(parents=True)
    stdout_path, stderr_path = directory / 'stdout.log', directory / 'stderr.log'
    image = Path(case['bin'])
    is_elf = False
    if image.is_file():
        with image.open('rb') as stream:
            is_elf = stream.read(4) == b'\x7fELF'
    command = [str(args.nemu), '-b', f'--max-instr={args.max_instr}', str(image)]
    result = dict(case, command=command, started_utc=datetime.now(timezone.utc).isoformat(),
                  stdout=str(stdout_path), stderr=str(stderr_path), sha256=None,
                  image_size_bytes=None)
    manifest_entry = case.get('source', {}).get('entry', {})
    build_status = manifest_entry.get('status')
    expected_sha = manifest_entry.get('sha256')
    with stdout_path.open('w') as stdout, stderr_path.open('w') as stderr:
        if build_status is not None and build_status != 'built':
            process = {'returncode': None, 'timed_out': False, 'elapsed_seconds': 0,
                       'launch_error': f'manifest build status is {build_status!r}, not built'}
        elif not image.is_file():
            process = {'returncode': None, 'timed_out': False, 'elapsed_seconds': 0,
                       'launch_error': f'raw .bin image does not exist: {image}'}
        elif is_elf:
            process = {'returncode': None, 'timed_out': False, 'elapsed_seconds': 0,
                       'launch_error': 'NEMU requires a raw .bin, not an ELF image'}
        else:
            result['sha256'] = sha256(image)
            result['image_size_bytes'] = image.stat().st_size
            if expected_sha is not None and expected_sha != result['sha256']:
                process = {'returncode': None, 'timed_out': False, 'elapsed_seconds': 0,
                           'launch_error': f'manifest BIN sha256 mismatch: expected {expected_sha}, actual {result["sha256"]}'}
            else:
                process = run_command(command, stdout, stderr, args.timeout)
    result.update(process)
    result.update(analyze_logs(stdout_path.read_text(errors='replace'),
                               stderr_path.read_text(errors='replace'),
                               returncode=process['returncode'], timed_out=process['timed_out'],
                               launch_error=process['launch_error'], max_instr=args.max_instr,
                               required_markers=case['required_markers'],
                               range_min=args.range_min, range_max=args.range_max))
    (directory / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f"{result['status'].upper()} {case['name']} instructions={result['guest_instructions']} "
          f"range={result['instruction_range_status']} elapsed={result['elapsed_seconds']:.3f}s", flush=True)
    return result


def count_summary(results: list[dict]) -> dict:
    values = [case['guest_instructions'] for case in results if case['guest_instructions'] is not None]
    return {'count': len(values), 'min': min(values) if values else None,
            'median': statistics.median(values) if values else None,
            'max': max(values) if values else None}


def save_results(output: Path, results: list[dict], config: dict) -> dict:
    summary = {'cases': len(results),
               'functional_pass': sum(case['functional_pass'] for case in results),
               'budget_met': sum(case['budget_met'] for case in results),
               'within_instruction_range': sum(case['within_instruction_range'] for case in results),
               'instruction_counts_all': count_summary(results),
               'instruction_counts_passed': count_summary([r for r in results if r['functional_pass']])}
    document = {'schema_version': 1, 'config': config, 'summary': summary, 'cases': results}
    (output / 'results.json').write_text(json.dumps(document, indent=2) + '\n')
    fields = ['case_id', 'suite', 'name', 'bin', 'sha256', 'image_size_bytes', 'status',
              'functional_pass', 'returncode', 'timed_out', 'elapsed_seconds',
              'guest_instructions', 'good_trap', 'instruction_budget_interrupted',
              'hard_limit_exceeded', 'within_instruction_range', 'budget_met',
              'instruction_range_status', 'required_markers', 'missing_markers',
              'error_markers', 'failure_reasons', 'stdout', 'stderr', 'command']
    with (output / 'results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for case in results:
            writer.writerow({key: json.dumps(value) if isinstance(value, (list, dict)) else value
                             for key, value in case.items()})
    lines = ['# veclibm NEMU run', '',
             f"- Functional pass: **{summary['functional_pass']}/{summary['cases']}**",
             f"- Functional pass and calibration range met: **{summary['budget_met']}/{summary['cases']}**",
             f"- Calibration range: **{config['range_min']:,}–{config['range_max']:,}** guest instructions; "
             f"target ~{config['target_instr']:,}; hard cap {config['max_instr']:,}.",
             '- Counts are NEMU host-reported totals, including boot, setup, checks and output.',
             '- Out-of-range short special-value tests are not padded; range status is separate from functional success.',
             '- NEMU cycle/instruction data is not a hardware throughput or numerical-accuracy proof.', '']
    for label, key in [('All counted runs', 'instruction_counts_all'), ('Functionally passed runs', 'instruction_counts_passed')]:
        stats = summary[key]
        lines.append(f"- {label}: count={stats['count']}, min={stats['min']}, median={stats['median']}, max={stats['max']}")
    lines += ['', '| Case | Functional status | Guest instructions | Calibration | Seconds | Evidence |',
              '|---|---|---:|---|---:|---|']
    for case in results:
        directory = Path(case['stdout']).parent.relative_to(output)
        name = case['name'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {name} | {case['status']} | {case['guest_instructions']} | "
                     f"{case['instruction_range_status']} | {case['elapsed_seconds']:.3f} | "
                     f"[stdout](<{directory}/stdout.log>) / [stderr](<{directory}/stderr.log>) |")
    lines += ['', '## Failure details', '']
    for case in results:
        if case['failure_reasons']:
            lines.append(f"- {case['name']}: " + '; '.join(case['failure_reasons']))
    (output / 'summary.md').write_text('\n'.join(lines) + '\n')
    return summary


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument('--manifest', type=Path)
    result.add_argument('--bins', nargs='+', type=Path)
    result.add_argument('--glob', action='append', help='quoted glob pattern, repeatable')
    result.add_argument('--nemu', type=Path, default=Path(DEFAULT_NEMU))
    result.add_argument('--jobs', type=int, default=4)
    result.add_argument('--timeout', type=float, default=120)
    result.add_argument('--max-instr', type=int, default=50_000_000, help='NEMU hard stop via --max-instr')
    result.add_argument('--target-instr', type=int, default=10_000_000, help='descriptive calibration target only')
    result.add_argument('--range-min', '--min-instr', type=int, default=1_000_000)
    result.add_argument('--range-max', '--target-max-instr', type=int, default=30_000_000)
    result.add_argument('--require-marker', action='append', help='required completion substring, repeatable')
    result.add_argument('--require-budget', action='store_true', help='return failure for functionally passed out-of-range runs too')
    result.add_argument('--output', type=Path, required=True, help='new directory; existing paths are rejected')
    return result


def main(argv=None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    if args.jobs < 1 or args.timeout <= 0 or args.max_instr < 1 or args.target_instr < 1:
        cli.error('jobs, timeout, max-instr and target-instr must be positive')
    if args.range_min < 0 or args.range_max < args.range_min:
        cli.error('require 0 <= range-min <= range-max')
    if args.require_marker and any(not marker for marker in args.require_marker):
        cli.error('completion markers must not be empty')
    try:
        cases = load_cases(args)
        requested_output = args.output.expanduser()
        if requested_output.exists() or requested_output.is_symlink():
            raise ValueError(f'output already exists: {requested_output}; select a fresh directory')
        output = requested_output.resolve()
        output.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError, TypeError) as exc:
        cli.error(str(exc))
    args.nemu = args.nemu.expanduser().resolve()
    # --bins is a list of Paths, so normalize nested values as well.
    config = json.loads(json.dumps(vars(args), default=str))
    config.update(started_utc=datetime.now(timezone.utc).isoformat(), working_directory=str(Path.cwd()),
                  nemu_sha256=sha256(args.nemu) if args.nemu.is_file() else None,
                  count_scope='whole-program NEMU host total guest instructions')
    (output / 'run-config.json').write_text(json.dumps(config, indent=2) + '\n')
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda case: execute(case, args, output), cases))
    summary = save_results(output, results, config)
    print(json.dumps(summary, indent=2))
    return int(any(not case['functional_pass'] or (args.require_budget and not case['budget_met']) for case in results))


if __name__ == '__main__':
    sys.exit(main())
