#!/usr/bin/env python3
"""严格展开 libmvec 紧凑 CSV v2，供 collect.audit_case 在 KV 审计前调用。

调用 ``expand(text, manifest_case)`` 返回 ``(canonical_text, metadata)``。
manifest_case 使用现有 manifest 的 name/expected；expected.wire_format 默认
为 kv1。kv1 原文透传，但拒绝 CSV 保留行；csv2 必须具有完整、唯一的 CSV
区间。任何格式、计数或 manifest 不匹配均抛出 ValueError。

CSV 数据行共享十轮 checksum，依赖 guest 在打印前已验证十轮摘要相同。
每条展开记录保留 ``_wire=D,...`` 和 ``wire_format=csv2``；cycle_end 使用
精确整数按模 2**64 重建。metadata 的 physical_rows 包括所有 CSV 控制行，
data_rows 仅计 D 行，logical_records 仅计 SAMPLE/CALIBRATION。
块外日志（包括 LIBMVEC_ERROR）逐字保留，由后续 collector 审计错误和 trap。
本模块不运行仿真，也不读写文件。
"""
from __future__ import annotations

import re

U64_MAX = (1 << 64) - 1
MODES = ('scalar_strict', 'scalar_fast', 'auto_m1', 'auto_m2',
         'direct_m1', 'direct_m2')
CALIBRATION_MODES = ('rdcycle', 'empty', 'copy')
FUNCTIONS = ('log', 'exp', 'sin', 'cos', 'pow', 'logf', 'expf')
PHASES = {'first': 1, 'warmup': 2, 'steady': 7}
ROUNDS = 10
MODE_HEADER = 'M,strict,fast,auto1,auto2,direct1,direct2,rdcycle,empty,copy'
COLUMN_HEADER = 'm,hash,' + ','.join(f'b{r},c{r}' for r in range(ROUNDS))
DECIMAL = re.compile(r'(?:0|[1-9][0-9]*)\Z')
CSV_HEADER = re.compile(r'\bLIBMVEC_CSV\b')
CSV_ROW = re.compile(r'^\s*(?:D|B|M|T|E|m)(?=\s*(?:,|$))')
KV_RECORD = re.compile(
    r'\bLIBMVEC_(?:CONFIG|SAMPLE|CALIBRATION|CHECK|COMPLETE)\b')


def _uint(value, label):
    # Bound length before int() so malicious huge decimals raise our ValueError.
    if (not isinstance(value, str) or len(value) > 20 or
            not DECIMAL.fullmatch(value)):
        raise ValueError(f'{label}: expected canonical decimal uint64')
    number = int(value)
    if number > U64_MAX:
        raise ValueError(f'{label}: uint64 overflow')
    return number


def _manifest_int(value, label, minimum=0):
    if type(value) is not int or not minimum <= value <= U64_MAX:
        raise ValueError(f'{label}: expected integer in [{minimum}, {U64_MAX}]')
    return value


def mode_table(profile):
    if profile == 'n16-trim':
        return ('scalar_fast', 'direct_m1', 'direct_m2'), (), 'M,fast,direct1,direct2'
    return MODES, CALIBRATION_MODES, MODE_HEADER


def _plan(manifest_case, expected):
    function = expected.get('function')
    if not isinstance(function, str) or function not in FUNCTIONS:
        raise ValueError('expected.function: unsupported function')
    name = manifest_case.get('name')
    if name != f'perf-{function}':
        raise ValueError('manifest case name does not match function')
    if manifest_case.get('suite', 'performance') != 'performance':
        raise ValueError('csv2 supports only performance cases')
    profile = expected.get('profile', 'full')
    if profile not in ('full', 'format-smoke', 'n16', 'n16-trim'):
        raise ValueError('expected.profile: unsupported profile')
    if profile == 'format-smoke' and function != 'log':
        raise ValueError('format-smoke supports only log')
    modes, controls, mode_header = mode_table(profile)
    for key, wanted in (('modes', modes), ('calibration_modes', controls)):
        if not isinstance(expected.get(key), list) or expected[key] != list(wanted):
            raise ValueError(f'expected.{key}: mode table mismatch')
    phases = expected.get('phases')
    if (not isinstance(phases, dict) or phases != PHASES or
            any(type(value) is not int for value in phases.values())):
        raise ValueError('expected.phases: must be first=1, warmup=2, steady=7')
    if _manifest_int(expected.get('rounds', ROUNDS), 'expected.rounds') != ROUNDS:
        raise ValueError('expected.rounds: must be 10')
    policy = 'rotate-all-' + str(len(modes) + len(controls))
    if expected.get('order_policy', policy) != policy:
        raise ValueError('expected.order_policy: must be ' + policy)
    dimensions = []
    for key in ('lengths', 'batches'):
        values = expected.get(key)
        if not isinstance(values, list) or not values:
            raise ValueError(f'expected.{key}: expected a nonempty list')
        for value in values:
            _manifest_int(value, f'expected.{key}', minimum=1)
        if len(set(values)) != len(values):
            raise ValueError(f'expected.{key}: duplicate value')
        dimensions.append(values)
    lengths, batches = dimensions
    block_count = len(lengths) * len(batches)
    for key, width in (('sample_count', len(modes)),
                       ('calibration_count', len(controls))):
        count = _manifest_int(expected.get(key), f'expected.{key}')
        if count != block_count * width * ROUNDS:
            raise ValueError(f'expected.{key}: inconsistent with dimensions')
    minimum_checks = _manifest_int(expected.get('min_checks', 0), 'expected.min_checks')
    return name, function, profile, lengths, batches, minimum_checks


