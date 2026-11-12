#!/usr/bin/env python3
"""严格离线分析；保留 newlib-full v1 完整矩阵与显式证据绑定语义。"""
import argparse
import csv
import hashlib
import html
import itertools
import json
import math
from pathlib import Path
import statistics as st

CAMPAIGN = '20261112-0126-newlib-full'
FAMILIES = ('old-original', 'old-nofrm', 'new')
FUNCTIONS = ('sin', 'cos', 'log', 'exp', 'pow', 'logf', 'expf')
MODES = ('scalar_strict', 'scalar_fast', 'auto_m1', 'auto_m2', 'direct_m1', 'direct_m2', 'rdcycle', 'empty', 'copy')
IDENTITIES = ('fixture_sha256', 'scalar_objects', 'auto_objects', 'direct_objects', 'gsim_sha256', 'reference_sha256', 'dram_config_sha256')

class Rejected(ValueError):
    pass

def require(ok, message):
    if not ok:
        raise Rejected(message)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)

def digest_tree(value):
    return digest(value) or (isinstance(value, dict) and bool(value) and all(digest_tree(x) for x in value.values()))

def read_bound(spec, base, root):
    require(isinstance(spec, dict) and digest(spec.get('sha256')), '文件必须提供SHA256')
    path = (base / spec['path']).resolve()
    require(path.is_relative_to(root), f'拒绝任务外输入: {path}')
    require(path.is_file() and sha(path) == spec['sha256'], f'文件hash不符: {path}')
    return path

def matrix(functions):
    for fn in functions:
        for n in (16, 128, 1024):
            for k in ((1, 4, 16) if fn in ('log', 'exp') else (1, 4)):
                for mode, r in itertools.product(MODES, range(10)):
                    yield fn, mode, n, k, r

def phase(r):
    return ('first', 0) if r == 0 else (('warmup', r - 1) if r < 3 else ('steady', r - 3))

def validate_samples(rows, functions):
    expected = set(matrix(functions))
    observed = {}
    for row in rows:
        try:
            fn, mode = row['function'], row['mode']
            n, k, r = (int(row[x]) for x in ('n', 'k', 'r'))
            key = fn, mode, n, k, r
            require(key in expected, f'额外matrix项: {key}')
            require(key not in observed, f'重复matrix项: {key}')
            require((row['phase'], int(row['round'])) == phase(r), f'phase/round错误: {key}')
            require(int(row['order']) == (MODES.index(mode) - r) % 9, f'rotate9错误: {key}')
            require(row['record_type'] == ('sample' if mode in MODES[:6] else 'calibration'), f'record_type错误: {key}')
            require(row['run_status'] == row['case_status'] == 'passed', f'失败sample: {key}')
            require(str(row['measurement_valid']).lower() == 'true', f'无效sample: {key}')
            cycles, begin, end = (int(row[x]) for x in ('cycles', 'cycle_begin', 'cycle_end'))
            u64 = (1 << 64) - 1
            require(0 < cycles <= u64 and 0 <= begin <= u64 and 0 <= end <= u64 and ((end - begin) & u64) == cycles, f'周期字段不一致/越界: {key}')
            require(row['suite'] == 'performance' and row['case'] == 'perf-' + fn, f'case/suite错误: {key}')
            item = dict(row)
            item.update(n=n, k=k, r=r, cycles=cycles, round=int(row['round']), order=int(row['order']))
            observed[key] = item
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, Rejected):
                raise
            raise Rejected(f'CSV字段错误: {exc}') from exc
    require(set(observed) == expected, f'不接受partial: 缺少{len(expected-set(observed))}项')
    return list(observed.values())

def validate_gates(gate):
    require(gate.get('campaign_id') == CAMPAIGN and gate.get('complete') is True, '门禁不完整/身份不符')
    found = {}
    for row in gate.get('gates', []):
        key = row.get('family'), row.get('function')
        require(key in set(itertools.product(FAMILIES, FUNCTIONS)) and key not in found, '门禁重复/未知项')
        require(digest(row.get('elf_sha256')) and digest(row.get('bin_sha256')), '门禁ELF/BIN hash缺失')
        for field in ('nemu_passed', 'rva23_passed'):
            require(row.get(field) is True, f'门禁未通过: {key} {field}')
        if key[0] != 'new':
            require(isinstance(row.get('baseline_validation'), dict) and row['baseline_validation'].get('passed') is True and digest(row['baseline_validation'].get('evidence_sha256')), '旧库需独立原ABI/正确性门禁证据，不伪造new PCS测试')
        if key[0] == 'new':
            for field in ('abi_passed', 'oracle_passed'):
                require(row.get(field) is True, f'新核门禁未通过: {key} {field}')
            require(row.get('implementation') == 'shared-register-core', '新实现必须shared-register-core')
            require(row.get('stack_bridge') is False, '主Nx不得stack bridge')
        found[key] = {'elf_sha256': row['elf_sha256'], 'bin_sha256': row['bin_sha256']}
    require(len(found) == 21, '必须21个最终BIN门禁')
    return found

