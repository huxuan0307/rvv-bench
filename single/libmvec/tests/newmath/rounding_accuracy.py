#!/usr/bin/env python3
"""七函数完整 corpus 五 frm 直接入口观察；ULP 是距 RNE 舍入参考值，不是数学误差证明。"""
from __future__ import annotations
import argparse
import array
import csv
from contextlib import closing
import gzip
import hashlib
import itertools
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
UNIT = HERE / 'unit'
HARNESS = HERE.parents[1]
ROOT = HERE.parents[3]
FUNCTIONS = ('log', 'exp', 'expf', 'logf', 'pow', 'sin', 'cos')
FIXTURES = {
 'exp': ('exp-unit.h',102039,1,'86b1414a952b04f5e30f62ba78e441dfc11b51f83a3fa65889c84e354cf64274'),
 'expf': ('expf-unit.h',100608,2,'d70e24909b2b1d2203e5d382b9643a427f90cc58f2747232b0fc81b334538106'),
 'logf': ('logf-unit.h',101385,3,'f5f37d8873c667f8e3a0c8c3576c1c93535695b04069ee352d6f16fbbe7ce1d1'),
 'log': ('log-unit.h',104965,1,'e07f8f882e82a1000fe2e785199bf0ebba2872e1f6a900decb0ccd2217b5e208'),
 'sin': ('trigvec-unit-data.h',103084,1,'247d2680e11043be5dd3c7a77639c948071362a68f44980fb8e38d148d1bae6d'),
 'cos': ('trigvec-unit-data.h',103084,1,'247d2680e11043be5dd3c7a77639c948071362a68f44980fb8e38d148d1bae6d'),
 'pow': ('powvec_unit_data.h',124238,1,'a4dbbb9daea603282f8f4df2734cf18e1075fc5adaff02c2a4fb7e5ff187898e')}
LENGTHS = (1,2,3,4,5,7,8,9,15,16,17,31,65,129,257)
GOOD = re.compile(r'(?m)^.*\bnemu:\s+HIT GOOD TRAP\s+at pc\s*=\s*0x[0-9a-fA-F]+\s*$')
ANSI = re.compile(r'\x1b\[[0-9;]*m')
BAD = ('HIT BAD TRAP','HIT CRITICAL ERROR','DIFFTEST ERROR','difftest check failed','NEMU exit with bad state')
# 仅匹配明确的独立诊断词，避免把路径、字段名和十六进制载荷误当成错误。
FATAL = re.compile(r'(?i)(?<![a-z0-9_./-])(?:error|fatal|assert(?:ion|ed)?|abort(?:ed)?|panic|timeout|timed[ -]out|segmentation fault)(?![a-z0-9_])')
FAILED = re.compile(r'(?i)(?<![a-z0-9_./-])(?:failed(?![a-z0-9_])(?!=0(?:\s|$))|fail(?![a-z0-9_])|failure(?![a-z0-9_]))')
DATE = re.compile(r'\d{8}[-_]\d{4,6}')
LANE_FIELDS = ('function','interface','lmul','sample_id','frm','x_bits','z_bits','y_bits','ref_bits','rne_ref_ulp')
GROUP_FIELDS = ('function','interface','lmul','frm','kind','group_id','n','emitted','incoming_vl','frm_after','flags','errno')
COMMAND_FIELDS = ('label','command','cwd','status','stdout','stderr','tmpdir','timeout_seconds','stdout_bytes','stdout_sha256','stderr_bytes','stderr_sha256')
ID_FIELDS = ('role','path','sha256','snapshot')


