#!/usr/bin/env python3
"""Build immutable 香山 libmvec images with installed libraries and MPFR data."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from generate_data import generate, FUNCS

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
DEFAULT_PREFIX=Path('/nfs/share/huxuan/riscv-gnu-toolchain-gcc16.1.0-20261109-142438-6ba7795932e0-libmvec')
MODES=['scalar_strict','scalar_fast','auto_m1','auto_m2','direct_m1','direct_m2']

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def identity(p):
    p=Path(p).resolve(); s=p.stat()
    return dict(path=str(p),size=s.st_size,mtime_ns=s.st_mtime_ns,sha256=sha(p))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix',type=Path,default=DEFAULT_PREFIX)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--case',action='append',choices=[*FUNCS,'abi-public'])
    parser.add_argument('--profile',choices=['full','format-smoke','n16','n16-trim'],default='full',
                        help='format-smoke: log only, n=16 K=1, same ten rounds; not full acceptance')
    parser.add_argument('--math-archive',type=Path,help='explicit replacement math archive; retain installed scalar libm')
    parser.add_argument('--lto',action='store_true',help='enable LTO in benchmark translation units and final link')
    a=parser.parse_args(); prefix=a.prefix.resolve()
    if a.math_archive and not a.math_archive.is_file(): parser.error('math archive does not exist')
    if a.profile=='format-smoke' and a.case not in (None,['log']):
        parser.error('format-smoke only supports --case log')
    if a.profile=='n16-trim' and a.case and 'abi-public' in a.case:
        parser.error('n16-trim contains only the seven performance cases')
    modes=['scalar_fast','direct_m1','direct_m2'] if a.profile=='n16-trim' else MODES
    controls=[] if a.profile=='n16-trim' else ['rdcycle','empty','copy']
    if a.output:
        out=a.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    else:
        base=HERE/'build'; base.mkdir(exist_ok=True)
        out=Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-'),dir=base))
    print('BUILD_DIRECTORY='+str(out),flush=True)
    commands=[]
    def run(cmd,label,cwd=None):
        cmd=list(map(str,cmd)); p=subprocess.run(cmd,cwd=cwd,capture_output=True,text=True)
        (out/(label+'.stdout')).write_text(p.stdout); (out/(label+'.stderr')).write_text(p.stderr)
        commands.append(dict(label=label,command=cmd,returncode=p.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        if p.returncode: raise RuntimeError(f'{label}: exit {p.returncode}: {p.stderr[-4000:]}')
        return p.stdout
    tools={n:prefix/'bin'/('riscv64-unknown-linux-gnu-'+n) for n in ('gcc','ar','nm','readelf','objdump','objcopy')}
    cc=tools['gcc']; common=['-O3','-g','-march=rva23u64','-mabi=lp64d','-mcmodel=medany',
        '-fno-pie','-fno-stack-protector','-flto' if a.lto else '-fno-lto','-mrvv-vector-bits=scalable', '-I'+str(HERE)]
    if a.math_archive: common+=['-I'+str(HERE/'overlay')]
    target=['-march=rva23u64','-mabi=lp64d']
    sysroot=Path(run([cc,'-print-sysroot'],'sysroot').strip()).resolve()
    if sysroot!=prefix/'sysroot': raise RuntimeError('compiler/sysroot mismatch')
    libs={n:Path(run([cc,*target,'-print-file-name='+n],'path-'+n).strip()).resolve() for n in ('libmvec.a','libm.a','libm-2.44.a','libc.a','libgcc.a')}
    for n,p in libs.items():
        if not p.is_file() or not p.is_relative_to(prefix): raise RuntimeError(f'library outside installed prefix: {n}={p}')
    allsyms=run([tools['nm'],'-g','--defined-only',libs['libmvec.a']],'libmvec-symbols')
    public_symbols=sorted(set(re.findall(r'\b(_ZGV\w+)\s*$',allsyms,re.M))-{x for x in re.findall(r'\b(_ZGV\w+)\s*$',allsyms,re.M) if x.endswith('_len')})
    if len(public_symbols)!=36: raise RuntimeError(f'expected 36 public symbols, got {len(public_symbols)}')
    version=run([cc,'--version'],'compiler-version').splitlines()[0]
    oracle=generate(out/'data')
    paths=list(HERE.glob('*.c'))+list(HERE.glob('*.h'))+list(HERE.glob('*.py'))+[HERE/'PROTOCOL.md',ROOT/'autovec/av_runtime.c',ROOT/'autovec/av_runtime.h',ROOT/'single/veclibm-runtime.c',ROOT/'autovec/tls.ld',ROOT/'thirdparty/link.ld']
    snap=out/'source'; snap.mkdir()
    source_ids={}
    for path in paths:
        relative=path.relative_to(ROOT); dest=snap/relative; dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(path.read_bytes()); source_ids[str(relative)]={**identity(dest),'original_path':str(path)}
    source_here=snap/'single/libmvec'
    source_root=snap
    # Compile the archived source bytes, not a live tree another worker may edit.
    common=[flag for flag in common if flag!='-I'+str(HERE)]+['-I'+str(source_here)]
    run([cc,*common,'-O2','-ffreestanding','-fno-builtin','-fno-tree-vectorize','-c',source_root/'single/veclibm-runtime.c','-o',out/'runtime.o'],'runtime')
    manifest=dict(schema_version=1,suite='libmvec-xs',build_status='building',
        created_at=datetime.now(timezone.utc).isoformat(), toolchain=dict(prefix=str(prefix),version=version,
        tools={n:identity(p) for n,p in tools.items()},libraries={n:identity(p) for n,p in libs.items()},
        headers={str(p.relative_to(sysroot)):identity(p) for p in [sysroot/'usr/include/math.h',sysroot/'usr/include/bits/math-vector.h']}),
        sources=source_ids,oracle=oracle,
        support_archives={str(p.relative_to(ROOT)):identity(p) for p in [ROOT/'thirdparty/am-riscv64-xs.a',ROOT/'thirdparty/klib-riscv64-xs.a']},cases=[])
    if a.math_archive:
        for name in ('entry_rne','tls_anchor'):
            run([cc,*common,'-fno-fast-math','-c',HERE/(name+'.c'),'-o',out/(name+'.o')],name)
    manifest['profile']=a.profile
    manifest['lto']=a.lto
    if a.math_archive: manifest['math_archive']=identity(a.math_archive)
    selected=a.case or (['log'] if a.profile=='format-smoke' else list(FUNCS) if a.profile in ('n16','n16-trim') else [*FUNCS,'abi-public'])
    def save(): (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    save()
    for f in selected:
        name='abi-public' if f=='abi-public' else 'perf-'+f
        dest=out/name; dest.mkdir(); elf=dest/(name+'.elf'); binary=elf.with_suffix('.bin')
        entry=dict(name=name,suite='accuracy' if f=='abi-public' else 'performance',status='failed',
            bin=str(binary.relative_to(out)),elf=str(elf.relative_to(out)),required_markers=[f'LIBMVEC_COMPLETE case={name} status=pass'])
        try:
            objs=[]; defs=[]
            if f=='abi-public':
                obj=dest/'abi.o'; run([cc,*common,'-fno-fast-math','-fno-tree-vectorize','-c',source_here/'abi.c','-o',obj],name+'/compile'); objs.append(obj)
                entry['expected']=dict(min_checks=36,abi_symbols=public_symbols)
            else:
                single=f.endswith('f'); binary_arg=f=='pow'; sew=32 if single else 64
                defs=['-DFUNC='+f,f'-DFLOAT_INPUT={int(single)}',f'-DBINARY_INPUT={int(binary_arg)}']
                for mode in modes:
                    obj=dest/(mode+'.o'); objs.append(obj)
                    flags=[*common,*defs,'-DKERNEL_NAME='+mode,f'-DN16_TRIM={int(a.profile=="n16-trim")}']
                    if mode.startswith('scalar'):
                        flags+=['-fno-tree-vectorize','-fno-tree-slp-vectorize','-ffast-math' if mode=='scalar_fast' else '-fno-fast-math']
                        source=source_here/'kernel-loop.c'
                    elif mode.startswith('auto'):
                        # Preserve each auto TU's LMUL and vectorization options;
                        # GCC LTO merges incompatible per-TU target settings.
                        if a.lto: flags+=['-fno-lto']
                        lmul=mode[-2:]; flags+=['-ffast-math','-fopenmp-simd','-DAUTO_VECTOR=1','-mrvv-max-lmul='+lmul,'-fopt-info-vec-all='+str(dest/(mode+'.vec.txt'))]
                        source=source_here/'kernel-loop.c'
                    else:
                        lmul=mode[-2:]; sym=f'_ZGV{lmul[-1]}Nx'+('vv' if binary_arg else 'v')+'_'+f
                        flags+=['-fno-fast-math','-fno-tree-vectorize',f'-DVECTOR_T=vfloat{sew}{lmul}_t','-DVECTOR_SYMBOL='+sym,
                            f'-DSETVLMAX=__riscv_vsetvlmax_e{sew}{lmul}',f'-DVLOAD=__riscv_vle{sew}_v_f{sew}{lmul}',f'-DVSTORE=__riscv_vse{sew}_v_f{sew}{lmul}']
                        source=source_here/'kernel-direct.c'
                    run([cc,*flags,'-c',source,'-o',obj],name+'/'+mode)
                    dis=run([tools['objdump'],'-dr',obj],name+'/'+mode+'.disassembly')
                    if a.lto:
                        continue  # Inspect actual calls after the final LTO link.
                    if mode.startswith(('auto','direct')):
                        expected=f'_ZGV{mode[-1]}Nx'+('vv' if binary_arg else 'v')+'_'+f
                        if expected not in dis: raise RuntimeError(f'{mode} missing actual call {expected}')
                        seen=set(re.findall(r'_ZGV\w+',dis))
                        allowed={expected}
                        # GCC may vectorize the m2 remainder with a real m1 clone.
                        # Record it rather than mislabeling a valid epilogue as m2-only.
                        if mode=='auto_m2': allowed.add('_ZGV1Nx'+('vv' if binary_arg else 'v')+'_'+f)
                        if seen-allowed: raise RuntimeError(f'{mode} unexpected vector symbols {seen-allowed}')
                        entry.setdefault('observed_vector_calls',{})[mode]=sorted(seen)
                    elif '_ZGV' in dis or not re.search(r'R_RISCV_CALL(?:_PLT)?\s+'+re.escape(f)+r'\b',dis):
                        raise RuntimeError(f'{mode} missing scalar call or unexpected vector clone')
                if controls:
                    obj=dest/'calibration.o'; objs.append(obj)
                    run([cc,*common,*defs,'-fno-fast-math','-fno-tree-vectorize','-fno-tree-loop-distribute-patterns','-c',source_here/'kernel-calibration.c','-o',obj],name+'/calibration')
                obj=dest/'main.o'; objs.append(obj)
                run([cc,*common,*defs,'-fno-fast-math','-fno-tree-vectorize',f'-DPILOT_BATCH={int(f in ("log","exp"))}',
                    f'-DFORMAT_SMOKE={int(a.profile=="format-smoke")}', f'-DN16_ONLY={int(a.profile in ("n16","n16-trim"))}', f'-DN16_TRIM={int(a.profile=="n16-trim")}',
                    '-DDATA_HEADER="'+str(out/'data'/(f+'.h'))+'"','-c',source_here/'perf.c','-o',obj],name+'/main')
                batches=[1] if a.profile=='format-smoke' else ([1,4,16] if f in ('log','exp') else [1,4])
                lengths=[16] if a.profile in ('format-smoke','n16','n16-trim') else [16,128,1024]
                entry['required_markers']=[f'E,{name},pass']
                entry['expected']=dict(wire_format='csv2',profile=a.profile,function=f,modes=modes,lengths=lengths,batches=batches,
                    phases=dict(first=1,warmup=2,steady=7),sample_count=len(modes)*len(lengths)*len(batches)*10,
                    calibration_count=len(controls)*len(lengths)*len(batches)*10,calibration_modes=controls,
                    order_policy='rotate-all-'+str(len(modes)+len(controls)),min_checks=1000)
            extra_link=([a.math_archive.resolve(),out/'entry_rne.o',out/'tls_anchor.o','-Wl,--wrap=main'] if a.math_archive else [])
            link=[cc,*common,'-nostdlib','-static','-no-pie',*objs,*extra_link,out/'runtime.o','-Wl,--wrap=_trm_init','-Wl,--build-id=none',
                '-T',HERE/'tls-safe.ld' if a.math_archive else source_root/'autovec/tls.ld','-T',source_root/'thirdparty/link.ld','-Wl,-Map='+str(dest/'link.map'),'-Wl,-t',
                ROOT/'thirdparty/am-riscv64-xs.a','-Wl,--start-group',ROOT/'thirdparty/klib-riscv64-xs.a','-lm','-lc','-lgcc','-Wl,--end-group','-o',elf]
            trace=run(link,name+'/link')
            if a.math_archive and str(a.math_archive.resolve()) not in trace:
                raise RuntimeError('replacement math archive missing from link trace')
            if a.math_archive and 'libmvec.a(' in (dest/'link.map').read_text():
                raise RuntimeError('installed libmvec supplied symbols instead of explicit math archive')
            for lib in (('libm-2.44.a',) if a.math_archive else ('libmvec.a','libm-2.44.a')):
                if lib not in trace: raise RuntimeError('link trace lacks '+lib)
            resolved_libraries={}
            for line in trace.splitlines():
                path=Path(line.strip())
                if path.name in libs:
                    resolved=path.resolve()
                    if resolved!=libs[path.name]: raise RuntimeError('link trace library mismatch: '+str(resolved))
                    resolved_libraries[path.name]=str(resolved)
            entry['linked_libraries']=resolved_libraries
            undef=run([tools['nm'],'-u',elf],name+'/undefined')
            if undef.strip(): raise RuntimeError('unresolved symbols: '+undef)
            headers=run([tools['readelf'],'-h','-l','-d','-A',elf],name+'/elf')
            if 'INTERP' in headers or '(NEEDED)' in headers or not re.search(r'Entry point address:\s+0x80000000\b',headers):
                raise RuntimeError('invalid bare-metal ELF entry/loader')
            linked_disassembly=run([tools['objdump'],'-d',elf],name+'/disassembly')
            if a.lto and f!='abi-public':
                # Record the real final-code result, including clones/internalization.
                entry['lto_vector_call_sites']={sym:len(re.findall(r'\b(?:jal|j)\s+[^\n]*<'+re.escape(sym)+r'(?:[.][^>]*)?>',linked_disassembly))
                    for sym in (f'_ZGV1Nx'+('vv' if binary_arg else 'v')+'_'+f,
                                f'_ZGV2Nx'+('vv' if binary_arg else 'v')+'_'+f)}
                if not any(entry['lto_vector_call_sites'].values()):
                    raise RuntimeError('LTO final image has no auditable vector call; inspect before accepting')
            run([tools['objcopy'],'-S','--set-section-flags','.bss=alloc,contents','-O','binary',elf,binary],name+'/objcopy')
            entry.update(status='built',sha256=sha(binary),elf_sha256=sha(elf),binary_size=binary.stat().st_size)
        except Exception as exc:
            entry['error']=str(exc); print(name+': '+str(exc),file=sys.stderr,flush=True)
        manifest['cases'].append(entry); save(); print(entry['status'].upper()+' '+name,flush=True)
    manifest['build_status']='built' if all(c['status']=='built' for c in manifest['cases']) else 'failed'; save()
    print('MANIFEST='+str(out/'manifest.json'),flush=True)
    return int(manifest['build_status']!='built')

if __name__=='__main__': sys.exit(main())
