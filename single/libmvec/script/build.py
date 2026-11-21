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
import shlex

HERE=Path(__file__).resolve().parent          # single/libmvec/script
LIB=HERE.parent                                # single/libmvec
ROOT=LIB.parents[1]                            # rvv-bench 仓库根
THROUGHPUT=LIB/'throughput'
RUNTIME=LIB/'runtime'
ABI=LIB/'tests/abi'
TRACE=LIB/'trace'
UNIT=LIB/'tests/unit'
OVERLAY=LIB/'overlay'
FUNCS=('log','exp','sin','cos','pow','logf','expf','sinf','cosf','powf',
       'log10','atan','atan2','acos')
# Two-argument math functions use the vv mangling and the input header's
# second_bits array.
BINARY_FUNCS=frozenset(('pow','powf','atan2'))
# Functions that only exist in an explicit replacement math archive (the
# installed libmvec has no float sin/cos/pow); they extend the ABI suite.
NEW_ONLY=('sinf','cosf','powf')
# Functions with an entry in the installed compiler's riscv_simd_math_table,
# i.e. the ones auto-vectorized code can call.  The new float entries have no
# compiler ABI yet, so their auto_* modes measure the scalar code GCC emits.
GCC_ABI_FUNCTIONS=('log','exp','sin','cos','pow','logf','expf',
                   'log10','atan','atan2','acos')
MODES=['scalar_strict','scalar_fast','auto_m1','auto_m2','direct_m1','direct_m2']
MODES_LMUL48=['direct_m4','direct_m8']
# Compiler-generated LMUL4/8 columns.  They are appended only when the build
# compiler advertises the 16-bit ABI variant mask (__riscv_simd_math_abi_v1_lmul8)
# AND the math archive exports the matching entry points.
MODES_AUTO_LMUL48=['auto_m4','auto_m8']
REQUIRED_PUBLIC=sum(([f'_ZGV{i}Nx{kind}_{f}' for i in (1,2)]
                     for kind,f in ((('vv' if f=='pow' else 'v'),f) for f in FUNCS)),[])

def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1<<20),b''): h.update(b)
    return h.hexdigest()

def identity(p):
    p=Path(p).resolve(); s=p.stat()
    return dict(path=str(p),size=s.st_size,mtime_ns=s.st_mtime_ns,sha256=sha(p))

def direct_symbol(function, mode, interface):
    if interface=='array': return function+'vec_'+mode[-2:]
    return f'_ZGV{mode[-1]}Nx'+('vv' if function in BINARY_FUNCS else 'v')+'_'+function

