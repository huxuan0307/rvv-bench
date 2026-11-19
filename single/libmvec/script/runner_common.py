#!/usr/bin/env python3
"""Shared strict helpers for the libmvec XiangShan runners and collector.

This module intentionally contains no simulator invocation at import time.  The
runner (``run.py``) and the auditor (``collect.py``) both use it so that manifest
validation, tool identity, staging checks, guest-record parsing and process
reclamation follow one contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
SUITE_NAME = 'libmvec-xs'
MODES = ('scalar_strict', 'scalar_fast', 'auto_m1', 'auto_m2',
         'direct_m1', 'direct_m2')
# Optional LMUL4/8 extension of the same ordered matrix.  It is appended only
# when the built archive exports those kernels; both prefixes remain valid
# manifests so previously measured six-mode runs stay auditable.
MODES_LMUL48 = MODES + ('direct_m4', 'direct_m8')
# Full ten-column matrix: the compiler-generated LMUL4/8 columns follow the
# direct ones, so MODES_LMUL48 stays an exact prefix of this tuple.
MODES_AUTO_LMUL48 = MODES_LMUL48 + ('auto_m4', 'auto_m8')
N16_TRIM_MODES = ('scalar_fast', 'direct_m1', 'direct_m2')
CALIBRATION_MODES = ('rdcycle', 'empty', 'copy')
ORDER_POLICY = 'rotate-all-9'
LENGTHS = (16, 128, 1024)
BASE_BATCHES = (1, 4)
WIDE_BATCH_FUNCTIONS = ('log', 'exp')
PHASES = {'first': 1, 'warmup': 2, 'steady': 7}
PLAN_PHASE_ORDER = ('first', 'warmup', 'steady')
ROUND_BASE = {'first': 0, 'warmup': 1, 'steady': 3}
PERF_FUNCTIONS = ('log', 'exp', 'sin', 'cos', 'pow', 'logf', 'expf',
                  'sinf', 'cosf', 'powf', 'log10', 'atan', 'atan2', 'acos')
BINARY_FUNCTIONS = ('pow', 'powf', 'atan2')
U64_MAX = (1 << 64) - 1
# 计时窗口质量标准（见 throughput/README.md）。
WINDOW_MIN = 20_000
MAX_TIMING_REPEAT = 64
MIN_STEADY = 5
CV_MAX = 0.01
REFERENCE_REQUIRED_SYMBOLS = (
    'difftest_init', 'difftest_memcpy', 'difftest_regcpy', 'difftest_exec')
NM_TOOL = None  # override for host tests; production uses $NM or PATH

CASE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*\Z')
SHA256_HEX = re.compile(r'[0-9a-fA-F]{64}\Z')
DECIMAL = re.compile(r'(?:0|[1-9][0-9]*)\Z')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
RECORD = re.compile(r'\b(LIBMVEC_[A-Z_]+)\b([^\r\n]*)')
KEY_VALUE = re.compile(r'\b([A-Za-z_][A-Za-z_0-9]*)=([^\s]+)')
GOOD_TRAP = re.compile(r'\bHIT\s+GOOD\s+TRAP\b', re.I)
CORE_GOOD_TRAP = re.compile(r'\bCore\s*[-#]?\s*0\s*:\s*HIT\s+GOOD\s+TRAP\b', re.I)
GUEST_COUNT = re.compile(r'total\s+guest\s+instructions\s*=\s*([\d,]+)', re.I)
ERROR_PATTERNS = (
    re.compile(r'HIT\s+BAD\s+TRAP', re.I),
    re.compile(r'HIT\s+CRITICAL\s+ERROR', re.I),
    re.compile(r'(?-i:\bERROR\b)'),
    re.compile(r'LIBMVEC_ERROR\b'),
    re.compile(r'\bFAIL(?:ED|URE)?\b', re.I),
    re.compile(r'\bABORT(?:ED)?\b', re.I),
    re.compile(r'(?m)^[ \t]*(?:\[[^\]\n]*\][ \t]*)*error\b(?:[ \t]*:|[ \t]+)', re.I),
    re.compile(r'\bASSERT(?:ION)?\b[^\n]*\bfail', re.I),
    re.compile(r'\bDIFFTEST\s+(?:FAIL|ERROR)\b', re.I),
    re.compile(r'(?im)^[ \t]*(?:DIFF(?:TEST)?|REF(?:ERENCE)?)\b[^\n]{0,100}'
               r'(?:fail|error|mismatch)'),
    re.compile(r'\bMISMATCH\b'),
    re.compile(r'Segmentation fault', re.I),
    re.compile(r'AddressSanitizer'),
)
TIMEOUT_PATTERN = re.compile(
    # RTL PERF identifiers such as coh_timeout/reset_timeout are counters,
    # not host timeout diagnostics (including nonzero reset_timeout counts).
    r'\b(?:timed?\s+out|timeout(?:\s+(?:expired|reached))?)\b', re.I)
LIMIT_PATTERN = re.compile(
    r'(?:reached|exceeded|hit)[^\n]{0,80}(?:instruction|instr)[^\n]{0,40}'
    r'(?:limit|budget)|'
    r'(?:instruction|instr)[^\n]{0,40}(?:limit|budget)[^\n]{0,80}'
    r'(?:reached|exceeded)|'
    r'max(?:imum)?(?:[ _-]+number[ _-]+of)?[ _-]+instr(?:uctions)?'
    r'[^\n]{0,40}(?:reached|exceeded)', re.I)


class ManifestError(ValueError):
    """A manifest did not satisfy the strict libmvec contract."""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def identity(path):
    path = Path(path).expanduser()
    stat = path.stat()
    return {'path': str(path.absolute()), 'realpath': str(path.resolve()),
            'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'sha256': sha256(path)}


def canonical_json(value):
    """Stable JSON bytes/text for structural equality checks."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False)


