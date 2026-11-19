#!/usr/bin/env python3
"""从显式 N16 非 LTO 基准复用原始 kernel/数学库，构建短 trace，不启动仿真。

当前只支持 --case log：链接后的 map 校验按 logvec.o 与 _ZGV2Nxv_log 编写，
其它函数需要同时确认数学库成员名与 Nx 符号形态后才能加入，不能靠改名冒充。
冻结源码默认从新布局键 single/libmvec/trace/trace.c 读取；只有显式
--source-key legacy 时才接受旧键 single/libmvec/trace.c（供历史构建复验，
不做路径猜测）。
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

MODES = ('scalar_strict','scalar_fast','auto_m1','auto_m2','direct_m1','direct_m2')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build', type=Path, required=True, help='显式原基准构建目录')
    ap.add_argument('--data-dir', type=Path, required=True, help='与原基准一致的共享冻结输入')
    ap.add_argument('--output', type=Path, required=True, help='新的构建输出目录')
    ap.add_argument('--mode', action='append', choices=MODES, required=True)
    ap.add_argument('--case', choices=('log',), default='log', help='当前仅支持 log（map 校验按 logvec.o/_ZGV2Nxv_log）')
    ap.add_argument('--source-key', choices=('new','legacy'), default='new',
                    help='冻结 trace.c 键：new=source/single/libmvec/trace/trace.c；'
                         'legacy=历史构建的 source/single/libmvec/trace.c（须显式指定）')
    ap.add_argument('--k', type=int, default=1)
    ap.add_argument('--offset', type=int, default=0)
    ap.add_argument('--indices', help='可选的 16 个逗号分隔的冻结输入下标')
    a = ap.parse_args()
    if a.k < 1 or not 0 <= a.offset <= 1008: ap.error('k 必须为正且 offset 在 0..1008')
    if len(set(a.mode)) != len(a.mode): ap.error('mode 不可重复')
    try: indices=[int(s) for s in a.indices.split(',')] if a.indices else None
    except ValueError: ap.error('indices 必须是整数')
    if indices is not None and (len(indices)!=16 or min(indices)<0 or max(indices)>=1024):
        ap.error('indices 需要 16 个 0..1023 之间的整数')
    source, data_dir = a.build.resolve(strict=True), a.data_dir.resolve(strict=True)
    manifest_file=source/'manifest.json'
    manifest=json.loads(manifest_file.read_text())
    if manifest.get('lto') or manifest.get('direct_interface')!='nx' or manifest.get('profile')!='n16':
        ap.error('只接受非 LTO、Nx direct、N16 基准')
    case_name='perf-'+a.case
    case=next((c for c in manifest['cases'] if c['name']==case_name),None)
    if not case or case['status']!='built' or manifest.get('build_status')!='built': ap.error('源 case 未成功构建')
    original=source/case['elf']
    if sha(original)!=case['elf_sha256'] or sha(source/case['bin'])!=case['sha256']: ap.error('源镜像 SHA 不匹配')
    data=(data_dir/(a.case+'.h')).resolve(strict=True)
    if sha(data)!=manifest['oracle']['functions'][a.case]['sha256']: ap.error('共享输入 SHA 与基准不匹配')
    key=('single/libmvec/trace.c' if a.source_key=='legacy'
         else 'single/libmvec/trace/trace.c')
    trace=source/'source'/key
    if key not in manifest['sources']:
        ap.error('基准 manifest 缺少冻结 trace 键 '+key
                 +'；历史构建请显式加 --source-key legacy')
    if sha(trace)!=manifest['sources'][key]['sha256']: ap.error('冻结 trace.c 已变化')
    math_archive=manifest.get('math_archive')
    if not math_archive: ap.error('源构建须显式传入 --math-archive')
    archive=Path(math_archive['path'])
    if sha(archive)!=math_archive['sha256']: ap.error('数学库 SHA 已变化')
    commands=json.loads((source/'commands.json').read_text())
    compile_cmd=next(c['command'] for c in commands if c['label']==case_name+'/main')
    link_cmd=next(c['command'] for c in commands if c['label']==case_name+'/link')
    objcopy=next(c['command'][0] for c in commands if c['label']==case_name+'/objcopy')
    objdump=next(c['command'][0] for c in commands if c['label']==case_name+'/disassembly')
    if '-fno-lto' not in compile_cmd or str(archive) not in link_cmd: ap.error('源构建命令与 non-LTO 数学库不一致')
    out=a.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    records=[]; images=[]
    def run(label,cmd):
        cmd=list(map(str,cmd))
        with (out/(label+'.stdout')).open('w') as so, (out/(label+'.stderr')).open('w') as se:
            p=subprocess.run(cmd,stdout=so,stderr=se)
        records.append(dict(label=label,command=cmd,cwd=str(Path.cwd()),returncode=p.returncode))
        (out/'commands.json').write_text(json.dumps(records,indent=2)+'\n')
        with (out/'commands.txt').open('a') as f: f.write('['+label+'] '+shlex.join(cmd)+'\nrc='+str(p.returncode)+'\n')
        if p.returncode: raise RuntimeError(label+' failed; see stderr')
    for mode in a.mode:
        obj=out/(mode+'-main.o'); elf=out/(mode+'.elf'); binary=elf.with_suffix('.bin')
        cmd=list(compile_cmd)
        cmd[cmd.index('-c')+1]=str(trace); cmd[cmd.index('-o')+1]=str(obj)
        data_flags=[i for i,v in enumerate(cmd) if v.startswith('-DDATA_HEADER=')]
        if len(data_flags)!=1: raise RuntimeError('source compile has no unique DATA_HEADER')
        cmd[data_flags[0]]='-DDATA_HEADER="'+str(data)+'"'
        cmd+=['-DTRACE_KERNEL='+mode,'-DTRACE_K='+str(a.k),'-DTRACE_OFFSET='+str(a.offset)]
        if indices is not None: cmd+=['-DTRACE_INDICES='+','.join(map(str,indices))]
        run(mode+'-compile',cmd)
        kernel=original.parent/(mode+'.o'); scalar=original.parent/'scalar_strict.o'
        identity={str(p):sha(p) for p in {kernel,scalar,archive}}
        cmd=[]
        for token in link_cmd:
            p=Path(token)
            if p.parent==original.parent and p.suffix=='.o':
                if p.name=='main.o': cmd.append(str(obj))
                elif p.stem==mode or p.stem=='scalar_strict': cmd.append(token)
            elif token.startswith('-Wl,-Map='): cmd.append('-Wl,-Map='+str(elf.with_suffix('.map')))
            elif token==str(original): cmd.append(str(elf))
            else: cmd.append(token)
        run(mode+'-link',cmd)
        if any(sha(p)!=s for p,s in identity.items()): raise RuntimeError('linked object or archive changed')
        mapping=elf.with_suffix('.map').read_text()
        other_providers=mapping.replace(f'{archive}(','')
        if f'{archive}(logvec.o)' not in mapping or re.search(r'(?:^|[\s/])libmvec\.a\(',other_providers):
            raise RuntimeError('wrong math symbol provider')
        run(mode+'-objcopy',[objcopy,'-S','--set-section-flags','.bss=alloc,contents','-O','binary',elf,binary])
        run(mode+'-disassembly',[objdump,'-d',elf])
        dis=(out/(mode+'-disassembly.stdout')).read_text()
        if '<trace_region>:' not in dis or '<'+mode+'>:' not in dis or '<_ZGV2Nxv_'+a.case+'>:' not in dis:
            raise RuntimeError('final trace missing trace region / kernel / Nx m2 symbol')
        images.append(dict(mode=mode,bin=str(binary),sha256=sha(binary),elf=str(elf),elf_sha256=sha(elf),linked_inputs=identity))
    if sha(data)!=manifest['oracle']['functions'][a.case]['sha256']: raise RuntimeError('input changed during build')
    result=dict(source_manifest=str(manifest_file),source_manifest_sha256=sha(manifest_file),function=a.case,n=16,k=a.k,
                data=str(data),data_sha256=sha(data),trace_source=str(trace),trace_source_sha256=sha(trace),
                offset=a.offset,indices=indices,scope='focused trace only; full gates required',images=images)
    (out/'trace-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(out/'trace-manifest.json')


if __name__=='__main__': main()