def load_campaign(manifest_path, root=None, main_only_authorized=False):
    manifest_path = Path(manifest_path).resolve()
    base = manifest_path.parent
    root = Path(root).resolve() if root is not None else base
    m = json.loads(manifest_path.read_text())
    require(m.get('schema_version') == 1 and m.get('campaign_id') == CAMPAIGN, 'manifest版本/任务身份错误')
    require(m.get('profile') == 'main-nx', '拒绝historical/aux-array混入主分析')
    gates_path = read_bound(m['gates'], base, root)
    gates = validate_gates(json.loads(gates_path.read_text()))
    samples, provenance, identities, run_keys = [], [], [], set()
    for run in m['runs']:
        family, role = run['family'], run['role']
        key = family, role
        require(family in FAMILIES and role in ('main', 'replay') and key not in run_keys, 'run身份重复/不合法')
        run_keys.add(key)
        sp = read_bound(run['samples'], base, root)
        rp = read_bound(run['results'], base, root)
        result = json.loads(rp.read_text())
        require(result.get('mode') == 'gsim' and result.get('complete') is True and result.get('all_passed') is True and result.get('state') == 'passed' and result.get('audit_issues') == [], 'collector未完整通过')
        funcs = FUNCTIONS if role == 'main' else ('log', 'exp')
        if 'receipt' not in run:
            require(result.get('final_bin_sha256') == {fn: gates[family, fn]['bin_sha256'] for fn in funcs}, 'collector最终BIN与NEMU门禁未绑定')
        identity = run['identity']
        require(identity.get('abi') == 'Nx', '主auto/direct必须同Nx ABI')
        require(all(digest_tree(identity.get(field)) for field in IDENTITIES), '身份hash缺失')
        require(digest_tree(identity.get('direct_objects')), 'direct_objects身份缺失')
        for field, modes in (('scalar_objects', MODES[:2]), ('auto_objects', MODES[2:4]), ('direct_objects', MODES[4:6])):
            require(isinstance(identity.get(field), dict) and set(identity[field]) == set(FUNCTIONS), f'{field}须全七function映射')
            require(all(isinstance(v, dict) and set(v) == set(modes) for v in identity[field].values()), f'{field}须perfn/permode完整映射')
        require(identity.get('elf_sha256') == {fn: gates[family, fn]['elf_sha256'] for fn in funcs}, 'identity ELF映射不符')
        require(identity.get('bin_sha256') == {fn: gates[family, fn]['bin_sha256'] for fn in funcs}, 'identity BIN映射不符')
        identities.append({field: identity[field] for field in IDENTITIES})
        # 独立receipt绑定原始results/samples，避免修改collector结果或hash自引用。
        if 'receipt' in run:
            receipt_path = read_bound(run['receipt'], base, root)
            receipt = json.loads(receipt_path.read_text())
            require(receipt.get('campaign_id') == CAMPAIGN and receipt.get('family') == family and receipt.get('role') == role, 'receipt身份错误')
            require(receipt.get('samples_sha256') == sha(sp) and receipt.get('results_snapshot_sha256') == sha(rp), 'receipt未绑定raw collector结果')
            require(receipt.get('analysis_identity') == identity and receipt.get('final_bin_sha256') == {fn: gates[family, fn]['bin_sha256'] for fn in funcs}, 'receipt未绑定构建/门禁身份')
        else:
            require(result.get('analysis_identity') == identity, 'collector身份与manifest未绑定')
        with sp.open() as stream:
            rows = validate_samples(list(csv.DictReader(stream)), funcs)
        for row in rows:
            row.update(family=family, role=role, input_sha256=sha(sp))
        samples.extend(rows)
        provenance.append({'family': family, 'role': role, 'samples': str(sp), 'samples_sha256': sha(sp), 'results': str(rp), 'results_sha256': sha(rp)})
    roles = ('main',) if main_only_authorized else ('main', 'replay')
    if main_only_authorized:
        require(m.get('acceptance_scope') == 'main-only-user-authorized' and m.get('replay_status') == 'cancelled-by-user', 'main-only必须显式授权且记录取消复跑')
    require(run_keys == set(itertools.product(FAMILIES, roles)), '授权范围内完整run矩阵缺失或有额外run')
    require(all(i == identities[0] for i in identities), 'fixture/scalar/auto/tools/DRAM身份不一致')
    ip = read_bound(m['instructions'], base, root)
    with ip.open() as stream:
        instructions = list(csv.DictReader(stream))
    seen = set()
    for row in instructions:
        key = row['family'], row['function']
        require(key in gates and key not in seen and row['bin_sha256'] == gates[key]['bin_sha256'], '整程序指令BIN身份不符/重复')
        require(row['scope'] == 'whole-program' and int(row['nemu_total_instructions']) > 0, '不得将整程序instr作timed-window')
        seen.add(key)
    require(seen == set(gates), '整程序指令层不完整')
    return samples, instructions, provenance

