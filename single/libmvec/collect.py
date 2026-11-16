#!/usr/bin/env python3
"""Strictly audit an immutable libmvec NEMU/gsim run without launching anything.

Usage::

  python3 single/libmvec/collect.py --run RUN_DIRECTORY
  python3 single/libmvec/collect.py --run RUN_DIRECTORY --require-complete

The collector reads ``run-metadata.json``, the immutable manifest snapshot,
``runner-state.json``, per-case ``result.json`` and guest logs.  It rewrites only
the reports directory (including independent ``recovered-samples.csv`` and
``recovery.json`` diagnostics). Complete CSV rows are recovered before any
case-state early return; recovery never grants functional/performance acceptance.

A case passes only when the staged BIN hash still matches both the metadata and
the manifest snapshot, the exact recorded command was used, the process exited
0 without timeout, the required ``LIBMVEC_COMPLETE`` marker is unique, the RTL
Core 0 (gsim) or NEMU GOOD TRAP is present, no failure/DIFF diagnostic appeared,
``LIBMVEC_CHECK`` is unique with ``checks >= min_checks``, and every expected
performance SAMPLE/CALIBRATION record or accuracy ABI record is present exactly
once.  ``uint64`` cycle fields are parsed as decimal integers and written to CSV
as exact decimal strings (never through float).

While the runner is active the report is a progress report and exits 0.  A final
failed/aborted run exits 1.  With ``--require-complete`` the exit code is 0 only
when the runner terminated and every selected case passed.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import statistics
import sys

import runner_common as common
import compact_csv

STATE_VALUES = ('in_progress', 'terminated', 'aborted')
CASE_STATES = ('completed', 'skipped', 'aborted')
REFERENCE_WORDS = re.compile(r'\b(?:ref(?:erence)?|nemu|difftest)\b', re.I)


def _json(path):
    return json.loads(Path(path).read_text())


def _int_field(record, name, issues, *, minimum=0, maximum=None):
    value = record.get(name)
    if not isinstance(value, str) or not common.DECIMAL.fullmatch(value):
        issues.append(f'{record.get("_raw", "record")}: invalid {name}={value!r}')
        return None
    number = int(value)
    if number < minimum:
        issues.append(f'{record.get("_raw", name)}: {name} must be >= {minimum}')
        return None
    if maximum is not None and number > maximum:
        issues.append(f'{record.get("_raw", name)}: {name} exceeds {maximum}')
        return None
    return number


def _u64(record, name, issues):
    return _int_field(record, name, issues, minimum=0, maximum=common.U64_MAX)


def _require_string(record, name, issues):
    value = record.get(name)
    if not isinstance(value, str) or not value:
        issues.append(f'{record.get("_raw", "record")}: missing {name}')
        return None
    return value


def rtl_core_traps(text):
    """GOOD TRAP lines explicitly attributed to RTL Core 0, not the reference."""
    traps = []
    for line in text.splitlines():
        if not common.CORE_GOOD_TRAP.search(line):
            continue
        if REFERENCE_WORDS.search(line):
            continue
        traps.append(line.strip())
    return traps


def audit_trap(text, metadata, issues):
    any_trap = bool(common.GOOD_TRAP.search(text))
    if metadata['mode'] == 'gsim':
        traps = rtl_core_traps(text)
        if not traps:
            if any_trap:
                issues.append(
                    'missing RTL Core 0 GOOD TRAP; only a reference-model trap was found')
            else:
                issues.append('missing RTL Core 0 GOOD TRAP')
        return {'core_good_trap': bool(traps), 'any_good_trap': any_trap,
                'rtl_traps': traps}
    if not any_trap:
        issues.append('missing NEMU HIT GOOD TRAP')
    return {'core_good_trap': None, 'any_good_trap': any_trap, 'rtl_traps': []}


def audit_config(text, manifest_case, issues):
    """Validate and retain the per-run CONFIG evidence, including VLEN."""
    records = common.records(text, 'LIBMVEC_CONFIG')
    if len(records) != 1:
        issues.append(f'expected exactly one LIBMVEC_CONFIG, found {len(records)}')
    if not records:
        return None
    record = records[0]
    plan = manifest_case['expected']
    accuracy = manifest_case['suite'] == 'accuracy'
    config = {'case': record.get('case'), 'function': record.get('function'),
              'vlen_bits': None, 'vlen_bits_raw': record.get('vlen_bits'),
              'seed': None, 'rounds': None}
    if record.get('case') != manifest_case['name']:
        issues.append(f'CONFIG case mismatch: {record.get("case")!r}')
    seed = _int_field(record, 'seed', issues, minimum=0)
    config['seed'] = seed
    if seed != 123456789:
        issues.append(f'CONFIG seed is {seed}, expected 123456789')
    rounds = _int_field(record, 'rounds', issues, minimum=0)
    config['rounds'] = rounds
    expected_rounds = 0 if accuracy else plan['rounds']
    if rounds is not None and rounds != expected_rounds:
        issues.append(f'CONFIG rounds is {rounds}, expected {expected_rounds}')
    if record.get('function') != ('abi' if accuracy else plan['function']):
        issues.append('CONFIG function mismatch')
    config['profile'] = record.get('profile', 'full')
    if not accuracy and config['profile'] != plan.get('profile', 'full'):
        issues.append('CONFIG profile mismatch')
    if not accuracy:
        repeat = _int_field(record, 'timing_repeat', issues, minimum=1)
        config['timing_repeat'] = repeat
        expected_repeat = plan.get('timing_repeat', 1)
        if repeat is not None and repeat != expected_repeat:
            issues.append(f'CONFIG timing_repeat is {repeat}, expected {expected_repeat}')
    else:
        config['timing_repeat'] = None
    vlen = _int_field(record, 'vlen_bits', issues, minimum=128)
    config['vlen_bits'] = vlen
    if vlen is not None:
        config['vlen_multiple_of_128'] = vlen % 128 == 0
        config['vlen_power_of_two'] = vlen & (vlen - 1) == 0
        if vlen % 128 or vlen & (vlen - 1):
            issues.append(f'CONFIG vlen_bits={vlen} is not a supported power-of-two multiple of 128')
    return config


def audit_check(text, manifest_case, issues):
    records = common.records(text, 'LIBMVEC_CHECK')
    result = {'checks': None, 'failed': None, 'count': len(records)}
    if len(records) != 1:
        issues.append(f'expected exactly one LIBMVEC_CHECK, found {len(records)}')
    for record in records:
        if record.get('case') != manifest_case['name']:
            issues.append(f'CHECK case mismatch: {record.get("case")!r}')
        failed = _int_field(record, 'failed', issues, minimum=0)
        checks = _int_field(record, 'checks', issues, minimum=0)
        if result['checks'] is None:
            result['checks'] = checks
            result['failed'] = failed
        if failed not in (None, 0):
            issues.append(f'CHECK reports failed={failed}')
        if checks is not None and checks < manifest_case['expected']['min_checks']:
            issues.append(
                f'CHECK checks={checks} < min_checks='
                f'{manifest_case["expected"]["min_checks"]}')
    if result['checks'] is not None and result['failed'] == 0 and len(records) == 1:
        pass
    return result


def audit_complete(text, manifest_case, issues):
    records = common.records(text, 'LIBMVEC_COMPLETE')
    # CSV framing/end marker is validated before canonical record expansion.
    expected = (f'LIBMVEC_COMPLETE case={manifest_case["name"]} status=pass'
                if manifest_case['expected'].get('wire_format') in ('csv2', 'csv3')
                else manifest_case['required_markers'][0])
    count = common.marker_count(text, expected)
    if count != 1:
        issues.append(f'required complete marker count is {count}, expected 1')
    if len(records) != 1:
        issues.append(f'expected exactly one LIBMVEC_COMPLETE, found {len(records)}')
        return
    record = records[0]
    if record.get('case') != manifest_case['name']:
        issues.append(f'COMPLETE case mismatch: {record.get("case")!r}')
    if record.get('status') != 'pass':
        issues.append(f'COMPLETE status is {record.get("status")!r}, expected pass')


def audit_cycles(record, kind, issues):
    begin = _u64(record, 'cycle_begin', issues)
    end = _u64(record, 'cycle_end', issues)
    cycles = _u64(record, 'cycles', issues)
    minimum = 1 if kind == 'sample' else 0
    if cycles is not None and cycles < minimum:
        issues.append(f'{kind} cycles must be >= {minimum}, got {cycles}')
    if begin is not None and end is not None and cycles is not None:
        if cycles != (end - begin) % (1 << 64):
            issues.append(
                f'{kind} cycles mismatch: cycles={cycles}, '
                f'cycle_begin={begin}, cycle_end={end}')
    return begin, end, cycles


def normalize_record(record, kind, name, plan, issues):
    """Validate one SAMPLE/CALIBRATION record and keep exact integer values."""
    parsed = {'record_type': kind, 'case': name, '_raw': record.get('_wire', record.get('_raw', '')),
              'wire_format': record.get('wire_format', 'kv1')}
    if record.get('case') != name:
        issues.append(f'{kind} case mismatch: {record.get("case")!r}')
    for key in ('mode', 'phase'):
        value = _require_string(record, key, issues)
        parsed[key] = value
    parsed['checksum'] = _u64(record, 'checksum', issues)
    function = _require_string(record, 'function', issues)
    if function is not None and function != plan['function']:
        issues.append(f'{kind} function {function!r} != {plan["function"]!r}')
    parsed['function'] = function
    n = _int_field(record, 'n', issues, minimum=1)
    k = _int_field(record, 'k', issues, minimum=1)
    round_index = _int_field(record, 'round', issues, minimum=0)
    order = _int_field(record, 'order', issues, minimum=0,
                       maximum=len(plan['modes']) + len(plan['calibration_modes']) - 1)
    parsed.update(n=n, k=k, round=round_index, order=order)
    phase = parsed.get('phase')
    if phase not in common.PHASES:
        if phase is not None:
            issues.append(f'invalid phase {phase!r}')
    elif round_index is not None and round_index >= common.PHASES[phase]:
        issues.append(f'round {round_index} out of range for phase {phase}')
    begin, end, cycles = audit_cycles(record, kind, issues)
    parsed.update(cycle_begin=begin, cycle_end=end, cycles=cycles)
    repeat = _int_field(record, 'timing_repeat', issues, minimum=1, maximum=64)
    if repeat is None:
        repeat = 1
    if repeat != plan.get('timing_repeat', 1):
        issues.append(
            f'{kind} timing_repeat={repeat} != manifest '
            f'{plan.get("timing_repeat", 1)}')
    parsed['timing_repeat'] = repeat
    parsed['cycles_per_round'] = cycles / repeat if cycles is not None else None
    if n is not None and n not in plan['lengths']:
        issues.append(f'n={n} is not in manifest lengths')
    if k is not None and k not in plan['batches']:
        issues.append(f'k={k} is not in manifest batches')
    if order is not None and order >= (len(plan['modes']) + len(plan['calibration_modes'])):
        issues.append(f'order={order} is outside the rotation')
    if phase in common.ROUND_BASE and round_index is not None:
        parsed['r'] = common.ROUND_BASE[phase] + round_index
    else:
        parsed['r'] = None
    return parsed


def audit_performance(text, manifest_case, issues):
    plan = manifest_case['expected']
    name = manifest_case['name']
    expected = common.expected_schedule_map(plan)
    samples = [normalize_record(record, 'sample', name, plan, issues)
               for record in common.records(text, 'LIBMVEC_SAMPLE')]
    calibrations = [normalize_record(record, 'calibration', name, plan, issues)
                    for record in common.records(text, 'LIBMVEC_CALIBRATION')]

    configs = common.records(text, 'LIBMVEC_CONFIG')
    if len(configs) != 1:
        issues.append(f'expected exactly one LIBMVEC_CONFIG, found {len(configs)}')
    for config in configs[:1]:
        if config.get('case') != name:
            issues.append(f'CONFIG case mismatch: {config.get("case")!r}')
        if config.get('function') != plan['function']:
            issues.append('CONFIG function mismatch')
        _int_field(config, 'vlen_bits', issues, minimum=1)
        seed = _int_field(config, 'seed', issues, minimum=0)
        if seed != 123456789:
            issues.append(f'CONFIG seed is {seed}, expected 123456789')
        rounds = _int_field(config, 'rounds', issues, minimum=1)
        if rounds is not None and rounds != plan['rounds']:
            issues.append(f'CONFIG rounds is {rounds}, expected {plan["rounds"]}')

    observed = {}
    for row in samples + calibrations:
        key = (row.get('n'), row.get('k'), row.get('phase'),
               row.get('round'), row.get('order'))
        if any(value is None for value in key):
            continue
        if key in observed:
            issues.append(f'duplicate guest record key {key}')
            continue
        observed[key] = (row['record_type'], row.get('mode'))
        wanted = expected.get(key)
        if wanted is None:
            issues.append(f'unexpected guest record key {key}')
        elif wanted[0] != row['record_type'] or wanted[1] != row.get('mode'):
            issues.append(
                f'guest record {key} is {row["record_type"]}/{row.get("mode")}, '
                f'expected {wanted[0]}/{wanted[1]}')
    missing = sorted(set(expected) - set(observed))
    if missing:
        issues.append(
            f'{len(missing)} expected guest record(s) missing; first: {missing[:3]}')
    if len(samples) != plan['sample_count']:
        issues.append(
            f'SAMPLE count {len(samples)} != manifest {plan["sample_count"]}')
    if len(calibrations) != plan['calibration_count']:
        issues.append(
            f'CALIBRATION count {len(calibrations)} != manifest '
            f'{plan["calibration_count"]}')

    def groups(rows):
        grouped = {}
        for row in rows:
            if (row.get('cycles') is None or row.get('n') is None or
                    row.get('k') is None or row.get('phase') not in common.PHASES):
                continue
            key = (row.get('mode'), row['n'], row['k'], row['phase'])
            grouped.setdefault(key, []).append(row)
        result = []
        for (mode, n, k, phase), values in sorted(
                grouped.items(), key=lambda item: (
                    str(item[0][0]), item[0][1], item[0][2],
                    common.PLAN_PHASE_ORDER.index(item[0][3]))):
            positive = [row for row in values if row['cycles'] > 0]
            result.append({
                'mode': mode, 'n': n, 'k': k, 'phase': phase,
                'count': len(values),
                'timing_repeat': (positive[0]['timing_repeat'] if positive else None),
                'median_cycles_raw': statistics.median(
                    [row['cycles'] for row in positive]) if positive else None,
                'median_cycles': statistics.median(
                    [row['cycles_per_round'] for row in positive]) if positive else None,
                'median_cycles_per_call': statistics.median(
                    [row['cycles_per_round'] / k for row in positive])
                if positive else None,
                'median_cycles_per_element': statistics.median(
                    [row['cycles_per_round'] / (k * n) for row in positive])
                if positive else None,
            })
        return result

    return {
        'samples': samples, 'calibrations': calibrations,
        'sample_count': len(samples), 'calibration_count': len(calibrations),
        # Performance statistics are grouped by (mode, n, k, phase).  They are
        # never pooled across phases or problem sizes, and cycles per call /
        # cycles per element divide by k / (k*n) respectively.  Calibration
        # records are raw only: rdcycle is not repeated per K and must not be
        # turned into a per-element metric.
        'groups': groups(samples),
        'measurement_valid': False,  # set true only for a passing gsim case
    }


def audit_accuracy(text, manifest_case, issues):
    plan = manifest_case['expected']
    unexpected = (len(common.records(text, 'LIBMVEC_SAMPLE')) +
                  len(common.records(text, 'LIBMVEC_CALIBRATION')))
    if unexpected:
        issues.append(f'accuracy case emitted {unexpected} sample/calibration records')
    abi_records = common.records(text, 'LIBMVEC_ABI')
    seen = {}
    for record in abi_records:
        symbol = _require_string(record, 'symbol', issues)
        if record.get('case') not in (None, manifest_case['name']):
            issues.append(f'ABI case mismatch: {record.get("case")!r}')
        failed = _int_field(record, 'failed', issues, minimum=0)
        checks = _int_field(record, 'checks', issues, minimum=1)
        if failed not in (None, 0):
            issues.append(f'ABI {symbol} reports failed={failed}')
        if symbol is not None:
            if symbol in seen:
                issues.append(f'duplicate ABI record for {symbol}')
            seen[symbol] = checks
    expected_symbols = list(plan['abi_symbols'])
    for symbol in expected_symbols:
        if symbol not in seen:
            issues.append(f'missing ABI record for {symbol}')
    for symbol in seen:
        if symbol not in expected_symbols:
            issues.append(f'unexpected ABI record for {symbol}')
    return {'abi': [{'symbol': symbol, 'checks': seen.get(symbol)}
                    for symbol in expected_symbols],
            'abi_count': len(abi_records)}


def verify_command(meta_case, metadata):
    issues = []
    command = meta_case.get('command')
    if not isinstance(command, list) or not command or not all(
            isinstance(item, str) for item in command):
        return ['recorded command is not a non-empty string list']
    tools = metadata['tools']
    if metadata['mode'] == 'gsim':
        expected = common.gsim_command(
            tools['gsim']['realpath'], tools['reference']['realpath'],
            meta_case['staged_bin'], tools['dram_config']['realpath'],
            meta_case['dram_outdir'], metadata['policy']['seed'])
        if command != expected:
            issues.append('recorded gsim command is not the expected exact command')
        if '--no-diff' in command:
            issues.append('gsim command disabled differential testing')
        if command[-2:] != ['-i', meta_case['staged_bin']]:
            issues.append('gsim command does not put -i immediately before the staged image')
    else:
        expected = common.nemu_command(
            tools['nemu']['realpath'], meta_case['staged_bin'],
            metadata['policy']['max_instr'])
        if command != expected:
            issues.append('recorded NEMU command is not the expected exact command')
    return issues


def _load_result(path, issues):
    path = Path(path)
    if not path.is_file():
        return None
    try:
        record = _json(path)
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(f'cannot parse result.json: {exc}')
        return None
    if not isinstance(record, dict):
        issues.append('result.json is not an object')
        return None
    return record


def _current_identity(path):
    path = Path(path)
    if not path.is_file():
        return None
    return common.identity(path)


def audit_case(manifest_case, meta_case, metadata, run, runner_state):
    name = manifest_case['name']
    final = runner_state != 'in_progress'
    reasons = []
    doc = {'name': name, 'suite': manifest_case['suite'], 'status': 'pending',
           'functional_pass': False, 'reasons': [], 'checks': None,
           'checks_failed': None, 'samples': 0, 'calibrations': 0,
           'measurement_valid': False, 'core_good_trap': False,
           'any_good_trap': False, 'ref_only_trap': False,
           'guest_instructions': None, 'returncode': None,
           'timed_out': False, 'launch_error': None, 'aborted': False,
           'staged_sha256': meta_case['staged_sha256'],
           'manifest_sha256': manifest_case['sha256'],
           'stdout': meta_case['stdout'], 'stderr': meta_case['stderr'],
           'result': meta_case['result'],
           'dram_outdir': meta_case.get('dram_outdir')}

    staged = Path(meta_case['staged_bin'])
    if not staged.is_file():
        reasons.append('staged BIN is missing')
    else:
        actual = common.sha256(staged)
        if actual != meta_case['staged_sha256']:
            reasons.append('staged BIN sha256 differs from metadata')
        if actual != manifest_case['sha256']:
            reasons.append('staged BIN sha256 differs from manifest snapshot')
    if not common.is_within(staged, Path(run) / 'staging'):
        reasons.append('staged BIN is outside the run staging directory')
    if not Path(meta_case['workdir']).is_dir():
        reasons.append('per-case working directory is missing')
    if metadata['mode'] == 'gsim':
        if not Path(meta_case['dram_outdir']).is_dir():
            reasons.append('per-case DRAM output directory is missing')
    reasons.extend(verify_command(meta_case, metadata))

    # 在任何终态提前返回前读取日志：运行中、被杀或缺result也能回收完整行。
    logs = {}
    for key in ('stdout', 'stderr'):
        path = Path(meta_case[key])
        if path.is_file():
            try:
                logs[key] = path.read_bytes().decode('utf-8', errors='replace')
            except OSError as exc:
                reasons.append(f'cannot read {key} log: {exc}')
    if manifest_case['expected'].get('wire_format') in ('csv2', 'csv3'):
        recovery = {'wire_format': manifest_case['expected'].get('wire_format'),
                    'records': [], 'streams': [], 'issues': []}
        for key, content in logs.items():
            # 不给未结束行补换行，也不跨stdout/stderr拼接CSV上下文。
            try:
                recovered = compact_csv.recover(content, manifest_case)
            except ValueError as exc:
                recovery['issues'].append({'source': meta_case[key], 'message': str(exc)})
                continue
            rows = recovered.pop('records')
            for row in rows:
                row['source'] = meta_case[key]
            recovery['records'].extend(rows)
            recovery['streams'].append({'source': meta_case[key], **recovered})
        recovery['logical_records'] = len(recovery['records'])
        doc['recovery'] = recovery

    result = _load_result(meta_case['result'], reasons)
    if result is None:
        if final:
            reasons.append('missing result.json after runner termination')
            doc['status'] = 'failed'
        else:
            has_output = any(Path(meta_case[key]).is_file() and
                             Path(meta_case[key]).stat().st_size > 0
                             for key in ('stdout', 'stderr')
                             if meta_case.get(key))
            doc['status'] = 'running' if has_output else 'pending'
        doc['reasons'] = reasons
        return doc

    doc['result_state'] = result.get('state')
    doc['returncode'] = result.get('returncode')
    doc['timed_out'] = bool(result.get('timed_out'))
    doc['launch_error'] = result.get('launch_error')
    doc['aborted'] = bool(result.get('aborted'))
    if result.get('command') != meta_case['command']:
        reasons.append('result.json command differs from the recorded metadata command')
    if metadata['policy'].get('backend') == 'run-emu':
        expected_proxy = ['--diff', metadata['tools']['reference']['realpath'], '-i', meta_case['staged_bin']]
        if result.get('backend') != 'run-emu' or result.get('proxy_command') != expected_proxy:
            reasons.append('external proxy execution receipt does not match the staged case/reference')
        if runner_state == 'terminated' and result.get('external_status') != 'pass':
            reasons.append('external per-case result is not pass')
    if result.get('cwd') != meta_case['workdir']:
        reasons.append('result.json cwd differs from the per-case working directory')
    if result.get('state') not in CASE_STATES:
        reasons.append(f'invalid case state {result.get("state")!r}')

    if result.get('state') == 'aborted':
        doc['status'] = 'aborted'
        doc['reasons'] = reasons or ['case aborted']
        return doc
    if result.get('state') == 'skipped':
        doc['status'] = 'failed'
        doc['reasons'] = reasons + [f'case skipped: {result.get("reason", "")}']
        return doc
    if result.get('timed_out'):
        reasons.append('case timed out')
    if result.get('launch_error'):
        reasons.append(f'launch error: {result["launch_error"]}')
    returncode = result.get('returncode')
    if type(returncode) is not int:
        reasons.append('missing or invalid process exit code')
    elif returncode != 0:
        reasons.append(f'process exit code {returncode}')

    for key in ('stdout', 'stderr'):
        path = Path(meta_case[key])
        if not path.is_file():
            reasons.append(f'{key} log is missing')
    text = common.clean_log('\n'.join(logs.values()))

    trap = audit_trap(text, metadata, reasons)
    doc.update(trap)
    doc['ref_only_trap'] = bool(trap['any_good_trap'] and not trap['core_good_trap']
                                and metadata['mode'] == 'gsim')
    if metadata['mode'] == 'nemu':
        guest = common.guest_instruction_count(text)
        doc['guest_instructions'] = guest
        if guest is None:
            reasons.append('missing NEMU guest instruction count')
        elif guest < 1:
            reasons.append('NEMU guest instruction count is not positive')
        elif guest > metadata['policy']['max_instr']:
            reasons.append('NEMU guest instruction count exceeds --max-instr')
    doc.update(common.host_counters(text))

    errors = common.error_markers(text)
    if errors:
        reasons.append('failure diagnostic(s): ' + '; '.join(errors[:5]))
    try:
        text, doc['wire'] = compact_csv.expand(text, manifest_case)
    except ValueError as exc:
        reasons.append(f'CSV protocol: {exc}')
        doc['status'] = 'timeout' if result.get('timed_out') else 'failed'
        doc['reasons'] = reasons
        return doc
    config_record = audit_config(text, manifest_case, reasons)
    doc['config'] = config_record
    doc['profile'] = manifest_case['expected'].get('profile', 'full')
    doc['vlen_bits'] = config_record.get('vlen_bits') if config_record else None
    audit_complete(text, manifest_case, reasons)
    check = audit_check(text, manifest_case, reasons)
    doc['checks'] = check['checks']
    doc['checks_failed'] = check['failed']

    if manifest_case['suite'] == 'performance':
        metrics = audit_performance(text, manifest_case, reasons)
        doc.update(samples=metrics['sample_count'],
                   calibrations=metrics['calibration_count'],
                   metrics=metrics,
                   sample_records=metrics['samples'],
                   calibration_records=metrics['calibrations'])
        doc['measurement_valid'] = bool(
            metrics['measurement_valid'] and metadata['mode'] == 'gsim')
    else:
        doc.update(audit_accuracy(text, manifest_case, reasons))

    if reasons:
        doc['status'] = 'timeout' if result.get('timed_out') else 'failed'
    else:
        doc['status'] = 'passed'
        doc['functional_pass'] = True
        if (manifest_case['suite'] == 'performance' and
                metadata['mode'] == 'gsim'):
            doc['measurement_valid'] = True
    doc['reasons'] = reasons
    return doc


def _validate_tool(identity_record, kind, issues):
    if not isinstance(identity_record, dict):
        issues.append(f'tools.{kind} is missing')
        return
    for key in ('path', 'realpath', 'sha256', 'size', 'mtime_ns'):
        if key not in identity_record:
            issues.append(f'tools.{kind}.{key} is missing')
    path = identity_record.get('path')
    if not isinstance(path, str):
        return
    current = _current_identity(path)
    if current is None:
        issues.append(f'tools.{kind} is no longer present: {path}')
        return
    for key in ('sha256', 'size', 'realpath'):
        if key in identity_record and identity_record[key] != current[key]:
            issues.append(f'tools.{kind}.{key} changed since the run')


def audit_external_runner(run, metadata):
    """Audit the external transport separately from the actual emulator command."""
    issues = []
    external = metadata.get('external_runner')
    if not isinstance(external, dict):
        return ['external_runner metadata is missing']
    tools = metadata['tools']
    _validate_tool(tools.get('runner'), 'runner', issues)
    if external.get('runner') != tools.get('runner'):
        issues.append('external runner identity differs from tools.runner')
    proxy = external.get('proxy')
    _validate_tool(proxy, 'proxy', issues)
    if not isinstance(proxy, dict) or not isinstance(proxy.get('path'), str):
        return issues + ['external proxy identity is missing']
    if not Path(proxy['path']).resolve().is_relative_to(run.resolve()):
        issues.append('external proxy is outside the run directory')
    if not external.get('hostname'):
        issues.append('external execution hostname is missing')
    for key in ('source', 'common_source'):
        if key in external:
            _validate_tool(external[key], 'external_' + key, issues)
    expected = [tools.get('runner', {}).get('realpath'), '-e', proxy['path'],
                '-r', tools['reference']['realpath'], '-t', str(run / 'staging'),
                '-j', str(metadata['policy']['jobs']), '--timeout', metadata['policy']['timeout'],
                '-o', str(run / 'external-logs'), '--show-cmd']
    if external.get('command') != expected:
        issues.append('external runner command differs from the required transport command')
    state = _json(run / 'runner-state.json') if (run / 'runner-state.json').is_file() else {}
    receipt_path = run / 'external-result.json'
    if state.get('state') == 'terminated':
        if not receipt_path.is_file():
            issues.append('external runner final receipt is missing')
        else:
            receipt = _json(receipt_path)
            if (receipt.get('returncode') != 0 or receipt.get('command') != expected or
                    receipt.get('issues') or receipt.get('aborted') or receipt.get('timed_out') or
                    receipt.get('launch_error')):
                issues.append('external runner receipt failed or command mismatched')
        # run_emu.sh returns zero even for timeout-only batches: audit each list.
        directories = [p for p in (run / 'external-logs').iterdir() if p.is_dir()] if (run / 'external-logs').is_dir() else []
        if len(directories) != 1:
            issues.append('external log directory is missing or ambiguous')
        else:
            logdir = directories[0]
            lists = {}
            for status in ('pass', 'fail', 'timeout'):
                path = logdir / ('.' + status + '_list')
                lists[status] = path.read_text().splitlines() if path.is_file() else []
            names = [c['name'] for c in metadata['cases']]
            if sorted(lists['pass']) != sorted(names) or lists['fail'] or lists['timeout']:
                issues.append('external per-case completion lists do not show every case passed exactly once')
    return issues


def audit_run(run, metadata, manifest):
    issues = []
    tools = metadata.get('tools')
    if not isinstance(tools, dict):
        raise ValueError('run-metadata.json tools must be an object')
    if metadata['mode'] == 'gsim':
        for kind in ('gsim', 'reference', 'dram_config'):
            if kind not in tools:
                issues.append(f'tools.{kind} is missing')
            else:
                _validate_tool(tools[kind], kind, issues)
        if 'dram_config' in tools and isinstance(tools['dram_config'], dict):
            if tools['dram_config'].get('realpath') != str(
                    Path(tools['dram_config'].get('path', '')).resolve()):
                issues.append('DRAM config realpath was not recorded consistently')
    else:
        if 'nemu' not in tools:
            issues.append('tools.nemu is missing')
        else:
            _validate_tool(tools['nemu'], 'nemu', issues)

    policy = metadata.get('policy')
    if not isinstance(policy, dict):
        raise ValueError('run-metadata.json policy must be an object')
    jobs = policy.get('jobs')
    backend = policy.get('backend', 'direct')
    if backend not in ('direct', 'run-emu') or (backend == 'run-emu' and metadata['mode'] != 'gsim'):
        issues.append('policy.backend is invalid for this mode')
    maximum_jobs = 32 if backend == 'run-emu' else (4 if metadata['mode'] == 'gsim' else 64)
    if type(jobs) is not int or jobs < 1 or jobs > maximum_jobs:
        issues.append('policy.jobs is outside the allowed range')
    if backend == 'run-emu':
        issues.extend(audit_external_runner(run, metadata))
    try:
        expected_timeout = common.parse_duration(policy.get('timeout'))
    except ValueError:
        expected_timeout = None
        issues.append('policy.timeout is invalid')
    if (expected_timeout is not None and
            policy.get('timeout_seconds') != expected_timeout):
        issues.append('policy.timeout_seconds does not match policy.timeout')
    if metadata['mode'] == 'gsim':
        if policy.get('seed') != 123456789:
            issues.append(f'policy.seed is {policy.get("seed")}, expected 123456789')
        if not policy.get('per_case_dram_outdir'):
            issues.append('policy.per_case_dram_outdir is not recorded')
        if not policy.get('diff'):
            issues.append('policy.diff is not enabled')
    else:
        max_instr = policy.get('max_instr')
        if type(max_instr) is not int or max_instr < 1:
            issues.append('policy.max_instr is invalid')
    if not policy.get('per_case_cwd'):
        issues.append('policy.per_case_cwd is not recorded')

    selection = metadata.get('selection')
    names = [case['name'] for case in metadata['cases']]
    if not isinstance(selection, dict):
        issues.append('selection metadata is missing')
    else:
        selected = selection.get('selected_cases')
        if not isinstance(selected, list) or len(selected) != len(set(selected)):
            issues.append('selection.selected_cases is invalid')
        elif set(selected) != set(names):
            issues.append('selection.selected_cases does not match metadata cases')
        requested = selection.get('requested')
        if not isinstance(requested, list):
            issues.append('selection.requested is invalid')
        elif requested and set(requested) != set(names):
            issues.append('selection.requested does not match metadata cases')
        elif not requested and set(selected or []) != {case['name'] for case in manifest['cases']}:
            issues.append('a default run must select every manifest case')
        if selection.get('manifest_cases') != len(manifest['cases']):
            issues.append('selection.manifest_cases does not match the manifest snapshot')

    dram_dirs = []
    work_dirs = []
    manifest_by_name = {case['name']: case for case in manifest['cases']}
    for meta_case in metadata['cases']:
        manifest_case = manifest_by_name[meta_case['name']]
        if not common.same_document(manifest_case['entry'],
                                    meta_case['manifest_entry']):
            issues.append(f'{manifest_case["name"]}: metadata manifest_entry differs from snapshot')
        if meta_case['sha256'] != manifest_case['sha256']:
            issues.append(f'{manifest_case["name"]}: metadata sha256 differs from snapshot')
        if meta_case['suite'] != manifest_case['suite']:
            issues.append(f'{manifest_case["name"]}: suite differs from snapshot')
        if meta_case.get('dram_outdir'):
            dram_dirs.append(meta_case['dram_outdir'])
        work_dirs.append(meta_case['workdir'])
    if len(dram_dirs) != len(set(dram_dirs)):
        issues.append('DRAM output directories are not unique per case')
    if len(work_dirs) != len(set(work_dirs)):
        issues.append('per-case working directories are not unique')
    return issues


def load_run(run):
    run = Path(run).expanduser().resolve()
    if not run.is_dir():
        raise ValueError(f'run directory is missing: {run}')
    reports = run / 'reports'
    if reports.is_symlink():
        raise ValueError('run reports directory must not be a symlink')
    metadata_path = run / 'run-metadata.json'
    if not metadata_path.is_file():
        raise ValueError(f'run-metadata.json is missing: {metadata_path}')
    metadata = _json(metadata_path)
    if not isinstance(metadata, dict):
        raise ValueError('run-metadata.json must be an object')
    if metadata.get('schema_version') != common.SCHEMA_VERSION:
        raise ValueError('run metadata schema_version mismatch')
    if metadata.get('suite') != common.SUITE_NAME:
        raise ValueError('run metadata suite mismatch')
    if metadata.get('mode') not in ('nemu', 'gsim'):
        raise ValueError('run metadata mode must be nemu or gsim')
    if Path(metadata.get('run_directory', '')).resolve() != run:
        raise ValueError('run metadata run_directory does not match --run')
    manifest_info = metadata.get('manifest')
    if not isinstance(manifest_info, dict):
        raise ValueError('run metadata manifest is missing')
    snapshot = (run / str(manifest_info.get('snapshot', ''))).resolve()
    if not common.is_within(snapshot, run) or not snapshot.is_file():
        raise ValueError('manifest snapshot is missing or outside the run')
    if common.sha256(snapshot) != manifest_info.get('sha256'):
        raise ValueError('manifest snapshot sha256 does not match metadata')
    manifest = common.load_manifest(snapshot, check_files=False)
    if metadata['mode'] == 'gsim' and not manifest_info.get('snapshot_sha256'):
        raise ValueError('manifest snapshot sha256 was not recorded')

    cases = metadata.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('run metadata cases must be a non-empty list')
    by_name = {case['name']: case for case in manifest['cases']}
    seen = set()
    for meta_case in cases:
        if not isinstance(meta_case, dict):
            raise ValueError('run metadata case must be an object')
        name = meta_case.get('name')
        if not common.case_name(name):
            raise ValueError(f'unsafe case name in run metadata: {name!r}')
        if name in seen:
            raise ValueError(f'duplicate case in run metadata: {name}')
        seen.add(name)
        if name not in by_name:
            raise ValueError(f'case {name} is not in the manifest snapshot')
        snapshot_case = by_name[name]
        if meta_case.get('suite') != snapshot_case['suite']:
            raise ValueError(f'{name}: suite differs from the manifest snapshot')
        if meta_case.get('sha256') != snapshot_case['sha256']:
            raise ValueError(f'{name}: sha256 differs from the manifest snapshot')
        if not isinstance(meta_case.get('command'), list):
            raise ValueError(f'{name}: command must be a list')
        staged = Path(meta_case.get('staged_bin', '')).resolve()
        if not common.is_within(staged, run / 'staging'):
            raise ValueError(f'{name}: staged BIN is outside run/staging')
        case_root = (run / 'cases' / name).resolve()
        work = Path(meta_case.get('workdir', '')).resolve()
        if work.parent != case_root or work.name != 'work':
            raise ValueError(f'{name}: working directory is not run/cases/{name}/work')
        for key in ('stdout', 'stderr', 'result'):
            path = Path(meta_case.get(key, '')).resolve()
            if path.parent != case_root:
                raise ValueError(f'{name}: {key} is not directly under the case directory')
        if metadata['mode'] == 'gsim':
            dram = Path(meta_case.get('dram_outdir', '')).resolve()
            if dram.parent != case_root or dram.name != 'dram':
                raise ValueError(f'{name}: DRAM outdir is not run/cases/{name}/dram')
    return run, metadata, manifest


def read_runner_state(run):
    path = Path(run) / 'runner-state.json'
    if not path.is_file():
        return {'state': 'aborted', 'missing': True, 'returncode': None,
                'error': 'missing runner state; completion cannot be established'}
    state = _json(path)
    if not isinstance(state, dict) or state.get('state') not in STATE_VALUES:
        raise ValueError('runner-state.json has an invalid state')
    if state['state']=='in_progress' and isinstance(state.get('launcher_pid'), int):
        try:
            os.kill(state['launcher_pid'], 0)
        except ProcessLookupError:
            return {**state, 'state': 'aborted', 'returncode': None,
                    'error': 'launcher no longer exists and no final state was recorded'}
        except PermissionError:
            pass  # Existing process, but not owned by this collector user.
    return state


def collect(run):
    run, metadata, manifest = load_run(run)
    state_record = read_runner_state(run)
    runner_state = state_record['state']
    final = runner_state != 'in_progress'
    issues = audit_run(run, metadata, manifest)
    if state_record.get('missing'):
        issues.append('runner-state.json is missing; treating the run as aborted')
    if state_record.get('error'):
        issues.append(str(state_record['error']))
    if final:
        if type(state_record.get('returncode')) is not int:
            issues.append('runner-state.json has no integer returncode')
        elif state_record['returncode'] != 0:
            issues.append(f'runner exited {state_record["returncode"]}')

    by_name = {case['name']: case for case in manifest['cases']}
    cases = []
    for meta_case in metadata['cases']:
        cases.append(audit_case(by_name[meta_case['name']], meta_case,
                                metadata, run, runner_state))

    status_counts = {}
    for case in cases:
        status_counts[case['status']] = status_counts.get(case['status'], 0) + 1
    suite_counts = {}
    for case in cases:
        suite = suite_counts.setdefault(
            case['suite'], {'selected': 0, 'passed': 0, 'failed': 0})
        suite['selected'] += 1
        if case['status'] == 'passed':
            suite['passed'] += 1
        elif case['status'] in ('failed', 'timeout', 'aborted'):
            suite['failed'] += 1
    # 回收数据与严格接受的数据分离；源行可解析并不代表运行通过。
    for case in cases:
        recovered = case.get('recovery', {}).get('records', [])
        case['recovered_samples'] = sum(row['record_type'] == 'sample' for row in recovered)
        case['recovered_calibrations'] = sum(row['record_type'] == 'calibration' for row in recovered)
    samples = sum(case.get('samples', 0) for case in cases)
    calibrations = sum(case.get('calibrations', 0) for case in cases)
    total_checks = sum(case['checks'] for case in cases
                       if isinstance(case.get('checks'), int))
    all_passed = (final and runner_state == 'terminated' and
                  not issues and status_counts.get('passed', 0) == len(cases) and
                  state_record.get('returncode') == 0)
    if runner_state == 'in_progress':
        state = 'in_progress'
    elif runner_state == 'aborted':
        state = 'aborted'
    else:
        state = 'passed' if all_passed else 'failed'

    dram = metadata['tools'].get('dram_config') if metadata['mode'] == 'gsim' else None
    dram_report = {'recorded': dram, 'current': None, 'verified': None}
    if isinstance(dram, dict):
        current = _current_identity(dram.get('path', ''))
        dram_report['current'] = current
        dram_report['verified'] = bool(
            current and current['sha256'] == dram.get('sha256') and
            current['realpath'] == dram.get('realpath'))

    document = {
        'schema_version': common.SCHEMA_VERSION,
        'suite': common.SUITE_NAME,
        'generated_at': common.utc_now(),
        'collector_sources': {p.name: common.identity(p) for p in
                              (Path(__file__).resolve(), Path(common.__file__).resolve(), Path(compact_csv.__file__).resolve())},
        'run': str(run),
        'mode': metadata['mode'],
        'runner_state': runner_state,
        'runner_state_record': state_record,
        'complete': final,
        'state': state,
        'all_passed': state == 'passed',
        'audit_issues': list(dict.fromkeys(issues)),
        'summary': {
            'selected_cases': len(cases),
            'manifest_cases': len(manifest['cases']),
            'passed': status_counts.get('passed', 0),
            'failed': status_counts.get('failed', 0),
            'timeout': status_counts.get('timeout', 0),
            'aborted': status_counts.get('aborted', 0),
            'running': status_counts.get('running', 0),
            'pending': status_counts.get('pending', 0),
            'suites': suite_counts,
            'performance_samples': samples,
            'calibrations': calibrations,
            'recovered_samples': sum(case['recovered_samples'] for case in cases),
            'recovered_calibrations': sum(case['recovered_calibrations'] for case in cases),
            'checks': total_checks,
        },
        'manifest': metadata['manifest'],
        'tools': metadata['tools'],
        'dram_config': dram_report,
        'cases': cases,
    }
    save_reports(run, document)
    return document


def _report_path(reports, name):
    path = reports / name
    if path.is_symlink():
        raise ValueError(f'report path must not be a symlink: {path}')
    return path


def _decimal(value):
    """Render a derived (non-raw) cycle count without float artefacts."""
    return None if value is None else f'{value:.3f}'


def _write_csv(path, fieldnames, rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction='ignore',
                            lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({key: '' if row.get(key) is None else
                         str(row[key]) if isinstance(row[key], int) else row[key]
                         for key in fieldnames})
    common.write_text(path, stream.getvalue())


RECOVERED_FIELDS = [
    'record_type', 'case', 'suite', 'function', 'mode', 'n', 'k', 'phase',
    'round', 'order', 'r', 'cycle_begin', 'cycle_end', 'cycles', 'timing_repeat',
    'checksum',
    'wire_format', 'row_status', 'measurement_valid', 'run_status', 'case_status',
    'source', 'source_line', 'raw']


def recovery_report(document):
    """诊断回收始终独立导出，不作为analyze可接受的性能报告。"""
    rows = []
    cases = []
    for case in document['cases']:
        recovered = case.get('recovery', {})
        for row in recovered.get('records', []):
            rows.append({**row, 'raw': row['_raw'],
                         'run_status': document['state'], 'case_status': case['status']})
        cases.append({'name': case['name'], 'status': case['status'],
                      'reasons': case['reasons'],
                      **{k: v for k, v in recovered.items() if k != 'records'}})
    return {'schema_version': 1, 'kind': 'diagnostic-recovery',
            'run': document['run'], 'run_status': document['state'],
            'measurement_valid': False,
            'note': '行解析成功不表示运行通过；诊断数据不进入有效性能汇总。',
            'logical_records': len(rows), 'records': rows, 'cases': cases}


def save_reports(run, document):
    reports = Path(run) / 'reports'
    if reports.is_symlink():
        raise ValueError('reports directory must not be a symlink')
    reports.mkdir(exist_ok=True)
    if not reports.is_dir():
        raise ValueError('reports path is not a directory')

    result_fields = [
        'name', 'suite', 'status', 'functional_pass', 'checks',
        'checks_failed', 'samples', 'calibrations', 'measurement_valid',
        'recovered_samples', 'recovered_calibrations',
        'core_good_trap', 'any_good_trap', 'ref_only_trap',
        'guest_instructions', 'returncode', 'timed_out', 'aborted',
        'launch_error', 'staged_sha256', 'manifest_sha256', 'result_state',
        'stdout', 'stderr', 'result', 'dram_outdir', 'reasons']
    rows = []
    for case in document['cases']:
        row = dict(case)
        row['reasons'] = '; '.join(case.get('reasons', []))
        rows.append(row)
    _write_csv(_report_path(reports, 'results.csv'), result_fields, rows)

    sample_fields = ['record_type', 'case', 'suite', 'function', 'mode', 'n',
                     'k', 'phase', 'round', 'order', 'r', 'cycle_begin',
                     'cycle_end', 'cycles', 'timing_repeat', 'cycles_per_round',
                     'checksum', 'wire_format', 'raw',
                     'run_status', 'case_status', 'measurement_valid']
    sample_rows = []
    for case in document['cases']:
        wire = case.get('wire', {})
        if wire.get('wire_format') in ('csv2', 'csv3') and wire.get('csv_text'):
            common.write_text(_report_path(reports, case['name'] + '.csv'), wire['csv_text'])
        for key, kind in (('sample_records', 'sample'),
                          ('calibration_records', 'calibration')):
            for row in case.get(key, []):
                sample_rows.append({
                    'record_type': kind, 'case': case['name'],
                    'suite': case['suite'], 'function': row.get('function'),
                    'mode': row.get('mode'), 'n': row.get('n'),
                    'k': row.get('k'), 'phase': row.get('phase'),
                    'round': row.get('round'), 'order': row.get('order'),
                    'r': row.get('r'), 'cycle_begin': row.get('cycle_begin'),
                    'cycle_end': row.get('cycle_end'),
                    'cycles': row.get('cycles'),
                    'timing_repeat': row.get('timing_repeat', 1),
                    'cycles_per_round': _decimal(
                        row.get('cycles_per_round')),
                    'checksum': row.get('checksum'),
                    'wire_format': row.get('wire_format', 'kv1'), 'raw': row.get('_raw'),
                    'run_status': document['state'], 'case_status': case['status'],
                    'measurement_valid': bool(document['state'] == 'passed' and
                                              case['measurement_valid']),
                })
    _write_csv(_report_path(reports, 'samples.csv'), sample_fields, sample_rows)
    recovered = recovery_report(document)
    _write_csv(_report_path(reports, 'recovered-samples.csv'), RECOVERED_FIELDS,
               recovered['records'])
    common.write_json(_report_path(reports, 'recovery.json'), recovered)
    common.write_json(_report_path(reports, 'dram-config.json'),
                      document['dram_config'])
    common.write_json(_report_path(reports, 'results.json'), document)
    common.write_text(_report_path(reports, 'summary.md'), summary_markdown(document))


def _fmt(value):
    return '' if value is None else str(value)


def summary_markdown(document):
    summary = document['summary']
    lines = [
        '# libmvec 香山运行报告',
        '',
        f"- 运行目录：`{document['run']}`",
        f"- 模式：`{document['mode']}`",
        f"- 运行器状态：**{document['runner_state']}**；收集器状态：**{document['state']}**",
        f"- 选中 {summary['selected_cases']} 项；manifest 共 {summary['manifest_cases']} 项",
        f"- 通过 {summary['passed']}；失败 {summary['failed']}；超时 {summary['timeout']}；中止 {summary['aborted']}；运行中 {summary['running']}；等待 {summary['pending']}",
        f"- 数学样本：{summary['performance_samples']}；夹具控制：{summary['calibrations']}",
        f"- 独立行级回收（未验收）：数学 {summary.get('recovered_samples', 0)}；控制 {summary.get('recovered_calibrations', 0)}",
        '- recovered-samples.csv / recovery.json 保留完整D行，即使缺少pass、超时、中止或仍运行；不作为有效性能汇总。',
        '',
    ]
    if not document['complete']:
        lines += ['> 仅为进度报告。运行器尚未结束；收集器退出0不表示全部测试通过。', '']
    elif document['state'] != 'passed':
        lines += ['> 最终审计未通过。不能将进程rc0或仅refmodel的GOOD TRAP当作成功。', '']
    lines += ['## 工具与 DRAM 配置', '']
    for kind in ('gsim', 'nemu', 'reference'):
        identity = document['tools'].get(kind)
        if isinstance(identity, dict):
            lines.append(f"- {kind}: `{identity.get('path')}` -> `{identity.get('realpath')}`")
            lines.append(f"  sha256 `{identity.get('sha256')}`")
    dram = document['dram_config']
    if isinstance(dram.get('recorded'), dict):
        recorded = dram['recorded']
        lines += [f"- DRAM 配置：`{recorded.get('path')}` -> `{recorded.get('realpath')}`",
                  f"  sha256 `{recorded.get('sha256')}`；当前身份核验：{dram.get('verified')}"]
    lines += ['', '## 用例', '',
              '| 用例 | 套件 | 状态 | 检查数 | 样本数 | 控制数 | RTL trap | rc | 原因 |',
              '|---|---|---:|---:|---:|---:|---|---:|---|']
    for case in document['cases']:
        lines.append(
            f"| {case['name']} | {case['suite']} | {case['status']} | "
            f"{_fmt(case.get('checks'))} | {case.get('samples', 0)} | "
            f"{case.get('calibrations', 0)} | {case.get('core_good_trap')} | "
            f"{_fmt(case.get('returncode'))} | "
            f"{'; '.join(case.get('reasons', []))[:180]} |")
    if document['audit_issues']:
        lines += ['', '## 审计问题', '']
        lines += [f'- {issue}' for issue in document['audit_issues']]
    failures = [case for case in document['cases'] if case['status'] != 'passed']
    if failures:
        lines += ['', '## 未通过项目', '']
        for case in failures:
            lines.append(f"- `{case['name']}` ({case['status']}): "
                         f"{'; '.join(case.get('reasons', [])) or 'no reason recorded'}")
    lines += ['', '## 证据说明', '',
              '- gsim 周期来自目标程序在香山 RTL 模型中读取的 rdcycle，不是宿主耗时。',
              '- NEMU 仅用于功能验证，其周期值不是性能证据。',
              '- JSON/CSV 原始记录以完整十进制整数保留 cycle_begin/cycle_end/cycles。',
              '- 暂存 BIN 的 SHA256 同时对照运行元信息与 manifest 快照核验。',
              '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--require-complete', action='store_true',
                        help='exit 0 only when the run terminated and all selected cases passed')
    args = parser.parse_args(argv)
    try:
        document = collect(args.run)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f'collector error: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({
        'state': document['state'], 'runner_state': document['runner_state'],
        'complete': document['complete'], 'all_passed': document['all_passed'],
        'summary': document['summary'], 'audit_issues': document['audit_issues'],
    }, indent=2))
    if args.require_complete:
        return 0 if document['state'] == 'passed' else 1
    return 0 if document['state'] in ('in_progress', 'passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
