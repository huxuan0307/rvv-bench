#!/usr/bin/env python3
"""从完整或截断日志回收已打印的CSV行，不判定测试通过、不运行模拟器。

python3 -B single/libmvec/recover.py --manifest BUILD/manifest.json \
    --case perf-log --log RUN/cases/perf-log/stdout.log --output NEW_DIRECTORY

退出0仅表示解析/导出操作完成（可能回收0条），绝不代表目标测试通过。
运行目录应使用collect.py，自动保留严格状态并导出独立回收数据。
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

import compact_csv
import runner_common as common
from collect import RECOVERED_FIELDS


def recover_logs(manifest, case_name, logs, output):
    # 离线解析不要求BIN还在；只核对manifest配置，不替代运行身份验收。
    loaded = common.load_manifest(manifest, check_files=False)
    cases = [c for c in loaded['cases'] if c['name'] == case_name]
    if len(cases) != 1 or cases[0]['expected'].get('wire_format') != 'csv2':
        raise ValueError('请选择manifest中的csv2性能case')
    case = cases[0]
    records, sources = [], []
    for path in logs:
        path = Path(path).resolve()
        data = path.read_bytes()
        result = compact_csv.recover(data.decode('utf-8', errors='replace'), case)
        for row in result.pop('records'):
            records.append({**row, 'raw': row['_raw'], 'source': str(path),
                            'run_status': 'not-audited', 'case_status': 'not-audited'})
        sources.append({'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(),
                        'size': len(data), **result})
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    document = {'schema_version': 1, 'kind': 'diagnostic-recovery',
                'measurement_valid': False, 'run_status': 'not-audited',
                'case': case_name, 'manifest': str(Path(manifest).resolve()),
                'manifest_sha256': loaded['sha256'], 'logical_records': len(records),
                'note': '解析成功不表示运行通过；不作为有效性能汇总。',
                'sources': sources, 'records': records}
    common.write_json(output / 'recovery.json', document)
    with (output / 'recovered-samples.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=RECOVERED_FIELDS,
                                extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(records)
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--log', action='append', type=Path, required=True,
                        help='可重复；每个文件独立解码，不拼接残行或共享上下文')
    parser.add_argument('--output', type=Path, required=True,
                        help='必须不存在的新目录')
    args = parser.parse_args(argv)
    try:
        doc = recover_logs(args.manifest, args.case, args.log, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'recovery error: {exc}', file=sys.stderr)
        return 1
    print(json.dumps({'output': str(args.output.resolve()),
                      'logical_records': doc['logical_records'],
                      'run_status': 'not-audited', 'measurement_valid': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