def statistics(values):
    med = st.median(values)
    return dict(count=len(values), raw_cycles=values, median_cycles=med, min_cycles=min(values), max_cycles=max(values), median_abs_deviation=st.median(abs(x-med) for x in values), coefficient_of_variation=st.pstdev(values)/st.mean(values))

def aggregate(samples):
    buckets = {}
    for row in samples:
        key = tuple(row[x] for x in ('family', 'role', 'function', 'mode', 'n', 'k', 'phase'))
        buckets.setdefault(key, []).append(row)
    groups = []
    for key, rows in sorted(buckets.items()):
        family, role, fn, mode, n, k, ph = key
        values = [row['cycles'] for row in sorted(rows, key=lambda row: row['r'])]
        group = dict(zip(('family','role','function','mode','n','k','phase'), key))
        group.update(statistics(values), cycles_per_call=st.median(values)/k, cycles_per_element=st.median(values)/(n*k))
        groups.append(group)
    index = {tuple(g[x] for x in ('family','role','function','mode','n','k','phase')): g for g in groups}
    pairs, speedups, replay = [], [], []
    for g in groups:
        family, role, fn, mode, n, k, ph = (g[x] for x in ('family','role','function','mode','n','k','phase'))
        if family in ('old-nofrm', 'new'):
            for old in FAMILIES[:FAMILIES.index(family)]:
                a = index[old,role,fn,mode,n,k,ph]
                pairs.append(dict(role=role,function=fn,mode=mode,n=n,k=k,phase=ph,baseline=old,comparison=family,paired_count=g['count'],baseline_median=a['median_cycles'],comparison_median=g['median_cycles'],ratio_of_medians=a['median_cycles']/g['median_cycles'],median_of_paired_ratios=st.median(x/y for x,y in zip(a['raw_cycles'],g['raw_cycles']))))
        if mode in MODES[2:6]:
            for scalar in MODES[:2]:
                s = index[family,role,fn,scalar,n,k,ph]
                speedups.append(dict(family=family,role=role,function=fn,mode=mode,n=n,k=k,phase=ph,baseline=scalar,speedup=s['median_cycles']/g['median_cycles']))
        if role == 'replay' and ph == 'steady':
            a = index[family,'main',fn,mode,n,k,ph]
            replay.append(dict(family=family,function=fn,mode=mode,n=n,k=k,main_median=a['median_cycles'],replay_median=g['median_cycles'],replay_over_main=g['median_cycles']/a['median_cycles']))
    return groups,pairs,speedups,replay

def write_csv(path, rows):
    if not rows:
        raise Rejected('禁止空输出表')
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v) if isinstance(v,list) else v for k,v in row.items()})

