#!/usr/bin/env python3
"""隔离运行短 trace 的 Verilator 批次；输出必须是全新的日期目录。"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import sys

import runner_common as common

LABEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*\Z')
TRACE = re.compile(r'^TRACE_RESULT n=(\d+) k=(\d+) begin=(\d+) end=(\d+) cycles=(\d+) failed=(\d+)\s*$', re.M)
COLUMNS = ('label','mode','status','reasons','returncode','timed_out','aborted','elapsed_seconds',
           'bin_sha256','staged_before','staged_after','manifest_sha256','trace_n','trace_k',
           'trace_begin','trace_end','trace_cycles','trace_failed','good_trap','fst_bytes','fst_sha256')


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20),b''): h.update(block)
    return h.hexdigest()


def analyze_logs(stdout,stderr,n,k):
    # 两个流分别解析，绝不把半行拼成目标记录；忽略 RTL 统计计数器。
    texts=[common.clean_log(t) for t in (stdout,stderr)]
    matches=[m for t in texts for m in TRACE.finditer(t)]
    markers=sum(t.count('TRACE_RESULT') for t in texts)
    good=any(common.CORE_GOOD_TRAP.search(t) for t in texts)
    issues=[]
    if not good: issues.append('missing Core0 GOOD TRAP')
    scrubbed=['\n'.join(line for line in t.splitlines() if not line.startswith('TRACE_RESULT')) for t in texts]
    if any(common.error_markers(t) for t in scrubbed): issues.append('error marker')
    if markers!=1 or len(matches)!=1:
        issues.append('missing, incomplete or duplicate TRACE_RESULT')
        return good, None, issues
    values=tuple(int(x) for x in matches[0].groups())
    if values[0]!=n or values[1]!=k: issues.append('trace n/k mismatch')
    if values[5]!=0: issues.append('trace failed nonzero')
    if values[4]!=(values[3]-values[2])%2**64: issues.append('trace cycle arithmetic mismatch')
    return good, values, issues


def parse_label(value):
    label,sep,path=value.partition('=')
    if not sep or not LABEL.fullmatch(label) or not path: raise argparse.ArgumentTypeError('使用 label=/absolute/trace-manifest.json')
    return label,Path(path)


def prepare_output(path):
    requested=Path(path).expanduser()
    if requested.exists() or requested.is_symlink(): raise ValueError('output already exists')
    out=requested.resolve()
    if any(part.lower()=='build' or part.lower().startswith('build-') for part in out.parts):
        raise ValueError('output must be outside build paths')
    if not re.search(r'\d{8}[-T]\d{6}',out.name): raise ValueError('output name must contain YYYYMMDD-HHMMSS')
    return out


def case_command(emu,ref,dram,case,staged,fst,seed):
    return [str(emu),'-i',str(staged),'--diff='+str(ref),'--seed='+str(seed),
            '--dramsim3-ini='+str(dram),'--dramsim3-outdir='+str(case/'dram'),
            '--dump-wave','--dump-commit-trace','--wave-path='+str(fst),
            '-b','0','-e','30000','-C','100000']


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--trace',type=parse_label,action='append',required=True,metavar='LABEL=MANIFEST')
    p.add_argument('--emu',type=Path,required=True); p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--dramsim3-ini',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--seed',type=int,default=123456789); p.add_argument('--jobs',type=int,default=3)
    p.add_argument('--timeout',default='48h',help='per-case duration, e.g. 30m or 48h')
    a=p.parse_args(argv)
    if not 1<=a.jobs<=3: p.error('jobs must be 1..3')
    if not 0<=a.seed<=common.U64_MAX: p.error('seed must be uint64')
    if len({x[0] for x in a.trace})!=len(a.trace): p.error('labels must be unique')
    try:
        timeout=common.parse_duration(a.timeout); out=prepare_output(a.output)
        emu=a.emu.resolve(strict=True); ref=a.reference.resolve(strict=True); dram=a.dramsim3_ini.resolve(strict=True)
        if 'verilator' not in emu.name.lower() or 'static' in emu.name.lower(): raise ValueError('emu must be a non-static verilator executable')
        tools={'emu':common.tool_identity(emu,kind='gsim'),'reference':common.tool_identity(ref,kind='reference'),
               'dram':common.identity(dram)}
        entries=[]
        for label,mpath in a.trace:
            mpath=mpath.resolve(strict=True); mhash=sha(mpath); doc=json.loads(mpath.read_text())
            if doc.get('n')!=16 or type(doc.get('k')) is not int or doc['k']<1: raise ValueError('trace manifest has invalid n/k')
            imgs=doc.get('images',[])
            if len(imgs)!=2 or {x.get('mode') for x in imgs}!={'auto_m2','direct_m2'}: raise ValueError('trace manifest must contain auto_m2 and direct_m2')
            for item in imgs:
                binary=Path(item['bin']).resolve(strict=True); elf=Path(item['elf']).resolve(strict=True)
                if sha(binary)!=item['sha256'] or sha(elf)!=item['elf_sha256']: raise ValueError('trace BIN/ELF SHA mismatch')
                entries.append(dict(label=label,mode=item['mode'],manifest=mpath,manifest_sha256=mhash,
                                    binary=binary,sha256=item['sha256'],n=doc['n'],k=doc['k']))
    except (OSError,ValueError,KeyError,TypeError,common.ManifestError,json.JSONDecodeError) as exc: p.error(str(exc))
    out.mkdir(parents=True,exist_ok=False)
    (out/'tool-identities.txt').write_text(''.join(f'{name} path={obj["realpath"]} sha256={obj["sha256"]}\n' for name,obj in tools.items()))
    (out/'configuration.txt').write_text(f'jobs={a.jobs}\ntimeout={a.timeout}\nseed={a.seed}\nwave=-b 0 -e 30000 -C 100000\n')
    cases=[]
    for entry in entries:
        case=out/(entry['label']+'-'+entry['mode']); case.mkdir()
        work=case/'work'; work.mkdir(); (case/'dram').mkdir()
        staged=case/(entry['mode']+'.bin'); shutil.copyfile(entry['binary'],staged)
        before=sha(staged)
        if before!=entry['sha256'] or sha(entry['binary'])!=before or sha(entry['manifest'])!=entry['manifest_sha256']:
            raise RuntimeError('staging BIN or manifest identity mismatch')
        shutil.copyfile(entry['manifest'],case/'trace-manifest.json')
        if sha(case/'trace-manifest.json')!=entry['manifest_sha256']: raise RuntimeError('manifest snapshot changed')
        fst=case/'trace.fst'; cmd=case_command(emu,ref,dram,case,staged,fst,a.seed)
        (case/'command.txt').write_text('cwd='+shlex.quote(str(work))+'\n'+shlex.join(cmd)+'\n')
        cases.append(dict(**entry,case=case,work=work,staged=staged,before=before,fst=fst,command=cmd))
    registry=common.ProcessRegistry(); previous={}
    def stop(signum,frame):
        registry.abort.set(); registry.terminate(signal.SIGTERM)
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): previous[sig]=signal.signal(sig,stop)
    def execute(c):
        if registry.abort.is_set(): result=dict(returncode=None,timed_out=False,aborted=True,elapsed_seconds=0,launch_error='aborted before launch')
        else:
            result=common.launch_process(c['command'],c['work'],c['case']/'stdout.log',c['case']/'stderr.log',timeout,registry)
        after=sha(c['staged']); reasons=[]
        if result['returncode']!=0: reasons.append('nonzero or missing rc')
        if result['timed_out']: reasons.append('timeout')
        if result['aborted']: reasons.append('aborted')
        if result['launch_error']: reasons.append('launch error: '+result['launch_error'])
        if after!=c['sha256'] or sha(c['binary'])!=c['sha256']: reasons.append('BIN changed')
        if sha(c['manifest'])!=c['manifest_sha256']: reasons.append('manifest changed')
        if any(sha(obj['realpath'])!=obj['sha256'] for obj in tools.values()): reasons.append('tool/config changed')
        texts=[(c['case']/name).read_text(errors='replace') if (c['case']/name).is_file() else '' for name in ('stdout.log','stderr.log')]
        good,trace,issues=analyze_logs(*texts,c['n'],c['k']); reasons.extend(issues)
        fst_bytes=c['fst'].stat().st_size if c['fst'].is_file() else 0
        fst_sha=sha(c['fst']) if fst_bytes else ''
        if not fst_bytes: reasons.append('missing or empty FST')
        row=dict(label=c['label'],mode=c['mode'],status='failed' if reasons else 'passed',reasons='; '.join(reasons),
                 returncode=result['returncode'],timed_out=result['timed_out'],aborted=result['aborted'],
                 elapsed_seconds=result['elapsed_seconds'],bin_sha256=c['sha256'],staged_before=c['before'],staged_after=after,
                 manifest_sha256=c['manifest_sha256'],good_trap=good,fst_bytes=fst_bytes,fst_sha256=fst_sha)
        if trace:
            for key,val in zip(('trace_n','trace_k','trace_begin','trace_end','trace_cycles','trace_failed'),trace): row[key]=val
        with (c['case']/'result.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=COLUMNS); w.writeheader(); w.writerow(row)
        print(c['label'],c['mode'],row['status'],'rc='+str(result['returncode']),flush=True)
        return row
    rows=[]
    try:
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            futures=[pool.submit(execute,c) for c in cases]
            for future in as_completed(futures): rows.append(future.result())
    finally:
        registry.terminate(signal.SIGKILL)
        for sig,handler in previous.items(): signal.signal(sig,handler)
    rows.sort(key=lambda x:(x['label'],x['mode']))
    with (out/'results.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=COLUMNS); w.writeheader(); w.writerows(rows)
    ok=len(rows)==len(cases) and all(r['status']=='passed' for r in rows)
    print('RUN_DIRECTORY='+str(out)); print('RUN_STATUS='+('passed' if ok else 'failed'))
    return 0 if ok else 1


if __name__=='__main__': sys.exit(main())
