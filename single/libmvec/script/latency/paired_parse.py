#!/usr/bin/env python3
"""按冻结构建清单核对配对协议 2；不启动模拟器。"""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

# 脚本可从任意运行目录调用；复用构建器的 ABI 注册和语料检查。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runner_common as common
from build_latency import PAIRED_FUNCTIONS, pair_cases, validate_paired_header


WINDOW_MIN = common.WINDOW_MIN
WINDOW_TARGET = 40_000
MAX_REPEAT = common.MAX_TIMING_REPEAT
MIN_STEADY = common.MIN_STEADY
U32_MAX = (1 << 32) - 1
TOKEN = re.compile(r'[A-Za-z0-9_.+-]+\Z')
HEX64 = re.compile(r'[0-9a-f]{1,16}\Z')
RECORD = re.compile(r'^[ \t]*(P\b|PAIR(?:_[A-Za-z0-9_]*)?\b)')
MODE = {'l': 'latency', 't': 'tput'}


class ProtocolError(ValueError):
    """构建身份或 wire 不符合当前协议。"""


def require(ok, reason):
    if not ok:
        raise ProtocolError(reason)


def strict_json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'重复 JSON 键：{key}')
            result[key] = value
        return result

    def invalid_constant(text):
        raise ProtocolError(f'非有限的 JSON 常量：{text}')

    with Path(path).open(encoding='utf-8') as stream:
        return json.load(stream, object_pairs_hook=pairs, parse_constant=invalid_constant)


def integer(value, field, low=0, high=common.U64_MAX):
    require(type(value) is int and low <= value <= high, f'{field} 越界：{value!r}')
    return value


def decimal(text, field, low=0, high=common.U64_MAX):
    require(isinstance(text, str) and len(text) <= 20 and common.DECIMAL.fullmatch(text),
            f'{field} 不是协议十进制：{text!r}')
    return integer(int(text), field, low, high)


def number_list(text, field, minimum=1):
    require(isinstance(text, str), f'{field} 必须是规范的十进制列表')
    values = tuple(decimal(v, field, minimum, U32_MAX) for v in text.split(','))
    require(1 <= len(values) <= 16 and len(set(values)) == len(values),
            f'{field} 为空、重复或超过 16 项')
    return values