def sha(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()


def csv_write(path, fields, rows):
 with Path(path).open('x',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)


def selected(text):
 fs=text.split(',')
 if not fs or len(set(fs))!=len(fs) or not set(fs)<=set(FUNCTIONS): raise ValueError('函数必须是不重复的七函数子集')
 return fs


def array_names(fn):
 if fn in ('sin','cos'): return ('trig_unit_input',None,'trig_unit_'+fn)
 if fn=='pow': return ('powvec_unit_x','powvec_unit_z','powvec_unit_reference')
 return ('unit_input',None,'unit_reference')


def oracle(headers,fn):
 name,count,limit,digest=FIXTURES[fn]; path=Path(headers)/name
 if not path.is_file() or sha(path)!=digest: raise ValueError(f'{fn}: 冻结 oracle SHA 不符: {path}')
 text=path.read_text(encoding='ascii')
 values=[]
 width=32 if fn in ('logf','expf') else 64
 for name in array_names(fn):
  if name is None: values.append(None); continue
  m=re.search(r'\b'+re.escape(name)+r'\s*\[[^]]+\]\s*=\s*\{(.*?)\};',text,re.S)
  if not m: raise ValueError(f'缺少 oracle 数组 {name}')
  tokens=re.findall(r'0x[0-9a-fA-F]+|\b[0-9]+\b',m[1])
  vals=array.array('I' if width==32 else 'Q',(int(s,0) for s in tokens))
  if len(vals)!=count: raise ValueError(f'{fn} {name} 数量不符: {len(vals)} != {count}')
  values.append(vals)
 return values


def semantics(fn,source=None):
 if source is not None: path=Path(source)
 elif fn=='pow': path=UNIT/'powvec_target_unit.c'
 elif fn in ('sin','cos'): path=UNIT/'trigvec_unit.c'
 elif fn=='log': path=UNIT/'logvec_safe_unit.c'
 else: path=UNIT/'batch01_unit.c'
 text=path.read_text(encoding='utf-8')
 if fn=='pow':
  m=re.search(r'static const uint64_t sem\[\]\[2\]=\{(.*?)\n\};',text,re.S)
  if not m: raise ValueError('pow 语义表未找到')
  pairs=re.findall(r'\{([^{}]+)\}',m[1])
  raw=[p.split(',') for p in pairs]
 else:
  if fn in ('expf','logf'):
   part=text.split('#if SINGLE\nstatic const bits_t special[]=',1)[1].split('#else',1)[0].split('{',1)[1].split('};',1)[0]
  elif fn in ('exp','log'):
   part=text.split('#else\nstatic const bits_t special[]={',1)[1].split('};',1)[0]
  else:
   m=re.search(r'static const (?:uint64_t|bits_t) special\[\]=\{(.*?)\};',text,re.S)
   if not m: raise ValueError('语义表未找到')
   part=m[1]
  raw=[[s,'0'] for s in part.replace('\n','').split(',') if s.strip()]
 def integer(s):
  s=s.strip(); m=re.fullmatch(r'(0x[0-9a-fA-F]+|[0-9]+)[uUlL]*',s)
  if not m: raise ValueError(f'无法解析语义输入 {s}')
  return int(m[1],0)
 pairs=[tuple(integer(s) for s in pair) for pair in raw]
 if not pairs or any(len(p)!=2 or any(x<0 or x >= (1 << (32 if fn in ('expf','logf') else 64)) for x in p) for p in pairs):
  raise ValueError('语义样本非法')
 return path,pairs


def case_header(dest,headers,fn):
 path=Path(headers)/FIXTURES[fn][0]
 _,pairs=semantics(fn)
 if any(c in str(path.resolve()) for c in ('"','\n','\\')): raise ValueError('oracle 路径不能安全写入 C 头')
 single=fn in ('expf','logf'); binary=fn=='pow'; kind=1 if fn in ('sin','cos') else 0
 count='TRIG_UNIT_COUNT' if kind else 'POWVEC_UNIT_COUNT' if binary else 'UNIT_COUNT'
 limit='TRIG_UNIT_ULP_LIMIT' if kind else 'POWVEC_UNIT_ULP_LIMIT' if binary else 'UNIT_ULP_LIMIT' if fn=='log' else 'UNIT_LIMIT'
 xn,zn,rn=array_names(fn); suffix='vv' if binary else 'v'
 lines=[f'#include "{path.resolve()}"',f'#define FN_TEXT "{fn}"',f'#define SINGLE {int(single)}',f'#define BINARY {int(binary)}',f'#define KIND {kind}',
        f'#define ORACLE_COUNT {count}',f'#define ORACLE_LIMIT {limit}',f'#define ORACLE_X {xn}',f'#define ORACLE_REF {rn}',
        f'#define NX1 _ZGV1Nx{suffix}_{fn}',f'#define NX2 _ZGV2Nx{suffix}_{fn}',f'#define ARRAY1 {fn}vec_m1',f'#define ARRAY2 {fn}vec_m2']
 if binary: lines.append(f'#define ORACLE_Z {zn}')
 lines.append('static const '+('uint32_t' if single else 'uint64_t')+' semantic[][2]={'+','.join('{0x%xULL,0x%xULL}'%p for p in pairs)+'};')
 Path(dest).write_text('\n'.join(lines)+'\n',encoding='ascii')


def check_dirs(build,run):
 if build.exists() or run.exists() or build==run or build in run.parents or run in build.parents: raise ValueError('build-dir、run-dir 必须是互不包含的全新目录')
 if not DATE.search(run.name) or any('build' in p.lower() for p in run.parts): raise ValueError('run-dir 名必须含日期时间、路径不得包含 build')


def errno_offset(text):
 matches=re.findall(r'^[ \t]*\d+:[ \t]+([0-9a-fA-F]+)[ \t]+4[ \t]+TLS[ \t]+GLOBAL[ \t]+DEFAULT[ \t]+\d+[ \t]+errno[ \t]*$',text,re.M)
 if len(matches)!=1: raise ValueError('未找到唯一的 errno TLS 符号')
 return int(matches[0],16)


def run(args):
 fs=selected(args.functions); build=args.build_dir.resolve(); rd=args.run_dir.resolve(); check_dirs(build,rd)
 if bool(args.nemu)==bool(args.build_only): raise ValueError('必须二选一：--build-only 或显式 --nemu')
 if args.nemu and (not args.timeout or not args.max_instr or args.timeout<=0 or args.max_instr<=0): raise ValueError('NEMU 运行必须显式给出正 --max-instr 和 --timeout')
 gcc=args.gcc.resolve(); archive=args.archive.resolve(); libm=args.scalar_libm.resolve(); headers=args.headers_dir.resolve()
 inputs=[gcc,archive,libm]+([args.nemu.resolve()] if args.nemu else [])
 if any(not p.is_file() for p in inputs): raise ValueError('工具或库文件不存在')
 for f in fs: oracle(headers,f); semantics(f)
 build.mkdir(parents=True); rd.mkdir(parents=True); logs=rd/'logs'; logs.mkdir(); tmp=rd/'tmp'; tmp.mkdir(); stage=rd/'staging'; stage.mkdir(); snapshots=rd/'snapshot'; snapshots.mkdir()
 cmds=[]; ids=[]
 def update(name,fields,rows):
  with tempfile.NamedTemporaryFile('w',newline='',encoding='utf-8',dir=rd,delete=False,prefix=name+'.',suffix='.partial') as f:
   w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows); p=Path(f.name)
  p.replace(rd/name)
 def identify(role,path):
  p=Path(path).resolve();digest=sha(p);snap=''
  # 保存执行依赖源码、oracle、数学 archive 和最终构件，外部工具/系统库保留哈希。
  stored={'archive','observer','source','tests','readme','probe','runtime','runtime-impl','runtime-header','tls-anchor','tls-linker','linker','am','klib'}
  if role in stored or role.startswith(('semantic-','oracle-','case-','elf-','binary-')):
   d=snapshots/role;d.mkdir();target=d/p.name;shutil.copyfile(p,target)
   if sha(target)!=digest: raise ValueError(f'快照哈希不符: {role}')
   snap=str(target)
  ids.append(dict(role=role,path=str(p),sha256=digest,snapshot=snap));update('identities.csv',ID_FIELDS,ids)
 def command(label,argv,timeout=120):
  argv=list(map(str,argv)); out=logs/(label+'.stdout');err=logs/(label+'.stderr'); t=tmp/label;t.mkdir()
  env=os.environ.copy();env.update(TMPDIR=str(t),TMP=str(t),TEMP=str(t))
  row=dict(label=label,command=shlex.join(argv),cwd=str(rd),status='running',stdout=str(out),stderr=str(err),tmpdir=str(t),timeout_seconds=str(timeout),
           stdout_bytes='',stdout_sha256='',stderr_bytes='',stderr_sha256='');cmds.append(row);update('commands.csv',COMMAND_FIELDS,cmds)
  try:
   with out.open('x') as o,err.open('x') as e:
    try: row['status']=str(subprocess.run(argv,cwd=rd,env=env,stdout=o,stderr=e,timeout=timeout).returncode)
    except subprocess.TimeoutExpired: row['status']='timeout'
    except OSError as exc: row['status']='launch-error';print(exc,file=e)
  finally:
   if row['status']=='running': row['status']='interrupted'
   for name,path in (('stdout',out),('stderr',err)):
    if path.is_file(): row[name+'_bytes']=str(path.stat().st_size);row[name+'_sha256']=sha(path)
   update('commands.csv',COMMAND_FIELDS,cmds)
  if row['status']!='0': raise RuntimeError(f'{label} 状态 {row["status"]}；详见 {err}')
  return out.read_text(encoding='utf-8',errors='replace') if not label.startswith('nemu-') else ''
 def query(flag): return command('query-'+flag.removeprefix('-').replace('=','-').replace('.','-'),[gcc,flag]).strip()
 objcopy=Path(query('-print-prog-name=objcopy'));readelf=Path(query('-print-prog-name=readelf'))
 libc=Path(query('-print-file-name=libc.a'));libgcc=Path(query('-print-file-name=libgcc.a'))
 deps=[('gcc',gcc),('archive',archive),('scalar-libm',libm),('objcopy',objcopy),('readelf',readelf),('libc',libc),('libgcc',libgcc),
       ('observer',HERE/'rounding_accuracy.py'),('source',HERE/'rounding_accuracy.c'),('tests',HERE/'test_rounding_accuracy.py'),('readme',HERE/'README-rounding-accuracy.md'),('probe',HERE/'nx_probe.S'),
       ('runtime',ROOT/'single/veclibm-runtime.c'),('runtime-impl',ROOT/'autovec/av_runtime.c'),('runtime-header',ROOT/'autovec/av_runtime.h'),
       ('tls-anchor',HARNESS/'tls_anchor.c'),('tls-linker',HARNESS/'tls-safe.ld'),('linker',ROOT/'thirdparty/link.ld'),
       ('am',ROOT/'thirdparty/am-riscv64-xs.a'),('klib',ROOT/'thirdparty/klib-riscv64-xs.a')]
 if args.nemu: deps.append(('nemu',args.nemu.resolve()))
 for f in fs: deps += [('oracle-'+f,headers/FIXTURES[f][0]),('semantic-'+f,semantics(f)[0])]
 for role,path in deps: identify(role,path)
 csv_write(rd/'plan.csv',('role','functions','build_only','headers_dir'),[dict(role=args.role,functions=','.join(fs),build_only=int(args.build_only),headers_dir=str(headers))])
 common=['-O2','-g','-march=rva23u64','-mabi=lp64d','-mcmodel=medany','-fno-pie','-fno-stack-protector','-fno-lto',
         '-mrvv-vector-bits=scalable','-fno-fast-math','-frounding-math','-ffp-contract=off','-fno-tree-vectorize','-fno-tree-slp-vectorize','-fno-builtin']
 def cc(label,src,dest,extra=()): command(label,[gcc,*common,*extra,'-c',src,'-o',dest])
 cc('runtime',ROOT/'single/veclibm-runtime.c',build/'runtime.o',['-ffreestanding','-I'+str(HARNESS)])
 cc('tls-anchor',HARNESS/'tls_anchor.c',build/'tls_anchor.o');cc('nx-probe',HERE/'nx_probe.S',build/'nx_probe.o')
 for f in fs:
  d=build/f;d.mkdir();case_header(d/'rounding_case.h',headers,f);identify('case-'+f,d/'rounding_case.h')
  elf=d/'accuracy.elf';binary=d/'accuracy.bin'
  def compile_driver(label,offset):
   cc(label,HERE/'rounding_accuracy.c',d/'driver.o',['-I'+str(d),'-DERRNO_TLS_OFFSET='+str(offset)])
  def link(label):
   command(label,[gcc,*common,'-nostdlib','-static','-no-pie',d/'driver.o',build/'nx_probe.o',archive,build/'runtime.o',build/'tls_anchor.o',
    '-Wl,--wrap=_trm_init','-Wl,--build-id=none','-T',HARNESS/'tls-safe.ld','-T',ROOT/'thirdparty/link.ld','-Wl,-Map='+str(d/'accuracy.map'),
    ROOT/'thirdparty/am-riscv64-xs.a','-Wl,--start-group',ROOT/'thirdparty/klib-riscv64-xs.a',libm,libc,libgcc,'-Wl,--end-group','-o',elf])
  def offset_from_elf(label):
   return errno_offset(command(label,[readelf,'-W','-s',elf]))
  compile_driver('driver-discover-'+f,0);link('link-discover-'+f)
  offset=offset_from_elf('tls-discover-'+f)
  compile_driver('driver-'+f,offset);link('link-'+f)
  if offset_from_elf('tls-final-'+f)!=offset: raise ValueError(f'{f}: 两次链接的 errno TLS 偏移不一致')
  command('objcopy-'+f,[objcopy,'-S','--set-section-flags','.bss=alloc,contents','-O','binary',elf,binary])
  staged=stage/(f+'.bin');shutil.copyfile(binary,staged)
  if sha(binary)!=sha(staged): raise ValueError('staging 哈希不符')
  for role,p in [('elf-'+f,elf),('binary-'+f,binary),('staged-'+f,staged)]: identify(role,p)
  if args.nemu: command('nemu-'+f,[args.nemu.resolve(),'-b','--max-instr='+str(args.max_instr),staged],timeout=args.timeout)
 for row in ids:
  if sha(row['path'])!=row['sha256'] or (row['snapshot'] and sha(row['snapshot'])!=row['sha256']):
   raise ValueError(f'输入、构件或快照改变: {row["path"]}')
 print('仅构建，未仿真；不是测试通过' if args.build_only else 'NEMU 进程已结束；仍须使用 analyze 核验完整记录与 GOOD TRAP')


