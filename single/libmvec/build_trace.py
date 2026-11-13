#!/usr/bin/env python3
"""从已验收N16构建拆出每实现独立的短波形镜像，不生成性能验收结论。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
MODES = ('scalar_strict','scalar_fast','auto_m1','auto_m2','direct_m1','direct_m2')

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--mode', action='append', choices=MODES)
    ap.add_argument('--k', type=int, default=1)
    a = ap.parse_args()
    if a.k < 1: ap.error('k must be positive')
    source = a.build.resolve()
    manifest = json.loads((source/'manifest.json').read_text())
    case = next(c for c in manifest['cases'] if c['name']=='perf-log')
    if case['status']!='built': ap.error('source image must be built')
    original = source/case['elf']
    if hashlib.sha256(original.read_bytes()).hexdigest()!=case['elf_sha256']:
        ap.error('source ELF changed')
    commands = json.loads((source/'commands.json').read_text())
    compile_cmd = next(c['command'] for c in commands if c['label']=='perf-log/main')
    link_cmd = next(c['command'] for c in commands if c['label']=='perf-log/link')
    objcopy = next(c['command'][0] for c in commands if c['label']=='perf-log/objcopy')
    objdump = next(c['command'][0] for c in commands if c['label']=='perf-log/disassembly')
    out = a.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    records=[]; images=[]
    def run(label,cmd):
        with (out/(label+'.stdout')).open('w') as so, (out/(label+'.stderr')).open('w') as se:
            p=subprocess.run(list(map(str,cmd)),stdout=so,stderr=se)
        records.append(dict(label=label,command=list(map(str,cmd)),returncode=p.returncode))
        (out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
        if p.returncode: raise RuntimeError(label+' failed')
    for mode in a.mode or MODES:
        obj=out/(mode+'-main.o'); elf=out/(mode+'.elf'); binary=elf.with_suffix('.bin')
        cmd=list(compile_cmd)
        cmd[cmd.index('-c')+1]=str(HERE/'trace.c');cmd[cmd.index('-o')+1]=str(obj)
        cmd+=['-DTRACE_KERNEL='+mode,'-DTRACE_K='+str(a.k)]
        run(mode+'-compile',cmd)
        cmd=[]
        for token in link_cmd:
            p=Path(token)
            if p.parent==original.parent and p.suffix=='.o':
                if p.name=='main.o': cmd.append(str(obj))
                elif p.stem==mode or p.stem=='scalar_strict': cmd.append(token)
            elif token.startswith('-Wl,-Map='):cmd.append('-Wl,-Map='+str(elf.with_suffix('.map')))
            elif token==str(original):cmd.append(str(elf))
            else:cmd.append(token)
        run(mode+'-link',cmd)
        run(mode+'-objcopy',[objcopy,'-S','--set-section-flags','.bss=alloc,contents','-O','binary',elf,binary])
        run(mode+'-disassembly',[objdump,'-d',elf])
        images.append(dict(mode=mode,bin=str(binary),sha256=hashlib.sha256(binary.read_bytes()).hexdigest()))
    (out/'trace-manifest.json').write_text(json.dumps(dict(source_manifest=str(source/'manifest.json'),n=16,k=a.k,scope='focused trace only; independent full gates required',images=images),indent=2)+'\n')
    print(out/'trace-manifest.json')

if __name__=='__main__':main()
