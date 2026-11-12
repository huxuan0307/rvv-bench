#!/usr/bin/env python3
"""Analyze complete RTL sample sets without discarding or pooling rounds."""
import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics

FUNCTIONS={'log','exp','sin','cos','pow','logf','expf'}

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def stats(rows):
    rows=sorted(rows,key=lambda x:x['round'])
    cycles=[x['cycles'] for x in rows]
    med=statistics.median(cycles)
    n,k=rows[0]['n'],rows[0]['k']
    return dict(count=len(rows),raw_cycles=cycles,rounds=[r['round'] for r in rows],
        raw_begin=[r['cycle_begin'] for r in rows],raw_end=[r['cycle_end'] for r in rows],
        checksums=[r['checksum'] for r in rows],orders=[r['order'] for r in rows],
        median_cycles=med,min_cycles=min(cycles),max_cycles=max(cycles),
        median_abs_deviation=statistics.median(abs(c-med) for c in cycles),
        coefficient_of_variation=statistics.pstdev(cycles)/statistics.mean(cycles) if sum(cycles) else 0,
        cycles_per_call=med/k,cycles_per_element=med/(n*k))

def analyze(paths):
    groups=[]; runs=[]; func_runs=defaultdict(set); abi_seen=False; tool_ids=None
    bin_identity={}; vlen=None; manifest_identity=None
    for path in paths:
        path=Path(path).resolve(); doc=json.loads(path.read_text())
        if doc.get('mode')!='gsim' or doc.get('state')!='passed' or doc.get('all_passed') is not True:
            raise ValueError(f'requires complete passed gsim evidence: {path}')
        current_manifest=doc.get('manifest',{}).get('snapshot_sha256')
        if not current_manifest: raise ValueError('missing manifest snapshot identity')
        if manifest_identity is None: manifest_identity=current_manifest
        elif manifest_identity!=current_manifest: raise ValueError('cannot mix different build manifests')
        tools={name:doc['tools'][name]['sha256'] for name in ('gsim','reference','dram_config')}
        if tool_ids is None: tool_ids=tools
        elif tool_ids!=tools: raise ValueError('cannot compare different gsim/reference/DRAM identities')
        run=doc['run']
        if run in [r['run'] for r in runs]: raise ValueError('duplicate run evidence')
        runs.append(dict(run=run,report=str(path),sha256=digest(path),tools=doc['tools'],manifest_sha256=current_manifest))
        for case in doc['cases']:
            if case['status']!='passed': raise ValueError('case not passed')
            actual_vlen=case.get('vlen_bits')
            if not isinstance(actual_vlen,int) or actual_vlen<128: raise ValueError('missing actual target VLEN')
            if vlen is None: vlen=actual_vlen
            elif vlen!=actual_vlen: raise ValueError('cannot compare different VLEN configurations')
            if case['suite']=='accuracy':
                if case['name']=='abi-public': abi_seen=True
                continue
            if not case.get('measurement_valid'): raise ValueError('performance data not marked valid')
            function=case['name'].removeprefix('perf-')
            profile=case.get('profile','full')
            if profile not in ('full','format-smoke','n16','n16-trim'): raise ValueError('unknown workload profile')
            if profile=='full': func_runs[function].add(run)
            thisbin=case.get('staged_sha256')
            if not thisbin: raise ValueError('missing measured BIN identity')
            old=bin_identity.setdefault(function,thisbin)
            if old!=thisbin: raise ValueError(f'{function}: changed BIN across runs')
            rows=defaultdict(list)
            for kind,key in [('math','sample_records'),('control','calibration_records')]:
                for row in case[key]:
                    rows[(kind,row['mode'],row['n'],row['k'],row['phase'])].append(row)
            local=[]
            for (kind,mode,n,k,phase),rr in rows.items():
                expected={'first':1,'warmup':2,'steady':7}[phase]
                if len(rr)!=expected or sorted(r['round'] for r in rr)!=list(range(expected)):
                    raise ValueError('incomplete or duplicate rounds in analyzed configuration')
                if kind=='math' and any(r['cycles']<=0 for r in rr): raise ValueError('nonpositive math cycles')
                stat=stats(rr)
                if kind=='control' and mode=='rdcycle':
                    stat['cycles_per_call']=None; stat['cycles_per_element']=None
                local.append(dict(run=run,profile=profile,function=function,kind=kind,mode=mode,n=n,k=k,phase=phase,**stat))
            bykey={(g['kind'],g['mode'],g['n'],g['k'],g['phase']):g for g in local}
            for g in local:
                if g['kind']!='math': continue
                def lookup(kind,mode,k=None):
                    return bykey[(kind,mode,g['n'],g['k'] if k is None else k,g['phase'])]
                g['speedup_vs_scalar_strict']=(None if profile=='n16-trim' else
                    lookup('math','scalar_strict')['median_cycles']/g['median_cycles'])
                g['speedup_vs_scalar_fast']=lookup('math','scalar_fast')['median_cycles']/g['median_cycles']
                g['empty_fraction']=(None if profile=='n16-trim' else
                    lookup('control','empty')['median_cycles']/g['median_cycles'])
                base=lookup('math',g['mode'],1)['cycles_per_element']
                g['normalized_change_vs_k1']=g['cycles_per_element']/base-1
                g['fixture_sensitive']=(g['empty_fraction'] is not None and g['empty_fraction']>0.05) or (g['k']>1 and abs(g['normalized_change_vs_k1'])>0.10)
            groups.extend(local)
    missing=sorted(FUNCTIONS-set(func_runs))
    replays={f:len(func_runs[f]) for f in ('log','exp')}
    full=not missing and abi_seen and all(n>=2 for n in replays.values())
    return dict(schema_version=1,generated_at=datetime.now(timezone.utc).isoformat(),
        scope='香山RTL数组调用区间；不代表真实硅片或完整应用',full_acceptance=full,
        missing_functions=missing,abi_public_seen=abi_seen,pilot_runs=replays,tools_sha256=tool_ids,
        runs=runs,groups=groups,vlen_bits=vlen,manifest_sha256=manifest_identity,
        notes=['Raw first/warmup/steady samples retained separately; no outlier removal or overhead subtraction.',
               'n is elements per call; K calls per timing window. CPE=cycles/(n*K).',
               'First is not cold-cache: correctness checks already ran.',
               'Control overhead and K sensitivity are diagnostics, not precise deconvolution.',
               'Identical deterministic simulator seeds do not constitute independent statistical replicates.'])

