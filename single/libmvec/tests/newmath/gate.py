#!/usr/bin/env python3
"""七函数独立array/Nx门禁；不构建新库，不启动gsim，所有目标编译为rva23u64。"""
from pathlib import Path
import argparse, gzip, hashlib, json, re, subprocess


def materialize_oracles(source, destination, names):
 # 外部.dat保存gzip压缩的冻结oracle；只在构建目录物化C头文件。
 destination.mkdir(parents=True,exist_ok=True)
 records={}
 for name in sorted(names):
  packed=source/Path(name).with_suffix('.dat')
  header=destination/name
  data=gzip.decompress(packed.read_bytes())
  with header.open('xb') as stream:stream.write(data)
  records[name]=dict(data=identity(packed),header=identity(header),encoding='gzip')
 return records
HERE=Path(__file__).resolve().parent
HARNESS=HERE.parents[1]
ROOT=HERE.parents[3]
UNIT=HERE/'unit'
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,d): p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')
def identity(p): return dict(path=str(Path(p).resolve()),sha256=sha(p))
def main():
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--new-archive',type=Path,required=True)
 ap.add_argument('--gcc',type=Path,required=True,help='RISC-V GCC绝对路径；配套binutils和libc/libgcc由此编译器查询')
 ap.add_argument('--scalar-libm',type=Path,required=True,help='显式指定标量libm archive')
 ap.add_argument('--oracle-dir',type=Path,required=True,help='gzip编码的冻结MPFR .dat数据目录，见README')
 ap.add_argument('--nemu',type=Path,help='运行时必须显式指定；build-only不需要')
 ap.add_argument('--functions',default='sin,cos,log,logf,exp,expf,pow')
 ap.add_argument('--build-only',action='store_true')
 ap.add_argument('--lto',action='store_true',help='对严格测试TU和最终链接启用LTO，其他数学语义选项不变')
 ap.add_argument('--nx-only',action='store_true',help='仅Nx；用于无array符号的旧library')
 ap.add_argument('--abi-only',action='store_true',help='只测试Nx寄存器/入站vl/环境，不以新算法全域oracle裁决旧数学')
 ap.add_argument('--family',default='new',help='结果family标签，不改变oracle或容差')
 ap.add_argument('--max-instr',type=int,default=1500000000)
 ap.add_argument('--timeout',type=int,default=900)
 ap.add_argument('--output',type=Path,required=True)
 a=ap.parse_args(); fs=a.functions.split(',')
 if not fs or len(fs)!=len(set(fs)) or not set(fs)<=set('sin cos log logf exp expf pow'.split()):ap.error('functions必须是不重复的七函数子集')
 if not a.build_only and a.nemu is None:ap.error('运行门禁必须指定 --nemu；只构建使用 --build-only')
 gcc=a.gcc.resolve();scalar_libm=a.scalar_libm.resolve();oracles=a.oracle_dir.resolve()
 for path in [gcc,scalar_libm]:
  if not path.is_file():ap.error(f'文件不存在: {path}')
 required={('trigvec-unit-data.h' if f in ['sin','cos'] else 'powvec_unit_data.h' if f=='pow' else f'{f}-unit.h') for f in fs}
 for name in required:
  packed=oracles/Path(name).with_suffix('.dat')
  if not packed.is_file():ap.error(f'oracle缺失: {packed}')
 def query(flag):return subprocess.check_output([str(gcc),flag],text=True).strip()
 tools={'gcc':str(gcc)}
 for name in ['readelf','objcopy','objdump']:
  tool=Path(query('-print-prog-name='+name))
  if not tool.is_file():ap.error(f'编译器未返回有效{name}路径: {tool}')
  tools[name]=str(tool.resolve())
 libraries={'libm':identity(scalar_libm)}
 for name in ['libc.a','libgcc.a']:
  lib=Path(query('-print-file-name='+name))
  if not lib.is_file():ap.error(f'编译器未找到{name}: {lib}')
  libraries[name]=identity(lib)
 nemu=a.nemu.resolve() if a.nemu else None
 nemu_id=identity(nemu) if nemu else None
 archive=a.new_archive.resolve();assert archive.is_file()
 out=a.output.resolve()
 if any(parent.name=='tasks' and parent.parent.name=='.dsh' for parent in [out,*out.parents]):ap.error('output不能位于任务目录；构建会物化C头文件，请使用仓库build目录或mktemp目录')
 assert not out.exists() or not any(out.iterdir()),'拒绝覆盖已有门禁目录'
 out.mkdir(parents=True,exist_ok=True)
 oracle_inputs=materialize_oracles(oracles,out/'oracles',required)
 oracles=out/'oracles'
 cmds=[];rows=[];archive_id=identity(archive)
 def run(label,cmd,timeout=120):
  cmd=list(map(str,cmd));so=out/(label+'.stdout');se=out/(label+'.stderr')
  with so.open('w') as of,se.open('w') as ef:
   p=subprocess.run(cmd,stdout=of,stderr=ef,timeout=timeout)
  cmds.append(dict(label=label,command=cmd,returncode=p.returncode));dump(out/'commands.json',cmds)
  if p.returncode:raise RuntimeError(f'{label}: rc={p.returncode}, stderr={se}')
  return so.read_text(),se.read_text()
 common=['-O2','-g','-march=rva23u64','-mabi=lp64d','-mcmodel=medany','-fno-pie','-fno-stack-protector','-flto' if a.lto else '-fno-lto','-mrvv-vector-bits=scalable','-fno-fast-math','-frounding-math','-ffp-contract=off','-fno-tree-vectorize','-fno-tree-slp-vectorize','-fno-builtin']
 def cc(label,src,dest,extra=()):run(label,[tools['gcc'],*common,*extra,'-c',src,'-o',dest])
 # 从仓库重新编译runtime及wrapper；外部预编译依赖单独登记。
 runtime=ROOT/'single/veclibm-runtime.c'
 cc('runtime',runtime,out/'runtime.o',['-ffreestanding','-I'+str(HARNESS)])
 for name in ['entry_rne','tls_anchor']:cc(name,HARNESS/f'{name}.c',out/f'{name}.o')
 cc('nx_probe',HERE/'nx_probe.S',out/'nx_probe.o')
 def link(label,objs,wraps,elf):
  return run(label,[tools['gcc'],*common,'-nostdlib','-static','-no-pie',*objs,archive,out/'entry_rne.o',out/'runtime.o',out/'tls_anchor.o',*[f'-Wl,--wrap={s}' for s in [*wraps,'main','_trm_init']],'-Wl,--build-id=none','-T',HARNESS/'tls-safe.ld','-T',ROOT/'thirdparty/link.ld','-Wl,-Map='+str(elf.with_suffix('.map')),ROOT/'thirdparty/am-riscv64-xs.a','-Wl,--start-group',ROOT/'thirdparty/klib-riscv64-xs.a',scalar_libm,libraries['libc.a']['path'],libraries['libgcc.a']['path'],'-Wl,--end-group','-o',elf])
 def tls_offset(elf,label):
  txt,_=run(label,[tools['readelf'],'-W','-s',elf]);mt=re.search(r'\d+:\s+([0-9a-f]+)\s+4\s+TLS\s+GLOBAL\s+DEFAULT\s+\d+\s+errno$',txt,re.M);assert mt,label
  return int(mt[1],16)
 def execute(tag,elf,marker):
  binary=elf.with_suffix('.bin');run(tag+'-objcopy',[tools['objcopy'],'-S','--set-section-flags','.bss=alloc,contents','-O','binary',elf,binary])
  row=dict(test=tag,elf=identity(elf),binary=identity(binary),status='built',marker=marker)
  if not a.build_only:
   so,se=run(tag+'-nemu',[nemu,'-b','--max-instr='+str(a.max_instr),binary],a.timeout)
   bad=('HIT BAD TRAP','HIT CRITICAL ERROR','DIFFTEST ERROR','difftest check failed')
   ok='HIT GOOD TRAP' in so and not any(b in so for b in bad) and not any(b in se for b in bad) and re.search(marker,se) is not None and '_ERROR ' not in se and row['binary']['sha256']==sha(binary)
   row.update(status='pass' if ok else 'fail',stdout=str(out/(tag+'-nemu.stdout')),stderr=str(out/(tag+'-nemu.stderr')))
   rows.append(row);save()
   if not ok:raise RuntimeError(f'{tag} correctness failed; {row["stderr"]}')
  else:rows.append(row);save()
 def save():dump(out/'gate-results.json',dict(schema=1,family=a.family,nx_only=a.nx_only,abi_only=a.abi_only,nemu=nemu_id,commands=str(out/'commands.json'),new_archive=archive_id,new_archive_post=identity(archive),required_march='rva23u64',caller_strict=True,direct_kernel_modes=['RNE'],outer_entry_wrapper_modes=[0,1,2,3,4],abi_scope='14 unmasked Nx only when all seven functions tested; not full36ABI',functions=fs,tests=rows,all_passed=bool(rows) and not a.build_only and all(r['status']=='pass' for r in rows),oracle_inputs=oracle_inputs,external_prebuilt_dependencies=libraries,tools={k:identity(v) for k,v in tools.items()},runtime=identity(runtime),entry_rne=identity(HARNESS/'entry_rne.c'),tls_anchor=identity(HARNESS/'tls_anchor.c'),tls_linker=identity(HARNESS/'tls-safe.ld'),am=identity(ROOT/'thirdparty/am-riscv64-xs.a'),klib=identity(ROOT/'thirdparty/klib-riscv64-xs.a')))
 for f in fs:
  d=out/('nx-'+f);d.mkdir(exist_ok=True)
  single=f in ['expf','logf'];binary=f=='pow';kind=1 if f in ['sin','cos'] else {'log':2,'logf':3,'exp':4,'expf':5,'pow':6}[f]
  if kind==1:
   oracle=oracles/'trigvec-unit-data.h';unit=UNIT/'trigvec_unit.c';names=('TRIG_UNIT_COUNT','TRIG_UNIT_ULP_LIMIT','trig_unit_input','trig_unit_'+f)
  elif binary:
   oracle=oracles/'powvec_unit_data.h';unit=UNIT/'powvec_target_unit.c';names=('POWVEC_UNIT_COUNT','POWVEC_UNIT_ULP_LIMIT','powvec_unit_x','powvec_unit_reference')
  elif f=='log':
   oracle=oracles/'log-unit.h';unit=UNIT/'logvec_safe_unit.c';names=('UNIT_COUNT','UNIT_ULP_LIMIT','unit_input','unit_reference')
  else:
   oracle=oracles/f'{f}-unit.h';unit=UNIT/'batch01_unit.c';names=('UNIT_COUNT','UNIT_LIMIT','unit_input','unit_reference')
  text=unit.read_text()
  if binary:sem=re.search(r'static const uint64_t sem\[\]\[2\]=\{(.*?)\n\};',text,re.S)[1];pairs=re.findall(r'\{([^{}]+)\}',sem)
  else:
   if single:text=text.split('#if SINGLE\nstatic const bits_t special[]=')[1].split('#else')[0];sem=text.split('{',1)[1].split('};',1)[0]
   elif f in ('exp','log'):sem=text.split('#else\nstatic const bits_t special[]={',1)[1].split('};',1)[0]
   else:sem=re.search(r'static const (?:uint64_t|bits_t) special\[\]=\{(.*?)\};',text,re.S)[1]
   pairs=[v.strip()+',0' for v in sem.replace('\n','').split(',') if v.strip()]
  header=['/* Frozen MPFR512 oracle; no tolerance relaxation. */',f'#include "{oracle}"',f'#define FUNC {f}',f'#define FN_TEXT "{f}"',f'#define SINGLE {int(single)}',f'#define BINARY {int(binary)}',f'#define KIND {kind}',f'#define COS_CASE {int(f=="cos")}',f'#define NX1 _ZGV1Nx{"vv" if binary else "v"}_{f}',f'#define NX2 _ZGV2Nx{"vv" if binary else "v"}_{f}']
  header += [f'#define {k} {v}' for k,v in zip(['ORACLE_COUNT','ORACLE_LIMIT','ORACLE_X','ORACLE_REF'],names)]
  if binary:header+=['#define ORACLE_Z powvec_unit_z']
  header+=['static const '+('uint32_t' if single else 'uint64_t')+' semantic[][2]={'+','.join('{'+p+'}' for p in pairs)+'};']
  (d/'nx_case.h').write_text('\n'.join(header)+'\n')
  dump(d/'inputs.json',dict(oracle=identity(oracle),historical_unit=identity(unit),nx_source=identity(HERE/'nx_gate.c'),probe=identity(HERE/'nx_probe.S')))
  cc('nx-'+f+'-rne-probe',HARNESS/'entry_rne.c',d/'rne_probe.o',['-D__real_main=nx_probe_main','-D__wrap_main=nx_rne_probe'])
  def compile_nx(offset):cc('nx-'+f+'-compile',HERE/'nx_gate.c',d/'unit.o',['-I'+str(d),f'-DUNIT_ERRNO_OFFSET={offset}',f'-DABI_ONLY={int(a.abi_only)}'])
  compile_nx(0);elf=d/'unit.elf';objs=[d/'unit.o',out/'nx_probe.o',d/'rne_probe.o']
  link('nx-'+f+'-link-discover',objs,[f],elf);offset=tls_offset(elf,'nx-'+f+'-tls-discover');compile_nx(offset);link('nx-'+f+'-link',objs,[f],elf);assert tls_offset(elf,'nx-'+f+'-tls')==offset
  run('nx-'+f+'-disassembly',[tools['objdump'],'-dr',elf])
  execute('nx-'+f,elf,rf'NX_COMPLETE fn={f} checks=\d+ failed=0 status=pass')
 # 原有array测试不以Nx==array代替oracle。所有旧测试直接链接新archive。
 cases=[]
 for f in fs:
  if f in ['exp','expf','logf','log']:
   islog=f=='log';src=UNIT/('logvec_safe_unit.c' if islog else 'batch01_unit.c');oracle=oracles/('log-unit.h' if islog else f'{f}-unit.h')
   defs=[f'-DFUNC={f}',f'-DVEC1={f}vec_m1',f'-DVEC2={f}vec_m2',f'-DSINGLE={int(f.endswith("f"))}',f'-DLOG_INPUT={int(f in ["log","logf"])}',f'-DDATA_HEADER="{oracle}"']
   if islog:defs+=['-DUNIT_LIMIT=UNIT_ULP_LIMIT']
   cases.append((f,src,[f],defs,'UNIT_ERRNO_OFFSET','probe_main','rne_probe',rf'UNIT_COMPLETE fn={f} checks=\d+ failed=0 status=pass'))
 if {'sin','cos'}<=set(fs):cases.append(('trig',UNIT/'trigvec_unit.c',['sin','cos'],[],'UNIT_ERRNO_OFFSET','trigvec_probe_main','trigvec_rne_probe',r'TRIG_COMPLETE checks=\d+ failed=0 status=pass'))
 if 'pow' in fs:cases.append(('pow',UNIT/'powvec_target_unit.c',['pow'],[],'POWVEC_ERRNO_TLS_OFFSET','powvec_probe_main','powvec_rne_probe',r'POWVEC_COMPLETE checks=\d+ failed=0 status=pass'))
 if a.nx_only:cases=[]
 for tag,src,wraps,defs,macro,probe_main,probe_name,marker in cases:
  d=out/('array-'+tag);d.mkdir(exist_ok=True)
  inc=['-I'+str(UNIT),'-I'+str(oracles)]
  cc('array-'+tag+'-probe',HARNESS/'entry_rne.c',d/'probe.o',[f'-D__real_main={probe_main}',f'-D__wrap_main={probe_name}'])
  def compile_array(off):cc('array-'+tag+'-compile',src,d/'unit.o',[*inc,*defs,f'-D{macro}={off}'])
  elf=d/'unit.elf';objs=[d/'unit.o',d/'probe.o'];compile_array(0);link('array-'+tag+'-discover',objs,wraps,elf);off=tls_offset(elf,'array-'+tag+'-tls-discover');compile_array(off);link('array-'+tag+'-link',objs,wraps,elf);assert tls_offset(elf,'array-'+tag+'-tls')==off
  execute('array-'+tag,elf,marker)
 assert sha(archive)==archive_id['sha256'],'新archive在测试期间变化，不能冻结门禁'
 save();print(json.dumps(dict(result=str(out/'gate-results.json'),tests=len(rows),build_only=a.build_only),ensure_ascii=False))
if __name__=='__main__':main()
