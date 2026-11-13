#!/usr/bin/env python3
"""Compare strictly audited complete gsim performance runs without rerunning simulation.

The collector is invoked in memory; input reports are not rewritten. Output JSON
retains all audited SAMPLE and CALIBRATION records, including first and warmup.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics
import sys
from unittest.mock import patch

import collect


def require_complete(doc):
    if not (doc.get('runner_state') == 'terminated' and doc.get('complete') is True
            and doc.get('all_passed') is True and doc.get('state') == 'passed'
            and doc.get('runner_state_record', {}).get('returncode') == 0):
        raise ValueError(f"run not strictly complete: {doc.get('run')}: {doc.get('state')}")
    if not doc.get('cases') or any(c.get('suite') != 'performance' or c.get('status') != 'passed' for c in doc['cases']):
        raise ValueError('all selected cases must be passed performance cases')


def load_audited(path):
    # 避免 collect.collect 默认覆盖输入 reports；审计逻辑保持原样。
    with patch.object(collect, 'save_reports', lambda *_: None):
        doc = collect.collect(path)
    require_complete(doc)
    if doc['mode'] != 'gsim':
        raise ValueError(f"{path}: mode={doc['mode']}; NEMU is not gsim performance")
    if any(not c.get('measurement_valid') for c in doc['cases']):
        raise ValueError('invalid gsim measurement')
    manifest = json.loads((Path(doc['run']) / doc['manifest']['snapshot']).read_text())
    return doc, manifest


def hashes(obj):
    return {k: v.get('sha256') if isinstance(v, dict) else None for k, v in sorted(obj.items())}


def interface_evidence(doc, manifest):
    declared = manifest.get('direct_interface')
    if declared in ('nx', 'array'):
        return {'value': declared, 'origin': 'manifest'}
    if declared is not None:
        raise ValueError('unrecognized direct_interface')
    # 旧快照必须同时提供匹配哈希的源码、成功的编译命令和反汇编调用记录。
    source = manifest.get('sources', {}).get('single/libmvec/kernel-direct.c', {})
    path = Path(source.get('path', ''))
    if not path.is_file() or collect.common.sha256(path) != source.get('sha256'):
        raise ValueError('legacy interface: source missing or SHA mismatch')
    text = path.read_text()
    if not all(s in text for s in ('SETVLMAX()', 'VECTOR_SYMBOL(a', 'VLOAD(', 'VSTORE(')):
        raise ValueError('legacy interface: source is not demonstrably Nx')
    command_path = Path(doc['manifest']['path']).parent / 'commands.json'
    commands = json.loads(command_path.read_text())
    by_label = {c.get('label'): c for c in commands}
    selected = {c['name'] for c in doc['cases']}
    evidence = []
    for case in manifest['cases']:
        if case['name'] not in selected:
            continue
        for mode in case['expected']['modes']:
            if not mode.startswith('direct_'):
                continue
            entry = by_label.get(case['name'] + '/' + mode, {})
            cmd = entry.get('command', [])
            symbols = [x.split('=', 1)[1] for x in cmd if x.startswith('-DVECTOR_SYMBOL=')]
            calls = case.get('observed_vector_calls', {}).get(mode, [])
            disassembly = by_label.get(case['name'] + '/' + mode + '.disassembly', {})
            if (entry.get('returncode') != 0 or disassembly.get('returncode') != 0 or len(symbols) != 1 or
                    not symbols[0].startswith(('_ZGV1Nx', '_ZGV2Nx')) or symbols[0] not in calls or
                    str(path) not in cmd or not any(x.startswith('-DSETVLMAX=') for x in cmd) or '-c' not in cmd):
                raise ValueError(f'legacy interface: insufficient direct compile/disassembly evidence: {case["name"]}/{mode}')
            evidence.append({'case': case['name'], 'mode': mode, 'symbol': symbols[0]})
    if not evidence:
        raise ValueError('legacy interface: no direct evidence')
    return {'value': 'nx', 'origin': 'inferred', 'source_path': str(path),
            'source_sha256': source['sha256'], 'commands_path': str(command_path),
            'commands_sha256': collect.common.sha256(command_path), 'direct_calls': evidence,
            'limitation': 'commands and recorded disassembly calls are build provenance, not immutable run artifacts'}


def compare_metadata(a, b, ma, mb):
    ia, ib = interface_evidence(a, ma), interface_evidence(b, mb)
    if ia['value'] != ib['value']:
        raise ValueError('direct_interface differs (nx/array cannot be mixed)')
    def plans(doc, manifest):
        selected = {c['name'] for c in doc['cases']}
        cases = [c for c in manifest['cases'] if c['name'] in selected]
        functions = {'log', 'exp', 'sin', 'cos', 'pow', 'logf', 'expf'}
        if len(selected) != 7 or len(cases) != 7:
            raise ValueError('comparison requires all seven full performance cases')
        result = {}
        for c in cases:
            p = c['expected']
            f = p['function']
            if f in result or p.get('profile', 'full') != 'full' or p.get('phases') != {'first': 1, 'warmup': 2, 'steady': 7} or 'scalar_fast' not in p['modes']:
                raise ValueError(f'{f}: expected unique full performance plan with steady7 and scalar_fast')
            result[f] = {k: p.get(k) for k in ('function', 'profile', 'modes', 'lengths', 'batches', 'phases', 'calibration_modes', 'order_policy')}
        if set(result) != functions:
            raise ValueError('comparison requires exactly the seven math functions')
        return result
    pa, pb = plans(a, ma), plans(b, mb)
    if pa != pb:
        raise ValueError('selected function/size/mode/schedule plans differ')
    va = {c['config']['function']: c['vlen_bits'] for c in a['cases']}
    vb = {c['config']['function']: c['vlen_bits'] for c in b['cases']}
    if va != vb:
        raise ValueError('VLEN differs between runs')
    checks = {}
    def source_parts(m):
        all_sources = hashes(m['sources'])
        harness_names = {'kernel-loop.c', 'kernel-direct.c', 'kernel-array.c', 'kernel-calibration.c',
                         'kernel.h', 'perf.c', 'entry_rne.c', 'tls_anchor.c', 'av_runtime.c',
                         'av_runtime.h', 'veclibm-runtime.c', 'tls.ld', 'link.ld'}
        return ({k: v for k, v in all_sources.items() if Path(k).name in harness_names},
                {k: v for k, v in all_sources.items() if Path(k).name not in harness_names})
    ah, ao = source_parts(ma)
    bh, bo = source_parts(mb)
    for label, x, y in [('emulator/reference/config', hashes(a['tools']), hashes(b['tools'])),
                         ('build_tools', hashes(ma['toolchain']['tools']), hashes(mb['toolchain']['tools'])),
                         ('source_harness', ah, bh),
                         ('other_build_and_tool_sources', ao, bo),
                         ('headers', hashes(ma['toolchain'].get('headers', {})), hashes(mb['toolchain'].get('headers', {}))),
                         ('libraries', hashes(ma['toolchain'].get('libraries', {})), hashes(mb['toolchain'].get('libraries', {})))]:
        checks[label] = {'baseline': x, 'candidate': y, 'same': x == y and bool(x) and all(x.values())}
    if not checks['emulator/reference/config']['same']:
        raise ValueError('emulator/reference/config SHA differs or missing')
    return {'direct_interface': ia['value'], 'interface_evidence': {'baseline': ia, 'candidate': ib},
            'plans': pa, 'vlen_bits': va, 'identities': checks,
            'note': 'Library/archive differences are allowed. Harness, compiler, headers, and build/tool source changes are reported separately; differences may affect comparability.'}


def group_samples(doc):
    groups = {}
    raw = []
    for case in doc['cases']:
        raw.append({'case': case['name'], 'samples': case['sample_records'], 'calibrations': case['calibration_records']})
        for r in case['sample_records']:
            if r['phase'] == 'steady':
                key = (r['function'], r['n'], r['k'], r['mode'])
                groups.setdefault(key, []).append(r['cycles'])
    if any(len(v) != 7 or any(type(x) is not int or x <= 0 for x in v) for v in groups.values()):
        raise ValueError('steady groups must have exactly seven positive integer cycles')
    return {k: statistics.median(v) for k, v in groups.items()}, raw


def calculate(a, b):
    aa, ar = group_samples(a)
    bb, br = group_samples(b)
    if not aa or aa.keys() != bb.keys():
        raise ValueError('steady sample groups differ or are empty')
    rows = []
    for key in sorted(aa):
        f, n, k, mode = key
        scalar = (f, n, k, 'scalar_fast')
        if scalar not in bb:
            raise ValueError(f'missing candidate scalar_fast: {key}')
        rows.append(dict(function=f, n=n, k=k, mode=mode, baseline_cycles=aa[key], candidate_cycles=bb[key],
                         baseline_over_candidate=aa[key] / bb[key], candidate_over_scalar_fast_speedup=bb[scalar] / bb[key]))
    summaries = []
    for f, mode in sorted({(r['function'], r['mode']) for r in rows}):
        selected = [r for r in rows if (r['function'], r['mode']) == (f, mode)]
        ratios = [r['baseline_over_candidate'] for r in selected]
        speeds = [r['candidate_over_scalar_fast_speedup'] for r in selected]
        summaries.append(dict(function=f, mode=mode, count=len(ratios), regression_count=sum(x < 1 for x in ratios),
                              ratio_geomean=math.exp(statistics.mean(map(math.log, ratios))), ratio_min=min(ratios), ratio_max=max(ratios),
                              candidate_speedup_geomean=math.exp(statistics.mean(map(math.log, speeds))), candidate_speedup_min=min(speeds), candidate_speedup_max=max(speeds)))
    return rows, summaries, {'baseline': ar, 'candidate': br}


def output_csv(path, rows):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def self_test():
    import copy
    import unittest

    class Tests(unittest.TestCase):
        def test_real_collector_schema_and_partial(self):
            path = Path(__file__).parent / 'build/sevenwave-full-baseline-perf-nemu/reports/results.json'
            if not path.is_file():
                self.skipTest('real NEMU report not available')
            doc = json.loads(path.read_text())
            require_complete(doc)
            medians, raw = group_samples(doc)
            self.assertTrue(medians)
            self.assertTrue(raw[0]['samples'])
            self.assertEqual({r['phase'] for r in raw[0]['samples']}, {'first', 'warmup', 'steady'})
            partial = copy.copy(doc)
            partial['runner_state'] = 'in_progress'
            with self.assertRaises(ValueError):
                require_complete(partial)
            with self.assertRaisesRegex(ValueError, 'NEMU'):
                load_audited(path.parents[1])

        def test_seven_function_comparison_required(self):
            path = Path(__file__).parent / 'build/sevenwave-full-baseline-perf-nemu/reports/results.json'
            if not path.is_file():
                self.skipTest('real build not available')
            doc = json.loads(path.read_text())
            manifest = json.loads((path.parents[1] / doc['manifest']['snapshot']).read_text())
            compare_metadata(doc, doc, manifest, manifest)
            doc['cases'] = doc['cases'][:1]
            with self.assertRaisesRegex(ValueError, 'seven'):
                compare_metadata(doc, doc, manifest, manifest)

        def test_legacy_interface_evidence(self):
            path = Path(__file__).parent / 'build/sevenwave-full-baseline-perf-nemu/reports/results.json'
            if not path.is_file():
                self.skipTest('real build not available')
            doc = json.loads(path.read_text())
            manifest = json.loads((path.parents[1] / doc['manifest']['snapshot']).read_text())
            manifest.pop('direct_interface')
            inferred = interface_evidence(doc, manifest)
            self.assertEqual((inferred['value'], inferred['origin']), ('nx', 'inferred'))
            manifest['cases'][0]['observed_vector_calls']['direct_m1'] = []
            with self.assertRaisesRegex(ValueError, 'insufficient'):
                interface_evidence(doc, manifest)

        def test_ratio_and_regression(self):
            def doc(scalar, vector):
                return {'cases': [{'name': 'p', 'sample_records': [
                    {'function': 'log', 'n': 16, 'k': 1, 'mode': mode, 'phase': 'steady', 'cycles': x}
                    for mode, x in [('scalar_fast', scalar), ('direct_m1', vector)] for _ in range(7)],
                    'calibration_records': []}]}
            rows, summary, _ = calculate(doc(100, 40), doc(80, 50))
            vector = next(r for r in rows if r['mode'] == 'direct_m1')
            self.assertEqual(vector['baseline_over_candidate'], .8)
            self.assertEqual(vector['candidate_over_scalar_fast_speedup'], 1.6)
            self.assertEqual(next(r for r in summary if r['mode'] == 'direct_m1')['regression_count'], 1)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline')
    p.add_argument('--candidate')
    p.add_argument('--output')
    p.add_argument('--self-test', action='store_true')
    args = p.parse_args(argv)
    if args.self_test:
        return self_test()
    if not all((args.baseline, args.candidate, args.output)):
        p.error('--baseline, --candidate and --output are required')
    try:
        a, ma = load_audited(args.baseline)
        b, mb = load_audited(args.candidate)
        info = compare_metadata(a, b, ma, mb)
        rows, summaries, raw = calculate(a, b)
        out = Path(args.output).resolve()
        for doc in (a, b):
            if out == Path(doc['run']) or Path(doc['run']) in out.parents:
                raise ValueError('output cannot be inside either input run')
        out.mkdir(parents=True, exist_ok=True)
        output_csv(out / 'comparison.csv', rows)
        output_csv(out / 'summary.csv', summaries)
        data = {'schema_version': 1, 'emulator': 'gsim', 'baseline': a['run'], 'candidate': b['run'],
                'comparability': info, 'comparison': rows, 'summary': summaries, 'raw_records': raw,
                'definitions': {'baseline_over_candidate': 'baseline cycles / candidate cycles; below 1 is regression',
                                'candidate_over_scalar_fast_speedup': 'candidate scalar_fast cycles / candidate mode cycles',
                                'aggregation': 'unweighted geometric mean over (n,k), steady seven-round median'}}
        (out / 'comparison.json').write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f'compare_campaign: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
