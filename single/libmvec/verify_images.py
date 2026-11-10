#!/usr/bin/env python3
"""Independently audit frozen ELF/BIN artifacts and effective ABI perturbation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def verify(manifest):
    manifest=Path(manifest).resolve();root=manifest.parent;doc=json.loads(manifest.read_text())
    tools=doc['toolchain']['tools']; checks=[]
    def check(name,condition,detail=None):
        checks.append(dict(name=name,passed=bool(condition),detail=detail))
    def run(tool,args):
        p=Path(tools[tool]['path'])
        if sha(p)!=tools[tool]['sha256']:raise ValueError('tool changed: '+tool)
        return subprocess.run([str(p),*map(str,args)],capture_output=True,text=True,check=True).stdout
    for relative,record in doc['sources'].items():
        snapshot=root/'source'/relative
        check('source snapshot '+relative, snapshot.is_file() and sha(snapshot)==record['sha256'])
    for case in doc['cases']:
        name=case['name'];elf=root/case['elf'];binary=root/case['bin']
        check(name+' BIN SHA',sha(binary)==case['sha256'])
        check(name+' ELF SHA',sha(elf)==case['elf_sha256'])
        head=run('readelf',['-h','-l','-d',elf]); syms=run('nm',['-n',elf]);dis=run('objdump',['-d',elf])
        check(name+' raw entry',bool(re.search(r'Entry point address:\s+0x80000000\b',head)))
        check(name+' no interpreter/dynamic deps','INTERP' not in head and '(NEEDED)' not in head)
        check(name+' no undefined symbols',not run('nm',['-u',elf]).strip())
        bodies={m[1]:m[2] for m in re.finditer(r'^[0-9a-f]+ <([^>]+)>:\n(.*?)(?=^[0-9a-f]+ <|\Z)',dis,re.M|re.S)}
        if case['suite']=='accuracy':
            expected=set(case['expected']['abi_symbols']); wrappers={}
            for symbol in expected:
                found=[(k,v) for k,v in bodies.items() if k in ('u_'+symbol,'m_'+symbol,'b_'+symbol)]
                check(symbol+' one wrapper',len(found)==1)
                if len(found)!=1:continue
                k,body=found[0]
                load=re.search(r'\bvle(?:32|64)\.v\b',body)
                perturb=re.search(r'\bvsetivli\s+zero,1,e(?:32|64),m[12],',body)
                call=re.search(r'\bjal\s+[^\n]*<'+re.escape(symbol)+'>',body)
                ok=load and perturb and call and load.start()<perturb.start()<call.start()
                check(symbol+' load < VL=1 < real call',ok)
                wrappers[symbol]=dict(wrapper=k,valid=bool(ok))
            check('all 36 wrappers',len(wrappers)==36)
        else:
            main=bodies.get('main','')
            tick=[m.start() for m in re.finditer(r'\brdcycle\b',main)]
            paired=False
            smoke=case['expected'].get('profile')=='format-smoke'
            for a,b in zip(tick,tick[1:]):
                middle=main[a:b]
                calls=re.findall(r'\bjalr\s+[as]\d+\b',middle)
                if smoke:
                    # K=1 is constant-folded to exactly one indirect call.
                    if len(calls)==1 and re.search(r'\bli\s+a0,16\b',middle) and not re.search(r'\bbne\b',middle):paired=True
                elif calls and re.search(r'\bbne\b',middle):paired=True
            check(name+(' rdcycle encloses one n16 K1 call' if smoke else ' rdcycle encloses indirect K-call loop'),paired)
            symbols={m[2]:int(m[1],16) for m in re.finditer(r'^([0-9a-f]+)\s+\w\s+(\w+)$',syms,re.M)}
            for var in ('x','z','output'):
                check(name+' '+var+' 64-byte aligned',var in symbols and symbols[var]%64==0)
            for mode,calls in case['observed_vector_calls'].items():
                obj=root/name/(mode+'.o'); od=run('objdump',['-dr',obj])
                actual=sorted(set(re.findall(r'\b_ZGV\w+',od)))
                check(name+' '+mode+' calls',actual==calls,actual)
    return dict(schema_version=1,manifest=str(manifest),manifest_sha256=sha(manifest),
        status='passed' if all(x['passed'] for x in checks) else 'failed',checks=checks,
        total=len(checks),failed=sum(not x['passed'] for x in checks))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();report=verify(a.manifest)
    with a.output.open('x') as f:f.write(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','total','failed')}))
    return int(report['status']!='passed')
if __name__=='__main__':raise SystemExit(main())