def markdown(doc):
    lines=['# libmvec 香山 RTL 周期分析','',
        '- 完整验收：'+('通过' if doc['full_acceptance'] else '尚未齐备（仅已完成配置分析）'),
        '- 全部原始 first/warmup/steady 数据保留；不去离群值、不扣除空壳开销。',
        '- cycles/元素 = 窗口周期 / (n × K)。严格标量和 fast-math 标量分开比较。',
        '- first 前已做正确性验证，不能称作冷缓存。计时不含初始化/校验/打印，但它们影响组间缓存状态。',
        '- 以下为指定香山 RTL 模型，不是NEMU速度、宿主耗时、硅片实测或完整应用性能。','',
        '## 来源','']
    for i,r in enumerate(doc['runs']): lines.append(f'- R{i+1}: [{Path(r["run"]).name}](<{r["report"]}>)；报告 SHA256 `{r["sha256"]}`')
    runidx={r['run']:i+1 for i,r in enumerate(doc['runs'])}
    lines+=['','## 稳态数学样本（完整7轮）','',
        '|运行|函数|模式|n|K|原始周期[7]|中位周期/元素|相对strict|相对fast|空壳占比|K相对1变化|敏感|',
        '|---|---|---|---:|---:|---|---:|---:|---:|---:|---:|---|']
    for g in doc['groups']:
        if g['kind']!='math' or g['phase']!='steady': continue
        raw=', '.join(map(str,g['raw_cycles']))
        lines.append(f'|R{runidx[g["run"]]}|{g["function"]}|{g["mode"]}|{g["n"]}|{g["k"]}|{raw}|{g["cycles_per_element"]:.6f}|{g["speedup_vs_scalar_strict"]:.4f}×|{g["speedup_vs_scalar_fast"]:.4f}×|{g["empty_fraction"]:.2%}|{g["normalized_change_vs_k1"]:+.2%}|{"是" if g["fixture_sensitive"] else "否"}|')
    lines+=['','## 稳态夹具控制（原始7轮）','','|运行|函数|控制|n|K|原始周期[7]|中位数|','|---|---|---|---:|---:|---|---:|']
    for g in doc['groups']:
        if g['kind']!='control' or g['phase']!='steady': continue
        lines.append(f'|R{runidx[g["run"]]}|{g["function"]}|{g["mode"]}|{g["n"]}|{g["k"]}|{", ".join(map(str,g["raw_cycles"]))}|{g["median_cycles"]}|')
    lines+=['','## 解释边界','',
        'empty/数学周期 >5% 或 K>1 相对 K=1 的归一化差异绝对值 >10% 标记为敏感；这是预设诊断阈值，不是硬件规律。原始数据始终保留。',
        'rdcycle 只有计数器对，不重复K次，所以不报告其每调用/每元素成本。copy 是标量复制控制，不可精确代表所有向量路径的访存成本。',
        '固定 seed 的RTL重跑用于复现，不把相同确定性结果当作独立随机实验或计算虚假的统计置信区间。','']
    return '\n'.join(lines)

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--report',action='append',required=True,type=Path)
    p.add_argument('--output',type=Path,required=True); p.add_argument('--require-full',action='store_true')
    a=p.parse_args(); doc=analyze(a.report); a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'analysis.json').write_text(json.dumps(doc,indent=2,ensure_ascii=False)+'\n')
    (a.output/'REPORT.zh.md').write_text(markdown(doc))
    fields=['run','profile','function','kind','mode','n','k','phase','count','raw_cycles','median_cycles','min_cycles','max_cycles',
        'median_abs_deviation','coefficient_of_variation','cycles_per_call','cycles_per_element',
        'speedup_vs_scalar_strict','speedup_vs_scalar_fast','empty_fraction','normalized_change_vs_k1','fixture_sensitive']
    with (a.output/'groups.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader()
        for g in doc['groups']: w.writerow({**g,'raw_cycles':json.dumps(g['raw_cycles'])})
    print(json.dumps({'output':str(a.output),'groups':len(doc['groups']),'full_acceptance':doc['full_acceptance']}))
    return int(a.require_full and not doc['full_acceptance'])
if __name__=='__main__': raise SystemExit(main())