def expected_groups(fn,iface,m,vlenb,corpus,sem):
 xs,zs,_=corpus; count=len(xs); width=4 if fn in ('logf','expf') else 8
 full=m*vlenb//width; p=li=0
 while p<count:
  if iface==0: n=full
  elif fn in ('sin','cos'): n=min(257,count-p)
  elif fn=='pow':
   n=min(257,count-p)
   if p<115422<p+n: n=115422-p
  else: n=min(LENGTHS[li%len(LENGTHS)],count-p)
  emit=min(n,count-p)
  incoming=0 if iface else (0,1,full-1,full)[li%4]
  values=[(xs[min(j,count-1)],zs[min(j,count-1)] if zs is not None else 0) for j in range(p,p+n)]
  yield ('C',p,n,emit,incoming,values)
  p+=emit;li+=1
 ns=len(sem)
 if iface==0:
  for mixed in range(2):
   for s in range(ns):
    for iv,incoming in enumerate((0,1,full-1,full)):
     yield ('S',(mixed*ns+s)*4+iv,full,full,incoming,[sem[(s+i)%ns if mixed else s] for i in range(full)])
 else:
  for s in range(ns): yield ('S',s,1,1,0,[sem[s]])
  yield ('S',ns,ns,ns,0,sem)


def fpclass(v,width):
 sign=1<<(width-1); magnitude=v & (sign-1)
 inf=0x7f800000 if width==32 else 0x7ff0000000000000
 if magnitude>inf: return 'nan'
 if magnitude==inf: return 'neg_inf' if v&sign else 'pos_inf'
 if magnitude==0: return 'neg_zero' if v&sign else 'pos_zero'
 return 'finite_nonzero'