def same_document(left, right):
    return canonical_json(left) == canonical_json(right)


def elf_info(path):
    """Return minimal ELF facts, or ``None`` when the file is not an ELF."""
    try:
        with Path(path).open('rb') as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:4] != b'\x7fELF':
                return None
            bits = 64 if header[4] == 2 else 32 if header[4] == 1 else None
            endian = 'little' if header[5] == 1 else 'big' if header[5] == 2 else None
            if bits != 64 or endian != 'little':
                return {'elf': True, 'bits': bits, 'endian': endian,
                        'type': None, 'machine': None, 'interp': False}
            e_type = int.from_bytes(header[16:18], 'little')
            e_machine = int.from_bytes(header[18:20], 'little')
            phoff = int.from_bytes(header[32:40], 'little')
            phentsize = int.from_bytes(header[54:56], 'little')
            phnum = int.from_bytes(header[56:58], 'little')
            interp = False
            if 0 < phnum < 4096 and phentsize >= 56:
                stream.seek(phoff)
                for _ in range(phnum):
                    entry = stream.read(phentsize)
                    if len(entry) < 4:
                        break
                    if int.from_bytes(entry[0:4], 'little') == 3:  # PT_INTERP
                        interp = True
            return {'elf': True, 'bits': 64, 'endian': 'little', 'type': e_type,
                    'machine': e_machine, 'interp': interp}
    except OSError:
        return None


def dynamic_executable_info(path):
    path = Path(path)
    info = elf_info(path)
    if not info or not info.get('elf'):
        raise ManifestError(f'not an ELF executable: {path}')
    if info.get('bits') != 64 or info.get('endian') != 'little':
        raise ManifestError(f'not a 64-bit little-endian ELF: {path}')
    if info.get('type') not in (2, 3):  # ET_EXEC / ET_DYN
        raise ManifestError(f'ELF is not an executable: {path}')
    if not info.get('interp'):
        raise ManifestError(f'ELF is not dynamically linked: {path}')
    return info


