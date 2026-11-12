#!/usr/bin/env python3
"""从完整合并原始 CSV 生成十列 steady7 周期中位数表；不启动仿真。"""
import argparse
import csv
from pathlib import Path
import statistics

try:
    from . import analyze as a
except ImportError:
    import analyze as a

FIELDS = ('function', 'n', 'k', 'mode', 'scalar_strict', 'scalar_fast',
          'veclibm', 'opt_nofrm', 'libm_vec_nx', 'libm_vec_array')
SOURCES = {('old-original', 'main-nx'): 'veclibm',
           ('old-nofrm', 'main-nx'): 'opt_nofrm',
           ('new', 'main-nx'): 'libm_vec_nx',
           ('new', 'aux-array'): 'libm_vec_array'}
FUNCTION_ORDER = ('log', 'exp', 'sin', 'cos', 'pow', 'logf', 'expf')


def summarize(rows):
    """严格完整 full 四来源；不丢弃失败、重复、额外或 replay 样本。"""
    buckets = {}
    for row in rows:
        key = row.get('family'), row.get('profile')
        a.require(key in SOURCES, f'未知 family/profile: {key}')
        a.require(row.get('run_role') == 'main', '仅接受 main 原始样本')
        mode = row.get('mode', '')
        interface = ('scalar' if mode in a.MODES[:2] else
                     'array' if key[1] == 'aux-array' and mode in a.MODES[4:6] else
                     'Nx' if mode in a.MODES[2:6] else 'control')
        a.require(row.get('interface') == interface, f'interface不符: {key}/{mode}')
        buckets.setdefault(key, []).append(row)
    a.require(set(buckets) == set(SOURCES), '必须完整四组 family/profile')
    index = {}
    for key, source_rows in buckets.items():
        validated = a.validate_samples(source_rows, a.FUNCTIONS)
        values = {}
        for row in validated:
            if row['phase'] == 'steady':
                point = row['function'], row['n'], row['k'], row['mode']
                values.setdefault(point, []).append(row['cycles'])
        index[SOURCES[key]] = {point: statistics.median(cycles)
                               for point, cycles in values.items()}
    result = []
    for fn in FUNCTION_ORDER:
        for n in (16, 128, 1024):
            for k in ((1, 4, 16) if fn in ('log', 'exp') else (1, 4)):
                for mode in a.MODES[2:6]:
                    point = fn, n, k, mode
                    nx = index['libm_vec_nx']
                    item = dict(function=fn, n=n, k=k, mode=mode,
                                scalar_strict=nx[fn, n, k, 'scalar_strict'],
                                scalar_fast=nx[fn, n, k, 'scalar_fast'])
                    item.update({name: index[name][point] for name in SOURCES.values()})
                    result.append(item)
    return result


def generate(input_path, output_path):
    """先完整验证，再独占创建输出；拒绝覆盖任何已有文件。"""
    input_path, output_path = Path(input_path), Path(output_path)
    with input_path.open(newline='') as stream:
        rows = summarize(list(csv.DictReader(stream)))
    with output_path.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='合并原始 cycles-all-raw.csv')
    parser.add_argument('--output', required=True, type=Path, help='新 cycles-summary.csv 文件')
    args = parser.parse_args()
    try:
        count = generate(args.input, args.output)
    except (a.Rejected, OSError, ValueError) as exc:
        parser.exit(2, f'汇总拒绝：{exc}\n')
    print(f'已生成 {count} 行：{args.output}')


if __name__ == '__main__':
    main()