def grid_values(text, *, max_value):
    try:
        values=[int(part) for part in text.split(',')]
    except ValueError as exc:
        raise argparse.ArgumentTypeError('expected comma-separated positive integers') from exc
    if not 1<=len(values)<=3 or any(x<1 or x>max_value for x in values) or len(set(values))!=len(values):
        raise argparse.ArgumentTypeError('expected 1–3 distinct positive integers in range')
    return values

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--data-dir',type=Path,required=True,help='explicit shared frozen perf input headers')
    parser.add_argument('--case',action='append',choices=[*FUNCS,'abi-public','float3-unit'],required=True)
    parser.add_argument('--profile',choices=['full','format-smoke','n16','grid'],default='n16',
                        help='grid uses explicit lengths/batches and retains the full mode matrix and ten rounds')
    parser.add_argument('--lengths',type=lambda s:grid_values(s,max_value=1024))
    parser.add_argument('--batches',type=lambda s:grid_values(s,max_value=1000000))
    parser.add_argument('--math-archive',type=Path,help='explicit replacement math archive; retain installed scalar libm')
    parser.add_argument('--binutils-prefix',type=Path,
        help='prefix supplying binutils (nm/ar/objdump/readelf/objcopy) when --prefix '
             'is a driver-only compiler install; defaults to --prefix')
    parser.add_argument('--external-sysroot',action='store_true',
        help='accept a compiler whose sysroot is not under --prefix (driver-only install); '
             'the external sysroot and every linked library are recorded with sha256')
    parser.add_argument('--lto',action='store_true',help='enable LTO in benchmark translation units and final link')
    parser.add_argument('--direct-interface',choices=['nx','array'],default='nx',
                        help='direct kernels call Nx vector ABI (default) or array ABI')
    parser.add_argument('--max-lmul',choices=['2','8'],default='8',
                        help='cap the measurement matrix at m2, or detect available modes through m8')
    parser.add_argument('--timing-repeat',type=int,default=1,
                        help='kernel repeats inside each rdcycle interval; R>=1, '
                             'not a workload dimension (K stays the call count)')
    a=parser.parse_args(); prefix=a.prefix.resolve(); data_dir=a.data_dir.resolve()
    if not prefix.is_dir() or not data_dir.is_dir(): parser.error('prefix and data-dir must be directories')
    if a.math_archive:
        a.math_archive=a.math_archive.resolve()
        if not a.math_archive.is_file(): parser.error('math archive does not exist')
        with a.math_archive.open('rb') as stream:
            if stream.read(8)!=b'!<arch>\n': parser.error('expected a regular static math archive')
    if a.profile=='format-smoke' and a.case!=['log']:
        parser.error('format-smoke only supports --case log')
    if len(set(a.case))!=len(a.case): parser.error('duplicate --case')
    if a.profile=='grid' and (not a.lengths or not a.batches): parser.error('grid needs --lengths and --batches')
    if a.profile!='grid' and (a.lengths or a.batches): parser.error('--lengths and --batches require --profile grid')
    if not 1<=a.timing_repeat<=64: parser.error('--timing-repeat must be in [1,64]')
    for f in a.case:
        if f not in ('abi-public','float3-unit') and not (data_dir/(f+'.h')).is_file():
            parser.error('missing frozen input header for '+f)
    modes=MODES
    controls=['rdcycle','empty','copy']
    out=a.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    print('BUILD_DIRECTORY='+str(out),flush=True)
    commands=[]
    def run(cmd,label,cwd=None):
        cmd=list(map(str,cmd)); p=subprocess.run(cmd,cwd=cwd,capture_output=True,text=True)
        (out/(label+'.stdout')).write_text(p.stdout); (out/(label+'.stderr')).write_text(p.stderr)
        commands.append(dict(label=label,command=cmd,cwd=str(Path(cwd).resolve()) if cwd else str(Path.cwd()),returncode=p.returncode))
        (out/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
        with (out/'commands.txt').open('a') as stream:
            stream.write('['+label+'] '+shlex.join(cmd)+'\nrc='+str(p.returncode)+'\n')
        if p.returncode: raise RuntimeError(f'{label}: exit {p.returncode}: {p.stderr[-4000:]}')
        return p.stdout
    binutils=prefix if not a.binutils_prefix else a.binutils_prefix
    tools={n:prefix/'bin'/('riscv64-unknown-linux-gnu-'+n) for n in ('gcc',)}
    tools.update({n:binutils/'bin'/('riscv64-unknown-linux-gnu-'+n)
                  for n in ('ar','nm','readelf','objdump','objcopy')})
    cc=tools['gcc']; common=['-O3','-g','-march=rva23u64','-mabi=lp64d','-mcmodel=medany',
        '-fno-pie','-fno-stack-protector','-flto' if a.lto else '-fno-lto','-mrvv-vector-bits=scalable',
        '--param=gpr2vr-cost=0','--param=fpr2vr-cost=0','-I'+str(THROUGHPUT)]
    if a.math_archive: common+=['-I'+str(OVERLAY)]
    target=['-march=rva23u64','-mabi=lp64d']
    sysroot=Path(run([cc,'-print-sysroot'],'sysroot').strip()).resolve()
    # A driver may bake in a sysroot from another prefix (the GCC-ABI experiment
    # toolchain shares build-20261115-release/combined/sysroot).  Accept that as
    # long as both the compiler and its sysroot are recorded in the manifest.
    if not sysroot.is_dir(): raise RuntimeError(f'compiler sysroot missing: {sysroot}')
    prefix_sysroot=prefix/'sysroot'
    if not prefix_sysroot.is_dir():
        print('NOTE: compiler sysroot is '+str(sysroot)+' (prefix has none)',file=sys.stderr)
    elif sysroot!=prefix_sysroot.resolve(): raise RuntimeError('compiler/sysroot mismatch')
    libs={n:Path(run([cc,*target,'-print-file-name='+n],'path-'+n).strip()).resolve() for n in ('libmvec.a','libm.a','libm-2.44.a','libc.a','libgcc.a')}
    # The driver, not this script, must be the single source of truth for which
    # runtime libraries a build links.  Standard prefixes keep everything under
    # prefix/ or the sysroot; an experiment driver may also resolve libgcc.a
    # from its own install dir (the GCC-ABI toolchain does).  Every resolved
    # path and hash still lands in the manifest, so a mixed toolchain is
    # auditable rather than silently accepted.
    outside=sorted(f'{n}={p}' for n,p in libs.items()
                   if not (p.is_relative_to(prefix) or p.is_relative_to(sysroot)))
    for n,p in libs.items():
        if not p.is_file(): raise RuntimeError(f'compiler-resolved library missing: {n}={p}')
    if outside:
        print('NOTE: compiler-resolved libraries outside prefix/sysroot: '
              + ', '.join(outside), file=sys.stderr)
    archive=a.math_archive or libs['libmvec.a']
    archive_id=identity(archive)
    if a.math_archive:
        if archive.samefile(libs['libmvec.a']):
            raise RuntimeError('explicit math archive aliases the installed libmvec')
        for name in ('libm-2.44.a','libc.a','libgcc.a'):
            with libs[name].open('rb') as stream:
                if stream.read(8)!=b'!<arch>\n': raise RuntimeError(f'not a regular static archive: {libs[name]}')
    # gcc-nm also reads slim GCC LTO archives.
    gcc_nm=prefix/'bin/riscv64-unknown-linux-gnu-gcc-nm'
    allsyms=run([gcc_nm,'-g','--defined-only',archive],'libmvec-symbols')
    available=set(re.findall(r'\b([A-Za-z_]\w*)\s*$',allsyms,re.M))
    public_symbols={x for x in available if x.startswith('_ZGV') and not x.endswith('_len')}
    if not a.math_archive and len(public_symbols)<36:
        raise RuntimeError(f'expected at least the 36 released symbols, got {len(public_symbols)}')
    # The ABI suite audits exactly the entries it declares (36 released plus any
    # candidates an explicit math archive adds); derive that list from abi.c so
    # the audit can never silently skip a declared entry.
    abi_declared=sorted(set(re.findall(r'\b(_ZGV\w+)\s*\(',(ABI/'abi.c').read_text())))
    selected=a.case
    if a.direct_interface=='array':
        required={f+'vec_'+mode for f in selected if f not in ('abi-public','float3-unit')
                  for mode in ('m1','m2')}
        missing=sorted(required-available)
        if missing: raise RuntimeError(f'array interface symbols missing from {archive}: {", ".join(missing)}')
    else:
        # Symbol set of the archive that actually supplies the direct kernels.
        archive=a.math_archive.resolve() if a.math_archive else libs['libmvec.a']
        gcc_nm=prefix/'bin/riscv64-unknown-linux-gnu-gcc-nm'
        symbol_text=run([gcc_nm,'-g','--defined-only',archive],'archive-symbols')
        available=set(re.findall(r'\b([A-Za-z_]\w*)\s*$',symbol_text,re.M))
    def extended_modes(function):
        """Append direct_m4/direct_m8 only when both kernels exist."""
        if a.direct_interface=='array':
            have=all(function+'vec_'+lmul in available for lmul in ('m4','m8'))
        else:
            kind='vv' if function=='pow' else 'v'
            have=all(f'_ZGV{lmul[-1]}Nx{kind}_{function}' in available
                     for lmul in ('m4','m8'))
        modes=MODES+(MODES_LMUL48 if have else [])
        if have and compiler_lmul48:
            modes=modes+MODES_AUTO_LMUL48
        return list(modes)
    version=run([cc,'--version'],'compiler-version').splitlines()[0]
    oracle={'kind':'explicit shared frozen headers','functions':{f:identity(data_dir/(f+'.h')) for f in selected if f not in ('abi-public','float3-unit')}}
    # 只冻结构建与 trace 实际复用的输入：吞吐/运行/ABI 源码与头、trace 源码、
    # 协议文档（throughput/README.md 即原 PROTOCOL.md 的迁移目标）、覆盖头、
    # 两个会产出/消费本 manifest 的脚本，以及仓库侧 runtime/link 输入。
    paths=([THROUGHPUT/n for n in ('perf.c','kernel-loop.c','kernel-direct.c','kernel-array.c',
                                   'kernel-calibration.c','kernel.h')]
           +[RUNTIME/n for n in ('entry_rne.c','tls_anchor.c','tls-safe.ld')]
           +[ABI/'abi.c',ABI/'abi-data.h',TRACE/'trace.c',UNIT/'float3_unit.c',
             THROUGHPUT/'README.md',OVERLAY/'bits/math-vector.h',HERE/'build.py',HERE/'build_trace.py',
             ROOT/'autovec/av_runtime.c',ROOT/'autovec/av_runtime.h',
             ROOT/'single/common/am_runtime.c',ROOT/'autovec/tls.ld',ROOT/'thirdparty/link.ld'])
    snap=out/'source'; snap.mkdir()
    source_ids={}
    for path in paths:
        relative=path.relative_to(ROOT); dest=snap/relative; dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(path.read_bytes()); source_ids[str(relative)]={**identity(dest),'original_path':str(path)}
    source_here=snap/'single/libmvec'
    throughput=source_here/'throughput'
    source_root=snap
    # Compile the archived source bytes, not a live tree another worker may edit.
    common=[flag for flag in common if flag not in ('-I'+str(THROUGHPUT),'-I'+str(OVERLAY))]
    common+=['-I'+str(source_here/'throughput'),'-I'+str(source_here/'tests/abi'),
             '-I'+str(source_root/'autovec'),'-I'+str(source_root)]
    if a.math_archive: common+=['-I'+str(source_here/'overlay')]
    run([cc,*common,'-O2','-ffreestanding','-fno-builtin','-fno-tree-vectorize','-c',source_root/'single/common/am_runtime.c','-o',out/'runtime.o'],'runtime')
    manifest=dict(schema_version=1,suite='libmvec-xs',build_status='building',
        created_at=datetime.now(timezone.utc).isoformat(), toolchain=dict(prefix=str(prefix),version=version,sysroot=str(sysroot),library_location_note=outside,
        tools={n:identity(p) for n,p in tools.items()},libraries={n:identity(p) for n,p in libs.items()},
        headers={str(p.relative_to(sysroot)):identity(p) for p in [sysroot/'usr/include/math.h',sysroot/'usr/include/bits/math-vector.h']}),
        sources=source_ids,oracle=oracle,library_extra_symbols=library_extra_symbols,
        support_archives={str(p.relative_to(ROOT)):identity(p) for p in [ROOT/'thirdparty/am-riscv64-xs.a',ROOT/'thirdparty/klib-riscv64-xs.a']},cases=[])
    if a.math_archive:
        for name in ('entry_rne','tls_anchor'):
            run([cc,*common,'-fno-lto','-fno-fast-math','-c',source_here/'runtime'/(name+'.c'),'-o',out/(name+'.o')],name)
    manifest['profile']=a.profile
    manifest['direct_interface']=a.direct_interface
    manifest['auto_interface']='nx'
    manifest['lto']=a.lto
    manifest['timing_repeat']=a.timing_repeat
    manifest['lto_policy']='mixed: auto TUs non-LTO; other TUs and link use LTO' if a.lto else 'disabled'
    if a.math_archive: manifest['math_archive']=identity(a.math_archive)
    if a.external_sysroot:
        # Driver-only install: the sysroot is shared with the release build and
        # must be captured explicitly, otherwise the measurement cannot be traced.
        manifest['external_sysroot']={'path':str(sysroot),
            'libraries':{n:identity(p) for n,p in libs.items()}}
    def save(): (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    save()
    for f in selected:
        name=f if f in ('abi-public','float3-unit') else 'perf-'+f
        dest=out/name; dest.mkdir(); elf=dest/(name+'.elf'); binary=elf.with_suffix('.bin')
        entry=dict(name=name,suite='accuracy' if f in ('abi-public','float3-unit') else 'performance',status='failed',
            bin=str(binary.relative_to(out)),elf=str(elf.relative_to(out)),required_markers=[f'LIBMVEC_COMPLETE case={name} status=pass'])
        try:
            objs=[]; defs=[]
            modes=extended_modes(f) if f!='abi-public' else list(MODES)
            if f=='abi-public':
                # Every entry abi.c declares must be provided by the installed
                # libmvec or by the explicit math archive; the collector then
                # requires one LIBMVEC_ABI record per declared symbol.
                missing=sorted(set(abi_declared)-public_symbols)
                if missing: raise RuntimeError('abi-public symbols not provided: '+', '.join(missing))
                obj=dest/'abi.o'; run([cc,*common,'-fno-fast-math','-fno-tree-vectorize','-c',source_here/'tests/abi/abi.c','-o',obj],name+'/compile'); objs.append(obj)
                entry['expected']=dict(min_checks=len(abi_declared),abi_symbols=abi_declared)
            elif f=='float3-unit':
                # Direct unit test of the array entries; it emits its own
                # LIBMVEC_CHECK/LIBMVEC_COMPLETE records and no LIBMVEC_ABI
                # records, so abi_symbols stays empty.
                obj=dest/'float3_unit.o'
                run([cc,*common,'-I'+str(source_here/'tests/unit'),'-I'+str(source_here/'tests/abi'),
                     '-fno-fast-math','-fno-tree-vectorize','-c',
                     source_here/'tests/unit/float3_unit.c','-o',obj],name+'/compile')
                objs.append(obj)
                entry['expected']=dict(min_checks=1,abi_symbols=[])
            else:
                single=f.endswith('f'); binary_arg=f in BINARY_FUNCS; sew=32 if single else 64
                defs=['-DFUNC='+f,f'-DFLOAT_INPUT={int(single)}',f'-DBINARY_INPUT={int(binary_arg)}']
                for mode in modes:
                    obj=dest/(mode+'.o'); objs.append(obj)
                    flags=[*common,*defs,'-DKERNEL_NAME='+mode,f'-DN16_TRIM={int(a.profile=="n16-trim")}']
                    if mode.startswith('scalar'):
                        flags+=['-fno-tree-vectorize','-fno-tree-slp-vectorize','-ffast-math' if mode=='scalar_fast' else '-fno-fast-math']
                        source=throughput/'kernel-loop.c'
                    elif mode.startswith('auto'):
                        # Preserve each auto TU's LMUL and vectorization options;
                        # GCC LTO merges incompatible per-TU target settings.
                        if a.lto: flags+=['-fno-lto']
                        lmul=mode[-2:]; flags+=['-ffast-math','-fopenmp-simd','-DAUTO_VECTOR=1','-mrvv-max-lmul='+lmul,'-fopt-info-vec-all='+str(dest/(mode+'.vec.txt'))]
                        source=throughput/'kernel-loop.c'
                    elif a.direct_interface=='array':
                        sym=direct_symbol(f,mode,'array')
                        flags+=['-fno-fast-math','-fno-tree-vectorize','-DARRAY_SYMBOL='+sym]
                        source=throughput/'kernel-array.c'
                    else:
                        lmul=mode[-2:]; sym=f'_ZGV{lmul[-1]}Nx'+('vv' if binary_arg else 'v')+'_'+f
                        flags+=['-fno-fast-math','-fno-tree-vectorize',f'-DVECTOR_T=vfloat{sew}{lmul}_t','-DVECTOR_SYMBOL='+sym,
                            f'-DSETVLMAX=__riscv_vsetvlmax_e{sew}{lmul}',f'-DVLOAD=__riscv_vle{sew}_v_f{sew}{lmul}',f'-DVSTORE=__riscv_vse{sew}_v_f{sew}{lmul}']
                        source=throughput/'kernel-direct.c'
                    run([cc,*flags,'-c',source,'-o',obj],name+'/'+mode)
                    dis=run([tools['objdump'],'-dr',obj],name+'/'+mode+'.disassembly')
                    if a.lto:
                        continue  # Inspect actual calls after the final LTO link.
                    if mode.startswith(('auto','direct')):
                        expected=direct_symbol(f,mode,a.direct_interface if mode.startswith('direct') else 'nx')
                        if not re.search(r'R_RISCV_CALL(?:_PLT)?\s+'+re.escape(expected)+r'\b',dis):
                            if mode.startswith('auto') and f not in GCC_ABI_FUNCTIONS:
                                # Honest negative result: the installed compiler
                                # has no vector-math ABI entry for this function,
                                # so the auto_* mode is the scalar loop.  Record
                                # it instead of failing or faking a vector call.
                                entry.setdefault('auto_vectorization',{})[mode]=(
                                    'unavailable: compiler riscv_simd_math_table has no '
                                    + f + ' entry; loop compiled scalar')
                                continue
                            raise RuntimeError(f'{mode} missing actual call {expected}')
                        seen=set(re.findall(r'_ZGV\w+|\b\w+vec_m[1248]\b',dis))
                        allowed={expected}
                        # The vectorizer may keep a remainder loop in a narrower
                        # LMUL (auto_m8 -> m4/m2/m1, auto_m4 -> m2/m1, auto_m2 ->
                        # m1).  Record those rather than failing a valid epilogue.
                        kind='vv' if binary_arg else 'v'
                        order={'auto_m1':(), 'auto_m2':('m1',), 'auto_m4':('m2','m1'),
                               'auto_m8':('m4','m2','m1')}.get(mode,())
                        allowed.update(f'_ZGV{lmul[-1]}Nx{kind}_{f}' for lmul in order)
                        if seen-allowed: raise RuntimeError(f'{mode} unexpected vector symbols {seen-allowed}')
                        entry.setdefault('observed_vector_calls',{})[mode]=sorted(seen)
                    elif '_ZGV' in dis or not re.search(r'R_RISCV_CALL(?:_PLT)?\s+'+re.escape(f)+r'\b',dis):
                        raise RuntimeError(f'{mode} missing scalar call or unexpected vector clone')
                if controls:
                    obj=dest/'calibration.o'; objs.append(obj)
                    run([cc,*common,*defs,'-fno-fast-math','-fno-tree-vectorize','-fno-tree-loop-distribute-patterns','-c',throughput/'kernel-calibration.c','-o',obj],name+'/calibration')
                obj=dest/'main.o'; objs.append(obj)
                grid_flags=(['-DGRID_LENGTHS='+','.join(map(str,a.lengths)),'-DGRID_BATCHES='+','.join(map(str,a.batches))] if a.profile=='grid' else [])
                run([cc,*common,*defs,*grid_flags,'-fno-fast-math','-fno-tree-vectorize',f'-DHARNESS_MODES={len(modes)}',
                    f'-DPILOT_BATCH={int(f in ("log","exp"))}',
                    f'-DFORMAT_SMOKE={int(a.profile=="format-smoke")}', f'-DN16_ONLY={int(a.profile=="n16")}', '-DN16_TRIM=0',
                    f'-DTIMING_REPEAT={a.timing_repeat}',
                    '-DDATA_HEADER="'+str(data_dir/(f+'.h'))+'"','-c',throughput/'perf.c','-o',obj],name+'/main')
                if sha(data_dir/(f+'.h'))!=oracle['functions'][f]['sha256']: raise RuntimeError('shared input changed during compile')
                batches=a.batches if a.profile=='grid' else [1] if a.profile=='format-smoke' else ([1,4,16] if f in ('log','exp') else [1,4])
                lengths=a.lengths if a.profile=='grid' else [16] if a.profile in ('format-smoke','n16') else [16,128,1024]
                entry['required_markers']=[f'E,{name},pass']
                entry['expected']=dict(wire_format='csv3',profile=a.profile,function=f,modes=modes,lengths=lengths,batches=batches,
                    phases=dict(first=1,warmup=2,steady=7),sample_count=len(modes)*len(lengths)*len(batches)*10,
                    calibration_count=len(controls)*len(lengths)*len(batches)*10,calibration_modes=controls,
                    order_policy='rotate-all-'+str(len(modes)+len(controls)),min_checks=1000,
                    timing_repeat=a.timing_repeat)
            extra_link=([out/'entry_rne.o',out/'tls_anchor.o','-Wl,--wrap=main'] if a.math_archive else [])
            # -lm is a linker script that can silently pull installed libmvec.
            math_libraries=([archive,libs['libm-2.44.a'],libs['libc.a'],libs['libgcc.a']]
                            if a.math_archive else ['-lm','-lc','-lgcc'])
            link=[cc,*common,'-nostdlib','-static','-no-pie',*objs,*extra_link,out/'runtime.o','-Wl,--wrap=_trm_init','-Wl,--build-id=none',
                '-T',source_here/'runtime/tls-safe.ld' if a.math_archive else source_root/'autovec/tls.ld','-T',source_root/'thirdparty/link.ld','-Wl,-Map='+str(dest/'link.map'),'-Wl,-t',
                ROOT/'thirdparty/am-riscv64-xs.a','-Wl,--start-group',ROOT/'thirdparty/klib-riscv64-xs.a',*math_libraries,'-Wl,--end-group','-o',elf]
            trace=run(link,name+'/link')
            if a.math_archive:
                mapping=(dest/'link.map').read_text()
                members=sorted(set(re.findall(re.escape(str(archive))+r'\(([^()\n]+)\)',mapping)))
                if not members: raise RuntimeError('no explicit math archive members linked')
                if re.search(r'(?:^|[\s/])libmvec\.a\(',mapping.replace(f'{archive}(','')):
                    raise RuntimeError('installed libmvec supplied symbols instead of explicit math archive')
                entry['vector_archive']={**archive_id,'members':members}
            if sha(archive)!=archive_id['sha256']:
                raise RuntimeError('vector archive changed during build')
            resolved_libraries={}
            for line in trace.splitlines():
                path=Path(line.strip())
                if a.math_archive and path==archive:
                    continue
                if path.name in libs:
                    resolved=path.resolve()
                    if resolved!=libs[path.name]: raise RuntimeError('link trace library mismatch: '+str(resolved))
                    resolved_libraries[path.name]=str(resolved)
            required_libraries={'libm-2.44.a','libc.a','libgcc.a'}
            if not a.math_archive: required_libraries.add('libmvec.a')
            if required_libraries-resolved_libraries.keys():
                raise RuntimeError('link trace lacks '+str(sorted(required_libraries-resolved_libraries.keys())))
            entry['linked_libraries']=resolved_libraries
            undef=run([tools['nm'],'-u',elf],name+'/undefined')
            if undef.strip(): raise RuntimeError('unresolved symbols: '+undef)
            headers=run([tools['readelf'],'-h','-l','-d','-A',elf],name+'/elf')
            if 'INTERP' in headers or '(NEEDED)' in headers or not re.search(r'Entry point address:\s+0x80000000\b',headers):
                raise RuntimeError('invalid bare-metal ELF entry/loader')
            linked_disassembly=run([tools['objdump'],'-d',elf],name+'/disassembly')
            if a.lto and f not in ('abi-public','float3-unit'):
                # Record the real final-code result, including clones/internalization.
                nx_symbols=[f'_ZGV{i}Nx'+('vv' if binary_arg else 'v')+'_'+f for i in (1,2)]
                array_symbols=[f+'vec_m'+str(i) for i in (1,2)]
                audited=nx_symbols if a.direct_interface=='nx' else array_symbols + (nx_symbols if 'auto_m1' in modes else [])
                entry['lto_vector_call_sites']={sym:len(re.findall(r'\b(?:jal|j)\s+[^\n]*<'+re.escape(sym)+r'(?:[.][^>]*)?>',linked_disassembly)) for sym in audited}
                if a.direct_interface=='nx':
                    if not any(entry['lto_vector_call_sites'].values()):
                        raise RuntimeError('LTO final image has no auditable vector call; inspect before accepting')
                elif not all(entry['lto_vector_call_sites'][sym] for sym in array_symbols):
                    raise RuntimeError('LTO final image missing auditable direct array calls')
                elif 'auto_m1' in modes and not any(entry['lto_vector_call_sites'][sym] for sym in nx_symbols):
                    raise RuntimeError('LTO final image has no auditable auto Nx call')
            run([tools['objcopy'],'-S','--set-section-flags','.bss=alloc,contents','-O','binary',elf,binary],name+'/objcopy')
            entry.update(status='built',sha256=sha(binary),elf_sha256=sha(elf),binary_size=binary.stat().st_size)
        except Exception as exc:
            entry['error']=str(exc); print(name+': '+str(exc),file=sys.stderr,flush=True)
        manifest['cases'].append(entry); save(); print(entry['status'].upper()+' '+name,flush=True)
    manifest['build_status']='built' if all(c['status']=='built' for c in manifest['cases']) else 'failed'; save()
    print('MANIFEST='+str(out/'manifest.json'),flush=True)
    return int(manifest['build_status']!='built')

if __name__=='__main__': sys.exit(main())
