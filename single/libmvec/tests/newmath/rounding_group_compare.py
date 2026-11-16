#!/usr/bin/env python3
"""Independently compare verified rounding-observation groups and lane inputs."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import itertools
import os
from pathlib import Path
import re
import sys
import tempfile

FUNCTIONS = ('log', 'exp', 'expf', 'logf', 'pow', 'sin', 'cos')
LENGTHS = (1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 65, 129, 257)
FILES = {'lanes': 'lanes.csv.gz', 'groups': 'groups.csv.gz', 'summary': 'summary.csv',
         'paired': 'paired.csv', 'coverage': 'coverage.csv'}
GROUP_FIELDS = ('function', 'interface', 'lmul', 'frm', 'kind', 'group_id', 'n',
                'emitted', 'incoming_vl', 'frm_after', 'flags', 'errno')
LANE_FIELDS = ('function', 'interface', 'lmul', 'sample_id', 'frm', 'x_bits',
               'z_bits', 'y_bits', 'ref_bits', 'rne_ref_ulp')
COVERAGE_FIELDS = ('function', 'vlenb', 'groups', 'emitted_lanes', 'corpus_samples')
OUT_FIELDS = ('record_type', 'function', 'interface', 'lmul', 'frm', 'kind',
              'group_id', 'n', 'emitted', 'incoming_vl',
              'baseline_frm_after', 'candidate_frm_after', 'baseline_fflags',
              'candidate_fflags', 'baseline_errno', 'candidate_errno', 'fflags_xor',
              'groups_compared', 'frm_after_differences', 'fflags_differences',
              'errno_differences', 'any_state_differences', 'lane_bit_differences',
              'baseline_complete_sha256', 'candidate_complete_sha256', 'comparator_sha256')
HASH_RE = re.compile(r'[0-9a-f]{64}\Z')
UINT_RE = re.compile(r'(?:0|[1-9][0-9]*)\Z')
SINT_RE = re.compile(r'(?:0|-?[1-9][0-9]*)\Z')
DATE_RE = re.compile(r'\d{8}[-_]\d{4,6}')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def uint(text, field, maximum=None):
    if not isinstance(text, str) or not UINT_RE.fullmatch(text):
        raise ValueError(f'{field}: 非规范无符号十进制: {text!r}')
    number = int(text)
    if maximum is not None and number > maximum:
        raise ValueError(f'{field}: 超出允许范围')
    return number


def sint(text, field):
    if not isinstance(text, str) or not SINT_RE.fullmatch(text):
        raise ValueError(f'{field}: 非规范有符号十进制: {text!r}')
    number = int(text)
    if not -(1 << 31) <= number < (1 << 31):
        raise ValueError(f'{field}: 超出 int32 范围')
    return number


def rows(path, fields, compressed=False):
    opener = gzip.open if compressed else open
    with opener(path, 'rt', newline='', encoding='ascii') as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != fields:
            raise ValueError(f'{path}: CSV 标题不符')
        for line, row in enumerate(reader, 2):
            if None in row or any(row[name] is None for name in fields):
                raise ValueError(f'{path}:{line}: CSV 字段数量不符')
            yield row


def manifest(directory):
    directory = Path(directory).resolve()
    path = directory / 'COMPLETE.csv'
    manifest_hash = sha(path)
    data = list(rows(path, ('role', 'value')))
    info = {row['role']: row['value'] for row in data}
    expected = {'run_role', 'archive_sha256', 'functions', 'environment_sha256',
                'observer_sha256'} | {name + '_sha256' for name in FILES}
    if len(info) != len(data) or set(info) != expected:
        raise ValueError(f'{path}: 完成清单缺失、重复或字段不符')
    if info['run_role'] not in ('baseline', 'candidate'):
        raise ValueError(f'{path}: 无效 run_role')
    functions = info['functions'].split(',')
    if not functions or len(set(functions)) != len(functions) or not set(functions) <= set(FUNCTIONS):
        raise ValueError(f'{path}: 函数集非法')
    for name in expected - {'run_role', 'functions'}:
        if not HASH_RE.fullmatch(info[name]):
            raise ValueError(f'{path}: {name} 不是 SHA-256')
    for name, filename in FILES.items():
        if sha(directory / filename) != info[name + '_sha256']:
            raise ValueError(f'{directory / filename}: SHA-256 不符')
    return info, functions, manifest_hash


def coverage(directory, functions):
    data = list(rows(directory / FILES['coverage'], COVERAGE_FIELDS))
    if [r['function'] for r in data] != functions:
        raise ValueError(f'{directory}: coverage 函数或顺序不符')
    result = {}
    for row in data:
        fn = row['function']
        vlenb = uint(row['vlenb'], 'vlenb', 2048)
        width = 4 if fn in ('expf', 'logf') else 8
        if vlenb < 16 or vlenb & (vlenb - 1) or 2 * vlenb // width > 512:
            raise ValueError(f'{fn}: VLEN 无效或超过缓冲区')
        result[fn] = {name: uint(row[name], name) for name in COVERAGE_FIELDS[1:]}
        if not all(result[fn].values()):
            raise ValueError(f'{fn}: coverage 含零')
    return result


def group_rows(directory):
    for row in rows(directory / FILES['groups'], GROUP_FIELDS, compressed=True):
        if row['function'] not in FUNCTIONS or row['interface'] not in ('Nx', 'array') or row['kind'] not in ('C', 'S'):
            raise ValueError('group 的函数、接口或类型非法')
        for name, maximum in (('lmul', 2), ('frm', 4), ('group_id', None), ('n', 512),
                              ('emitted', 512), ('incoming_vl', 512), ('frm_after', 4), ('flags', 31)):
            uint(row[name], name, maximum)
        sint(row['errno'], 'errno')
        if row['lmul'] not in ('1', '2') or not (1 <= int(row['emitted']) <= int(row['n'])):
            raise ValueError('group 的 LMUL 或长度非法')
        yield row


class Cursor:
    def __init__(self, iterator):
        self.iterator = iter(iterator)
        self.pending = None
        self.done = False

    def peek(self):
        if self.pending is None and not self.done:
            try:
                self.pending = next(self.iterator)
            except StopIteration:
                self.done = True
        return self.pending

    def pop(self):
        value = self.peek()
        if value is None:
            raise ValueError('group 提前结束')
        self.pending = None
        return value


def c_length(fn, iface, p, li, count, full):
    if iface == 'Nx':
        return full
    if fn in ('sin', 'cos'):
        return min(257, count - p)
    if fn == 'pow':
        n = min(257, count - p)
        return 115422 - p if p < 115422 < p + n else n
    return min(LENGTHS[li % len(LENGTHS)], count - p)


def common_row(a, b, fn, iface, m, rm, kind, gid, n, emitted, incoming):
    expected = (fn, iface, str(m), str(rm), kind, str(gid), str(n), str(emitted), str(incoming))
    fields = GROUP_FIELDS[:9]
    for which, row in (('baseline', a), ('candidate', b)):
        if tuple(row[k] for k in fields) != expected:
            raise ValueError(f'{which}: group 无法对齐，期望 {expected}，实际 {tuple(row[k] for k in fields)}')


def check_groups(base, cand, functions, cover, writer, all_groups, provenance):
    left, right = Cursor(group_rows(base)), Cursor(group_rows(cand))
    summaries = {}
    semantic = {}
    total = {fn: [0, 0] for fn in functions}

    def consume(fn, iface, m, rm, kind, gid, n, emitted, incoming):
        a, b = left.pop(), right.pop()
        common_row(a, b, fn, iface, m, rm, kind, gid, n, emitted, incoming)
        key = (fn, iface, str(m), str(rm), kind)
        summary = summaries.setdefault(key, [0, 0, 0, 0, 0])
        diff = [int(a[k] != b[k]) for k in ('frm_after', 'flags', 'errno')]
        changed = int(any(diff))
        summary[0] += 1
        for i, value in enumerate(diff, 1):
            summary[i] += value
        summary[4] += changed
        total[fn][0] += 1
        total[fn][1] += emitted
        if changed or all_groups:
            writer.writerow(dict(zip(('function', 'interface', 'lmul', 'frm', 'kind'), key),
                record_type='group', group_id=gid, n=n, emitted=emitted, incoming_vl=incoming,
                baseline_frm_after=a['frm_after'], candidate_frm_after=b['frm_after'],
                baseline_fflags=a['flags'], candidate_fflags=b['flags'],
                baseline_errno=a['errno'], candidate_errno=b['errno'],
                fflags_xor=int(a['flags']) ^ int(b['flags']), groups_compared=1,
                frm_after_differences=diff[0], fflags_differences=diff[1], errno_differences=diff[2],
                any_state_differences=changed, **provenance))

    for fn in functions:
        ns = None
        count = cover[fn]['corpus_samples']
        width = 4 if fn in ('expf', 'logf') else 8
        for iface in ('Nx', 'array'):
            for m in (1, 2):
                full = m * cover[fn]['vlenb'] // width
                scope_semantic = None
                for rm in range(5):
                    p = li = 0
                    while p < count:
                        n = c_length(fn, iface, p, li, count, full)
                        emitted = min(n, count - p)
                        incoming = (0, 1, full - 1, full)[li % 4] if iface == 'Nx' else 0
                        consume(fn, iface, m, rm, 'C', p, n, emitted, incoming)
                        p += emitted
                        li += 1
                    specs = []
                    if ns is None:
                        # 第一块 Nx/m1/rm0 的语义数可从连续语义组推导，不依赖可变源码。
                        while True:
                            a = left.peek()
                            if a is None or tuple(a[k] for k in ('function', 'interface', 'lmul', 'frm', 'kind')) != (fn, iface, str(m), str(rm), 'S'):
                                break
                            gid = len(specs)
                            incoming = (0, 1, full - 1, full)[gid % 4]
                            consume(fn, iface, m, rm, 'S', gid, full, full, incoming)
                            specs.append((gid, full))
                        if not specs or len(specs) % 8:
                            raise ValueError(f'{fn}: Nx 语义组数不是正的 8 倍')
                        ns = len(specs) // 8
                    else:
                        nsem = ns * 8 if iface == 'Nx' else ns + 1
                        for gid in range(nsem):
                            n = full if iface == 'Nx' else (1 if gid < ns else ns)
                            incoming = (0, 1, full - 1, full)[gid % 4] if iface == 'Nx' else 0
                            consume(fn, iface, m, rm, 'S', gid, n, n, incoming)
                            specs.append((gid, n))
                    if scope_semantic is None:
                        scope_semantic = specs
                    elif specs != scope_semantic:
                        raise ValueError(f'{fn}: 不同 frm 的语义组不一致')
                semantic[(fn, iface, str(m))] = scope_semantic
        if total[fn] != [cover[fn]['groups'], cover[fn]['emitted_lanes']]:
            raise ValueError(f'{fn}: group 总数或 emitted 总数与 coverage 不符')
    if left.peek() is not None or right.peek() is not None:
        raise ValueError('group 有额外记录或双方组数不同')
    return summaries, semantic


def valid_bits(text, digits, optional, label):
    if optional and text == '':
        return
    if re.fullmatch(r'[0-9a-f]{%d}' % digits, text) is None:
        raise ValueError(f'{label}: 位串宽度或格式非法')


def check_lanes(base, cand, functions, cover, semantic):
    left = iter(rows(base / FILES['lanes'], LANE_FIELDS, compressed=True))
    right = iter(rows(cand / FILES['lanes'], LANE_FIELDS, compressed=True))
    end = object()
    paired = itertools.zip_longest(left, right, fillvalue=end)
    count = 0
    differences = {}
    for fn in functions:
        digits = 8 if fn in ('expf', 'logf') else 16
        for iface in ('Nx', 'array'):
            for m in ('1', '2'):
                cs = ('C' + str(i) for i in range(cover[fn]['corpus_samples']))
                ss = (f'S{gid}:{i}' for gid, n in semantic[(fn, iface, m)] for i in range(n))
                for sample in itertools.chain(cs, ss):
                    first_inputs = None
                    for rm in range(5):
                        a, b = next(paired, (end, end))
                        if a is end or b is end:
                            raise ValueError('lane 提前结束或双方行数不同')
                        expected = (fn, iface, m, sample, str(rm))
                        for label, row in (('baseline', a), ('candidate', b)):
                            if tuple(row[k] for k in LANE_FIELDS[:5]) != expected:
                                raise ValueError(f'{label}: lane 样本无法对齐: {expected}')
                            valid_bits(row['x_bits'], digits, False, 'x_bits')
                            valid_bits(row['z_bits'], digits, fn != 'pow', 'z_bits')
                            if fn != 'pow' and row['z_bits'] != '':
                                raise ValueError('单目函数 z_bits 必须为空')
                            valid_bits(row['y_bits'], digits, False, 'y_bits')
                            valid_bits(row['ref_bits'], digits, sample.startswith('S'), 'ref_bits')
                            if sample.startswith('S') and row['ref_bits']:
                                raise ValueError('语义样本 ref_bits 必须为空')
                            if row['rne_ref_ulp'] and not UINT_RE.fullmatch(row['rne_ref_ulp']):
                                raise ValueError('rne_ref_ulp 格式非法')
                        inputs = tuple(a[k] for k in ('x_bits', 'z_bits', 'ref_bits'))
                        if inputs != tuple(b[k] for k in ('x_bits', 'z_bits', 'ref_bits')):
                            raise ValueError(f'{fn}/{iface}/m{m}/{sample}/frm{rm}: 输入或参考不一致')
                        if first_inputs is None:
                            first_inputs = inputs
                        elif inputs != first_inputs:
                            raise ValueError(f'{fn}/{iface}/m{m}/{sample}: 模式之间输入不一致')
                        key = (fn, iface, m, str(rm), sample[0])
                        differences[key] = differences.get(key, 0) + int(a['y_bits'] != b['y_bits'])
                        count += 1
    if next(paired, (end, end)) != (end, end):
        raise ValueError('lane 有额外记录或双方行数不同')
    return count, differences


def compare(base, cand, output, all_groups=False):
    base, cand, output = Path(base).resolve(), Path(cand).resolve(), Path(output).resolve()
    if base == cand or base not in output.parents or output.exists() or not output.parent.is_dir():
        raise ValueError('输入必须不同；输出必须为 baseline 分析目录下不存在的文件')
    for path in (base, cand, output):
        if any('build' in part.lower() for part in path.parts) or not any(DATE_RE.search(part) for part in path.parts):
            raise ValueError('输入和输出必须位于带日期时间且不含 build 路径的运行目录')
    a, fs, ah = manifest(base)
    b, fsb, bh = manifest(cand)
    if a['run_role'] != 'baseline' or b['run_role'] != 'candidate' or fs != fsb:
        raise ValueError('baseline/candidate 角色或函数集不匹配')
    if a['archive_sha256'] == b['archive_sha256']:
        raise ValueError('数学 archive SHA 相同，拒绝比较')
    if any(a[k] != b[k] for k in ('environment_sha256', 'observer_sha256')):
        raise ValueError('environment 或 observer SHA 不一致')
    ca, cb = coverage(base, fs), coverage(cand, fs)
    if ca != cb:
        raise ValueError('两个运行的 coverage 或 VLEN 不一致')
    script_path = Path(__file__).resolve()
    script_sha = sha(script_path)
    provenance = dict(baseline_complete_sha256=ah, candidate_complete_sha256=bh,
                      comparator_sha256=script_sha)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile('w', newline='', encoding='ascii', dir=output.parent,
                                         prefix='.' + output.name + '.', suffix='.partial', delete=False) as stream:
            temp_name = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=OUT_FIELDS)
            writer.writeheader()
            summaries, semantic = check_groups(base, cand, fs, ca, writer, all_groups, provenance)
            lane_rows, lane_differences = check_lanes(base, cand, fs, ca, semantic)
            for key, values in summaries.items():
                writer.writerow(dict(zip(('function', 'interface', 'lmul', 'frm', 'kind'), key),
                    record_type='summary', groups_compared=values[0], frm_after_differences=values[1],
                    fflags_differences=values[2], errno_differences=values[3],
                    any_state_differences=values[4], lane_bit_differences=lane_differences.get(key, 0),
                    **provenance))
        # 处理耗时较长，发布前再次校验完整输入和当前脚本。
        if manifest(base) != (a, fs, ah) or manifest(cand) != (b, fsb, bh) or sha(script_path) != script_sha:
            raise ValueError('比较过程中输入、清单或比较器已改变')
        # 使用硬链接以原子且不覆盖的方式发布；两者位于同一目录。
        os.link(temp_name, output)
        return sum(v[0] for v in summaries.values()), lane_rows, sum(v[4] for v in summaries.values())
    finally:
        if temp_name is not None and temp_name.exists():
            actual = temp_name.resolve()
            if actual.parent != output.parent or not actual.name.startswith('.' + output.name + '.') or not actual.name.endswith('.partial'):
                raise RuntimeError('拒绝清理不属于本次比较的临时文件')
            actual.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='baseline 的已完成分析目录')
    parser.add_argument('--candidate', type=Path, required=True, help='candidate 的已完成分析目录')
    parser.add_argument('--output', type=Path, required=True, help='baseline 分析目录下的新 CSV')
    parser.add_argument('--all-groups', action='store_true', help='除差异组外也输出未改变的组')
    args = parser.parse_args(argv)
    try:
        groups, lanes, changed = compare(args.baseline, args.candidate, args.output, args.all_groups)
    except (OSError, ValueError, KeyError, UnicodeError, csv.Error, EOFError) as exc:
        parser.exit(2, f'组级比较停止：{exc}\n')
    print(f'组级比较完成：groups={groups} lane_rows={lanes} state_changed_groups={changed}；不构成 sticky/ABI 验收')
    return 0


if __name__ == '__main__':
    sys.exit(main())