def svg_plot(groups, fn):
    rows = [g for g in groups if g['function']==fn and g['role']=='main' and g['phase']=='steady' and g['mode'] in MODES[:6]]
    panels = sorted(set((r['n'],r['k']) for r in rows))
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="%s">' % (260*len(panels)), '<rect width="100%" height="100%" fill="white"/>']
    colors = ('#555555','#e69f00','#0072b2')
    for panel,(n,k) in enumerate(panels):
        subset = [r for r in rows if (r['n'],r['k'])==(n,k)]
        ymax = max(r['cycles_per_element'] for r in subset)*1.15
        y0 = panel*260+210
        out.append(f'<text x="65" y="{y0-185}" font-size="16">{html.escape(fn)} N={n} K={k} steady7 median cycles/element (zero baseline)</text>')
        for tick in range(5):
            y = y0-160*tick/4
            out.append(f'<path d="M65 {y} H1160" stroke="#ddd"/><text x="5" y="{y+4}" font-size="11">{ymax*tick/4:.3g}</text>')
        for mi,mode in enumerate(MODES[:6]):
            for fi,family in enumerate(FAMILIES):
                g = next(r for r in subset if r['mode']==mode and r['family']==family)
                h = g['cycles_per_element']/ymax*160
                x = 90+mi*175+fi*36
                out.append(f'<rect x="{x}" y="{y0-h}" width="30" height="{h}" fill="{colors[fi]}"/>')
            out.append(f'<text x="{90+mi*175}" y="{y0+18}" font-size="11">{mode}</text>')
        for fi,family in enumerate(FAMILIES):
            out.append(f'<text x="{100+fi*240}" y="{y0+42}" fill="{colors[fi]}" font-size="12">{family}</text>')
    return '\n'.join(out+['</svg>'])

def render(output, samples, instructions, provenance, main_only_authorized=False):
    require(not output.exists(), '输出目录已存在；使用新的目录防止旧结果污染')
    groups,pairs,speedups,replay = aggregate(samples)
    output.mkdir(parents=True)
    for name,rows in (('samples',samples),('groups',groups),('pairs',pairs),('speedups',speedups),('replay',replay),('instructions',instructions)):
        if rows:
            write_csv(output/(name+'.csv'), rows)
    report = dict(schema_version=1,campaign_id=CAMPAIGN,profile='main-nx',complete=True,source_provenance=provenance,scope='gsim timed intervals; NEMU instructions separately whole-program',groups=groups,pairs=pairs,speedups=speedups,replay=replay,instructions=instructions)
    report.update(acceptance_scope='main-only-user-authorized' if main_only_authorized else 'main-and-replay', replay_status='cancelled-by-user' if main_only_authorized else 'complete')
    (output/'analysis.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    for fn in FUNCTIONS:
        (output/(fn+'.svg')).write_text(svg_plot(groups,fn))
    text = ['# 七函数三family full分析验收报告','', '全部输入通过完整矩阵、身份hash、最终BIN门禁及独立复跑存在性检查。没有自动接受partial。', '', '主指标：steady7周期中位数；old/new为同function/mode/N/K的中位数比，大于1表示new更快。逐轮ratio中位数另列pairs.csv，不等同主指标。控制组不扣除。', '', '图为线性零起点周期/元素；NEMU指令单独为整程序，绝非timedwindow。仅模拟器区间，不代表真实硅片或完整应用。', '', '|函数|N|K|模式|old-original/new|old-nofrm/new|','|---|---:|---:|---|---:|---:|']
    for p in pairs:
        if p['role']=='main' and p['phase']=='steady' and p['mode'] in MODES[2:6] and p['baseline']==FAMILIES[0] and p['comparison']=='new':
            q = next(q for q in pairs if all(q[x]==p[x] for x in ('role','phase','mode','function','n','k')) and q['baseline']==FAMILIES[1])
            text.append(f"|{p['function']}|{p['n']}|{p['k']}|{p['mode']}|{p['ratio_of_medians']:.4f}|{q['ratio_of_medians']:.4f}|")
    if main_only_authorized:
        text[2] = '用户明确取消独立复跑；本报告按main-only-user-authorized新范围验收三family各4320条完整主批，共12960条。不是自动接受partial；独立复跑已取消，不作重复运行一致性结论。'
    (output/'report.zh.md').write_text('\n'.join(text)+'\n')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--input-root',type=Path,help='允许绑定输入的根目录；默认 manifest 所在目录')
    parser.add_argument('--user-authorized-main-only',action='store_true',help='仅用户明确取消复跑后启用；仍要求完整三family主批')
    args = parser.parse_args()
    try:
        data = load_campaign(args.manifest,root=args.input_root,main_only_authorized=args.user_authorized_main_only)
        render(args.output,*data,main_only_authorized=args.user_authorized_main_only)
    except (Rejected,KeyError,OSError,json.JSONDecodeError) as exc:
        parser.exit(2, f'验收拒绝：{exc}\n')

if __name__ == '__main__':
    main()
