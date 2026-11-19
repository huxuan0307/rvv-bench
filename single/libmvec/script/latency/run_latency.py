#!/usr/bin/env python3
"""运行历史 LAT 校准镜像；与配对 protocol 2 的计时证据独立。"""

import argparse
from collections import Counter
from pathlib import Path
import re
import sys

from paired_parse import common, decimal, fields, require
from run_paired import audit_diagnostics, audit_rtl, launch_rtl, phase, rtl_arguments

LAT_RECORD = re.compile(r'^[ \t]*LAT(?:_[A-Za-z0-9_]*)?\b')
HEX64 = re.compile(r'[0-9a-f]{1,16}\Z')


def audit_lat(stdout, stderr):
    """核对旧 LAT 自报矩阵、窗阶段及 oracle 汇总，不推定配对身份。"""
    require(not any(LAT_RECORD.match(line) for line in common.ANSI.sub('', stderr).split('\n')),
            'stderr 中出现旧 LAT 记录')
    counts = Counter()
    checks, chains, points, end = {}, set(), set(), None
    header = None
    lines = common.ANSI.sub('', stdout).split('\n')
    for i, raw in enumerate(lines):
        line = raw.removesuffix('\r')
        if not LAT_RECORD.match(line):
            continue
        require(i < len(lines) - 1 and end is None, '未终止的 LAT 行或 footer 后还有记录')
        kind, *words = line.split(',')
        if kind == 'LAT_HEADER':
            require(header is None, '重复 LAT_HEADER')
            h = fields(words, 'protocol', 'vlenb', 'vlen', 'rounds', 'prelude', 'serial',
                       'nop_block', 'nop_block64', 'k', 'groups', 'entries')
            require((h['protocol'], h['vlenb'], h['vlen']) == ('1', '16', '128'), '不是旧 LAT v1')
            rounds = decimal(h['rounds'], 'rounds', 1, 4096)
            entries = h['entries'].split(';')
            ks = [decimal(k, 'K', 0, 65535) for k in h['k'].split(';')]
            require(h['entries'] != 'none' and all(common.case_name(e) for e in entries)
                    and len(entries) == len(set(entries)) and len(ks) == len(set(ks))
                    and len(entries) * len(ks) * rounds <= 4096, '旧 LAT 矩阵无效')
            points = {(e, k) for e in entries for k in ks}
            header = line.partition(',')[2]
            continue
        require(header is not None, '记录先于 LAT_HEADER')
        if kind == 'LAT':
            row = fields(words, 'entry', 'K', 'phase', 'calls', 'begin', 'cycles', 'checksum')
            key = (row['entry'], decimal(row['K'], 'K'))
            require(key in points and counts[key] < rounds and row['phase'] == phase(counts[key])
                    and HEX64.fullmatch(row['checksum']), '旧 LAT 窗口、阶段或 checksum 不符')
            for name in ('calls', 'begin', 'cycles'):
                decimal(row[name], name)
            counts[key] += 1
        elif kind == 'LAT_CHAIN':
            row = fields(words, 'entry', 'K', 'chain_checksum', 'stable')
            key = (row['entry'], decimal(row['K'], 'K'))
            require(key in points and key not in chains and row['stable'] in ('yes', 'no')
                    and HEX64.fullmatch(row['chain_checksum']), '旧 LAT 链记录不符')
            chains.add(key)
        elif kind == 'LAT_CHECK':
            row = fields(words, 'entry', 'checks', 'failed')
            key = row['entry']
            require(key in entries and key not in checks and row['failed'] == '0',
                    '旧 LAT 检查失败、重复或不匹配')
            checks[key] = decimal(row['checks'], 'checks')
        elif kind == 'LAT_END':
            end = fields(words, 'status', 'checks', 'failed')
        else:
            raise ValueError(f'不支持的旧 LAT 记录：{kind}')
    require(header is not None and end == {'status': 'ok', 'checks': str(sum(checks.values())),
                                          'failed': '0'}, '缺少有效的 LAT_HEADER 或 LAT_END')
    require(checks.keys() == set(entries) and points == chains and counts.keys() == points
            and all(count == rounds for count in counts.values()), '旧 LAT 记录覆盖不全')
    audit_diagnostics(stdout, stderr, 'LAT_END,')
    return {'header': header, 'good_trap': True, 'end_status': 'ok', 'checks': sum(checks.values()),
            'failed': 0, 'lat_windows': sum(counts.values()), 'lat_checks': len(checks), 'status': 'ok'}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bin', required=True, type=Path, help='历史构建器生成的 LAT 镜像')
    rtl_arguments(ap)
    args = ap.parse_args(argv)
    try:
        image = args.bin.expanduser().resolve(strict=True)
        run = launch_rtl(args, image, {'sha256': common.sha256(image)})
        audit_rtl(run, 'libmvec-lat')
        summary = audit_lat(*((run / name).read_text(encoding='utf-8', errors='replace')
                              for name in ('stdout.log', 'stderr.log')))
        text = ''.join(f'{key}={value}\n' for key, value in summary.items())
        common.write_text(run / 'run-report.txt', text)
        print(text, end='')
        return 0
    except (OSError, ValueError) as exc:
        print(f'LAT_REJECTED {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