def shared_object_info(path):
    path = Path(path)
    info = elf_info(path)
    if not info or not info.get('elf'):
        raise ManifestError(f'reference is not an ELF shared object: {path}')
    if info.get('bits') != 64 or info.get('endian') != 'little':
        raise ManifestError(f'reference is not 64-bit little-endian: {path}')
    if info.get('type') != 3:  # ET_DYN
        raise ManifestError(f'reference is not an ELF shared object: {path}')
    return info


def _nm_tool():
    candidate = NM_TOOL or os.environ.get('NM') or shutil.which('nm')
    if not candidate:
        raise ManifestError('nm is required to validate reference difftest symbols')
    path = Path(candidate)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ManifestError(f'nm is not executable: {path}')
    return path


def reference_symbols(path, *, nm=None):
    """Return defined dynamic symbols exported by a difftest reference.

    The reference must really be a NEMU difftest shared object, not merely any
    ET_DYN file.  ``nm -D --defined-only`` is used because these are the symbols
    the XiangShan model resolves at runtime.
    """
    nm_path = Path(nm) if nm is not None else _nm_tool()
    try:
        completed = subprocess.run(
            [str(nm_path), '-D', '--defined-only', str(path)],
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ManifestError(f'cannot run nm on reference {path}: {exc}') from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or '').strip()
        raise ManifestError(
            f'nm -D failed for reference {path} (rc {completed.returncode}): {detail}')
    symbols = set()
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 3:
            symbols.add(fields[-1])
    return symbols


def validate_reference(path):
    symbols = reference_symbols(path)
    missing = [name for name in REFERENCE_REQUIRED_SYMBOLS if name not in symbols]
    if missing:
        raise ManifestError(
            f'reference is missing required difftest symbols: {", ".join(missing)}')
    return {'required': list(REFERENCE_REQUIRED_SYMBOLS),
            'found': [name for name in REFERENCE_REQUIRED_SYMBOLS if name in symbols],
            'nm': str(_nm_tool()), 'nm_sha256': sha256(_nm_tool())}


def tool_identity(path, *, kind):
    path = Path(path).expanduser()
    if not path.is_file():
        raise ManifestError(f'{kind} is missing: {path}')
    if kind in ('gsim', 'nemu') and not os.access(path, os.X_OK):
        raise ManifestError(f'{kind} is not executable: {path}')
    if kind == 'gsim':
        elf = dynamic_executable_info(path)
    elif kind == 'nemu':
        elf = elf_info(path)
        if not elf or elf.get('type') not in (2, 3):
            raise ManifestError(f'NEMU is not an ELF executable: {path}')
    elif kind == 'reference':
        elf = shared_object_info(path)
        difftest = validate_reference(path)
    else:
        elf = elf_info(path)
        difftest = None
    result = identity(path)
    result['kind'] = kind
    result['elf'] = elf
    if kind == 'reference':
        result['difftest'] = difftest
    return result


DURATION = re.compile(r'([0-9]+(?:\.[0-9]+)?)([smhd]?)\Z', re.I)
DURATION_FACTORS = {'': 1.0, 's': 1.0, 'm': 60.0, 'h': 3600.0, 'd': 86400.0}