def identity(info, what):
    require(isinstance(info, dict) and isinstance(info.get('path'), str) and info['path']
            and isinstance(info.get('sha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', info['sha256']), f'{what} 缺少有效身份')
    return info


def file_matches(path, info, what):
    require(path.is_file() and common.sha256(path) == info['sha256'],
            f'{what} 不存在或 SHA256 不匹配：{path}')


def frozen_seeds(path, info, ns, function, modes):
    file_matches(path, info, '冻结语料快照')
    count = integer(info['DATA_COUNT'], 'DATA_COUNT', 1, U32_MAX)
    threshold = integer(info['ORACLE_LIMIT'], 'ORACLE_LIMIT', 0, U32_MAX)
    text = re.sub(r'/\*.*?\*/|//[^\n]*', '', path.read_text(), flags=re.S)
    for key, want in [('DATA_COUNT', count), ('ORACLE_LIMIT', threshold)]:
        values = re.findall(rf'^\s*#define[ \t]+{key}[ \t]+([0-9]+)[ \t]*$', text, re.M)
        require(values == [str(want)], f'{path}: {key} 与 manifest 不符')
    try:
        validate_paired_header(path, info, function=function, modes=modes)
    except SystemExit as exc:
        raise ProtocolError(str(exc)) from exc

    def bits(name):
        init = re.search(rf'\b{name}\s*\[\s*(?:DATA_COUNT|[0-9]+)\s*\]'
                         r'\s*=\s*\{([^}]*)\}\s*;', text)[1]
        return [int(v, 16) for v in re.findall(r'0x[0-9a-fA-F]{16}', init)]

    limit = min(max(ns), count)
    unary = pair = 1469598103934665603
    operands = bits('input_bits')
    other = bits('second_bits') if function == 'atan2' else None
    result = {}

    def fnv(h, value):
        return ((h ^ value) * 1099511628211) & common.U64_MAX

    for i, value in enumerate(operands[:limit], 1):
        unary = fnv(unary, value)
        pair = fnv(pair, value)
        if other is not None:
            pair = fnv(pair, other[i - 1])
        if i in ns:
            result[i] = {'N': str(i), 'count': str(limit), 'unary_fnv': f'{unary:x}',
                         'pair_fnv': f'{pair:x}'}
    return limit, result


@dataclass(frozen=True)
class Plan:
    header: str
    cases: tuple
    seeds: dict
    skips: dict
    checks: dict
    points: dict
    rounds: int
    repeat: int | str
    bin_info: dict


def load_plan(manifest, *, headers=None):
    """从 manifest 及其真实语料快照重建 case、点、skip 和检查预期。"""
    try:
        return _load_plan(manifest, headers)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ProtocolError(f'manifest 缺字段或类型有误：{exc}') from exc


def _load_plan(manifest, headers):
    require(manifest['schema_version'] == 2 and manifest['suite'] == 'libmvec-paired'
            and manifest['protocol'] == 2 and manifest['build_status'] == 'built',
            '不支持的配对构建清单')
    knobs = manifest['knobs']
    require(manifest['wire_format'] == 'pair-compact-r' and knobs['trace'] is False,
            'trace/历史协议不是当前计时日志')
    require(manifest['data']['frozen'] is True and manifest['audit']['status'] == 'ok'
            and manifest['audit']['findings'] == [], '输入未冻结或依赖审计未通过')
    archive = identity(manifest['vector_archive'], '向量归档')
    members = archive['members']
    require(isinstance(members, list) and members
            and all(isinstance(member, str) and member for member in members)
            and len(set(members)) == len(members), '向量归档成员无效')
    if manifest['math_archive'] is not None:
        require(manifest['math_archive'] == archive, '显式库与实际链接向量库不一致')
    candidate = manifest['candidate']
    require(TOKEN.fullmatch(manifest['tag']) and TOKEN.fullmatch(candidate['id'])
            and candidate['lto'] in ('on', 'off'), '无效的 tag、candidate 或 lto')
    functions = manifest['functions']
    require(isinstance(functions, list) and functions and set(functions) <= set(PAIRED_FUNCTIONS)
            and len(functions) == len(set(functions)), '函数集合无效')
    ns, bs, ks, ss = (number_list(knobs[name], name, minimum)
                     for name, minimum in [('lengths', 1), ('batches', 1),
                                           ('paired_k', 0), ('streams', 1)])
    require(set(ss) <= {1, 2, 4}, 'streams 无效')
    repeat = knobs['timing_repeat']
    if repeat != 'auto':
        integer(repeat, 'timing_repeat', 1, MAX_REPEAT)
    rounds = integer(knobs['rounds'], 'rounds', 1, 65535)
    serial = integer(knobs['serial'], 'serial', 0, 1)
    vlen = integer(knobs['vlen'], 'vlen', 128, 256)
    require(vlen in (128, 256) and knobs['vlenb'] == vlen // 8
            and knobs['fixed_lanes'] == {'m1': 2, 'm2': 4}
            and knobs['scalable_lanes'] == {'m1': vlen // 64, 'm2': vlen // 32},
            'VLEN 与 lane 配置不一致')
    modes, calib = knobs['modes'], knobs['calib']
    require(modes in ('both', 'latency', 'tput') and type(calib) is bool
            and (not calib or 'atan' in functions and modes != 'tput'), 'mode/calib 无效')
    args = SimpleNamespace(only_case=knobs['only_cases'], modes=modes, vlen=vlen, calib=calib)
    try:
        cases, selected = pair_cases(functions, args)
    except SystemExit as exc:
        raise ProtocolError(str(exc)) from exc
    require(bool(cases) and cases == manifest['cases'] and selected == args.only_case,
            'manifest case 与构建配置不一致')
    require(len(cases) * len(ns) * len(ks) * rounds * (1 + len(bs) * len(ss)) <= 2_000_000,
            '配对矩阵超出构建器限制')
    data_headers = manifest['data']['headers']
    require(set(data_headers) == {fn + '.h' for fn in functions}, '语料集合不一致')
    require(len({entry['DATA_COUNT'] for entry in data_headers.values()}) == 1,
            'DATA_COUNT 不一致')
    seeds, limits = {}, {}
    for fn in functions:
        name = fn + '.h'
        info = identity(data_headers[name], name)
        path = Path(info['snapshot']) if headers is None else headers / name
        limits[fn], prefix = frozen_seeds(path, info, ns, fn, modes)
        for n, values in prefix.items():
            seeds[(fn, n)] = {'function': fn, **values}
    if calib:
        limits['calib'] = limits['atan']
        for n in ns:
            if ('atan', n) in seeds:
                seeds[('calib', n)] = {**seeds[('atan', n)], 'function': 'calib'}
    header = (
        f'PAIR_HEADER,protocol=2,vlenb={vlen // 8},vlen={vlen},candidate={candidate["id"]},'
        f'lto={candidate["lto"]},modes={modes},lengths={knobs["lengths"]},'
        f'batches={knobs["batches"]},streams={knobs["streams"]},k={knobs["paired_k"]},'
        f'rounds={rounds},serial={serial},trace=0,seed_order=scalar_prefix,latency_batch=1,'
        f'timing_repeat={repeat},window_min={WINDOW_MIN},window_target={WINDOW_TARGET},'
        f'max_repeat={MAX_REPEAT},min_steady={MIN_STEADY},only_cases={selected}')
    skips, checks, points = {}, {}, {}
    for idx, case in enumerate(cases):
        name, fn, lane = case['case'], case['function'], case['lanes']
        for mode in case['modes']:
            for n in ns:
                block = (name, mode, n)
                values = {'case': name, 'mode': mode, 'N': str(n)}
                if n > limits[fn]:
                    skips[block] = {**values, 'kind': 'n_gt_corpus', 'corpus': str(limits[fn])}
                    continue
                if n % lane:
                    skips[block] = {**values, 'kind': 'n_not_multiple_of_lanes', 'L': str(lane)}
                    continue
                ncall = n // lane
                batches, streams = ((1,), (1,)) if mode == 'latency' else (bs, ss)
                count = 0
                for batch in batches:
                    for k in ks:
                        for stream in streams:
                            if ncall % stream:
                                skips[(*block, batch, k, stream)] = {
                                    **values, 'kind': 'ncall_not_divisible_by_streams',
                                    'L': str(lane), 'B': str(batch), 'K': str(k), 'streams': str(stream)}
                                continue
                            unit_calls = ncall * batch * k
                            max_repeat = MAX_REPEAT if repeat == 'auto' else repeat
                            require(unit_calls * max_repeat <= U32_MAX, 'calls 超出探针计数范围')
                            points[(idx, mode, n, batch, k, stream)] = unit_calls
                            count += 1
                checks[block] = lane * count * (rounds + (repeat == 'auto')) if fn != 'calib' else 0
    require(bool(points), '没有可表示的窗口')
    image_name = f'paired-{manifest["tag"]}.bin'
    bin_info = identity(manifest['artifacts'][image_name], 'BIN')
    require(Path(bin_info['path']).name == image_name, 'BIN 路径与 tag 不符')
    integer(bin_info['bytes'], 'BIN bytes', 1)
    return Plan(header, tuple(cases), seeds, skips, checks, points, rounds, repeat, bin_info)


def fields(words, *needed):
    values = {}
    for word in words:
        key, equals, value = word.partition('=')
        require(equals and key not in values, f'键值格式无效或重复：{word!r}')
        values[key] = value
    require(values.keys() == set(needed), f'字段不匹配：{sorted(values)}')
    return values


def retain(seen, key, expected, value, kind):
    require(key not in seen and expected.get(key) == value, f'{kind} 重复或值不符：{key}')
    seen.add(key)


@dataclass(slots=True)
class Point:
    repeat: int
    cycles: dict


def parse_wire(lines, plan):
    """严格核对当前紧凑日志。返回按点索引的窗口，不判断主机或精度。"""
    mapped = records = checks_done = 0
    begun = ended = False
    seed_seen, skip_seen, check_seen = set(), set(), set()
    points, warns = {}, {}
    case_indexes = {case['case']: i for i, case in enumerate(plan.cases)}
    for lineno, raw in enumerate(lines, 1):
        line = common.ANSI.sub('', raw.removesuffix('\n').removesuffix('\r'))
        if not RECORD.match(line):
            continue
        try:
            require(raw.endswith('\n') and not ended, '未终止的行，或 footer 后还有记录')
            kind, *words = line.split(',')
            if not begun:
                require(line == plan.header, '缺少唯一的匹配 PAIR_HEADER')
                begun = True
                continue
            if kind == 'PAIR_CASE':
                require(mapped < len(plan.cases), '过多 PAIR_CASE 行')
                case = plan.cases[mapped]
                require(words == [str(mapped), case['case'], case['function'], str(case['lanes'])],
                        'PAIR_CASE 顺序、索引或 ABI 错误')
                mapped += 1
                continue
            require(mapped == len(plan.cases), 'PAIR_CASE 映射不完整')
            if kind == 'P':
                require(len(words) == 10 and words[1] in MODE, 'P 行缺字段或 mode 无效')
                idx = decimal(words[0], 'index', 0, len(plan.cases) - 1)
                n, batch, k, stream = (decimal(s, key, low, U32_MAX)
                                      for s, key, low in zip(words[2:6], ('N', 'B', 'K', 'S'), (1, 1, 0, 1)))
                key = (idx, MODE[words[1]], n, batch, k, stream)
                require(key in plan.points, f'矩阵之外的点：{key}')
                require((plan.cases[idx]['function'], n) in seed_seen, '窗口先于对应的 PAIR_SEED')
                round_ = decimal(words[6], 'round', 0, plan.rounds - 1)
                calls = decimal(words[7], 'calls', 0, U32_MAX)
                cycles = decimal(words[8], 'cycles', 1 if k else 0)
                repeat = decimal(words[9], 'R', 1, MAX_REPEAT)
                require(calls == repeat * plan.points[key], f'calls 不符：{key}')
                require(plan.repeat in ('auto', repeat), f'R 与固定配置不符：{key}')
                point = points.get(key)
                if point is None:
                    point = points[key] = Point(repeat, {})
                require(point.repeat == repeat and round_ not in point.cycles, '同一点 R 变化或轮次重复')
                point.cycles[round_] = cycles
                records += 1
            elif kind == 'PAIR_SEED':
                row = fields(words, 'function', 'N', 'count', 'unary_fnv', 'pair_fnv')
                key = (row['function'], decimal(row['N'], 'N', 1, U32_MAX))
                require(HEX64.fullmatch(row['unary_fnv']) and HEX64.fullmatch(row['pair_fnv']),
                        'PAIR_SEED 哈希不是 uint64 十六进制')
                retain(seed_seen, key, plan.seeds, row, kind)
            elif kind == 'PAIR_SKIP':
                extras = {
                    'n_gt_corpus': ('corpus',), 'n_not_multiple_of_lanes': ('L',),
                    'ncall_not_divisible_by_streams': ('L', 'B', 'K', 'streams')}
                kinds = [word[5:] for word in words if word.startswith('kind=')]
                require(len(kinds) == 1 and kinds[0] in extras, '未知的 PAIR_SKIP 原因')
                row = fields(words, 'case', 'mode', 'kind', 'N', *extras[kinds[0]])
                key = (row['case'], row['mode'], decimal(row['N'], 'N', 1, U32_MAX))
                if 'streams' in row:
                    key += tuple(decimal(row[field], field, high=U32_MAX)
                                 for field in ('B', 'K', 'streams'))
                retain(skip_seen, key, plan.skips, row, kind)
            elif kind == 'PAIR_WARN':
                row = fields(words, 'case', 'mode', 'N', 'B', 'K', 'S', 'kind', 'window_min',
                             'window_cycles', 'timing_repeat')
                key = (case_indexes.get(row['case']), row['mode'],
                       *(decimal(row[field], field, 1, U32_MAX) for field in ('N', 'B', 'K', 'S')))
                estimate = decimal(row['window_cycles'], 'window_cycles', 0, WINDOW_MIN - 1)
                require(key in plan.points and key not in warns and plan.repeat == 'auto'
                        and row['kind'] == 'window_too_short' and row['window_min'] == str(WINDOW_MIN)
                        and row['timing_repeat'] == str(MAX_REPEAT) and estimate % MAX_REPEAT == 0,
                        'PAIR_WARN 不匹配 auto 非零工作点的预测值')
                warns[key] = estimate
            elif kind == 'PAIR_CHECK':
                row = fields(words, 'case', 'mode', 'N', 'checks', 'failed')
                key = (row['case'], row['mode'], decimal(row['N'], 'N', 1, U32_MAX))
                count = decimal(row['checks'], 'checks', high=U32_MAX)
                require(row['failed'] == '0', f'oracle 检查失败：{key}')
                retain(check_seen, key, plan.checks, count, kind)
                checks_done += count
            elif kind == 'PAIR_END':
                row = fields(words, 'status', 'checks', 'failed', 'records')
                require(row == {'status': 'ok', 'checks': str(checks_done),
                                'failed': '0', 'records': str(records)}, 'PAIR_END 计数或状态不符')
                ended = True
            else:
                raise ProtocolError(f'不允许的记录：{kind}')
        except ProtocolError as exc:
            raise ProtocolError(f'第 {lineno} 行：{exc}') from exc
    require(begun and ended and mapped == len(plan.cases), '没有完整 PAIR_HEADER、case 表和 PAIR_END')
    require(seed_seen == plan.seeds.keys(), 'PAIR_SEED 覆盖不全')
    require(skip_seen == plan.skips.keys(), 'PAIR_SKIP 覆盖不全')
    require(check_seen == plan.checks.keys(), 'PAIR_CHECK 覆盖不全')
    require(records == len(plan.points) * plan.rounds and points.keys() == plan.points.keys(),
            'P 窗口或轮次覆盖不全')
    require(all(points[key].repeat == MAX_REPEAT for key in warns), 'PAIR_WARN 与实际 R 不符')
    return points, warns, checks_done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', type=Path, help='用 run_paired.py 保存的完整运行目录')
    ap.add_argument('--manifest', type=Path, help='只诊断 guest wire 时的构建 manifest')
    ap.add_argument('--log', type=Path, help='只诊断 guest wire，不采信 RTL 证据')
    ap.add_argument('--out', type=Path, help='新的分析输出目录，仅用于 --run')
    args = ap.parse_args(argv)
    try:
        if args.run is not None and args.out is not None and args.manifest is None and args.log is None:
            from run_paired import audit_run, report
            report(args.out, *audit_run(args.run))
            print('run=complete scope=single-launch')
        elif args.run is None and args.out is None and args.manifest is not None and args.log is not None:
            plan = load_plan(strict_json(args.manifest))
            with args.log.open(encoding='utf-8', errors='replace', newline='') as stream:
                points, _, checks = parse_wire(stream, plan)
            print(f'wire=complete evidence=diagnostic windows={sum(len(p.cycles) for p in points.values())} checks={checks}')
        else:
            ap.error('使用 --run RUN --out OUTPUT，或 --manifest FILE --log FILE')
    except (OSError, ValueError) as exc:
        print(f'PAIR_REJECTED {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
