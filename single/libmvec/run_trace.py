#!/usr/bin/env python3
"""并行运行独立短镜像；仅Verilator可采集波形，不作为全规模性能验收。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--manifest',type=Path,action='append',required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--emu',type=Path,default=Path('/nfs/share/huxuan/emus/emu-verilator-j8-20261111-170241-409f429572'))
    ap.add_argument('--nemu',type=Path,default=Path('/nfs/home/huxuan/opt/bin/riscv64-nemu-interpreter'))
    ap.add_argument('--reference',type=Path,default=Path('/nfs/share/huxuan/refmodel/riscv64-nemu-interpreter-so-fixvfredusum'))
    ap.add_argument('--jobs',type=int,default=2)
    ap.add_argument('--cycles',type=int,default=150000)
    ap.add_argument('--wave-end',type=int,default=80000)
    a=ap.parse_args()
    if 'verilator' not in a.emu.name:ap.error('wave capture requires a Verilator emu')
    if a.jobs<1 or a.cycles<1 or a.wave_end<1:ap.error('positive jobs and cycle bounds required')
    out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
    items=[]
    def identity(m,i):
        import hashlib
        suffix=('-indices'+hashlib.sha256(json.dumps(m['indices']).encode()).hexdigest()[:10]) if m.get('indices') else '-offset'+str(m.get('offset',0))
        return m.get('function',Path(m['source_manifest']).parent.name)+'-'+i['mode']+suffix
    import hashlib
    for path in a.manifest:
        manifest=json.loads(path.read_text())
        for image in manifest['images']:
            binary=Path(image['bin']).resolve()
            if hashlib.sha256(binary.read_bytes()).hexdigest()!=image['sha256']:raise ValueError('image changed: '+str(binary))
            items.append((manifest,image,binary))
    names=[identity(m,i) for m,i,b in items]
    if len(names)!=len(set(names)):raise ValueError('duplicate trace identities')
    (out/'inputs.json').write_text(json.dumps([str(p.resolve()) for p in a.manifest],indent=2)+'\n')
    def run(item):
        m,i,b=item;name=identity(m,i)
        d=out/name;(d/'dram').mkdir(parents=True)
        with (d/'nemu.stdout').open('w') as so,(d/'nemu.stderr').open('w') as se:
            r=subprocess.run([str(a.nemu),'-b',str(b)],stdout=so,stderr=se,timeout=120)
        if r.returncode:return dict(name=name,phase='nemu',returncode=r.returncode)
        cmd=[str(a.emu.resolve()),'-i',str(b),'--diff='+str(a.reference.resolve()),'--seed=123456789',
             '--dramsim3-ini=/nfs/home/share/ci-workloads/DRAMsim3/configs/XiangShan.ini',
             '--dramsim3-outdir='+str(d/'dram'),'--dump-wave','--dump-commit-trace',
             '--wave-path='+str(d/'trace.fst'),'-b','0','-e',str(a.wave_end),'-C',str(a.cycles)]
        (d/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
        with (d/'stdout.log').open('w') as so,(d/'stderr.log').open('w') as se:r=subprocess.run(cmd,cwd=d,stdout=so,stderr=se)
        good='HIT GOOD TRAP' in (d/'stdout.log').read_text()
        result=dict(name=name,phase='verilator',returncode=r.returncode,good_trap=good)
        (d/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:results=list(pool.map(run,items))
    (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    if not all(r['returncode']==0 and r.get('good_trap') for r in results):raise SystemExit(1)
    print(out/'results.json')

if __name__=='__main__':main()
