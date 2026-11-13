#!/usr/bin/env python3
"""严格合并四个完整 gsim 性能批次；不启动模拟器，不修改输入。"""
import argparse
import csv
import json
from pathlib import Path
import sys

import compare_campaign as cmp
from analysis_newmath import analyze, cycles_summary

LABELS = (('old-original', 'main-nx', 'nx'), ('old-nofrm', 'main-nx', 'nx'),
          ('new', 'main-nx', 'nx'), ('new', 'aux-array', 'array'))


def check(ok, message):
    if not ok:
        raise ValueError(message)


def digest_map(obj, name):
    values = cmp.hashes(obj)
    check(bool(values) and all(analyze.digest(x) for x in values.values()), f'{name}: SHA 缺失')
    return values


def metadata(docs, manifests):
    plans, vlens, tools, evidence = [], [], [], []
    for (family, profile, interface), doc, manifest in zip(LABELS, docs, manifests):
        ev = cmp.interface_evidence(doc, manifest)
        check(ev['value'] == interface, f'{family}/{profile}: direct interface 不符')
        evidence.append(ev)
        selected = {c['name'] for c in doc['cases']}
        cases = [c for c in manifest['cases'] if c['name'] in selected]
        check(len(cases) == len(selected) == 7, '必须恰好七个 performance case')
        p = {}
        for case in cases:
            e = case['expected']
            fn = e['function']
            check(fn not in p and e.get('profile', 'full') == 'full', 'plan function/profile 错误')
            p[fn] = {k: e.get(k) for k in ('function', 'profile', 'modes', 'lengths', 'batches', 'phases', 'calibration_modes', 'order_policy')}
            for mode in ('auto_m1', 'auto_m2'):
                calls = case.get('observed_vector_calls', {}).get(mode, [])
                check(bool(calls) and all(s.startswith(('_ZGV1Nx', '_ZGV2Nx')) for s in calls), f'{fn}/{mode}: 缺少 Nx 调用证据')
        check(set(p) == set(analyze.FUNCTIONS), 'plan 函数集合错误')
        plans.append(p)
        vlens.append({c['config']['function']: c['vlen_bits'] for c in doc['cases']})
        tools.append(digest_map({k: doc['tools'][k] for k in ('gsim', 'reference', 'dram_config')}, 'gsim/reference/DRAM'))
    check(all(p == plans[0] for p in plans), '四个 plan 不一致')
    check(all(v == vlens[0] for v in vlens) and len(vlens[0]) == 7 and all(vlens[0].values()), 'VLEN 不一致或缺失')
    check(all(t == tools[0] for t in tools), 'gsim/reference/DRAM SHA 不一致')
    archives = [m.get('math_archive', {}).get('sha256') for m in manifests]
    check(all(analyze.digest(x) for x in archives), 'math archive SHA 缺失')
    check(archives[0] != archives[1], '旧 original/nofrm math archive 必须不同')
    check(archives[2] == archives[3], '新 Nx/array math archive 必须相同')
    # 源脚本不同允许；实际编译 harness、工具、头文件和非数学库必须相同。
    harness_names = {'kernel-loop.c', 'kernel-direct.c', 'kernel-array.c', 'kernel-calibration.c', 'kernel.h', 'perf.c', 'entry_rne.c', 'tls_anchor.c', 'av_runtime.c', 'av_runtime.h', 'veclibm-runtime.c', 'tls.ld', 'link.ld'}
    identities = []
    for m in manifests:
        harness = {k: v for k, v in m['sources'].items() if Path(k).name in harness_names}
        check(set(Path(k).name for k in harness) == harness_names, 'harness 源码记录不完整')
        identities.append({'harness': digest_map(harness, 'harness'),
                           'build_tools': digest_map(m['toolchain']['tools'], 'build tools'),
                           'headers': digest_map(m['toolchain']['headers'], 'headers'),
                           'libraries': digest_map(m['toolchain']['libraries'], 'libraries'),
                           'support_archives': digest_map(m['support_archives'], 'support archives'),
                           'lto': m.get('lto')})
    check(all(item == identities[0] for item in identities),
          '四来源的 harness、编译工具、headers、非数学库或 LTO 身份不同')
    return {'plans': plans[0], 'vlen_bits': vlens[0], 'runtime_sha256': tools[0],
            'interface_evidence': evidence, 'math_archive_sha256': archives,
            'build_identities': identities,
            'sources': [m['sources'] for m in manifests]}


def source_rows(doc, family, profile):
    result = []
    for case in doc['cases']:
        # Collector 将两种记录分别保存；保留各列表原顺序及所有字段。
        for collection in ('sample_records', 'calibration_records'):
            for record in case[collection]:
                mode = record['mode']
                interface = ('scalar' if mode in analyze.MODES[:2] else
                             'array' if profile == 'aux-array' and mode in analyze.MODES[4:6] else
                             'Nx' if mode in analyze.MODES[2:6] else 'control')
                additions = dict(family=family, profile=profile, interface=interface, run_role='main',
                                 suite=case['suite'], run_status=doc['state'], case_status=case['status'],
                                 measurement_valid=case['measurement_valid'])
                check(not any(k in record and record[k] != v for k, v in additions.items()), '记录与来源元数据冲突')
                result.append(dict(record, **additions))
    analyze.validate_samples(result, analyze.FUNCTIONS)
    return result


def merge(docs, manifests):
    info = metadata(docs, manifests)
    rows = []
    for (family, profile, _), doc in zip(LABELS, docs):
        rows.extend(source_rows(doc, family, profile))
    check(len(rows) == 17280, 'raw 行数错误')
    summary = cycles_summary.summarize(rows)
    check(len(summary) == 192, 'summary 行数错误')
    return rows, summary, info


def write_csv(path, rows, fields):
    with path.open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('old-original', 'old-nofrm', 'new-nx', 'new-array', 'output'):
        parser.add_argument('--' + arg, required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        paths = [args.old_original, args.old_nofrm, args.new_nx, args.new_array]
        out = args.output.resolve()
        check(not out.exists(), '输出目录必须不存在')
        for path in paths:
            run = path.resolve()
            check(out != run and not out.is_relative_to(run) and not run.is_relative_to(out), '输出与输入目录不得包含彼此')
        loaded = [cmp.load_audited(path) for path in paths]
        docs, manifests = map(list, zip(*loaded))
        rows, summary, info = merge(docs, manifests)
        fields = list(dict.fromkeys(k for row in rows for k in row))
        out.mkdir(parents=True)
        write_csv(out / 'cycles-all-raw.csv', rows, fields)
        write_csv(out / 'cycles-summary.csv', summary, cycles_summary.FIELDS)
        (out / 'provenance.json').write_text(json.dumps({'runs': [d['run'] for d in docs], 'comparability': info}, indent=2, ensure_ascii=False) + '\n')
        print(f'已生成 {len(rows)} raw / {len(summary)} summary 行：{out}')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'merge_full_campaign: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