def parse_duration(text):
    if not isinstance(text, str):
        raise ValueError(f'invalid duration: {text!r}')
    match = DURATION.fullmatch(text.strip())
    if not match:
        raise ValueError(f'invalid duration: {text!r}')
    seconds = float(match.group(1)) * DURATION_FACTORS[match.group(2).lower()]
    if seconds <= 0:
        raise ValueError(f'duration must be positive: {text!r}')
    return seconds


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, document):
    """Atomically replace a JSON file and fsync it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(document, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def clean_log(text):
    """Strip terminal controls and copied command headers, not diagnostics."""
    clean = ANSI.sub('', text).replace('\r', '').replace('\x00', '')
    return '\n'.join(line for line in clean.splitlines()
                     if not line.lstrip().startswith('cmd:'))


def records(text, kind):
    """Parse one guest record kind into dicts, preserving the raw line."""
    result = []
    for match in RECORD.finditer(text):
        if match.group(1) != kind:
            continue
        pairs = KEY_VALUE.findall(match.group(2))
        if len({key for key,value in pairs}) != len(pairs):
            raise ValueError('duplicate field in guest record: '+match.group(0))
        row = dict(pairs)
        row['_raw'] = match.group(0)
        result.append(row)
    return result


def marker_count(text, marker):
    return text.count(marker)


def error_markers(text):
    """Return explicit failure diagnostics present in guest/simulator output."""
    scanned = re.sub(r'\bfailed=0(?=$|[\s,;\]}])', '', text, flags=re.I)
    errors = []
    for pattern in ERROR_PATTERNS:
        for match in pattern.finditer(scanned):
            errors.append(match.group(0).strip())
    if TIMEOUT_PATTERN.search(scanned):
        errors.append('timeout diagnostic')
    if LIMIT_PATTERN.search(scanned):
        errors.append('instruction limit diagnostic')
    return list(dict.fromkeys(errors))


def guest_instruction_count(text):
    counts = [int(value.replace(',', '')) for value in GUEST_COUNT.findall(text)]
    return counts[-1] if counts else None


def host_counters(text):
    """Parse explicitly labelled gsim model-core counters only."""
    result = {}
    pattern = re.compile(
        r'Core-(\d+)\s+instrCnt\s*=\s*([0-9,]+),\s*cycleCnt\s*=\s*([0-9,]+),'
        r'\s*IPC\s*=\s*([0-9.]+)')
    cores = []
    for match in pattern.finditer(text):
        cores.append({'core': int(match.group(1)),
                      'retired_instructions': int(match.group(2).replace(',', '')),
                      'simulation_cycles': int(match.group(3).replace(',', '')),
                      'ipc': float(match.group(4)), 'evidence': match.group(0)})
    if cores:
        result['gsim_cores'] = cores
    return result


def is_within(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _int(value, what, *, minimum=0):
    if type(value) is not int:
        raise ManifestError(f'{what} must be an integer, got {value!r}')
    if value < minimum:
        raise ManifestError(f'{what} must be >= {minimum}, got {value}')
    return value


def _string(value, what):
    if not isinstance(value, str) or not value:
        raise ManifestError(f'{what} must be a non-empty string')
    return value


def _resolve_bin(value, base, what):
    _string(value, what)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _validate_markers(entry, name):
    markers = entry.get('required_markers')
    wire = entry.get('expected', {}).get('wire_format', 'kv1')
    if (wire not in ('kv1', 'csv2', 'csv3') or
            (wire in ('csv2', 'csv3') and entry.get('suite') != 'performance')):
        raise ManifestError(f'{name}: unsupported wire format {wire!r}')
    expected = ([f'E,{name},pass'] if wire in ('csv2', 'csv3')
                else [f'LIBMVEC_COMPLETE case={name} status=pass'])
    if not isinstance(markers, list) or markers != expected:
        raise ManifestError(
            f'{name}: required_markers must be exactly {expected!r}')
    return list(markers)


def _timing_repeat(expected, wire, name):
    """Validate the measurement repeat; csv2 predates the field and means R=1."""
    value = expected.get('timing_repeat', 1)
    if type(value) is not int or not 1 <= value <= MAX_TIMING_REPEAT:
        raise ManifestError(f'{name}: expected.timing_repeat must be an integer in [1,{MAX_TIMING_REPEAT}]')
    if wire in ('kv1', 'csv2') and value != 1:
        raise ManifestError(f'{name}: {wire} requires timing_repeat=1')
    if wire == 'csv3' and 'timing_repeat' not in expected:
        raise ManifestError(f'{name}: csv3 requires expected.timing_repeat')
    return value


def expected_plan(entry, suite, name):
    """Validate and normalize the ``expected`` block of one manifest case."""
    expected = entry.get('expected')
    if not isinstance(expected, dict):
        raise ManifestError(f'{name}: expected must be an object')
    if suite == 'performance':
        function = expected.get('function')
        if function not in PERF_FUNCTIONS:
            raise ManifestError(
                f'{name}: expected.function must be one of {PERF_FUNCTIONS}')
        if name != 'perf-' + function:
            raise ManifestError(
                f'{name}: performance case name must be perf-{function}')
        profile = expected.get('profile', 'full')
        if profile not in ('full', 'format-smoke', 'n16', 'n16-trim', 'grid'):
            raise ManifestError(f'{name}: unknown profile {profile!r}')
        modes = expected.get('modes')
        if profile == 'n16-trim':
            wanted_modes = N16_TRIM_MODES
        elif isinstance(modes, list) and tuple(modes) in (MODES, MODES_LMUL48,
                                                          MODES_AUTO_LMUL48):
            wanted_modes = tuple(modes)
        else:
            raise ManifestError(
                f'{name}: expected.modes must be one of '
                f'{list(MODES)!r}, {list(MODES_LMUL48)!r} or '
                f'{list(MODES_AUTO_LMUL48)!r}')
        wanted_controls = () if profile == 'n16-trim' else CALIBRATION_MODES
        wanted_policy = 'rotate-all-' + str(len(wanted_modes) + len(wanted_controls))
        if not isinstance(modes, list) or modes != list(wanted_modes):
            raise ManifestError(f'{name}: expected.modes must be {list(wanted_modes)!r}')
        if profile == 'format-smoke' and function != 'log':
            raise ManifestError('format-smoke is restricted to log')
        lengths = expected.get('lengths')
        batches = expected.get('batches')
        if profile == 'grid':
            for field, values, maximum in (('lengths', lengths, 1024), ('batches', batches, 1000000)):
                if (not isinstance(values, list) or not 1 <= len(values) <= 3 or
                    any(type(v) is not int or not 1 <= v <= maximum for v in values) or
                    len(set(values)) != len(values)):
                    raise ManifestError(f'{name}: invalid grid {field}')
        else:
            wanted_lengths = [16] if profile in ('format-smoke', 'n16', 'n16-trim') else list(LENGTHS)
            if not isinstance(lengths, list) or list(lengths) != wanted_lengths:
                raise ManifestError(f'{name}: expected.lengths must be {wanted_lengths!r}')
            wanted_batches = ([1] if profile == 'format-smoke' else
                              [1, 4, 16] if function in WIDE_BATCH_FUNCTIONS else [1, 4])
            if not isinstance(batches, list) or list(batches) != wanted_batches:
                raise ManifestError(f'{name}: expected.batches must be {wanted_batches!r}')
        phases = expected.get('phases')
        if phases != PHASES or any(type(value) is not int for value in phases.values()):
            raise ManifestError(f'{name}: expected.phases must be {PHASES!r}')
        policy = expected.get('order_policy')
        if policy != wanted_policy:
            raise ManifestError(
                f'{name}: expected.order_policy must be {wanted_policy!r}')
        calibration_modes = expected.get('calibration_modes')
        if not isinstance(calibration_modes, list) or calibration_modes != list(wanted_controls):
            raise ManifestError(
                f'{name}: expected.calibration_modes must be '
                f'{list(wanted_controls)!r}')
        rounds = sum(PHASES.values())
        sample_count = _int(expected.get('sample_count'),
                            f'{name}: expected.sample_count', minimum=1)
        derived = len(wanted_modes) * len(lengths) * len(batches) * rounds
        if sample_count != derived:
            raise ManifestError(
                f'{name}: expected.sample_count {sample_count} != derived {derived}')
        calibration_count = len(wanted_controls) * len(lengths) * len(batches) * rounds
        if 'calibration_count' in expected and expected['calibration_count'] != calibration_count:
            raise ManifestError(
                f'{name}: expected.calibration_count must be {calibration_count}')
        min_checks = _int(expected.get('min_checks'),
                          f'{name}: expected.min_checks', minimum=0)
        abi_symbols = expected.get('abi_symbols')
        if abi_symbols not in (None, []):
            raise ManifestError(
                f'{name}: performance expected.abi_symbols must be absent or empty')
        wire = expected.get('wire_format', 'kv1')
        if wire not in ('kv1', 'csv2', 'csv3'):
            raise ManifestError(f'{name}: unsupported wire format {wire!r}')
        timing_repeat = _timing_repeat(expected, wire, name)
        return {'wire_format': wire, 'profile': profile, 'function': function, 'modes': list(wanted_modes),
                'calibration_modes': list(wanted_controls),
                'lengths': list(lengths), 'batches': list(batches),
                'phases': dict(PHASES), 'order_policy': wanted_policy,
                'rounds': rounds, 'sample_count': sample_count,
                'calibration_count': calibration_count,
                'timing_repeat': timing_repeat,
                'min_checks': min_checks, 'abi_symbols': []}
    # accuracy
    min_checks = _int(expected.get('min_checks'),
                      f'{name}: expected.min_checks', minimum=0)
    abi_symbols = expected.get('abi_symbols', [])
    if not isinstance(abi_symbols, list):
        raise ManifestError(f'{name}: expected.abi_symbols must be a list')
    for symbol in abi_symbols:
        if (not isinstance(symbol, str) or not symbol or
                any(char.isspace() or char == '=' for char in symbol)):
            raise ManifestError(f'{name}: invalid ABI symbol {symbol!r}')
    if len(set(abi_symbols)) != len(abi_symbols):
        raise ManifestError(f'{name}: duplicate expected.abi_symbols')
    return {'function': None, 'min_checks': min_checks,
            'abi_symbols': list(abi_symbols)}


def load_manifest(path, *, check_files=True):
    """Strictly parse a libmvec manifest.

    Returns ``{'path', 'sha256', 'document', 'cases'}``.  Every case path is
    resolved against the manifest directory; hashes are verified unless
    ``check_files`` is false (used for the immutable snapshot in a run).
    """
    path = Path(path).expanduser()
    if not path.is_file():
        raise ManifestError(f'manifest is missing: {path}')
    manifest_path = path.resolve()
    try:
        document = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        raise ManifestError(f'manifest is not valid JSON: {exc}') from exc
    if not isinstance(document, dict):
        raise ManifestError('manifest must be a JSON object')
    if document.get('schema_version') != SCHEMA_VERSION:
        raise ManifestError(f'manifest schema_version must be {SCHEMA_VERSION}')
    if document.get('suite') != SUITE_NAME:
        raise ManifestError(f'manifest suite must be {SUITE_NAME!r}')
    if document.get('build_status') != 'built':
        raise ManifestError('manifest build_status must be "built"')
    _string(document.get('created_at'), 'manifest created_at')
    if not isinstance(document.get('toolchain'), dict) or not document['toolchain']:
        raise ManifestError('manifest toolchain must be a non-empty object')
    sources = document.get('sources')
    if not isinstance(sources, (list, dict)) or not sources:
        raise ManifestError('manifest sources must be a non-empty list or object')
    entries = document.get('cases')
    if not isinstance(entries, list) or not entries:
        raise ManifestError('manifest cases must be a non-empty list')

    cases = []
    names = set()
    for index, entry in enumerate(entries):
        where = f'cases[{index}]'
        if not isinstance(entry, dict):
            raise ManifestError(f'{where} must be an object')
        name = entry.get('name')
        if not isinstance(name, str) or not CASE_NAME.fullmatch(name):
            raise ManifestError(f'{where} has an unsafe or missing name: {name!r}')
        if name in names:
            raise ManifestError(f'duplicate case name: {name}')
        names.add(name)
        suite = entry.get('suite')
        if suite not in ('performance', 'accuracy'):
            raise ManifestError(f'{name}: suite must be performance or accuracy')
        if entry.get('status') != 'built':
            raise ManifestError(f'{name}: status must be "built"')
        digest = entry.get('sha256')
        if not isinstance(digest, str) or not SHA256_HEX.fullmatch(digest):
            raise ManifestError(f'{name}: sha256 must be 64 hexadecimal digits')
        digest = digest.lower()
        binary = _resolve_bin(entry.get('bin'), manifest_path.parent, f'{name}: bin')
        if check_files:
            if not binary.is_file():
                raise ManifestError(f'{name}: BIN is missing: {binary}')
            actual = sha256(binary)
            if actual != digest:
                raise ManifestError(
                    f'{name}: BIN sha256 mismatch (manifest {digest}, actual {actual})')
        elf = None
        if entry.get('elf') is not None:
            elf = _resolve_bin(entry['elf'], manifest_path.parent, f'{name}: elf')
            if check_files and not elf.is_file():
                raise ManifestError(f'{name}: ELF is missing: {elf}')
        markers = _validate_markers(entry, name)
        plan = expected_plan(entry, suite, name)
        cases.append({'name': name, 'suite': suite, 'sha256': digest,
                      'bin': str(binary), 'elf': str(elf) if elf else None,
                      'required_markers': markers, 'expected': plan,
                      'entry': entry})
    return {'path': str(manifest_path), 'sha256': sha256(manifest_path),
            'document': document, 'cases': cases}


def rotation_schedule(plan):
    """Enumerate the expected guest records for one performance case.

    ``order`` is 0..8 for every round.  The effective mode list is
    ``modes + calibration_modes`` and the record at ``order`` is
    ``list[(order + r) % 9]`` where ``r`` is 0 for first, ``round + 1`` for
    warmup and ``round + 3`` for steady.
    """
    modes = list(plan['modes'])
    calibration = list(plan['calibration_modes'])
    combined = modes + calibration
    schedule = []
    for length in plan['lengths']:
        for batch in plan['batches']:
            for phase in PLAN_PHASE_ORDER:
                for round_index in range(plan['phases'][phase]):
                    r = ROUND_BASE[phase] + round_index
                    for order in range(len(combined)):
                        index = (order + r) % len(combined)
                        if index < len(modes):
                            kind, mode = 'sample', modes[index]
                        else:
                            kind, mode = 'calibration', calibration[index - len(modes)]
                        schedule.append({'n': length, 'k': batch,
                                         'phase': phase, 'round': round_index,
                                         'r': r, 'order': order,
                                         'kind': kind, 'mode': mode})
    return schedule


def expected_schedule_map(plan):
    return {(row['n'], row['k'], row['phase'], row['round'], row['order']):
            (row['kind'], row['mode']) for row in rotation_schedule(plan)}


def expected_counts(plan):
    schedule = rotation_schedule(plan)
    return {'sample_count': sum(row['kind'] == 'sample' for row in schedule),
            'calibration_count': sum(row['kind'] == 'calibration' for row in schedule)}


class ProcessRegistry:
    """Track simulator process groups so timeout/interrupt can reclaim them."""

    def __init__(self):
        self._lock = threading.Lock()
        self._pgids = set()
        self.abort = threading.Event()

    def add(self, pgid):
        with self._lock:
            self._pgids.add(pgid)

    def discard(self, pgid):
        with self._lock:
            self._pgids.discard(pgid)

    def terminate(self, sig=signal.SIGTERM):
        with self._lock:
            pgids = list(self._pgids)
        for pgid in pgids:
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError):
                pass

    def live(self):
        with self._lock:
            return list(self._pgids)


def launch_process(command, cwd, stdout_path, stderr_path, timeout, registry):
    """Run ``command`` in its own session and reclaim the whole group.

    Waits in short slices so an interrupt requested by a signal handler kills
    the child even if the nominal timeout is many hours away.
    """
    started = time.monotonic()
    if not isinstance(command, list) or not command or not all(
            isinstance(item, str) for item in command):
        return {'returncode': None, 'timed_out': False,
                'launch_error': 'command must be a non-empty string list',
                'elapsed_seconds': 0.0, 'aborted': registry.abort.is_set()}
    stdout_path, stderr_path = Path(stdout_path), Path(stderr_path)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    returncode, timed_out, launch_error = None, False, None
    process = None
    try:
        with stdout_path.open('w') as stdout, stderr_path.open('w') as stderr:
            try:
                env = os.environ.copy()
                env.update(TMPDIR=str(Path(cwd).resolve()), TMP=str(Path(cwd).resolve()),
                           TEMP=str(Path(cwd).resolve()))
                process = subprocess.Popen(
                    command, cwd=str(cwd), stdout=stdout, stderr=stderr, env=env,
                    start_new_session=True)
            except OSError as exc:
                launch_error = str(exc)
            if process is not None:
                registry.add(process.pid)
                try:
                    deadline = started + timeout
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            timed_out = True
                            _killpg(process.pid, signal.SIGKILL)
                            returncode = process.wait()
                            break
                        try:
                            returncode = process.wait(timeout=min(remaining, 0.5))
                            break
                        except subprocess.TimeoutExpired:
                            if registry.abort.is_set():
                                _killpg(process.pid, signal.SIGKILL)
                                returncode = process.wait()
                                break
                finally:
                    registry.discard(process.pid)
    except BaseException:
        if process is not None:
            _killpg(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        raise
    return {'returncode': returncode, 'timed_out': timed_out,
            'launch_error': launch_error,
            'elapsed_seconds': time.monotonic() - started,
            'aborted': registry.abort.is_set()}


def _killpg(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def stage_binaries(cases, run):
    """Copy inputs into an immutable staging directory and verify twice."""
    staging = Path(run) / 'staging'
    if staging.exists():
        raise ManifestError(f'staging directory already exists: {staging}')
    staging.mkdir(parents=True)
    for case in cases:
        source = Path(case['bin'])
        if not source.is_file():
            raise ManifestError(f'{case["name"]}: source BIN disappeared')
        if sha256(source) != case['sha256']:
            raise ManifestError(f'{case["name"]}: source BIN changed before staging')
        target = staging / (case['name'] + '.bin')
        if target.exists() or target.is_symlink():
            raise ManifestError(f'{case["name"]}: staging target already exists')
        shutil.copy2(source, target)
        staged = sha256(target)
        if staged != case['sha256']:
            raise ManifestError(
                f'{case["name"]}: staged BIN sha256 mismatch '
                f'(expected {case["sha256"]}, actual {staged})')
        case['staged_bin'] = str(target)
        case['staged_sha256'] = staged


def gsim_command(emulator, reference, image, dram_ini, dram_outdir, seed):
    # -i must be immediately followed by the image; no other options between.
    return [str(emulator), '--diff', str(reference), f'--seed={seed}',
            f'--dramsim3-ini={dram_ini}', f'--dramsim3-outdir={dram_outdir}',
            '-i', str(image)]


def nemu_command(nemu, image, max_instr):
    return [str(nemu), '-b', f'--max-instr={max_instr}', str(image)]


def smoke_ok(case, result, text):
    """Cheap serial-launch sanity check; final acceptance remains the collector."""
    reasons = []
    if result.get('launch_error'):
        reasons.append('launch error: ' + str(result['launch_error']))
    if result.get('timed_out'):
        reasons.append('smoke timed out')
    if result.get('returncode') != 0:
        reasons.append(f'smoke exit code {result.get("returncode")}')
    if result.get('aborted'):
        reasons.append('smoke aborted')
    for marker in case['required_markers']:
        if marker_count(text, marker) != 1:
            reasons.append(f'smoke marker count != 1: {marker}')
    if not GOOD_TRAP.search(text):
        reasons.append('smoke missing HIT GOOD TRAP')
    errors = error_markers(text)
    if errors:
        reasons.append('smoke error marker(s): ' + ', '.join(errors))
    return not reasons, reasons


def case_name(value):
    return isinstance(value, str) and bool(CASE_NAME.fullmatch(value))