def finite(v,width):
 return fpclass(v,width) in ('finite_nonzero','neg_zero','pos_zero')


def ordered(v,width):
 sign=1<<(width-1);return ((~v)&((1<<width)-1)) if v&sign else v|sign


def distance(got,ref,width):
 if not finite(got,width) or not finite(ref,width): return None
 return abs(ordered(got,width)-ordered(ref,width))


def gate_exceeded(fn,iface,got,ref,width,limit):
 # Exact reproduction of the individual historic value predicate, not flags,
 # canaries, fallback policy, or the full gate's acceptance decision.
 if iface=='Nx' or fn=='pow':
  a=got & ((1<<(width-1))-1); b=ref & ((1<<(width-1))-1)
  inf=0x7f800000 if width==32 else 0x7ff0000000000000
  if b>inf: return a<=inf
  if b==inf or b==0: return got!=ref
 return abs(ordered(got,width)-ordered(ref,width))>limit


def fatal_line(line):
 return (line.startswith('RA_ERROR') or any(x.lower() in line.lower() for x in BAD)
         or FATAL.search(line) is not None or FAILED.search(line) is not None)


def identity_path(row,rd):
 snapshot=row.get('snapshot','')
 if snapshot:
  path=Path(snapshot).resolve()
  if rd/'snapshot' not in path.parents: raise ValueError('快照路径越界')
 else: path=Path(row['path'])
 if sha(path)!=row['sha256']: raise ValueError(f'身份哈希变化: {row["role"]}')
 return path


def check_observer(expected):
 # 分析和比较必须由与 run 快照完全相同的脚本执行，而非只检查归档副本。
 if not re.fullmatch(r'[0-9a-f]{64}',expected) or sha(Path(__file__))!=expected:
  raise ValueError('当前执行脚本与 run 记录的 observer SHA 不符')


def check_streams(row):
 for name in ('stdout','stderr'):
  p=Path(row[name])
  if not re.fullmatch(r'\d+',row.get(name+'_bytes','')) or p.stat().st_size!=int(row[name+'_bytes']) or sha(p)!=row.get(name+'_sha256'):
   raise ValueError(f'{row["label"]} {name} 长度或 SHA 不符')