def _body(line):
    # Permit LF/CRLF without altering any preserved simulator text.
    if line.endswith('\n'):
        line = line[:-1]
        if line.endswith('\r'):
            line = line[:-1]
    return line


def _reserved(line):
    return bool(CSV_HEADER.search(line) or CSV_ROW.search(line))


def expand(text, manifest_case):
    """将完整 CSV v2 区间替换为 KV v1 canonical；失败抛出 ValueError。"""
    if not isinstance(text, str):
        raise ValueError('guest text must be a string')
    if not isinstance(manifest_case, dict):
        raise ValueError('manifest case must be an object')
    expected = manifest_case.get('expected')
    if not isinstance(expected, dict):
        raise ValueError('manifest expected must be an object')
    wire_format = expected.get('wire_format', 'kv1')
    if wire_format not in ('kv1', 'csv2'):
        raise ValueError(f'unsupported wire_format: {wire_format!r}')
    lines = text.splitlines(keepends=True)
    bodies = [_body(line) for line in lines]
    if wire_format == 'kv1':
        if any(_reserved(line) for line in bodies):
            raise ValueError('kv1 manifest cannot accept compact CSV records')
        return text, {'wire_format': 'kv1'}

    name, function, profile, lengths, batches, minimum_checks = _plan(
        manifest_case, expected)
    if any(KV_RECORD.search(line) for line in bodies):
        raise ValueError('csv2 cannot contain mixed KV records')
    starts = [index for index, line in enumerate(bodies) if CSV_HEADER.search(line)]
    if len(starts) != 1:
        raise ValueError(f'csv2 requires exactly one header, found {len(starts)}')
    start = cursor = starts[0]

    def take(tag, size):
        nonlocal cursor
        if cursor >= len(bodies):
            raise ValueError(f'truncated CSV: expected {tag} row')
        raw = bodies[cursor]
        cursor += 1
        fields = raw.split(',')
        if any(char.isspace() for char in raw) or fields[0] != tag or len(fields) != size:
            raise ValueError(f'CSV line {cursor}: expected {tag} with {size} fields')
        return raw, fields

    _, header = take('LIBMVEC_CSV', 6)
    if header[1] != '2':
        raise ValueError(f'unsupported CSV version: {header[1]!r}')
    if header[2] != function or header[5] != profile:
        raise ValueError('CSV function/profile does not match manifest')
    vlen_bits = _uint(header[3], 'header vlen_bits')
    if vlen_bits < 128 or vlen_bits % 128 or vlen_bits & (vlen_bits - 1):
        raise ValueError('header vlen_bits must be a power-of-two multiple of 128')
    if _uint(header[4], 'header seed') != 123456789:
        raise ValueError('header seed must be 123456789')
    modes, controls, mode_header = mode_table(profile)
    raw, _ = take('M', 1 + len(modes) + len(controls))
    if raw != mode_header:
        raise ValueError('CSV mode header mismatch')
    raw, _ = take('m', 22)
    if raw != COLUMN_HEADER:
        raise ValueError('CSV column header mismatch')

    canonical = [
        f'LIBMVEC_CONFIG case={name} function={function} vlen_bits={vlen_bits} '
        f'seed=123456789 rounds=10 profile={profile} wire_format=csv2']
    data_rows = 0
    logical_records = 0
    modes, controls, mode_header = mode_table(profile)
    all_modes = modes + controls
    for n in lengths:
        for k in batches:
            _, fields = take('B', 3)
            observed = (_uint(fields[1], 'B n'), _uint(fields[2], 'B k'))
            if observed != (n, k):
                raise ValueError(f'CSV block order mismatch: {observed} != {(n, k)}')
            block = []
            for mode_index in range(len(all_modes)):
                raw, fields = take('D', 23)
                if _uint(fields[1], 'D mode') != mode_index:
                    raise ValueError(f'CSV mode row order mismatch: expected {mode_index}')
                checksum = _uint(fields[2], 'D hash')
                values = [_uint(value, f'D mode {mode_index} tuple {index}')
                          for index, value in enumerate(fields[3:])]
                if mode_index < len(modes) and any(value == 0 for value in values[1::2]):
                    raise ValueError('mathematical sample cycles must be positive')
                block.append((raw, checksum, values))
                data_rows += 1
            # Recreate the original execution sequence, not the compressed order.
            for r in range(ROUNDS):
                phase, phase_round = (('first', 0) if r == 0 else
                                      ('warmup', r - 1) if r < 3 else
                                      ('steady', r - 3))
                for order in range(len(all_modes)):
                    mode_index = (order + r) % len(all_modes)
                    raw, checksum, values = block[mode_index]
                    begin, cycles = values[2*r:2*r + 2]
                    end = (begin + cycles) & U64_MAX
                    tag = 'SAMPLE' if mode_index < len(modes) else 'CALIBRATION'
                    canonical.append(
                        f'LIBMVEC_{tag} case={name} function={function} '
                        f'mode={all_modes[mode_index]} n={n} k={k} phase={phase} '
                        f'round={phase_round} order={order} cycle_begin={begin} '
                        f'cycle_end={end} cycles={cycles} checksum={checksum} '
                        f'wire_format=csv2 _wire={raw}')
                    logical_records += 1

    _, footer = take('T', 4)
    checks = _uint(footer[1], 'T checks')
    failed = _uint(footer[2], 'T failed')
    count = _uint(footer[3], 'T count')
    if checks < minimum_checks or failed != 0:
        raise ValueError('CSV footer reports failed or insufficient checks')
    if count != logical_records:
        raise ValueError(f'CSV footer count mismatch: {count} != {logical_records}')
    _, end = take('E', 3)
    if end != ['E', name, 'pass']:
        raise ValueError('CSV end case/status mismatch')
    for line in bodies[:start] + bodies[cursor:]:
        if _reserved(line):
            raise ValueError('extra CSV record outside the compact block')
    canonical.extend((f'LIBMVEC_CHECK case={name} checks={checks} failed=0',
                      f'LIBMVEC_COMPLETE case={name} status=pass'))
    replacement = '\n'.join(canonical)
    if lines[cursor - 1].endswith('\n'):
        replacement += '\n'
    expanded = ''.join(lines[:start]) + replacement + ''.join(lines[cursor:])
    metadata = {
        'wire_format': 'csv2', 'physical_rows': cursor - start,
        'data_rows': data_rows, 'logical_records': logical_records,
        'csv_text': ''.join(lines[start:cursor]).rstrip('\n') + '\n',
        'end_counter': 'derived-modulo-2^64',
        'checksum': 'shared-after-guest-equality-check',
    }
    return expanded, metadata