def clean_status(rd,fn,ids,commands):
 for row in ids: identity_path(row,rd)
 def identity(role):
  rows=[r for r in ids if r['role']==role]
  if len(rows)!=1: raise ValueError(f'身份缺失或重复: {role}')
  return rows[0]
 binary,staged=identity('binary-'+fn),identity('staged-'+fn)
 identity('elf-'+fn);identity('archive');nemu=identity('nemu')
 if binary['sha256']!=staged['sha256']: raise ValueError('binary/staging 身份不一致')
 matches=[r for r in commands if r.get('label')=='nemu-'+fn]
 if len(matches)!=1 or matches[0]['status']!='0': raise ValueError(f'{fn}: NEMU 缺少唯一真实 rc=0 记录')
 row=matches[0];check_streams(row);argv=shlex.split(row['command'])
 if len(argv)!=4 or Path(argv[0]).resolve()!=Path(nemu['path']) or argv[1]!='-b' or not re.fullmatch(r'--max-instr=[1-9]\d*',argv[2]) or Path(argv[3]).resolve()!=Path(staged['path']):
  raise ValueError(f'{fn}: NEMU 命令与身份不符')
 if Path(row['cwd']).resolve()!=rd or Path(row['stdout']).resolve()!=rd/'logs'/('nemu-'+fn+'.stdout') or Path(row['stderr']).resolve()!=rd/'logs'/('nemu-'+fn+'.stderr') or Path(row['tmpdir']).resolve()!=rd/'tmp'/('nemu-'+fn):
  raise ValueError('NEMU cwd、输出或 TMP 不属于本次运行')
 out=ANSI.sub('',Path(row['stdout']).read_text(encoding='utf-8',errors='replace'))
 if len(GOOD.findall(out))!=1 or any(fatal_line(line) for line in out.splitlines()) or re.search(r'(?m)^RA_',out): raise ValueError(f'{fn}: NEMU stdout 没有唯一真实 GOOD TRAP 或混入 UART/错误')
 return Path(row['stderr'])


def checked_tls(rd,fn,ids,commands):
 def exactly_one(seq):
  if len(seq)!=1: raise ValueError('TLS 工具、ELF 或 readelf 命令缺失/重复')
  return seq[0]
 readelf=exactly_one([r for r in ids if r['role']=='readelf'])
 elf=exactly_one([r for r in ids if r['role']=='elf-'+fn])
 row=exactly_one([r for r in commands if r['label']=='tls-final-'+fn])
 argv=shlex.split(row['command'])
 if row['status']!='0' or len(argv)!=4 or Path(argv[0]).resolve()!=Path(readelf['path']) or argv[1:3]!=['-W','-s'] or Path(argv[3]).resolve()!=Path(elf['path']):
  raise ValueError('最终 ELF 的 readelf 命令或状态不符')
 if Path(row['cwd']).resolve()!=rd or Path(row['stdout']).resolve()!=rd/'logs'/('tls-final-'+fn+'.stdout'):
  raise ValueError('最终 ELF 的 readelf 输出位置不符')
 check_streams(row)
 return errno_offset(Path(row['stdout']).read_text(encoding='utf-8'))


def records(path):
 with path.open(encoding='utf-8',errors='strict',newline='') as stream:
  for raw in stream:
   if raw.endswith('\r\n'): body=raw[:-2]
   elif raw.endswith('\n'): body=raw[:-1]
   else: body=raw
   line=ANSI.sub('',body)
   if fatal_line(line): raise ValueError('NEMU stderr 含致命标记: '+line[:200])
   if line.startswith('RA_'):
    if not raw.endswith('\n') or '\r' in body: raise ValueError('目标记录缺少 LF 或包含额外 CR')
    yield line


def get_record(it,prefix):
 try: line=next(it)
 except StopIteration: raise ValueError(f'目标输出被截断，缺少 {prefix}') from None
 parts=line.split()
 if not parts or parts[0]!=prefix: raise ValueError(f'记录类型不符，期望 {prefix}: {line[:160]}')
 return parts


def parse_group(parts,fn,iface,m,rm,spec,corpus,width):
 if len(parts)!=13: raise ValueError('目标 group 字段数不符')
 kind,id_,n,emit,incoming,inputs=spec
 try:
  observed=(int(parts[1]),int(parts[2]),int(parts[3]),parts[4],int(parts[5]),int(parts[6]),int(parts[7]),int(parts[8]),int(parts[9]))
  err,flags=int(parts[10]),int(parts[11])
 except ValueError: raise ValueError('group 整数字段非法') from None
 if observed!=(iface,m,rm,kind,id_,n,emit,incoming,rm) or not 0<=flags<=31:
  raise ValueError(f'group 次序、n、输入数量或 frm 不符: {observed}, expected={spec[:5]}, rm={rm}')
 digits=width//4; binary=fn=='pow';stride=digits*(3 if binary else 2); payload=parts[12]
 if len(payload)!=stride*n or len(inputs)!=n or not re.fullmatch('[0-9a-f]+',payload): raise ValueError('目标位串长度不符、截断或非法')
 ys=[]
 for i,(wantx,wantz) in enumerate(inputs):
  chunk=payload[i*stride:(i+1)*stride]; x=int(chunk[:digits],16)
  z=int(chunk[digits:2*digits],16) if binary else 0
  y=int(chunk[-digits:],16)
  if (x,z)!=(wantx,wantz): raise ValueError(f'{fn}: {kind}{id_}:{i} 输入位不符')
  if i<emit: ys.append(y)
 row=dict(function=fn,interface='Nx' if iface==0 else 'array',lmul=m,frm=rm,kind=kind,group_id=id_,n=n,emitted=emit,incoming_vl=incoming,frm_after=observed[-1],flags=flags,errno=err)
 return row,ys


def analyze_function(fn,path,headers,lane_writer,group_writer,stats,pairs,expected_tls,semantic_source=None):
 # 发生错误时立刻关闭持有 UART 文件的生成器，避免 NFS .nfs 临时文件残留。
 with closing(records(path)) as it:
  return _analyze_function(fn,it,headers,lane_writer,group_writer,stats,pairs,expected_tls,semantic_source)


def _analyze_function(fn,it,headers,lane_writer,group_writer,stats,pairs,expected_tls,semantic_source):
 corpus=oracle(headers,fn);_,sem=semantics(fn,semantic_source);width=32 if fn in ('expf','logf') else 64; digits=width//4
 tls=get_record(it,'RA_TLS')
 if len(tls)!=8 or tls[1]!=fn: raise ValueError('TLS 记录缺失或函数不符')
 try: tp,base,anchor,anchor_value,actual,reported=map(int,tls[2:])
 except ValueError: raise ValueError('TLS 记录非整数') from None
 if not (tp==base==anchor and base>0 and anchor_value==1 and actual==reported==expected_tls):
  raise ValueError('TLS 基址、anchor 或 errno 偏移与最终 ELF 不符')
 config=get_record(it,'RA_CONFIG')
 if len(config)!=6 or config[1]!=fn: raise ValueError('目标配置缺失或函数不符')
 try: vlenb,count,ns,limit=map(int,config[2:])
 except ValueError: raise ValueError('目标配置非整数') from None
 if vlenb<16 or vlenb>2048 or vlenb&(vlenb-1) or 2*vlenb//(width//8)>512 or (count,ns,limit)!=(FIXTURES[fn][1],len(sem),FIXTURES[fn][2]):
  raise ValueError('VLEN、数量、语义数量或阈值与冻结配置不符')
 total_groups=total_values=0
 for iface in range(2):
  for m in (1,2):
   runs=[];semantic_samples=None
   for rm in range(5):
    values=array.array('I' if width==32 else 'Q'); special=[];descriptors=[]
    for spec in expected_groups(fn,iface,m,vlenb,corpus,sem):
     row,ys=parse_group(get_record(it,'RA_GROUP'),fn,iface,m,rm,spec,corpus,width)
     group_writer.writerow(row);total_groups+=1;total_values+=len(ys)
     if spec[0]=='C': values.extend(ys)
     else:
      for i,(x,z) in enumerate(spec[5]):
       descriptors.append((f'S{spec[1]}:{i}',x,z));special.append(ys[i])
    if len(values)!=count: raise ValueError('corpus 行数不符')
    if semantic_samples is None: semantic_samples=descriptors
    elif descriptors!=semantic_samples: raise ValueError('语义五模式输入组不一致')
    runs.append((values,special))
   interface='Nx' if iface==0 else 'array'; refs=corpus[2]
   counters=[dict(function=fn,interface=interface,lmul=m,frm=rm,n=count,finite_n=0,nonfinite_n=0,
       exact_bits=0,sum_ulp=0,max_ulp=0,ulp0=0,ulp1=0,ulp2=0,ulp3=0,ulp4plus=0,historic_predicate_exceeded=0,
       ref_pos_zero=0,ref_neg_zero=0,ref_pos_inf=0,ref_neg_inf=0,ref_nan=0,
       got_pos_zero=0,got_neg_zero=0,got_pos_inf=0,got_neg_inf=0,got_nan=0,
       zero_ref_exact_bits=0,inf_ref_exact_bits=0,nan_ref_exact_bits=0,nan_ref_got_nan=0) for rm in range(5)]
   compar=[dict(function=fn,interface=interface,lmul=m,frm=rm,paired_finite=0,closer_to_rne_reference=0,
       equal_distance=0,farther_from_rne_reference=0,nonfinite_unpaired=0) for rm in range(1,5)]
   for i in range(count):
    x=corpus[0][i];z=corpus[1][i] if corpus[1] is not None else None;ref=refs[i]
    ds=[]
    for rm,(ys,_) in enumerate(runs):
     y=ys[i];d=distance(y,ref,width);ds.append(d);c=counters[rm]
     c['exact_bits']+=int(y==ref);c['historic_predicate_exceeded']+=int(gate_exceeded(fn,interface,y,ref,width,limit))
     rc,gc=fpclass(ref,width),fpclass(y,width)
     if rc!='finite_nonzero': c['ref_'+rc]+=1
     if gc!='finite_nonzero': c['got_'+gc]+=1
     if rc in ('pos_zero','neg_zero'): c['zero_ref_exact_bits']+=int(y==ref)
     if rc in ('pos_inf','neg_inf'): c['inf_ref_exact_bits']+=int(y==ref)
     if rc=='nan': c['nan_ref_exact_bits']+=int(y==ref);c['nan_ref_got_nan']+=int(gc=='nan')
     if d is None: c['nonfinite_n']+=1
     else:
      c['finite_n']+=1;c['sum_ulp']+=d;c['max_ulp']=max(c['max_ulp'],d);c['ulp'+str(d) if d<4 else 'ulp4plus']+=1
     lane_writer.writerow((fn,interface,m,'C'+str(i),rm,f'{x:0{digits}x}',f'{z:0{digits}x}' if z is not None else '',f'{y:0{digits}x}',f'{ref:0{digits}x}', '' if d is None else d))
    for rm in range(1,5):
     c=compar[rm-1];a,b=ds[0],ds[rm]
     if a is None or b is None: c['nonfinite_unpaired']+=1
     else:
      c['paired_finite']+=1;c['closer_to_rne_reference']+=int(b<a);c['equal_distance']+=int(b==a);c['farther_from_rne_reference']+=int(b>a)
   for i,(sid,x,z) in enumerate(semantic_samples):
    for rm,(_,ys) in enumerate(runs):
     lane_writer.writerow((fn,interface,m,sid,rm,f'{x:0{digits}x}',f'{z:0{digits}x}' if fn=='pow' else '',f'{ys[i]:0{digits}x}','',''))
   for c in counters:
    c['mean_ulp']=format(c['sum_ulp']/c['finite_n'],'.9g') if c['finite_n'] else ''
    c['max_ulp']=c['max_ulp'] if c['finite_n'] else ''
    stats.append(c)
   pairs.extend(compar)
 complete=get_record(it,'RA_COMPLETE')
 if len(complete)!=5 or complete[1]!=fn or complete[2:]!=[str(total_groups),str(total_values),'0']: raise ValueError('目标完成计数或失败数不符')
 try: extra=next(it)
 except StopIteration: extra=None
 if extra is not None: raise ValueError(f'完成标记后仍有记录: {extra[:120]}')
 return vlenb,total_groups,total_values