def recover(text, manifest_case):
    """逐行恢复完整 D 行，仅供诊断；不证明运行成功，也不参与严格验收。

    参数与 expand 相同，非法 manifest 抛出 ValueError；默认 kv1 返回空结果。
    records 按源 D 行及轮次排列，所有记录均为 parsed-unverified，且
    measurement_valid=False。调用方必须分别传入 stdout/stderr，不能拼接碎片。
    _raw 不含行尾，source_line 从 1 开始；仅接受 LF/CRLF 终止的完整行。
    config 为首个完整有效表头，headers 保留所有完整有效表头及其来源。
    issues 只描述恢复过程，不代表完整审计；即使为空也不表示通过。
    """
    if not isinstance(text, str):
        raise ValueError('guest text must be a string')
    if not isinstance(manifest_case, dict):
        raise ValueError('manifest case must be an object')
    expected = manifest_case.get('expected')
    if not isinstance(expected, dict):
        raise ValueError('manifest expected must be an object')
    wire_format = expected.get('wire_format', 'kv1')
    if wire_format not in ('kv1', 'csv2'):
        raise ValueError(f'unsupported wire_format: {wire_format!r}')
    result = {'wire_format': wire_format, 'records': [], 'issues': [],
              'data_rows': 0, 'logical_records': 0, 'config': None, 'headers': []}
    if wire_format == 'kv1':
        return result
    name, function, profile, lengths, batches, minimum_checks = _plan(
        manifest_case, expected)
    modes, controls, mode_header = mode_table(profile)
    all_modes = modes + controls
    ansi = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
    pending = None
    stage = None
    header = None
    block = None
    seen_headers = 0
    seen_blocks = set()
    seen_rows = set()
    session_rows = 0
    footer_seen = False
    end_seen = False

    def issue(line, message):
        result['issues'].append({'line': line, 'message': message})

    def fields_for(raw, tag, size):
        fields = raw.split(',')
        if (any(char.isspace() for char in raw) or fields[0] != tag or
                len(fields) != size):
            raise ValueError(f'expected {tag} with {size} fields')
        return fields

    def finish_block():
        nonlocal block
        if block is not None and len(block['modes']) != len(all_modes):
            issue(block['line'], f'incomplete B block: parsed {len(block["modes"])} '
                  f'of {len(all_modes)} distinct mode rows')
        block = None

    def clear_header():
        nonlocal pending, stage, header
        finish_block()
        pending = stage = header = None

    def finish_header():
        if header is not None and not end_seen:
            issue(header['source_line'], 'missing CSV E end row')
        elif pending is not None:
            issue(pending['source_line'], 'incomplete CSV header')

    # 只按 LF 分割，避免把孤立 CR、Unicode 行分隔符误当作完整串口行。
    parts = text.split('\n')
    for source_line, part in enumerate(parts, 1):
        terminated = source_line < len(parts)
        if not terminated:
            if part:
                issue(source_line, 'unterminated final line ignored')
            break
        original = part[:-1] if part.endswith('\r') else part
        # 只移除 ANSI 着色；保留 NUL、嵌入 CR 和原始行以便诊断溯源。
        raw = ansi.sub('', original)
        # 畸形控制行（包括引号包裹）也要清除对应上下文，不能继承旧 n/K。
        match = re.match(r'^(D|B|M|T|E|m)(?=\s*(?:[^\w\s]|$))',
                         raw.lstrip().lstrip('\"\'\x00'))
        tag = match.group(1) if match else None
        header_like = (CSV_HEADER.search(raw) or
                       raw.lstrip().startswith('LIBMVEC_CSV'))
        if tag is None and header_like:
            finish_header()
            clear_header()
            seen_headers += 1
            if seen_headers > 1:
                issue(source_line, 'repeated CSV header; preserving separate occurrences')
            session_rows = 0
            footer_seen = end_seen = False
            try:
                fields = fields_for(raw, 'LIBMVEC_CSV', 6)
                if fields[1] != '2':
                    raise ValueError(f'unsupported CSV version: {fields[1]!r}')
                if fields[2] != function or fields[5] != profile:
                    raise ValueError('CSV function/profile does not match manifest')
                vlen_bits = _uint(fields[3], 'header vlen_bits')
                if vlen_bits < 128 or vlen_bits % 128 or vlen_bits & (vlen_bits - 1):
                    raise ValueError('header vlen_bits must be a power-of-two multiple of 128')
                seed = _uint(fields[4], 'header seed')
                if seed != 123456789:
                    raise ValueError('header seed must be 123456789')
            except ValueError as error:
                issue(source_line, str(error))
                continue
            pending = {'case': name, 'suite': 'performance', 'function': function,
                       'version': 2, 'vlen_bits': vlen_bits, 'seed': seed,
                       'rounds': ROUNDS, 'profile': profile, 'wire_format': 'csv2',
                       'source_line': source_line, '_raw': original}
            stage = 'M'
            continue
        if tag is None and KV_RECORD.search(raw):
            issue(source_line, 'mixed KV record invalidates CSV header context')
            finish_header()
            clear_header()
            continue
        if tag in ('M', 'm'):
            wanted = mode_header if tag == 'M' else COLUMN_HEADER
            if stage != tag or raw != wanted:
                issue(source_line, f'CSV {tag} header mismatch or unexpected header row')
                clear_header()
            elif tag == 'M':
                stage = 'm'
            else:
                header = pending
                pending = stage = None
                result['headers'].append(dict(header))
                if result['config'] is None:
                    result['config'] = dict(header)
            continue
        if tag is None:
            # 仿真器日志、错误提示和空行不改变已验证的串口上下文。
            continue
        if pending is not None:
            issue(source_line, f'incomplete CSV header before {tag} row')
            clear_header()
        if tag == 'B':
            finish_block()
            try:
                fields = fields_for(raw, 'B', 3)
                n, k = _uint(fields[1], 'B n'), _uint(fields[2], 'B k')
                if n not in lengths or k not in batches:
                    raise ValueError('B dimensions do not match manifest')
                if header is None:
                    raise ValueError('B requires a validated complete CSV header')
            except ValueError as error:
                issue(source_line, str(error))
                continue
            if (n, k) in seen_blocks:
                issue(source_line, f'repeated B block ({n}, {k}); preserving occurrences')
            seen_blocks.add((n, k))
            block = {'n': n, 'k': k, 'line': source_line, 'modes': set()}
            continue
        if tag == 'D':
            try:
                fields = fields_for(raw, 'D', 23)
                mode = _uint(fields[1], 'D mode')
                if mode >= len(all_modes):
                    raise ValueError(f'D mode must be in [0, {len(all_modes)-1}]')
                checksum = _uint(fields[2], 'D hash')
                values = [_uint(value, f'D mode {mode} tuple {index}')
                          for index, value in enumerate(fields[3:])]
                if mode < len(modes) and any(value == 0 for value in values[1::2]):
                    raise ValueError('mathematical sample cycles must be positive')
                if header is None or block is None:
                    raise ValueError('D requires a validated CSV header and B context')
            except ValueError as error:
                issue(source_line, str(error))
                continue
            n, k = block['n'], block['k']
            key = (n, k, mode)
            if key in seen_rows:
                issue(source_line, f'duplicate D row ({n}, {k}, {mode}); preserving occurrence')
            seen_rows.add(key)
            block['modes'].add(mode)
            # 整行先验证再展开，任意一轮损坏都不能产生部分样本。
            for r in range(ROUNDS):
                phase, phase_round = (('first', 0) if r == 0 else
                                      ('warmup', r - 1) if r < 3 else
                                      ('steady', r - 3))
                begin, cycles = values[2*r:2*r + 2]
                result['records'].append({
                    'record_type': 'sample' if mode < len(modes) else 'calibration',
                    'case': name, 'suite': 'performance', 'function': function,
                    'mode': all_modes[mode], 'n': n, 'k': k, 'phase': phase,
                    'round': phase_round, 'order': (mode - r) % len(all_modes), 'r': r,
                    'cycle_begin': begin, 'cycle_end': (begin + cycles) & U64_MAX,
                    'cycles': cycles, 'checksum': checksum, 'wire_format': 'csv2',
                    '_raw': original, 'source_line': source_line,
                    'row_status': 'parsed-unverified', 'measurement_valid': False,
                })
            result['data_rows'] += 1
            result['logical_records'] += ROUNDS
            session_rows += 1
            continue
        if tag == 'T':
            finish_block()
            if footer_seen:
                issue(source_line, 'repeated CSV T footer')
            footer_seen = True
            try:
                fields = fields_for(raw, 'T', 4)
                checks, failed, count = (_uint(fields[index], f'T {label}')
                                        for index, label in enumerate(
                                            ('checks', 'failed', 'count'), 1))
                if header is None:
                    raise ValueError('T requires a validated complete CSV header')
                if checks < minimum_checks or failed != 0:
                    raise ValueError('CSV footer reports failed or insufficient checks')
                if count != session_rows * ROUNDS:
                    raise ValueError('CSV footer count does not match recovered records')
            except ValueError as error:
                issue(source_line, str(error))
            continue
        if tag == 'E':
            finish_block()
            try:
                fields = fields_for(raw, 'E', 3)
                if header is None:
                    raise ValueError('E requires a validated complete CSV header')
                if fields != ['E', name, 'pass']:
                    raise ValueError('CSV end case/status mismatch')
                if not footer_seen:
                    raise ValueError('CSV E end row without T footer')
            except ValueError as error:
                issue(source_line, str(error))
            # 无论结束行是否有效，均终止当前区间；新数据需要完整新表头。
            end_seen = True
            clear_header()
    finish_block()
    finish_header()
    return result