def read_csv(path):
 with Path(path).open(newline='',encoding='utf-8') as f: return list(csv.DictReader(f))


def environment_digest(fs,ids,commands):
 # 排除预期不同的数学 archive、由其生成的 ELF/bin 与 staging；其余身份及 NEMU 限额一致。
 kept=sorted((r['role'],r['sha256']) for r in ids if r['role']!='archive' and not r['role'].startswith(('elf-','binary-','staged-')))
 settings=[]
 for f in fs:
  rows=[r for r in commands if r['label']=='nemu-'+f]
  if len(rows)!=1: raise ValueError('NEMU 设置缺失或重复')
  argv=shlex.split(rows[0]['command'])
  if len(argv)!=4 or not re.fullmatch(r'[1-9]\d*',rows[0].get('timeout_seconds','')): raise ValueError('NEMU 超时记录缺失或非法')
  settings.append((f,argv[2],rows[0]['timeout_seconds']))
 text='\n'.join('identity '+k+' '+v for k,v in kept)+'\n'+'\n'.join('nemu '+' '.join(s) for s in settings)
 return hashlib.sha256(text.encode('utf-8')).hexdigest()


def analyze(args):
 rd=args.run_dir.resolve();output=args.output_dir.resolve()
 if output.exists(): raise ValueError('output-dir 必须不存在')
 if not DATE.search(rd.name) or any('build' in p.lower() for p in rd.parts): raise ValueError('run-dir 名称或路径不符合运行目录约束')
 if rd not in output.parents: raise ValueError('分析输出必须放在相应独立运行目录下的新子目录')
 plan=read_csv(rd/'plan.csv')
 if len(plan)!=1 or plan[0]['role'] not in ('baseline','candidate') or plan[0]['build_only']!='0': raise ValueError('计划不存在、身份非法或仅构建尚未仿真')
 fs=selected(plan[0]['functions']);headers=Path(plan[0]['headers_dir'])
 ids=read_csv(rd/'identities.csv');cmd=read_csv(rd/'commands.csv')
 roles=[r['role'] for r in ids]
 if len(roles)!=len(set(roles)): raise ValueError('身份记录重复')
 observer=[r for r in ids if r['role']=='observer']
 if len(observer)!=1 or not observer[0].get('snapshot'): raise ValueError('observer 身份/快照缺失或重复')
 observer_sha=observer[0]['sha256'];check_observer(observer_sha)
 if any(r['status']!='0' for r in cmd): raise ValueError('存在未成功命令')
 for f in fs:
  expected=(headers/FIXTURES[f][0]).resolve()
  matches=[r for r in ids if r['role']=='oracle-'+f]
  if len(matches)!=1 or Path(matches[0]['path'])!=expected or matches[0]['sha256']!=FIXTURES[f][3]: raise ValueError('运行 oracle 身份不符')
 paths={f:clean_status(rd,f,ids,cmd) for f in fs};env_digest=environment_digest(fs,ids,cmd)
 snapshots={r['role']:identity_path(r,rd) for r in ids}
 for f in fs:
  for role in ('oracle-'+f,'semantic-'+f,'elf-'+f,'binary-'+f):
   row=next(r for r in ids if r['role']==role)
   if not row.get('snapshot'): raise ValueError(f'缺少快照: {role}')
 output.mkdir(parents=True);stats=[];paired=[];coverage=[]
 # .partial suffix makes a killed or rejected analysis visibly incomplete.
 lanes=output/'lanes.csv.gz.partial';groups=output/'groups.csv.gz.partial'
 with gzip.open(lanes,'xt',newline='',encoding='ascii') as l, gzip.open(groups,'wt',newline='',encoding='ascii') as g:
  lw=csv.writer(l);lw.writerow(LANE_FIELDS);gw=csv.DictWriter(g,fieldnames=GROUP_FIELDS);gw.writeheader()
  for f in fs:
   expected_tls=checked_tls(rd,f,ids,cmd)
   vlenb,ncases,nvalues=analyze_function(f,paths[f],snapshots['oracle-'+f].parent,lw,gw,stats,paired,expected_tls,snapshots['semantic-'+f])
   coverage.append(dict(function=f,vlenb=vlenb,groups=ncases,emitted_lanes=nvalues,corpus_samples=FIXTURES[f][1]))
 for row in ids: identity_path(row,rd)
 check_observer(observer_sha)
 for row in cmd:
  if row['label'].startswith(('nemu-','tls-final-')): check_streams(row)
 stat_fields=('function','interface','lmul','frm','n','finite_n','nonfinite_n','exact_bits','max_ulp','mean_ulp','sum_ulp','ulp0','ulp1','ulp2','ulp3','ulp4plus','historic_predicate_exceeded',
              'ref_pos_zero','ref_neg_zero','ref_pos_inf','ref_neg_inf','ref_nan','got_pos_zero','got_neg_zero','got_pos_inf','got_neg_inf','got_nan',
              'zero_ref_exact_bits','inf_ref_exact_bits','nan_ref_exact_bits','nan_ref_got_nan')
 csv_write(output/'summary.csv',stat_fields,stats)
 csv_write(output/'paired.csv',('function','interface','lmul','frm','paired_finite','closer_to_rne_reference','equal_distance','farther_from_rne_reference','nonfinite_unpaired'),paired)
 csv_write(output/'coverage.csv',('function','vlenb','groups','emitted_lanes','corpus_samples'),coverage)
 lanes.rename(output/'lanes.csv.gz');groups.rename(output/'groups.csv.gz')
 archive=next(r for r in ids if r['role']=='archive')
 rows=[dict(role='run_role',value=plan[0]['role']),dict(role='archive_sha256',value=archive['sha256']),dict(role='functions',value=','.join(fs)),
       dict(role='environment_sha256',value=env_digest),dict(role='observer_sha256',value=observer_sha)]
 for role,name in (('lanes','lanes.csv.gz'),('groups','groups.csv.gz'),('summary','summary.csv'),('paired','paired.csv'),('coverage','coverage.csv')):
  rows.append(dict(role=role+'_sha256',value=sha(output/name)))
 check_observer(observer_sha)
 csv_write(output/'COMPLETE.csv',('role','value'),rows)
 print(f'{output}: 完整性已核验；结果只表示与 RNE 舍入参考值的 ULP 距离')


def identity_map(directory):
 rows=read_csv(directory/'COMPLETE.csv'); out={r['role']:r['value'] for r in rows}
 files=(('lanes','lanes.csv.gz'),('groups','groups.csv.gz'),('summary','summary.csv'),('paired','paired.csv'),('coverage','coverage.csv'))
 expected={'run_role','archive_sha256','functions','environment_sha256','observer_sha256'} | {role+'_sha256' for role,_ in files}
 if len(out)!=len(rows) or set(out)!=expected: raise ValueError('分析完成清单不完整')
 for role,name in files:
  if sha(directory/name)!=out[role+'_sha256']: raise ValueError('分析数据身份变化: '+name)
 return out


def compare(args):
 first=args.baseline.resolve();second=args.candidate.resolve();out=args.output.resolve()
 if out.exists(): raise ValueError('比较输出不得覆盖')
 if first==second: raise ValueError('baseline 和 candidate 必须为独立运行结果')
 a=identity_map(first);b=identity_map(second)
 if a['run_role']!='baseline' or b['run_role']!='candidate' or a['functions']!=b['functions']: raise ValueError('baseline/candidate 角色或函数集不匹配')
 if a['archive_sha256']==b['archive_sha256']: raise ValueError('baseline/candidate 的数学库完全相同；请先核对身份')
 if a['environment_sha256']!=b['environment_sha256'] or a['observer_sha256']!=b['observer_sha256']: raise ValueError('编译器、其他库、采样源码、oracle 或模拟器配置不同；拒绝比较')
 observer_sha=a['observer_sha256'];check_observer(observer_sha)
 for d in (first,second):
  if not any(DATE.search(p.name) and all('build' not in x.lower() for x in p.parts) for p in [d,*d.parents]): raise ValueError('输入不属于带日期的运行目录')
 if first not in out.parents: raise ValueError('比较输出必须位于 baseline 已核验分析目录内')
 rows={}
 with gzip.open(first/'lanes.csv.gz','rt',newline='',encoding='ascii') as f1,gzip.open(second/'lanes.csv.gz','rt',newline='',encoding='ascii') as f2:
  r1=csv.DictReader(f1);r2=csv.DictReader(f2)
  if tuple(r1.fieldnames or ())!=LANE_FIELDS or tuple(r2.fieldnames or ())!=LANE_FIELDS: raise ValueError('lane CSV 标题不符')
  for x,y in itertools.zip_longest(r1,r2):
   if x is None or y is None: raise ValueError('两运行样本数不等')
   if any(x[k]!=y[k] for k in LANE_FIELDS if k not in ('y_bits','rne_ref_ulp')): raise ValueError('两运行的模式、调用样本、输入或参考位不一致')
   key=tuple(x[k] for k in ('function','interface','lmul','frm'))
   row=rows.setdefault(key,dict(zip(('function','interface','lmul','frm'),key),n=0,bit_difference=0,closer=0,equal_distance=0,farther=0,nonfinite_or_semantic=0))
   row['n']+=1;row['bit_difference']+=int(x['y_bits']!=y['y_bits'])
   if not x['ref_bits']: row['nonfinite_or_semantic']+=1;continue
   width=4*len(x['ref_bits']);ref=int(x['ref_bits'],16);da=distance(int(x['y_bits'],16),ref,width);db=distance(int(y['y_bits'],16),ref,width)
   if da is None or db is None: row['nonfinite_or_semantic']+=1
   else: row['closer']+=int(db<da);row['equal_distance']+=int(db==da);row['farther']+=int(db>da)
 check_observer(observer_sha)
 csv_write(out,('function','interface','lmul','frm','n','bit_difference','closer','equal_distance','farther','nonfinite_or_semantic'),rows.values())
 print(f'{out}: 比较的是相同输入与同一 RNE 舍入参考的距离，不是数学误差优劣')


def main(argv=None):
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
 r=sub.add_parser('run',help='仅构建，或显式提供 NEMU 运行；每函数一个镜像')
 for arg in ('gcc','archive','scalar-libm','headers-dir','build-dir','run-dir'): r.add_argument('--'+arg,type=Path,required=True)
 r.add_argument('--role',choices=('baseline','candidate'),required=True);r.add_argument('--functions',default=','.join(FUNCTIONS))
 r.add_argument('--build-only',action='store_true');r.add_argument('--nemu',type=Path);r.add_argument('--max-instr',type=int);r.add_argument('--timeout',type=int)
 a=sub.add_parser('analyze',help='核验 NEMU、样本和状态并生成逐 lane CSV、分布及配对统计')
 a.add_argument('--run-dir',type=Path,required=True);a.add_argument('--output-dir',type=Path,required=True)
 c=sub.add_parser('compare',help='基于两个已核验运行按相同样本比较')
 c.add_argument('--baseline',type=Path,required=True);c.add_argument('--candidate',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
 args=p.parse_args(argv)
 try:
  {'run':run,'analyze':analyze,'compare':compare}[args.action](args)
 except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError) as exc:
  p.exit(2,f'精度观察停止：{exc}\n')
 return 0


if __name__=='__main__': sys.exit(main())
